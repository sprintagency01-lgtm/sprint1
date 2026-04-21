"""Fase 2: notas de voz por WhatsApp.

Recibe un audio entrante de WhatsApp:
1. Descarga el audio usando la Graph API de Meta (audio/ogg).
2. Lo transcribe con ElevenLabs Speech-to-Text (Scribe).
3. Pasa el texto al agente como si fuera un mensaje de texto normal.
4. Genera audio de respuesta con ElevenLabs TTS.
5. Sube el audio a Meta y lo envía como nota de voz.

Requiere en .env:
- ELEVENLABS_API_KEY
- ELEVENLABS_VOICE_ID (opcional; por defecto una voz estándar)
- WHATSAPP_ACCESS_TOKEN (ya usado por whatsapp.py)
"""
from __future__ import annotations

import logging
import pathlib
import tempfile
import uuid
from typing import Any

import httpx

from .config import settings

log = logging.getLogger(__name__)

ELEVENLABS_API = "https://api.elevenlabs.io"
GRAPH_API = "https://graph.facebook.com/v21.0"

# Voz por defecto (Rachel, inglés neutro). Para español recomiendo configurar
# ELEVENLABS_VOICE_ID en .env con una voz específica castellana del marketplace.
DEFAULT_VOICE_ID = "21m00Tcm4TlvDq8ikWAM"
DEFAULT_MODEL_STT = "scribe_v1"
DEFAULT_MODEL_TTS = "eleven_multilingual_v2"


# ---------- Descarga de audio desde WhatsApp ----------

async def download_whatsapp_media(media_id: str) -> bytes:
    """Descarga un archivo de media (audio) de WhatsApp por su media_id."""
    headers = {"Authorization": f"Bearer {settings.whatsapp_access_token}"}
    async with httpx.AsyncClient(timeout=30) as client:
        # 1. Obtener la URL temporal del recurso
        r = await client.get(f"{GRAPH_API}/{media_id}", headers=headers)
        r.raise_for_status()
        url = r.json()["url"]
        # 2. Descargar el binario (usa el mismo token)
        r2 = await client.get(url, headers=headers)
        r2.raise_for_status()
        return r2.content


# ---------- Transcripción (ElevenLabs Scribe) ----------

async def transcribe(audio_bytes: bytes, mime_type: str = "audio/ogg") -> str:
    """Transcribe audio con ElevenLabs Speech-to-Text."""
    if not settings.elevenlabs_api_key:
        raise RuntimeError("ELEVENLABS_API_KEY no configurada")

    headers = {"xi-api-key": settings.elevenlabs_api_key}
    files = {
        "file": ("audio.ogg", audio_bytes, mime_type),
        "model_id": (None, DEFAULT_MODEL_STT),
        "language_code": (None, "spa"),  # español; ISO 639-3
    }
    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.post(
            f"{ELEVENLABS_API}/v1/speech-to-text",
            headers=headers,
            files=files,
        )
    if r.status_code >= 400:
        log.error("STT error %s: %s", r.status_code, r.text)
        r.raise_for_status()
    data = r.json()
    return data.get("text", "").strip()


# ---------- TTS (ElevenLabs) ----------

async def synthesize(text: str, voice_id: str | None = None) -> bytes:
    """Genera audio (mp3) a partir de texto."""
    if not settings.elevenlabs_api_key:
        raise RuntimeError("ELEVENLABS_API_KEY no configurada")

    vid = voice_id or settings.elevenlabs_voice_id or DEFAULT_VOICE_ID
    headers = {
        "xi-api-key": settings.elevenlabs_api_key,
        "Content-Type": "application/json",
        "accept": "audio/mpeg",
    }
    payload = {
        "text": text,
        "model_id": DEFAULT_MODEL_TTS,
        "voice_settings": {"stability": 0.5, "similarity_boost": 0.75},
    }
    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.post(
            f"{ELEVENLABS_API}/v1/text-to-speech/{vid}",
            headers=headers,
            json=payload,
        )
    if r.status_code >= 400:
        log.error("TTS error %s: %s", r.status_code, r.text)
        r.raise_for_status()
    return r.content


# ---------- Subida a WhatsApp y envío como nota de voz ----------

async def upload_to_whatsapp(audio_bytes: bytes, phone_number_id: str, mime_type: str = "audio/mpeg") -> str:
    """Sube un archivo a Meta y devuelve el media_id para enviarlo después."""
    url = f"{GRAPH_API}/{phone_number_id}/media"
    headers = {"Authorization": f"Bearer {settings.whatsapp_access_token}"}
    files = {
        "file": ("reply.mp3", audio_bytes, mime_type),
        "type": (None, mime_type),
        "messaging_product": (None, "whatsapp"),
    }
    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.post(url, headers=headers, files=files)
    r.raise_for_status()
    return r.json()["id"]


async def send_audio(to_phone: str, media_id: str, phone_number_id: str) -> dict[str, Any]:
    """Envía un mensaje de tipo audio (se renderiza como nota de voz en WhatsApp)."""
    url = f"{GRAPH_API}/{phone_number_id}/messages"
    headers = {
        "Authorization": f"Bearer {settings.whatsapp_access_token}",
        "Content-Type": "application/json",
    }
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to_phone,
        "type": "audio",
        "audio": {"id": media_id},
    }
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.post(url, json=payload, headers=headers)
    if r.status_code >= 400:
        log.error("send_audio error %s: %s", r.status_code, r.text)
    r.raise_for_status()
    return r.json()


# ---------- Pipeline completo ----------

async def handle_incoming_voice(
    from_phone: str,
    media_id: str,
    phone_number_id: str,
    agent_reply_fn,
) -> None:
    """
    Recibe un audio entrante, lo transcribe, pasa el texto al agente, genera
    respuesta en audio y la envía.

    agent_reply_fn(text) → str   (pasado desde main.py para no hacer import circular)
    """
    try:
        log.info("voz in  %s media_id=%s", from_phone, media_id)
        audio_in = await download_whatsapp_media(media_id)
        text_in = await transcribe(audio_in)
        log.info("voz transcrita: %s", text_in)

        if not text_in:
            # No hemos entendido nada → respuesta texto
            from . import whatsapp as wa
            await wa.send_text(
                from_phone,
                "Perdona, no he podido entender el audio. ¿Puedes escribirlo o volver a grabarlo?",
                phone_number_id,
            )
            return

        reply_text = await agent_reply_fn(text_in)
        audio_out = await synthesize(reply_text)
        media_id_out = await upload_to_whatsapp(audio_out, phone_number_id)
        await send_audio(from_phone, media_id_out, phone_number_id)
        log.info("voz out %s: %s", from_phone, reply_text[:80])

    except Exception:
        log.exception("Error procesando voz entrante")
        try:
            from . import whatsapp as wa
            await wa.send_text(
                from_phone,
                "Ha habido un problema procesando tu audio. Vuelve a intentarlo en un rato.",
                phone_number_id,
            )
        except Exception:
            pass
