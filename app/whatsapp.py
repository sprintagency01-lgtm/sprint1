"""Cliente mínimo de la WhatsApp Cloud API de Meta.

Cubre:
- Verificación del webhook (GET).
- Validación de firma del webhook (X-Hub-Signature-256).
- Envío de mensajes de texto.
- Detección de audios entrantes (el envío de audios vive en voice.py).

TODO futuro:
- Templates salientes (marketing).
- Markers de leído.
- Soporte de imágenes / documentos / interactive replies.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
from typing import Any

import httpx

from .config import settings

log = logging.getLogger(__name__)

GRAPH_API_BASE = "https://graph.facebook.com/v21.0"


def verify_signature(raw_body: bytes, signature_header: str | None) -> bool:
    """Valida X-Hub-Signature-256 que Meta añade a los webhooks."""
    if not settings.whatsapp_app_secret:
        log.warning("WHATSAPP_APP_SECRET no configurado; la firma NO se valida.")
        return True  # en dev permitimos; en prod hay que forzar
    if not signature_header or not signature_header.startswith("sha256="):
        return False
    expected = hmac.new(
        settings.whatsapp_app_secret.encode(),
        raw_body,
        hashlib.sha256,
    ).hexdigest()
    received = signature_header.split("=", 1)[1]
    return hmac.compare_digest(expected, received)


async def send_text(to_phone: str, body: str, phone_number_id: str | None = None) -> dict[str, Any]:
    """Envía un mensaje de texto por la Cloud API."""
    pnid = phone_number_id or settings.whatsapp_phone_number_id
    url = f"{GRAPH_API_BASE}/{pnid}/messages"
    headers = {
        "Authorization": f"Bearer {settings.whatsapp_access_token}",
        "Content-Type": "application/json",
    }
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to_phone,
        "type": "text",
        "text": {"body": body[:4000]},  # límite conservador
    }
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.post(url, json=payload, headers=headers)
    if r.status_code >= 400:
        log.error("WhatsApp send_text error %s: %s", r.status_code, r.text)
    r.raise_for_status()
    return r.json()


def extract_message(payload: dict[str, Any]) -> dict[str, Any] | None:
    """Extrae los campos interesantes del webhook de Meta.

    Devuelve un dict:
      - Texto:  {type: "text",  from, text, message_id, phone_number_id}
      - Audio:  {type: "audio", from, audio_media_id, message_id, phone_number_id}
    None si el webhook no es un mensaje entrante (p. ej. status update) o si
    el tipo no está soportado aún.
    """
    try:
        entry = payload["entry"][0]
        change = entry["changes"][0]
        value = change["value"]
        if "messages" not in value:
            return None
        msg = value["messages"][0]
        msg_type = msg.get("type")
        base = {
            "from": msg["from"],
            "message_id": msg["id"],
            "phone_number_id": value["metadata"]["phone_number_id"],
        }
        if msg_type == "text":
            return {**base, "type": "text", "text": msg["text"]["body"]}
        if msg_type == "audio":
            return {
                **base,
                "type": "audio",
                "audio_media_id": msg["audio"]["id"],
            }
        # Otros tipos (image, document, interactive, location...) ignorados.
        log.info("Tipo de mensaje no soportado: %s", msg_type)
        return None
    except (KeyError, IndexError, TypeError):
        log.warning("Payload inesperado: %s", payload)
        return None
