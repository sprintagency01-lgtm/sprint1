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
                "esta función antes de proponer una hora al cliente."
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
                    "max_resultados": {
                        "type": "integer",
                        "description": "Máximo número de huecos a devolver. Por defecto 5.",
                    },
                },
                "required": ["fecha_desde_iso", "fecha_hasta_iso", "duracion_minutos"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "crear_reserva",
            "description": (
                "Crea una cita en el calendario. SOLO tras confirmación explícita "
                "del cliente. NUNCA la llames sin tener 'nombre_cliente' — si "
                "todavía no sabes el nombre, pide '¿a qué nombre pongo la cita?' "
                "antes de invocar esta función."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "titulo": {
                        "type": "string",
                        "description": (
                            "Título de la cita en formato 'Servicio — Nombre (con Peluquero)'. "
                            "Ej: 'Corte hombre — Marcos (con Laura)'."
                        ),
                    },
                    "nombre_cliente": {
                        "type": "string",
                        "description": (
                            "Nombre del cliente tal y como lo ha dicho. Obligatorio. "
                            "Si no lo sabes, pregúntalo antes de llamar a esta función."
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
            return json.dumps({"huecos": out})

        if name == "crear_reserva":
            nombre_cliente = (args.get("nombre_cliente") or "").strip()
            if not nombre_cliente:
                # Red de seguridad: el schema ya lo marca required, pero si el LLM
                # se salta, abortamos con un error claro en vez de crear evento sin nombre.
                return json.dumps({
                    "error": "Falta nombre_cliente. Pregunta al cliente por su nombre antes de llamar a crear_reserva.",
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
    """Devuelve la respuesta de texto del agente tras resolver tool calls."""
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

    for _ in range(6):  # máx 6 rondas para evitar loops infinitos
        resp = client.chat.completions.create(
            model=settings.openai_model,
            messages=messages,
            tools=TOOLS,
            tool_choice="auto",
            max_tokens=1024,
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
        return text or "¿En qué puedo ayudarte?"

    return "Lo siento, no he podido completar la petición. ¿Puedes intentarlo de otra forma?"
