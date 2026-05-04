"""Adaptador de las tools del backend Sprintia al formato de Gemini Live.

Dos partes:

1) DECLARACIONES — esquemas JSON que se le pasan a Gemini en el `LiveConnectConfig`.
   Mismas tools que ElevenLabs invoca en producción, traducidas al schema que
   espera google-genai (function_declarations).

2) HANDLER — una función `handle_function_call(name, args)` que ejecuta la
   llamada HTTP real contra el backend de Railway y devuelve el dict que Gemini
   espera en `FunctionResponse.response`.

NOTA importante (briefing de la doc de Gemini 3.1 Flash Live):
    Function calling es SÍNCRONO. El modelo se queda mudo bloqueado hasta que
    le devolvemos `send_tool_response`. No existe equivalente al `pre_tool_speech`
    de ElevenLabs. Si la tool tarda >500ms el silencio se nota — por eso el
    cliente reproduce un beep/ack discreto cuando arranca una tool call.
"""
from __future__ import annotations

import logging
from typing import Any

import httpx

from config import settings

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 1) Declaraciones de tools en formato Gemini.
# ---------------------------------------------------------------------------

# Mismos nombres y mismos parámetros que en app/eleven_tools.py para que el
# prompt de Ana funcione 1:1 sin tocarlo.
TOOL_DECLARATIONS: list[dict[str, Any]] = [
    {
        "name": "consultar_disponibilidad",
        "description": (
            "Consulta huecos libres en el calendario para una franja y duración. "
            "Devuelve hasta `max_resultados` huecos ordenados por hora. Usar SIEMPRE "
            "antes de proponer una hora al cliente."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "fecha_desde_iso": {
                    "type": "string",
                    "description": "Inicio del rango en ISO 8601 local sin Z y sin offset, p.ej. 2026-05-04T16:00:00.",
                },
                "fecha_hasta_iso": {
                    "type": "string",
                    "description": "Fin del rango en ISO 8601 local sin Z y sin offset, p.ej. 2026-05-04T18:00:00.",
                },
                "duracion_minutos": {
                    "type": "integer",
                    "description": "Duración del servicio en minutos (5-240).",
                },
                "peluquero_preferido": {
                    "type": "string",
                    "description": "Nombre del peluquero pedido por el cliente, o cadena vacía / 'sin preferencia' si da igual.",
                },
                "max_resultados": {
                    "type": "integer",
                    "description": "Máximo de huecos a devolver (1-15). Usa 5 por defecto.",
                },
            },
            "required": ["fecha_desde_iso", "fecha_hasta_iso", "duracion_minutos"],
        },
    },
    {
        "name": "crear_reserva",
        "description": (
            "Crea una cita en el calendario tras confirmar hora con el cliente. "
            "Devuelve event_id y peluquero asignado."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "titulo": {
                    "type": "string",
                    "description": "Título del evento, formato 'Nombre — Servicio (Peluquero|sin preferencia)'.",
                },
                "inicio_iso": {
                    "type": "string",
                    "description": "Inicio en ISO 8601 local sin Z, p.ej. 2026-05-04T17:00:00.",
                },
                "fin_iso": {
                    "type": "string",
                    "description": "Fin en ISO 8601 local sin Z. La duración debe estar entre 5 y 240 min.",
                },
                "telefono_cliente": {
                    "type": "string",
                    "description": "Teléfono del cliente en formato internacional. Si no lo tienes, deja vacío.",
                },
                "peluquero": {
                    "type": "string",
                    "description": "Nombre del peluquero o 'sin preferencia' (asignación walk-in en el backend).",
                },
                "notas": {
                    "type": "string",
                    "description": "Notas extra (alergias, etc.). Vacío si no hay.",
                },
            },
            "required": ["titulo", "inicio_iso", "fin_iso", "peluquero"],
        },
    },
    {
        "name": "buscar_reserva_cliente",
        "description": (
            "Busca la próxima reserva del cliente por teléfono y/o por nombre. "
            "Llamar antes de mover o cancelar. Devuelve event_id y calendar_id "
            "que hay que reenviar a las siguientes tools."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "telefono_cliente": {
                    "type": "string",
                    "description": "Teléfono del cliente. Vacío si solo se busca por nombre.",
                },
                "nombre_cliente": {
                    "type": "string",
                    "description": "Nombre con el que se reservó. Vacío si solo se busca por teléfono.",
                },
                "dias_adelante": {
                    "type": "integer",
                    "description": "Cuántos días hacia delante mirar. 30 por defecto.",
                },
            },
            # Sin requeridos: hay que mandar al menos uno de los dos, validado en backend.
            "required": [],
        },
    },
    {
        "name": "mover_reserva",
        "description": (
            "Cambia la hora de una reserva existente. Requiere event_id obtenido "
            "previamente con buscar_reserva_cliente. Reenvía calendar_id si lo "
            "tienes (acelera 200-1500ms)."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "event_id": {
                    "type": "string",
                    "description": "ID del evento de Google Calendar.",
                },
                "nuevo_inicio_iso": {
                    "type": "string",
                    "description": "Nuevo inicio en ISO 8601 local sin Z.",
                },
                "nuevo_fin_iso": {
                    "type": "string",
                    "description": "Nuevo fin en ISO 8601 local sin Z.",
                },
                "peluquero": {
                    "type": "string",
                    "description": "Si se mueve a otro peluquero, su nombre. Vacío para mantener el actual.",
                },
                "calendar_id": {
                    "type": "string",
                    "description": "calendar_id devuelto por buscar_reserva_cliente. Acelera la operación.",
                },
            },
            "required": ["event_id", "nuevo_inicio_iso", "nuevo_fin_iso"],
        },
    },
    {
        "name": "cancelar_reserva",
        "description": (
            "Cancela una reserva existente. Requiere event_id de "
            "buscar_reserva_cliente. Reenvía calendar_id si lo tienes."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "event_id": {
                    "type": "string",
                    "description": "ID del evento de Google Calendar a cancelar.",
                },
                "calendar_id": {
                    "type": "string",
                    "description": "calendar_id devuelto por buscar_reserva_cliente.",
                },
            },
            "required": ["event_id"],
        },
    },
    {
        "name": "end_call",
        "description": (
            "Cierra la conversación. Llamar SOLO cuando el cliente se despide o "
            "ha confirmado la reserva y dice que no necesita nada más, después "
            "de tu última frase de cierre ('venga, hasta luego')."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "motivo": {
                    "type": "string",
                    "description": "Razón corta del cierre, p.ej. 'reserva confirmada' o 'despedida cliente'.",
                },
            },
            "required": [],
        },
    },
]


