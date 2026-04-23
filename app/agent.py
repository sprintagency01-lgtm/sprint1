"""Agente LLM con function calling (OpenAI).

El agente recibe el mensaje del cliente + el historial + el prompt del tenant
y decide:
- Contestar texto directamente.
- Llamar a una de las funciones (consultar_disponibilidad, crear_reserva, ...).

Las funciones se ejecutan en el backend contra Google Calendar (seguro:
el LLM NO escribe nunca al calendario directamente, todo pasa por funciones
que validan entrada).

Modelo por defecto: gpt-4o-mini (rápido y barato, suficiente para function
calling de un chatbot de reservas). Podéis subir a gpt-4o o gpt-4.1 si
veis que falla con casos complejos.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta
from typing import Any

from openai import OpenAI

from .config import settings
from . import calendar_service as cal
from . import db as db_module

log = logging.getLogger(__name__)

client = OpenAI(api_key=settings.openai_api_key)

# ---------- Definición de herramientas (tools) para OpenAI ----------
# Formato OpenAI: {"type": "function", "function": {name, description, parameters}}
# `parameters` sigue JSON Schema.

TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "consultar_disponibilidad",
            "description": (
                "Devuelve huecos libres en el calendario del negocio para la "
                "duración pedida, dentro de un rango de fechas. Usa SIEMPRE "
                "esta función antes de proponer una hora al cliente. NUNCA la "
                "llames sin haber preguntado antes la preferencia de "
                "peluquero/a al cliente."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "fecha_desde_iso": {
                        "type": "string",
                        "description": "Fecha/hora inicio en ISO 8601, zona Europe/Madrid. Ej: 2026-04-25T09:00:00",
                    },
                    "fecha_hasta_iso": {
                        "type": "string",
                        "description": "Fecha/hora fin en ISO 8601.",
                    },
                    "duracion_minutos": {
                        "type": "integer",
                        "description": (
                            "Duración del servicio en minutos. Debe coincidir "
                            "con el servicio que ha pedido el cliente — NO la "
                            "inventes. Duraciones típicas de esta peluquería: "
                            "Corte hombre=30, Corte mujer=45, Color=90, Mechas=120."
                        ),
                    },
                    "peluquero_preferido": {
                        "type": "string",
                        "description": (
                            "Nombre del peluquero/a que el cliente prefiere, o "
                            "la cadena literal 'sin preferencia' si le da igual. "
                            "OBLIGATORIO: pregunta al cliente '¿tienes "
                            "preferencia de peluquero o te da igual?' ANTES de "
                            "llamar a esta función. No inventes un nombre — si "
                            "el cliente no ha contestado, haz la pregunta y "
                            "espera su respuesta."
                        ),
                    },
                    "max_resultados": {
                        "type": "integer",
                        "description": "Máximo número de huecos a devolver. Por defecto 5.",
                    },
                },
                "required": [
                    "fecha_desde_iso",
                    "fecha_hasta_iso",
                    "duracion_minutos",
                    "peluquero_preferido",
                ],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "crear_reserva",
            "description": (
                "Crea una cita en el calendario. SOLO tras confirmación explícita "
                "del cliente. NUNCA la llames sin tener 'nombre_cliente' ni "
                "'peluquero_preferido' — si falta alguno, pregúntalo antes."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "titulo": {
                        "type": "string",
                        "description": (
                            "Título de la cita en formato 'Servicio — Nombre (con Peluquero)'. "
                            "Ej: 'Corte hombre — Marcos (con Laura)'. Si no hay "
                            "preferencia de peluquero: 'Corte hombre — Marcos (sin preferencia)'."
                        ),
                    },
                    "nombre_cliente": {
                        "type": "string",
                        "description": (
                            "Nombre del cliente tal y como lo ha dicho. Obligatorio. "
                            "Si no lo sabes, pregúntalo antes de llamar a esta función."
                        ),
                    },
                    "peluquero_preferido": {
                        "type": "string",
                        "description": (
                            "Mismo valor que se usó en consultar_disponibilidad: "
                            "nombre del peluquero/a o 'sin preferencia'. Obligatorio."
                        ),
                    },
                    "inicio_iso": {"type": "string"},
                    "fin_iso": {"type": "string"},
                    "telefono_cliente": {"type": "string"},
                    "notas": {"type": "string"},
                },
                "required": [
                    "titulo",
                    "nombre_cliente",
                    "peluquero_preferido",
                    "inicio_iso",
                    "fin_iso",
                    "telefono_cliente",
                ],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "buscar_reserva_cliente",
            "description": "Busca la próxima reserva del cliente por su teléfono.",
            "parameters": {
                "type": "object",
                "properties": {
                    "telefono_cliente": {"type": "string"},
                    "dias_adelante": {"type": "integer"},
                },
                "required": ["telefono_cliente"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "mover_reserva",
            "description": "Mueve una reserva existente a nueva fecha/hora.",
            "parameters": {
                "type": "object",
                "properties": {
                    "event_id": {"type": "string"},
                    "nuevo_inicio_iso": {"type": "string"},
                    "nuevo_fin_iso": {"type": "string"},
                },
                "required": ["event_id", "nuevo_inicio_iso", "nuevo_fin_iso"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "cancelar_reserva",
            "description": "Cancela una reserva. SOLO tras confirmación explícita del cliente.",
            "parameters": {
                "type": "object",
                "properties": {"event_id": {"type": "string"}},
                "required": ["event_id"],
            },
        },
    },
]


# ---------- Ejecutor de herramientas (lado backend) ----------

def _execute_tool(name: str, args: dict, tenant: dict, caller_phone: str) -> str:
    """Ejecuta la herramienta y devuelve un string JSON con el resultado.
    OpenAI recibe este string como contenido de un mensaje role=tool."""
    calendar_id = tenant.get("calendar_id") or settings.default_calendar_id
    tenant_id = tenant.get("id", "default")

    try:
        if name == "consultar_disponibilidad":
            peluquero = (args.get("peluquero_preferido") or "").strip()
            if not peluquero:
                # Red de seguridad: schema ya lo marca required, pero si el LLM
                # se salta forzamos la pregunta antes de devolver huecos.
                return json.dumps({
                    "error": (
                        "Falta peluquero_preferido. Pregunta al cliente "
                        "'¿tienes preferencia de peluquero o te da igual?' "
                        "ANTES de consultar disponibilidad."
                    ),
                })
            desde = datetime.fromisoformat(args["fecha_desde_iso"])
            hasta = datetime.fromisoformat(args["fecha_hasta_iso"])
            dur = int(args["duracion_minutos"])
            slots = cal.listar_huecos_libres(
                desde, hasta, dur, calendar_id=calendar_id, tenant_id=tenant_id
            )
            limit = int(args.get("max_resultados", 5))
            out = [
                {"inicio": s.start.isoformat(), "fin": s.end.isoformat()}
                for s in slots[:limit]
            ]
            return json.dumps({"huecos": out, "peluquero_preferido": peluquero})

        if name == "crear_reserva":
            nombre_cliente = (args.get("nombre_cliente") or "").strip()
            if not nombre_cliente:
                # Red de seguridad: el schema ya lo marca required, pero si el LLM
                # se salta, abortamos con un error claro en vez de crear evento sin nombre.
                return json.dumps({
                    "error": "Falta nombre_cliente. Pregunta al cliente por su nombre antes de llamar a crear_reserva.",
                })
            peluquero = (args.get("peluquero_preferido") or "").strip()
            if not peluquero:
                return json.dumps({
                    "error": (
                        "Falta peluquero_preferido. Pregunta la preferencia "
                        "de peluquero antes de crear la reserva."
                    ),
                })
            ev = cal.crear_evento(
                titulo=args["titulo"],
                inicio=datetime.fromisoformat(args["inicio_iso"]),
                fin=datetime.fromisoformat(args["fin_iso"]),
                descripcion=args.get("notas", ""),
                telefono_cliente=args.get("telefono_cliente", caller_phone),
                nombre_cliente=nombre_cliente,
                calendar_id=calendar_id,
                tenant_id=tenant_id,
            )
            return json.dumps({"ok": True, "event_id": ev.get("id"), "link": ev.get("htmlLink")})

        if name == "buscar_reserva_cliente":
            desde = datetime.utcnow()
            hasta = desde + timedelta(days=int(args.get("dias_adelante", 30)))
            ev = cal.buscar_evento_por_telefono(
                args["telefono_cliente"], desde, hasta,
                calendar_id=calendar_id, tenant_id=tenant_id,
            )
            if not ev:
                return json.dumps({"encontrada": False})
            return json.dumps({
                "encontrada": True,
                "event_id": ev["id"],
                "titulo": ev.get("summary"),
                "inicio": ev["start"].get("dateTime"),
                "fin": ev["end"].get("dateTime"),
            })

        if name == "mover_reserva":
            ev = cal.mover_evento(
                event_id=args["event_id"],
                nuevo_inicio=datetime.fromisoformat(args["nuevo_inicio_iso"]),
                nuevo_fin=datetime.fromisoformat(args["nuevo_fin_iso"]),
                calendar_id=calendar_id, tenant_id=tenant_id,
            )
            return json.dumps({"ok": True, "event_id": ev.get("id")})

        if name == "cancelar_reserva":
            cal.cancelar_evento(args["event_id"], calendar_id=calendar_id, tenant_id=tenant_id)
            return json.dumps({"ok": True})

        return json.dumps({"error": f"herramienta desconocida: {name}"})

    except Exception as e:
        log.exception("Error ejecutando tool %s", name)
        return json.dumps({"error": str(e)})


# ---------- Sanitizer de salida (WhatsApp-friendly) ----------

# Marcadores numéricos/viñeta típicos al inicio de cada línea.
#   - `1.`, `12)` → decimales seguidos de punto o paréntesis
#   - `1️⃣ 2️⃣ 3️⃣ …` → dígitos con enclosing keycap (U+20E3) precedidos por el dígito
#   - `-`, `*`, `•` al inicio de línea
_RE_LIST_PREFIX = re.compile(
    r"^\s*(?:"
    r"(?:\d+[\.\)])"           # "1." o "1)"
    r"|(?:[0-9]\uFE0F?\u20E3)" # "1️⃣"
    r"|(?:🥇|🥈|🥉)"            # medallas
    r"|[-*•·]"                 # guiones/bullets
    r")\s+",
    flags=re.UNICODE,
)

# Markdown: negritas ** **, __ __, cursivas *x* o _x_ (conservadoras: sólo si
# envuelven contenido sin saltos de línea). Dejamos los asteriscos simples *x*
# porque WhatsApp sí los renderiza como negrita y a veces son intencionados;
# pero **x** SIEMPRE queda como ruido en WhatsApp.
_RE_DOUBLE_STAR = re.compile(r"\*\*(.+?)\*\*", flags=re.DOTALL)
_RE_DOUBLE_UNDERSCORE = re.compile(r"__(.+?)__", flags=re.DOTALL)


def _sanitize_whatsapp(text: str) -> str:
    """Limpia la salida del LLM para WhatsApp.

    - Elimina markdown de negrita doble (** **, __ __).
    - Si detecta líneas que empiezan con marcadores de lista (1., 1️⃣, -, *),
      quita el marcador y une las líneas contiguas con ", " para que el
      resultado sea una frase continua.
    - Colapsa saltos de línea múltiples.

    Conservador: si no ve marcadores de lista deja el texto tal cual. El
    objetivo es respetar el estilo del LLM cuando ya responde bien, y sólo
    intervenir cuando lo estropea.
    """
    if not text:
        return text

    # 1) quitar negritas markdown dobles
    text = _RE_DOUBLE_STAR.sub(r"\1", text)
    text = _RE_DOUBLE_UNDERSCORE.sub(r"\1", text)

    # 2) aplanar listas
    lines = text.split("\n")
    # Detectamos bloques consecutivos de líneas-lista
    out_lines: list[str] = []
    buffer_items: list[str] = []

    def _flush_buffer() -> None:
        nonlocal buffer_items
        if not buffer_items:
            return
        if len(buffer_items) == 1:
            out_lines.append(buffer_items[0])
        else:
            # une con ", " y sustituye el penúltimo separador por " o "
            joined = ", ".join(buffer_items[:-1]) + " o " + buffer_items[-1]
            out_lines.append(joined)
        buffer_items = []

    for raw in lines:
        m = _RE_LIST_PREFIX.match(raw)
        if m:
            item = raw[m.end():].strip()
            if item:
                buffer_items.append(item)
            # si la línea sólo era marcador (raro), la ignoramos
            continue
        _flush_buffer()
        out_lines.append(raw)
    _flush_buffer()

    # 3) colapsar líneas en blanco múltiples
    result = "\n".join(out_lines)
    result = re.sub(r"\n{3,}", "\n\n", result)
    return result.strip()


# ---------- Loop principal del agente ----------

def _history_to_openai(history: list[dict]) -> list[dict]:
    """Convierte el historial guardado (role=user|assistant, content=str) al
    formato que acepta OpenAI (mismos roles). Se ignoran roles desconocidos.

    OpenAI permite mensajes consecutivos del mismo rol, pero los fusionamos
    igualmente para dejar el historial limpio.
    """
    out: list[dict] = []
    last_role = None
    for m in history:
        role = m["role"]
        if role not in ("user", "assistant", "system"):
            continue
        if role == last_role and role != "system":
            out[-1]["content"] += "\n" + m["content"]
        else:
            out.append({"role": role, "content": m["content"]})
            last_role = role
    return out


def reply(user_message: str, history: list[dict], tenant: dict, caller_phone: str) -> str:
    """Dispatcher: delega al provider configurado (OpenAI o Anthropic).

    `LLM_PROVIDER=anthropic` cambia al adaptador de Anthropic (Claude). Cualquier
    otro valor (o vacío) cae en OpenAI, que es el comportamiento original.
    """
    if settings.llm_provider == "anthropic":
        from . import agent_anthropic  # import tardío: no forzar dep si no se usa
        return agent_anthropic.reply(
            user_message=user_message,
            history=history,
            tenant=tenant,
            caller_phone=caller_phone,
        )
    return _reply_openai(
        user_message=user_message,
        history=history,
        tenant=tenant,
        caller_phone=caller_phone,
    )


def _reply_openai(user_message: str, history: list[dict], tenant: dict, caller_phone: str) -> str:
    """Devuelve la respuesta de texto del agente tras resolver tool calls (provider OpenAI)."""
    now_iso = datetime.now().strftime("%Y-%m-%dT%H:%M")
    system_prompt = (
        tenant["system_prompt"]
        + f"\n\n(Contexto: fecha y hora actual = {now_iso} "
        f"zona {settings.default_timezone}. Teléfono del cliente = {caller_phone}.)"
    )

    # OpenAI mete el system prompt como un mensaje más al inicio.
    messages: list[dict] = [{"role": "system", "content": system_prompt}]
    messages.extend(_history_to_openai(history))
    messages.append({"role": "user", "content": user_message})

    tenant_id = tenant.get("id", "default")

    # La familia GPT-5 (y los modelos o1/o3) usa `max_completion_tokens` en vez
    # de `max_tokens`. Detectamos por prefijo; si añadimos más modelos con la
    # API nueva, incluirlos aquí.
    model_name = settings.openai_model
    uses_new_token_param = (
        model_name.startswith("gpt-5")
        or model_name.startswith("o1")
        or model_name.startswith("o3")
        or model_name.startswith("o4")
    )
    token_kwargs: dict[str, int] = (
        {"max_completion_tokens": 1024} if uses_new_token_param else {"max_tokens": 1024}
    )

    for _ in range(6):  # máx 6 rondas para evitar loops infinitos
        resp = client.chat.completions.create(
            model=model_name,
            messages=messages,
            tools=TOOLS,
            tool_choice="auto",
            **token_kwargs,
        )

        # --- Tracking de tokens (no-op si falla) ------------------------
        try:
            usage = getattr(resp, "usage", None)
            if usage is not None:
                db_module.save_token_usage(
                    tenant_id=tenant_id,
                    model=settings.openai_model,
                    input_tokens=getattr(usage, "prompt_tokens", 0) or 0,
                    output_tokens=getattr(usage, "completion_tokens", 0) or 0,
                    customer_phone=caller_phone,
                )
        except Exception:
            log.exception("Error guardando token_usage")
        # ----------------------------------------------------------------

        choice = resp.choices[0]
        msg = choice.message

        # Caso 1: el modelo quiere ejecutar herramientas
        if msg.tool_calls:
            # Añadir la respuesta del assistant con los tool_calls al historial
            messages.append({
                "role": "assistant",
                "content": msg.content or "",
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments,
                        },
                    }
                    for tc in msg.tool_calls
                ],
            })

            # Ejecutar cada tool call y añadir un mensaje role=tool por cada uno
            for tc in msg.tool_calls:
                try:
                    args = json.loads(tc.function.arguments or "{}")
                except json.JSONDecodeError:
                    args = {}
                result = _execute_tool(tc.function.name, args, tenant, caller_phone)
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": result,
                })
            continue

        # Caso 2: el modelo acaba sin pedir herramientas
        text = (msg.content or "").strip()
        if choice.finish_reason not in ("stop", "length", None):
            log.warning("finish_reason inesperado: %s", choice.finish_reason)
        clean = _sanitize_whatsapp(text)
        return clean or "¿En qué puedo ayudarte?"

    return "Lo siento, no he podido completar la petición. ¿Puedes intentarlo de otra forma?"
