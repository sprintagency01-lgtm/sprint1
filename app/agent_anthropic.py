"""Agente LLM con tool-use (Anthropic Claude).

Mismo contrato que `app.agent._reply_openai`: recibe el mensaje del cliente +
historial + tenant, devuelve la respuesta en texto y, si el modelo pide
herramientas, las ejecuta contra Google Calendar (reutilizando el ejecutor
compartido de `app.agent._execute_tool`).

Se activa cuando `settings.llm_provider == "anthropic"`. Modelo por defecto:
Claude Haiku 4.5 — rápido y barato, generalmente mejor que gpt-4o-mini para
seguir reglas de formato estrictas en español.

Formato de tools en Anthropic: lista de `{name, description, input_schema}`,
donde `input_schema` es JSON Schema (idéntico al campo `parameters` de OpenAI),
así que convertimos nuestras TOOLS existentes sin duplicar definiciones.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import anthropic

from .config import settings
from . import db as db_module
from . import agent as _agent_openai_mod  # para reutilizar TOOLS y _execute_tool

log = logging.getLogger(__name__)

_client = anthropic.Anthropic(api_key=settings.anthropic_api_key) if settings.anthropic_api_key else None


def _openai_tools_to_anthropic(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convierte TOOLS (formato OpenAI) al formato que espera Anthropic.

    OpenAI: {"type": "function", "function": {"name", "description", "parameters"}}
    Anthropic: {"name", "description", "input_schema"}
    """
    out: list[dict[str, Any]] = []
    for t in tools:
        fn = t.get("function") or {}
        out.append({
            "name": fn.get("name", ""),
            "description": fn.get("description", ""),
            "input_schema": fn.get("parameters") or {"type": "object", "properties": {}},
        })
    return out


def _history_to_anthropic(history: list[dict]) -> list[dict]:
    """Convierte el historial guardado al formato messages de Anthropic.

    Anthropic requiere roles alternados user/assistant empezando por user, y
    NO acepta role=system en messages (va aparte). Filtramos system, fusionamos
    mensajes consecutivos del mismo rol.
    """
    out: list[dict] = []
    last_role: str | None = None
    for m in history:
        role = m.get("role")
        if role not in ("user", "assistant"):
            continue
        content = m.get("content") or ""
        if role == last_role and out:
            out[-1]["content"] += "\n" + content
        else:
            out.append({"role": role, "content": content})
            last_role = role
    return out