# ---------------------------------------------------------------------------
# 2) Handler que ejecuta la tool contra el backend.
# ---------------------------------------------------------------------------

# Map tool_name -> ruta en el backend. end_call es local, no llama a nada.
_TOOL_PATHS: dict[str, str] = {
    "consultar_disponibilidad": "/tools/consultar_disponibilidad",
    "crear_reserva": "/tools/crear_reserva",
    "buscar_reserva_cliente": "/tools/buscar_reserva_cliente",
    "mover_reserva": "/tools/mover_reserva",
    "cancelar_reserva": "/tools/cancelar_reserva",
}


# Cliente HTTP reusable. Cerramos al final del programa.
_http_client: httpx.AsyncClient | None = None


def _get_client() -> httpx.AsyncClient:
    global _http_client
    if _http_client is None:
        _http_client = httpx.AsyncClient(
            timeout=httpx.Timeout(20.0, connect=5.0),
            headers={"X-Tool-Secret": settings.tool_secret},
        )
    return _http_client


async def close_http_client() -> None:
    global _http_client
    if _http_client is not None:
        await _http_client.aclose()
        _http_client = None


async def handle_function_call(name: str, args: dict[str, Any]) -> dict[str, Any]:
    """Ejecuta una tool call de Gemini. Devuelve el dict que va a `FunctionResponse.response`.

    Importante: SIEMPRE devolver un dict, nunca lanzar — si lanzamos, el bucle
    receive() de Gemini se queda esperando y el modelo no continúa.
    """
    log.info("tool call: %s args=%s", name, _safe_args_repr(args))

    if name == "end_call":
        # Local, no toca al backend. El bucle principal lo intercepta antes
        # de llamar aquí; este return es por si algún día se reordena.
        return {"ok": True, "closed": True}

    path = _TOOL_PATHS.get(name)
    if path is None:
        log.warning("tool desconocida: %s", name)
        return {"ok": False, "error": f"Tool desconocida '{name}'"}

    url = f"{settings.backend_url}{path}"
    params = {
        "tenant_id": settings.tenant_id,
        # `caller_id` solo lo usan crear_reserva y buscar_reserva_cliente, pero
        # mandarlo en todas no rompe nada (los demás endpoints lo ignoran).
        "caller_id": settings.caller_id,
    }
    try:
        client = _get_client()
        resp = await client.post(url, params=params, json=args)
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
        return {
            "ok": False,
            "error": "Timeout llamando al backend.",
            "retryable": True,
        }
    except Exception as e:  # noqa: BLE001
        log.exception("tool %s error inesperado", name)
        return {
            "ok": False,
            "error": "Fallo de red llamando al backend.",
            "detail": str(e)[:200],
            "retryable": True,
        }


def _safe_args_repr(args: dict[str, Any]) -> str:
    """Recorta repr de args para logs sin filtrar el teléfono completo."""
    if not args:
        return "{}"
    redacted = dict(args)
    if "telefono_cliente" in redacted and redacted["telefono_cliente"]:
        tel = str(redacted["telefono_cliente"])
        if len(tel) > 4:
            redacted["telefono_cliente"] = tel[:3] + "***" + tel[-2:]
    return repr(redacted)[:300]
