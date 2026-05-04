"""Bridge WebSocket browser ↔ Gemini 3.1 Flash Live + tools de Sprintia.

Demo paralela a ElevenLabs Conversational AI. La GEMINI_API_KEY se queda
SIEMPRE en servidor (nunca llega al browser). El cliente solo abre un
WebSocket contra este bridge.

Flujo por conexión:

   Browser                     Bridge (este módulo)            Gemini Live
   ───────                     ─────────────────────           ───────────
   getUserMedia 16kHz PCM16
        │
        │  WS binary (chunks)
        ▼
                          recv_browser():
                              session.send_realtime_input(Blob 16kHz)
                                                                ▼
                                                          (modelo procesa)
                                                                │
                                                                ▼ audio 24kHz
                          recv_gemini():
                              ws.send_bytes(audio)
        ▼
   AudioContext 24kHz
   reproduce

Tools: cuando Gemini emite tool_call, hacemos POST loopback a /tools/* con
el X-Tool-Secret cargado de settings y devolvemos send_tool_response. El
frontend recibe eventos JSON (`tool_start`, `tool_end`, transcripciones,
status) por el mismo WebSocket en frames de texto.

Endpoint público:
   GET   /gemini-demo        → UI HTML (inline en este módulo)
   WS    /gemini-demo/ws     → bridge bidireccional

Sin auth: la página es accesible públicamente. La API key sigue protegida
porque vive en el server. Si quisiéramos restringir el acceso, basic auth
HTTP por delante con un usuario+pass distinto al admin del CMS.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import httpx
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse

from .config import settings

log = logging.getLogger(__name__)
router = APIRouter(prefix="/gemini-demo", tags=["gemini-live-demo"])


# ---------------------------------------------------------------------------
# Carga perezosa del SDK + render del prompt.
# ---------------------------------------------------------------------------

_PROMPT_CACHE: tuple[str, str] | None = None  # (fecha_iso_hoy, prompt)
_DIA_ES = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
_MES_ES = [
    "enero", "febrero", "marzo", "abril", "mayo", "junio",
    "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre",
]
_TZ = ZoneInfo("Europe/Madrid")


def _fecha_natural(d) -> str:
    return f"{_DIA_ES[d.weekday()]} {d.day} de {_MES_ES[d.month - 1]}"


def _render_prompt(caller_id: str) -> str:
    """Render del ana_prompt_new.txt con fechas locales y variables ElevenLabs."""
    global _PROMPT_CACHE
    now = datetime.now(_TZ)
    hoy = now.date()
    cache_key = hoy.isoformat() + "|" + caller_id
    if _PROMPT_CACHE and _PROMPT_CACHE[0] == cache_key:
        return _PROMPT_CACHE[1]

    # ana_prompt_new.txt vive en la raíz del repo.
    prompt_path = os.path.join(os.path.dirname(__file__), "..", "ana_prompt_new.txt")
    raw = open(prompt_path, "r", encoding="utf-8").read()

    manana = hoy + timedelta(days=1)
    pasado = hoy + timedelta(days=2)
    sustituciones = {
        "__HOY_FECHA__": hoy.isoformat(),
        "__MANANA_FECHA__": manana.isoformat(),
        "__PASADO_FECHA__": pasado.isoformat(),
        "__HOY_DIA_NATURAL__": _fecha_natural(hoy),
        "__MANANA_DIA_NATURAL__": _fecha_natural(manana),
        "__PASADO_DIA_NATURAL__": _fecha_natural(pasado),
        "__ANO_ACTUAL__": str(hoy.year),
    }
    out = raw
    for k, v in sustituciones.items():
        out = out.replace(k, v)
    out = out.replace("{{system__time}}", now.strftime("%Y-%m-%d %H:%M %z"))
    out = out.replace("{{system__caller_id}}", caller_id or "unknown")

    out += (
        "\n\n## NOTA TÉCNICA (no recitar)\n"
        "Modelo: Gemini 3.1 Flash Live native audio. Habla SIEMPRE en español "
        "de España, con acento castellano peninsular. NUNCA uses inglés bajo "
        "ningún concepto. Si por error sale algo en inglés, corrige inmediatamente.\n"
    )

    _PROMPT_CACHE = (cache_key, out)
    return out


# ---------------------------------------------------------------------------
# Tool declarations (mismas que en demo_gemini_live/tools_adapter.py).
# ---------------------------------------------------------------------------

TOOL_DECLARATIONS: list[dict[str, Any]] = [
    {
        "name": "consultar_disponibilidad",
        "description": (
            "Consulta huecos libres en el calendario. Usar SIEMPRE antes de "
            "proponer una hora al cliente."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "fecha_desde_iso": {"type": "string", "description": "ISO local sin Z, ej 2026-05-04T16:00:00."},
                "fecha_hasta_iso": {"type": "string", "description": "ISO local sin Z."},
                "duracion_minutos": {"type": "integer", "description": "5-240 min."},
                "peluquero_preferido": {"type": "string", "description": "Nombre o vacío."},
                "max_resultados": {"type": "integer", "description": "1-15. Default 5."},
            },
            "required": ["fecha_desde_iso", "fecha_hasta_iso", "duracion_minutos"],
        },
    },
    {
        "name": "crear_reserva",
        "description": "Crea una cita tras confirmar hora con el cliente.",
        "parameters": {
            "type": "object",
            "properties": {
                "titulo": {"type": "string", "description": "'Nombre — Servicio (Peluquero|sin preferencia)'"},
                "inicio_iso": {"type": "string"},
                "fin_iso": {"type": "string"},
                "telefono_cliente": {"type": "string"},
                "peluquero": {"type": "string", "description": "Nombre o 'sin preferencia'."},
                "notas": {"type": "string"},
            },
            "required": ["titulo", "inicio_iso", "fin_iso", "peluquero"],
        },
    },
    {
        "name": "buscar_reserva_cliente",
        "description": "Busca próxima reserva por teléfono y/o nombre. Llamar antes de mover/cancelar.",
        "parameters": {
            "type": "object",
            "properties": {
                "telefono_cliente": {"type": "string"},
                "nombre_cliente": {"type": "string"},
                "dias_adelante": {"type": "integer", "description": "Default 30."},
            },
            "required": [],
        },
    },
    {
        "name": "mover_reserva",
        "description": "Cambia hora de una reserva. Reenvía calendar_id si lo tienes.",
        "parameters": {
            "type": "object",
            "properties": {
                "event_id": {"type": "string"},
                "nuevo_inicio_iso": {"type": "string"},
                "nuevo_fin_iso": {"type": "string"},
                "peluquero": {"type": "string"},
                "calendar_id": {"type": "string"},
            },
            "required": ["event_id", "nuevo_inicio_iso", "nuevo_fin_iso"],
        },
    },
    {
        "name": "cancelar_reserva",
        "description": "Cancela reserva. Reenvía calendar_id si lo tienes.",
        "parameters": {
            "type": "object",
            "properties": {
                "event_id": {"type": "string"},
                "calendar_id": {"type": "string"},
            },
            "required": ["event_id"],
        },
    },
    {
        "name": "end_call",
        "description": "Cierra la conversación tras despedida del cliente.",
        "parameters": {
            "type": "object",
            "properties": {
                "motivo": {"type": "string"},
            },
            "required": [],
        },
    },
]


_TOOL_PATHS: dict[str, str] = {
    "consultar_disponibilidad": "/tools/consultar_disponibilidad",
    "crear_reserva": "/tools/crear_reserva",
    "buscar_reserva_cliente": "/tools/buscar_reserva_cliente",
    "mover_reserva": "/tools/mover_reserva",
    "cancelar_reserva": "/tools/cancelar_reserva",
}


# ---------------------------------------------------------------------------
# Tool handler (loopback HTTP a este mismo backend).
# ---------------------------------------------------------------------------

async def _call_tool(http: httpx.AsyncClient, name: str, args: dict[str, Any],
                     tenant_id: str, caller_id: str) -> dict[str, Any]:
    """Llama a /tools/<name> en este mismo backend (loopback localhost)."""
    if name == "end_call":
        return {"ok": True, "closed": True}
    path = _TOOL_PATHS.get(name)
    if not path:
        return {"ok": False, "error": f"Tool desconocida '{name}'"}

    # Loopback: llamamos a 127.0.0.1 en el puerto donde está corriendo uvicorn.
    # Railway expone el contenedor en $PORT; uvicorn lo lee. Si no hay $PORT,
    # caemos a 8080 (puerto típico de Railway).
    port = os.environ.get("PORT", "8080")
    url = f"http://127.0.0.1:{port}{path}"
    params = {"tenant_id": tenant_id, "caller_id": caller_id}
    headers = {"X-Tool-Secret": settings.tool_secret}

    try:
        resp = await http.post(url, params=params, json=args, headers=headers, timeout=20.0)
        if resp.status_code >= 400:
            log.warning("tool %s HTTP %s: %s", name, resp.status_code, resp.text[:200])
            return {
                "ok": False,
                "error": f"Backend devolvió HTTP {resp.status_code}",
                "detail": resp.text[:200],
                "retryable": resp.status_code >= 500,
            }
        return resp.json()
    except httpx.TimeoutException:
        log.warning("tool %s timeout", name)
        return {"ok": False, "error": "Timeout llamando al backend.", "retryable": True}
    except Exception as e:  # noqa: BLE001
        log.exception("tool %s error inesperado", name)
        return {
            "ok": False,
            "error": "Fallo de red llamando al backend.",
            "detail": str(e)[:200],
            "retryable": True,
        }


# ---------------------------------------------------------------------------
# Endpoint HTML (UI estática inline).
# ---------------------------------------------------------------------------

_HTML_PATH = os.path.join(os.path.dirname(__file__), "templates", "gemini_demo.html")


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
async def gemini_demo_page() -> HTMLResponse:
    try:
        with open(_HTML_PATH, "r", encoding="utf-8") as f:
            return HTMLResponse(f.read())
    except FileNotFoundError:
        return HTMLResponse(
            "<h1>Demo Gemini Live</h1><p>Falta app/templates/gemini_demo.html</p>",
            status_code=500,
        )


# ---------------------------------------------------------------------------
# WebSocket endpoint: el bridge.
# ---------------------------------------------------------------------------

@router.websocket("/ws")
async def gemini_demo_ws(ws: WebSocket) -> None:
    await ws.accept()

    # Comprobamos que GEMINI_API_KEY está configurada antes de hacer nada.
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        await ws.send_json({
            "type": "error",
            "message": "GEMINI_API_KEY no configurada en este despliegue. "
                       "Pídele al admin que la añada a Railway env vars.",
        })
        await ws.close()
        return

    # Imports perezosos del SDK — si google-genai no está instalado, devolvemos
    # error legible en vez de 500 al levantar el módulo.
    try:
        from google import genai
        from google.genai import types
    except ImportError as e:
        await ws.send_json({
            "type": "error",
            "message": f"google-genai no instalado en el server: {e}. "
                       "pip install google-genai>=1.5.0",
        })
        await ws.close()
        return

    # Parámetros desde query string del WS (configurables desde el frontend):
    qp = ws.query_params
    tenant_id = qp.get("tenant_id") or "pelu_demo"
    caller_id = qp.get("caller_id") or "+34600000000"
    voice = qp.get("voice") or "Kore"
    model = qp.get("model") or "gemini-3.1-flash-live-preview"

    log.info("gemini-demo WS: tenant=%s voice=%s model=%s", tenant_id, voice, model)

    # Cliente Gemini.
    client = genai.Client(api_key=api_key)

    config = {
        "response_modalities": ["AUDIO"],
        "system_instruction": _render_prompt(caller_id),
        "speech_config": {
            "voice_config": {
                "prebuilt_voice_config": {"voice_name": voice},
            },
        },
        "tools": [{"function_declarations": TOOL_DECLARATIONS}],
        "input_audio_transcription": {},
        "output_audio_transcription": {},
        # VAD: dejamos los defaults de Gemini. Probamos con sensitivity LOW +
        # prefix_padding 20ms y resultaba que el usuario tenía que gritar para
        # que el modelo registrara start_of_speech del 2º turno. Default funciona
        # mejor en práctica.
    }

    http = httpx.AsyncClient(timeout=httpx.Timeout(20.0, connect=5.0))
    shutdown = asyncio.Event()

    async def send_evt(payload: dict[str, Any]) -> None:
        """Envía un evento JSON al frontend (logs, transcripciones, tools)."""
        try:
            await ws.send_json(payload)
        except Exception:  # noqa: BLE001
            pass

    try:
        async with client.aio.live.connect(model=model, config=config) as session:
            await send_evt({"type": "ready", "model": model, "voice": voice, "tenant_id": tenant_id})

            # ---- Task A: browser → Gemini ----
            chunks_recv = 0
            bytes_recv = 0
            async def browser_to_gemini() -> None:
                nonlocal chunks_recv, bytes_recv
                try:
                    while not shutdown.is_set():
                        msg = await ws.receive()
                        if msg["type"] == "websocket.disconnect":
                            shutdown.set()
                            return
                        # Audio crudo viene en bytes binarios (PCM16 16kHz mono).
                        if "bytes" in msg and msg["bytes"]:
                            chunks_recv += 1
                            bytes_recv += len(msg["bytes"])
                            if chunks_recv % 40 == 0:
                                # ~2s de audio (a 50ms/chunk). Loguea para confirmar
                                # que el browser sigue mandando audio durante toda
                                # la sesión (ayuda a discriminar fallo de browser
                                # vs fallo de VAD del modelo).
                                log.info(
                                    "gemini-demo audio in: chunks=%d bytes=%d (~%.1fs)",
                                    chunks_recv, bytes_recv, chunks_recv * 0.05,
                                )
                            try:
                                await session.send_realtime_input(
                                    audio=types.Blob(
                                        data=msg["bytes"],
                                        mime_type="audio/pcm;rate=16000",
                                    ),
                                )
                            except Exception as exc:
                                log.error("send_realtime_input EXCEPCION chunk=%d: %s", chunks_recv, exc)
                                shutdown.set()
                                return
                        elif "text" in msg and msg["text"]:
                            # Mensajes de control desde el browser (p.ej. "stop").
                            try:
                                ctrl = json.loads(msg["text"])
                            except Exception:
                                continue
                            if ctrl.get("type") == "stop":
                                shutdown.set()
                                return
                except WebSocketDisconnect:
                    shutdown.set()
                except Exception:  # noqa: BLE001
                    log.exception("browser_to_gemini caído")
                    shutdown.set()

            # ---- Task B: Gemini → browser ----
            evt_count = 0
            async def gemini_to_browser() -> None:
                nonlocal evt_count
                try:
                    async for response in session.receive():
                        if shutdown.is_set():
                            return
                        evt_count += 1
                        # DIAG VERBOSO: loguea el "shape" de cada evento
                        # (tipos de campos no None) cada N eventos para ver
                        # qué llega.
                        # LOG cada evento (modo debug agresivo)
                        if True:
                            shape = []
                            if response.data: shape.append(f"data={len(response.data)}b")
                            if response.text: shape.append(f"text={len(response.text)}c")
                            sc_dbg = getattr(response, "server_content", None)
                            if sc_dbg is not None:
                                if getattr(sc_dbg, "interrupted", None): shape.append("interrupted")
                                if getattr(sc_dbg, "turn_complete", None): shape.append("turn_complete")
                                if getattr(sc_dbg, "generation_complete", None): shape.append("generation_complete")
                                if getattr(sc_dbg, "input_transcription", None): shape.append("input_tr")
                                if getattr(sc_dbg, "output_transcription", None): shape.append("output_tr")
                            tc_dbg = getattr(response, "tool_call", None)
                            if tc_dbg: shape.append("tool_call")
                            log.info("gemini-demo evt #%d: %s", evt_count, ",".join(shape) or "empty")
                        # Audio del modelo (24kHz PCM16) → binary frame al browser.
                        if response.data:
                            try:
                                await ws.send_bytes(response.data)
                            except Exception:
                                shutdown.set()
                                return

                        sc = getattr(response, "server_content", None)
                        if sc is not None:
                            if getattr(sc, "interrupted", False):
                                await send_evt({"type": "interrupted"})
                            inp_tr = getattr(sc, "input_transcription", None)
                            if inp_tr and getattr(inp_tr, "text", None):
                                await send_evt({"type": "user_transcript", "text": inp_tr.text})
                            out_tr = getattr(sc, "output_transcription", None)
                            if out_tr and getattr(out_tr, "text", None):
                                await send_evt({"type": "assistant_transcript", "text": out_tr.text})

                        tc = getattr(response, "tool_call", None)
                        if tc and getattr(tc, "function_calls", None):
                            await _resolve_tools(tc.function_calls, session, types)

                        tcc = getattr(response, "tool_call_cancellation", None)
                        if tcc and getattr(tcc, "ids", None):
                            await send_evt({"type": "tool_cancelled", "ids": list(tcc.ids)})

                        # DIAG: loguea eventos de fin de turno y generación.
                        # Con esto en logs de Railway sabremos si el modelo
                        # cierra turnos cuando debe (turn_complete) o si se
                        # queda colgado.
                        if sc is not None:
                            if getattr(sc, "turn_complete", False):
                                log.info("gemini-demo: turn_complete=True (modelo terminó turno)")
                                await send_evt({"type": "turn_complete"})
                            if getattr(sc, "generation_complete", False):
                                log.info("gemini-demo: generation_complete=True")
                    log.warning("gemini-demo: async for response in session.receive() SALIO normalmente — sesion cerrada por el modelo (evts=%d)", evt_count)
                    shutdown.set()
                except Exception:  # noqa: BLE001
                    log.exception("gemini_to_browser caído")
                    shutdown.set()

            async def _resolve_tools(function_calls, session, types_mod) -> None:
                """Resuelve tool_calls en paralelo y devuelve send_tool_response."""
                async def _one(fc):
                    args = dict(fc.args or {})
                    await send_evt({"type": "tool_start", "name": fc.name, "args": _safe_args(args)})
                    result = await _call_tool(http, fc.name, args, tenant_id, caller_id)
                    await send_evt({"type": "tool_end", "name": fc.name, "result": _trunc(result, 600)})
                    if fc.name == "end_call":
                        async def _close_later():
                            await asyncio.sleep(2.0)
                            shutdown.set()
                        asyncio.create_task(_close_later())
                    return types_mod.FunctionResponse(
                        id=fc.id,
                        name=fc.name,
                        response=result if isinstance(result, dict) else {"result": result},
                    )

                resps = await asyncio.gather(*[_one(fc) for fc in function_calls])
                await session.send_tool_response(function_responses=resps)

            await asyncio.gather(
                browser_to_gemini(),
                gemini_to_browser(),
                _wait_event(shutdown),
            )
    except Exception as e:  # noqa: BLE001
        log.exception("gemini-demo WS: error en sesión")
        await send_evt({"type": "error", "message": f"Sesión Gemini cayó: {str(e)[:200]}"})
    finally:
        try:
            await http.aclose()
        except Exception:
            pass
        try:
            await ws.close()
        except Exception:
            pass
        log.info("gemini-demo WS: sesión cerrada")


async def _wait_event(ev: asyncio.Event) -> None:
    await ev.wait()


def _safe_args(args: dict[str, Any]) -> dict[str, Any]:
    """Trunca/redacta args para mostrar en frontend sin filtrar tel completos."""
    out = {}
    for k, v in args.items():
        if k == "telefono_cliente" and isinstance(v, str) and len(v) > 4:
            out[k] = v[:3] + "***" + v[-2:]
        else:
            sv = str(v)
            out[k] = sv if len(sv) <= 80 else sv[:77] + "..."
    return out


def _trunc(obj: Any, n: int) -> Any:
    s = json.dumps(obj, ensure_ascii=False) if not isinstance(obj, str) else obj
    return s if len(s) <= n else s[: n - 3] + "..."