def reply(user_message: str, history: list[dict], tenant: dict, caller_phone: str) -> "_agent_openai_mod.AgentReply":
    """Devuelve la respuesta del agente tras resolver tool_use (Claude).

    Retorna `AgentReply` (text + opcional interactive). Las tools de oferta
    (`ofrecer_huecos`, `ofrecer_equipo`, `pedir_confirmacion`) terminan el
    turno lanzando `_EarlyReply`, que capturamos para construir la reply
    interactiva igual que en el flow de OpenAI.
    """
    if _client is None:
        raise RuntimeError(
            "ANTHROPIC_API_KEY no configurada. Pon LLM_PROVIDER=openai o añade la key."
        )

    time_ctx = _agent_openai_mod._build_time_context(
        datetime.now(ZoneInfo(settings.default_timezone))
    )
    system_prompt = (
        tenant["system_prompt"]
        + _agent_openai_mod._build_context_footer(
            tenant=tenant, time_ctx=time_ctx, caller_phone=caller_phone
        )
    )

    tools_anthropic = _openai_tools_to_anthropic(_agent_openai_mod.TOOLS)

    messages: list[dict[str, Any]] = []
    messages.extend(_history_to_anthropic(history))
    # El último turno siempre es user (con el mensaje actual).
    if messages and messages[-1]["role"] == "user":
        messages[-1]["content"] += "\n" + user_message
    else:
        messages.append({"role": "user", "content": user_message})

    tenant_id = tenant.get("id", "default")
    model_name = settings.anthropic_model

    # Si en algún momento del loop ejecutamos crear_reserva con ok:true,
    # guardamos aquí los datos para adjuntarlos como .ics al cliente.
    calendar_event: dict[str, Any] | None = None

    for _ in range(6):  # límite defensivo contra loops de tool_use
        resp = _client.messages.create(
            model=model_name,
            max_tokens=1024,
            system=system_prompt,
            tools=tools_anthropic,
            messages=messages,
        )

        # Tracking de tokens — no-op si algo falla
        try:
            usage = getattr(resp, "usage", None)
            if usage is not None:
                db_module.save_token_usage(
                    tenant_id=tenant_id,
                    model=model_name,
                    input_tokens=getattr(usage, "input_tokens", 0) or 0,
                    output_tokens=getattr(usage, "output_tokens", 0) or 0,
                    customer_phone=caller_phone,
                )
        except Exception:
            log.exception("Error guardando token_usage (anthropic)")

        stop_reason = getattr(resp, "stop_reason", None)
        content_blocks = list(resp.content or [])

        # ¿Pidió tool_use?
        tool_use_blocks = [b for b in content_blocks if getattr(b, "type", None) == "tool_use"]

        if stop_reason == "tool_use" and tool_use_blocks:
            # Añadir el turno del assistant con los bloques raw (text + tool_use)
            messages.append({
                "role": "assistant",
                "content": [
                    _block_to_dict(b) for b in content_blocks
                ],
            })

            # Ejecutar cada tool_use y empaquetar los resultados en un único
            # mensaje user con bloques tool_result (así lo espera Anthropic).
            tool_results: list[dict[str, Any]] = []
            for b in tool_use_blocks:
                name = getattr(b, "name", "")
                tool_input = getattr(b, "input", {}) or {}
                try:
                    result_json = _agent_openai_mod._execute_tool(
                        name=name,
                        args=tool_input if isinstance(tool_input, dict) else {},
                        tenant=tenant,
                        caller_phone=caller_phone,
                    )
                except _agent_openai_mod._EarlyReply as er:
                    # Una tool de oferta terminó el turno: devolvemos la
                    # AgentReply interactiva sin continuar la conversación.
                    return er.reply
                # Si acabamos de crear una reserva con éxito, guarda los
                # datos del evento para poder adjuntar el .ics al final.
                if name == "crear_reserva":
                    try:
                        parsed = json.loads(result_json or "{}")
                    except Exception:
                        parsed = {}
                    if parsed.get("ok") is True and isinstance(tool_input, dict):
                        calendar_event = {
                            "titulo": tool_input.get("titulo") or "",
                            "inicio_iso": tool_input.get("inicio_iso") or "",
                            "fin_iso": tool_input.get("fin_iso") or "",
                            "descripcion": tool_input.get("notas") or "",
                            "ubicacion": (tenant.get("name") or "").strip(),
                            "tz": (tenant.get("timezone") or settings.default_timezone),
                            "event_id": parsed.get("event_id"),
                            "add_to_calendar_url": parsed.get("add_to_calendar_url"),
                        }
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": getattr(b, "id", ""),
                    "content": result_json,  # string JSON; Claude lo parsea
                })
            messages.append({"role": "user", "content": tool_results})
            continue

        # Respuesta final (end_turn / max_tokens / stop_sequence)
        text_parts = [
            getattr(b, "text", "") for b in content_blocks
            if getattr(b, "type", None) == "text"
        ]
        text = "\n".join(p for p in text_parts if p).strip()
        if stop_reason not in ("end_turn", "max_tokens", "stop_sequence", None):
            log.warning("stop_reason inesperado: %s", stop_reason)
        clean = _agent_openai_mod._sanitize_whatsapp(text)
        return _agent_openai_mod.AgentReply(
            text=clean or "¿En qué puedo ayudarte?",
            calendar_event=calendar_event,
        )

    return _agent_openai_mod.AgentReply(
        text="Lo siento, no he podido completar la petición. ¿Puedes intentarlo de otra forma?",
        calendar_event=calendar_event,
    )


def _block_to_dict(block: Any) -> dict[str, Any]:
    """Serializa un ContentBlock de Anthropic al dict equivalente para messages.

    Preservar el bloque tal cual lo devolvió el modelo es lo que permite a
    Anthropic correlacionar tool_use → tool_result en la siguiente ronda.
    """
    btype = getattr(block, "type", None)
    if btype == "text":
        return {"type": "text", "text": getattr(block, "text", "")}
    if btype == "tool_use":
        tool_input = getattr(block, "input", {}) or {}
        return {
            "type": "tool_use",
            "id": getattr(block, "id", ""),
            "name": getattr(block, "name", ""),
            "input": tool_input if isinstance(tool_input, dict) else {},
        }
    # Fallback razonable — si aparece algún bloque raro, lo convertimos a texto.
    return {"type": "text", "text": str(block)}
