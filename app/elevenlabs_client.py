"""Cliente HTTP minimalista para la API Conversational AI de ElevenLabs.

El propósito es que el CMS pueda, con un solo botón, sincronizar el prompt y los
parámetros TTS del agente Ana desde la BD hacia el agente remoto en ElevenLabs.

Diseño:
- Una sola función pública útil para el CMS: `sync_agent(...)`.
- Errores capturados con mensajes legibles (no HTML crudo) — el CMS los guarda
  en `tenants.voice_last_sync_status` para pintar en pantalla.
- Sin side effects en import (nada de PATCHs al cargar el módulo).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import httpx

from .config import settings

log = logging.getLogger(__name__)

API_BASE = "https://api.elevenlabs.io"
_TIMEOUT = httpx.Timeout(20.0, connect=5.0)


class ElevenLabsError(Exception):
    """Error 4xx/5xx al hablar con ElevenLabs, con mensaje ya formateado."""


@dataclass(frozen=True)
class VoiceParams:
    voice_id: str
    stability: float
    similarity_boost: float
    speed: float


def _headers() -> dict[str, str]:
    if not settings.elevenlabs_api_key:
        raise ElevenLabsError(
            "ELEVENLABS_API_KEY no está configurada en el entorno. "
            "No se puede sincronizar con ElevenLabs."
        )
    return {
        "xi-api-key": settings.elevenlabs_api_key,
        "Content-Type": "application/json",
    }


def _resolve_agent_id(tenant_agent_id: str | None) -> str:
    """Devuelve el agent_id a usar: el del tenant si tiene, o el del .env global.

    El MVP es monotenant, así que aceptamos fallback al `ELEVENLABS_AGENT_ID`
    global. Cuando el proyecto pase a multi-tenant, cada tenant tendrá el suyo
    propio y el fallback dejará de usarse.
    """
    aid = (tenant_agent_id or "").strip() or (settings.elevenlabs_agent_id or "").strip()
    if not aid:
        raise ElevenLabsError(
            "No hay agent_id para este tenant ni ELEVENLABS_AGENT_ID global. "
            "Revisa las variables de entorno en Railway."
        )
    return aid


def _raise_for_status(r: httpx.Response, context: str) -> None:
    if r.status_code >= 400:
        # Recorta para que el mensaje quepa en voice_last_sync_status (VARCHAR 400).
        body_preview = (r.text or "")[:280]
        raise ElevenLabsError(
            f"{context}: HTTP {r.status_code} — {body_preview}"
        )


def _build_tools(tool_base_url: str, tool_secret: str, tenant_id: str) -> list[dict]:
    """Definición de los 5 server tools que registramos en el agente.

    Mismo shape que el script `scripts/setup_elevenlabs_agent.py`; duplicado
    aquí para no forzar dependencia inversa del script desde el runtime.
    """
    base_headers = {"X-Tool-Secret": tool_secret, "Content-Type": "application/json"}
    base = tool_base_url.rstrip("/")

    def wh(name: str, description: str, path: str, body_schema: dict) -> dict:
        return {
            "type": "webhook",
            "name": name,
            "description": description,
            "api_schema": {
                "url": f"{base}{path}?tenant_id={tenant_id}",
                "method": "POST",
                "request_headers": base_headers,
                "request_body_schema": body_schema,
            },
        }

    return [
        wh("consultar_disponibilidad",
           "Devuelve huecos libres del negocio para la duración pedida dentro de un rango.",
           "/tools/consultar_disponibilidad",
           {"type": "object", "required": ["fecha_desde_iso", "fecha_hasta_iso", "duracion_minutos"],
            "properties": {
                "fecha_desde_iso": {"type": "string"},
                "fecha_hasta_iso": {"type": "string"},
                "duracion_minutos": {"type": "integer"},
                "peluquero_preferido": {"type": "string"},
                "max_resultados": {"type": "integer"},
            }}),
        wh("crear_reserva",
           "Crea una reserva en el calendario. Confirma SIEMPRE la hora antes.",
           "/tools/crear_reserva",
           {"type": "object", "required": ["titulo", "inicio_iso", "fin_iso"],
            "properties": {
                "titulo": {"type": "string"},
                "inicio_iso": {"type": "string"},
                "fin_iso": {"type": "string"},
                "telefono_cliente": {"type": "string"},
                "peluquero": {"type": "string"},
                "notas": {"type": "string"},
            }}),
        wh("buscar_reserva_cliente",
           "Busca reservas futuras de un cliente por su teléfono.",
           "/tools/buscar_reserva_cliente",
           {"type": "object",
            "properties": {
                "telefono_cliente": {"type": "string"},
                "dias_adelante": {"type": "integer"},
            }}),
        wh("mover_reserva",
           "Mueve una reserva existente a otra hora. Usa event_id obtenido de buscar_reserva_cliente.",
           "/tools/mover_reserva",
           {"type": "object", "required": ["event_id", "nuevo_inicio_iso", "nuevo_fin_iso"],
            "properties": {
                "event_id": {"type": "string"},
                "nuevo_inicio_iso": {"type": "string"},
                "nuevo_fin_iso": {"type": "string"},
                "peluquero": {"type": "string"},
            }}),
        wh("cancelar_reserva",
           "Cancela una reserva existente. Usa event_id obtenido de buscar_reserva_cliente.",
           "/tools/cancelar_reserva",
           {"type": "object", "required": ["event_id"],
            "properties": {"event_id": {"type": "string"}}}),
    ]


def create_agent_for_tenant(
    *,
    tenant: dict[str, Any],
    tool_base_url: str,
    prompt: str,
    voice: VoiceParams,
    tool_secret: str | None = None,
) -> str:
    """Crea un agente nuevo en ElevenLabs y devuelve su agent_id.

    Usado por el CMS cuando das de alta un cliente nuevo: un POST a
    /v1/convai/agents/create con el prompt del tenant, su voz, y las 5 tools
    apuntando a `tool_base_url/tools/*?tenant_id=<id>`.

    `tool_secret` cae a `settings.tool_secret` si no se pasa.
    """
    from .config import settings as _settings
    secret = (tool_secret or _settings.tool_secret or "").strip()
    if not secret:
        raise ElevenLabsError(
            "TOOL_SECRET vacío en el entorno. Sin secreto compartido no se puede "
            "crear el agente, porque las tools del backend rechazan peticiones."
        )

    if not tool_base_url or not tool_base_url.strip():
        raise ElevenLabsError(
            "tool_base_url vacío. Necesitamos la URL pública del backend "
            "(Railway) para registrar las tools en ElevenLabs."
        )

    tenant_id = tenant.get("id") or ""
    if not tenant_id:
        raise ElevenLabsError("Tenant sin id. No se puede crear agente.")

    if not (prompt or "").strip():
        raise ElevenLabsError("El prompt está vacío. No creamos un agente mudo.")

    tools = _build_tools(tool_base_url, secret, tenant_id)
    payload = {
        "name": f"Ana · {tenant.get('name') or tenant_id}",
        "conversation_config": {
            "agent": {
                "prompt": {"prompt": prompt, "tools": tools},
                "first_message": f"¡Hola! Soy Ana de {tenant.get('name') or 'la peluquería'}. ¿En qué te puedo ayudar?",
                "language": "es",
            },
            "tts": {
                "voice_id": voice.voice_id.strip() or "1eHrpOW5l98cxiSRjbzJ",
                "model_id": "eleven_flash_v2_5",
                "stability": round(float(voice.stability), 3),
                "similarity_boost": round(float(voice.similarity_boost), 3),
                "speed": round(float(voice.speed), 3),
            },
        },
    }

    url = f"{API_BASE}/v1/convai/agents/create"
    log.info("ElevenLabs POST agent create tenant=%s", tenant_id)
    try:
        r = httpx.post(url, headers=_headers(), json=payload, timeout=_TIMEOUT)
    except httpx.HTTPError as e:
        raise ElevenLabsError(f"Error de red creando agente: {e}") from e
    _raise_for_status(r, "create_agent")

    body = r.json()
    agent_id = (body.get("agent_id") or "").strip()
    if not agent_id:
        raise ElevenLabsError(f"ElevenLabs no devolvió agent_id. Body: {str(body)[:200]}")
    return agent_id


def get_agent(agent_id: str) -> dict[str, Any]:
    """GET del estado remoto del agente. Útil para verificar cambios."""
    aid = _resolve_agent_id(agent_id)
    url = f"{API_BASE}/v1/convai/agents/{aid}"
    try:
        r = httpx.get(url, headers=_headers(), timeout=_TIMEOUT)
    except httpx.HTTPError as e:
        raise ElevenLabsError(f"Error de red hablando con ElevenLabs: {e}") from e
    _raise_for_status(r, "get_agent")
    return r.json()


def sync_agent(
    agent_id: str,
    *,
    prompt: str | None = None,
    voice: VoiceParams | None = None,
) -> dict[str, Any]:
    """PATCH al agente remoto con prompt y/o parámetros TTS.

    Solo se envían las secciones que se especifican explícitamente. Si pasas
    `prompt=None` no se tocará el prompt remoto; lo mismo para `voice`.

    Devuelve el body JSON de la respuesta (contiene el estado actualizado del
    agente). Lanza `ElevenLabsError` en caso de fallo, con mensaje ya formateado
    para mostrar al usuario.
    """
    aid = _resolve_agent_id(agent_id)

    if prompt is None and voice is None:
        raise ElevenLabsError(
            "Nada que sincronizar: ni prompt ni voz. Rellena al menos uno de los dos."
        )

    # Construimos solo las secciones tocadas. ElevenLabs hace merge parcial,
    # así que omitir una sección no borra el valor anterior.
    conversation_config: dict[str, Any] = {}

    if prompt is not None:
        prompt_text = (prompt or "").strip()
        if not prompt_text:
            raise ElevenLabsError("El prompt está vacío. Añade texto antes de sincronizar.")
        conversation_config["agent"] = {"prompt": {"prompt": prompt_text}}

    if voice is not None:
        if not voice.voice_id or not voice.voice_id.strip():
            raise ElevenLabsError(
                "voice_id vacío. Pon el ID de la voz de ElevenLabs que quieres usar."
            )
        # Validaciones blandas pero útiles: si el usuario mete valores fuera de
        # rango, ElevenLabs responde 4xx con mensaje denso; mejor avisar antes.
        if not (0.0 <= voice.stability <= 1.0):
            raise ElevenLabsError("stability debe estar entre 0.0 y 1.0.")
        if not (0.0 <= voice.similarity_boost <= 1.0):
            raise ElevenLabsError("similarity_boost debe estar entre 0.0 y 1.0.")
        if not (0.5 <= voice.speed <= 1.5):
            raise ElevenLabsError("speed debe estar entre 0.5 y 1.5.")
        conversation_config["tts"] = {
            "voice_id": voice.voice_id.strip(),
            "stability": round(float(voice.stability), 3),
            "similarity_boost": round(float(voice.similarity_boost), 3),
            "speed": round(float(voice.speed), 3),
        }

    payload = {"conversation_config": conversation_config}
    url = f"{API_BASE}/v1/convai/agents/{aid}"

    log.info(
        "ElevenLabs PATCH agent=%s sections=%s",
        aid,
        ",".join(sorted(conversation_config.keys())) or "(none)",
    )
    try:
        r = httpx.patch(url, headers=_headers(), json=payload, timeout=_TIMEOUT)
    except httpx.HTTPError as e:
        raise ElevenLabsError(f"Error de red al sincronizar con ElevenLabs: {e}") from e
    _raise_for_status(r, "sync_agent (PATCH)")
    return r.json()
