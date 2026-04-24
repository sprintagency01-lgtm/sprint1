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

# Modelo de TTS por defecto. `eleven_flash_v2_5` está optimizado para
# latencia (150-250ms al primer audio), frente a `eleven_v3_conversational`
# (300-500ms). Este producto prioriza latencia sobre expresividad. Si en el
# futuro se quiere experimentar con v3, cámbialo aquí y/o pasa `model_id`
# explícito a create_agent_for_tenant / sync_agent.
DEFAULT_TTS_MODEL_ID = "eleven_flash_v2_5"


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


def _prop(type_: str, description: str) -> dict:
    """Construye una propiedad del request_body_schema compatible con ElevenLabs.

    ElevenLabs API exige que cada propiedad tenga al menos uno de:
    description, dynamic_variable, is_system_provided, constant_value. Con
    solo `type` la creación del agente falla con 422.
    """
    return {"type": type_, "description": description}


def _build_tools(tool_base_url: str, tool_secret: str, tenant_id: str) -> list[dict]:
    """Definición de los 5 server tools que registramos en el agente.

    Mismo shape que el script `scripts/setup_elevenlabs_agent.py` y que el
    snapshot versionado en `elevenlabs_agent_config.json`. Duplicado aquí para
    no forzar dependencia inversa del script desde el runtime.
    """
    base_headers = {"X-Tool-Secret": tool_secret, "Content-Type": "application/json"}
    base = tool_base_url.rstrip("/")

    def wh(name: str, description: str, path: str, body_schema: dict) -> dict:
        return {
            "type": "webhook",
            "name": name,
            "description": description,
            # Arranca el TTS del filler en paralelo a la HTTP call: el usuario
            # oye "vale, te miro un momento..." mientras el backend habla con
            # Google. Enmascara 200-600ms de latencia por tool call.
            #
            # Ojo: el flag booleano `force_pre_tool_speech: true` SIN `pre_tool_speech`
            # lo ignora la API — hay que fijar el enum a "force" para que se
            # active. Valores válidos: 'auto' | 'force' | 'off'.
            "pre_tool_speech": "force",
            "force_pre_tool_speech": True,
            "api_schema": {
                "url": f"{base}{path}?tenant_id={tenant_id}",
                "method": "POST",
                "request_headers": base_headers,
                "request_body_schema": body_schema,
            },
        }

    return [
        wh(
            "consultar_disponibilidad",
            "Consulta huecos libres en los calendarios del equipo. USA SIEMPRE esta función antes de proponer una hora al cliente. Si el cliente tiene preferencia, pásalo en peluquero_preferido; si no, déjalo vacío.",
            "/tools/consultar_disponibilidad",
            {
                "type": "object",
                "required": ["fecha_desde_iso", "fecha_hasta_iso", "duracion_minutos"],
                "properties": {
                    "fecha_desde_iso": _prop("string", "Inicio del rango, ISO 8601. Ej: 2026-04-22T09:30:00"),
                    "fecha_hasta_iso": _prop("string", "Fin del rango, ISO 8601."),
                    "duracion_minutos": _prop("integer", "Duración del servicio (30, 45, 90...)."),
                    "peluquero_preferido": _prop("string", "Nombre del miembro del equipo si hay preferencia. Vacío = cualquiera."),
                    "max_resultados": _prop("integer", "Máximo de huecos a devolver. Por defecto 5."),
                },
            },
        ),
        wh(
            "crear_reserva",
            "Crea una reserva en el calendario. Confirma SIEMPRE la hora antes. No llamarla sin haber usado consultar_disponibilidad primero.",
            "/tools/crear_reserva",
            {
                "type": "object",
                "required": ["titulo", "inicio_iso", "fin_iso", "peluquero"],
                "properties": {
                    "titulo": _prop("string", "Título del evento. FORMATO EXACTO: 'Nombre — Servicio (Peluquero)'. Ejemplos: 'Lucía — Corte mujer (Mario)', 'Juan — Asesoría (sin preferencia)'. El NOMBRE DEL CLIENTE va PRIMERO."),
                    "inicio_iso": _prop("string", "Fecha/hora de inicio, ISO 8601."),
                    "fin_iso": _prop("string", "Fecha/hora de fin, ISO 8601."),
                    "telefono_cliente": _prop("string", "Teléfono del cliente. Normalmente es {{system__caller_id}} (el de la llamada)."),
                    "peluquero": _prop("string", "Nombre del miembro del equipo asignado o 'sin preferencia'."),
                    "notas": _prop("string", "Notas extra (alergias, detalles). Puede estar vacío."),
                },
            },
        ),
        wh(
            "buscar_reserva_cliente",
            "Busca reservas futuras de un cliente por su teléfono. Úsala antes de mover/cancelar para obtener el event_id.",
            "/tools/buscar_reserva_cliente",
            {
                "type": "object",
                "required": [],
                "properties": {
                    "telefono_cliente": _prop("string", "Teléfono del cliente a buscar. Normalmente es {{system__caller_id}}."),
                    "dias_adelante": _prop("integer", "Cuántos días mirar hacia adelante. Por defecto 30."),
                },
            },
        ),
        wh(
            "mover_reserva",
            "Mueve una reserva existente a otra hora. Usa event_id obtenido de buscar_reserva_cliente. Pasa también calendar_id si lo recibiste.",
            "/tools/mover_reserva",
            {
                "type": "object",
                "required": ["event_id", "nuevo_inicio_iso", "nuevo_fin_iso"],
                "properties": {
                    "event_id": _prop("string", "ID del evento devuelto por buscar_reserva_cliente."),
                    "nuevo_inicio_iso": _prop("string", "Nuevo inicio, ISO 8601."),
                    "nuevo_fin_iso": _prop("string", "Nuevo fin, ISO 8601."),
                    "peluquero": _prop("string", "Nombre del miembro si se mueve a otro peluquero/profesional. Vacío = no cambia."),
                    "calendar_id": _prop("string", "calendar_id devuelto por buscar_reserva_cliente. Si lo pasas, el backend mueve directo sin iterar peluqueros — MÁS RÁPIDO. Si falta, el backend lo busca."),
                },
            },
        ),
        wh(
            "cancelar_reserva",
            "Cancela una reserva existente. Usa event_id obtenido de buscar_reserva_cliente. Pasa también calendar_id si lo recibiste.",
            "/tools/cancelar_reserva",
            {
                "type": "object",
                "required": ["event_id"],
                "properties": {
                    "event_id": _prop("string", "ID del evento a cancelar."),
                    "calendar_id": _prop("string", "calendar_id devuelto por buscar_reserva_cliente. Si lo pasas, el backend cancela directo sin iterar peluqueros — MÁS RÁPIDO. Si falta, el backend lo busca."),
                },
            },
        ),
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
                "model_id": DEFAULT_TTS_MODEL_ID,
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
    model_id: str | None = None,
) -> dict[str, Any]:
    """PATCH al agente remoto con prompt y/o parámetros TTS.

    Solo se envían las secciones que se especifican explícitamente. Si pasas
    `prompt=None` no se tocará el prompt remoto; lo mismo para `voice`.

    `model_id` (opcional): si viene, se envía como `tts.model_id` para forzar
    un modelo de TTS concreto. Útil para migrar agentes que quedaron en v3
    conversacional a `eleven_flash_v2_5` (más rápido). Si se pasa sin `voice`,
    el payload solo actualiza el modelo.

    Devuelve el body JSON de la respuesta (contiene el estado actualizado del
    agente). Lanza `ElevenLabsError` en caso de fallo, con mensaje ya formateado
    para mostrar al usuario.
    """
    aid = _resolve_agent_id(agent_id)

    if prompt is None and voice is None and model_id is None:
        raise ElevenLabsError(
            "Nada que sincronizar: ni prompt, ni voz, ni modelo. "
            "Rellena al menos uno."
        )

    # Construimos solo las secciones tocadas. ElevenLabs hace merge parcial,
    # así que omitir una sección no borra el valor anterior.
    conversation_config: dict[str, Any] = {}

    if prompt is not None:
        prompt_text = (prompt or "").strip()
        if not prompt_text:
            raise ElevenLabsError("El prompt está vacío. Añade texto antes de sincronizar.")
        conversation_config["agent"] = {"prompt": {"prompt": prompt_text}}

    tts_payload: dict[str, Any] = {}
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
        tts_payload.update({
            "voice_id": voice.voice_id.strip(),
            "stability": round(float(voice.stability), 3),
            "similarity_boost": round(float(voice.similarity_boost), 3),
            "speed": round(float(voice.speed), 3),
        })
    if model_id is not None:
        mid = model_id.strip()
        if not mid:
            raise ElevenLabsError("model_id vacío. Pasa el nombre del modelo TTS o None.")
        tts_payload["model_id"] = mid
    if tts_payload:
        conversation_config["tts"] = tts_payload

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
