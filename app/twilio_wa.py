"""Adaptador de WhatsApp vía Twilio (sandbox o número real).

Este módulo convive con `whatsapp.py` (Meta Cloud API) sin sustituirlo. La ruta
`/whatsapp/twilio` recibe los webhooks de Twilio (form-encoded, no JSON) y los
normaliza al mismo pipeline interno que usa el flujo de Meta.

Twilio WhatsApp Sandbox permite probar sin verificación de Meta Business: el
usuario envía "join <keyword>" a un número compartido y a partir de ahí puede
conversar. Ideal para testing del bot mientras Meta for Developers se valida.

Referencias:
- https://www.twilio.com/docs/whatsapp/sandbox
- https://www.twilio.com/docs/usage/webhooks/webhooks-security (firma HMAC-SHA1)
- https://www.twilio.com/docs/messaging/api/message-resource (envío)

Formato de payload entrante (application/x-www-form-urlencoded):
    MessageSid=SMxxx
    From=whatsapp:+34600000000
    To=whatsapp:+14155238886
    Body=Hola
    NumMedia=1
    MediaUrl0=https://api.twilio.com/...
    MediaContentType0=audio/ogg
    ProfileName=Marcos
    WaId=34600000000
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import logging
from typing import Any, Iterable
from urllib.parse import urlencode

import httpx

from .config import settings

log = logging.getLogger(__name__)

TWILIO_API_BASE = "https://api.twilio.com/2010-04-01"


# ---------- Firma de webhook ----------

def verify_signature(
    url: str,
    params: dict[str, str],
    signature_header: str | None,
) -> bool:
    """Valida X-Twilio-Signature.

    Twilio firma (URL + concatenación de key=value ordenadas por key) con HMAC-SHA1
    usando el AuthToken como clave, y manda el resultado base64 en el header.
    """
    if not settings.twilio_auth_token:
        log.warning("TWILIO_AUTH_TOKEN no configurado; la firma NO se valida.")
        return True  # dev: permitimos, prod: conviene forzar
    if not signature_header:
        return False

    # Concatenar params ordenados por clave: url + k1v1 + k2v2 + ...
    sorted_items = sorted(params.items(), key=lambda kv: kv[0])
    data = url + "".join(f"{k}{v}" for k, v in sorted_items)
    digest = hmac.new(
        settings.twilio_auth_token.encode(),
        data.encode(),
        hashlib.sha1,
    ).digest()
    expected = base64.b64encode(digest).decode()
    return hmac.compare_digest(expected, signature_header)


# ---------- Extracción del mensaje ----------

def _strip_whatsapp_prefix(addr: str) -> str:
    """Convierte 'whatsapp:+34600000000' → '+34600000000'."""
    if addr and addr.startswith("whatsapp:"):
        return addr.split(":", 1)[1]
    return addr or ""


def extract_message(form: dict[str, str]) -> dict[str, Any] | None:
    """Normaliza un form-payload de Twilio a la misma forma que usa el pipeline.

    Devuelve:
      - texto: {type: "text",  from, text, message_id, to, profile_name}
      - audio: {type: "audio", from, media_url, media_content_type, message_id, to, profile_name}
      - None si el evento no es un mensaje accionable.
    """
    msg_type = (form.get("MessageType") or "").lower()
    message_id = form.get("MessageSid") or form.get("SmsMessageSid") or ""
    from_addr = _strip_whatsapp_prefix(form.get("From", ""))
    to_addr = _strip_whatsapp_prefix(form.get("To", ""))
    profile_name = form.get("ProfileName", "") or ""
    if not from_addr or not message_id:
        log.info("twilio webhook sin From/MessageSid, skip")
        return None

    base = {
        "from": from_addr,
        "to": to_addr,
        "message_id": message_id,
        "profile_name": profile_name,
    }

    try:
        num_media = int(form.get("NumMedia", "0") or "0")
    except ValueError:
        num_media = 0

    # Audio entrante (nota de voz de WhatsApp via Twilio)
    if num_media > 0:
        media_url = form.get("MediaUrl0", "")
        media_ct = form.get("MediaContentType0", "")
        if media_url and media_ct.startswith("audio/"):
            return {
                **base,
                "type": "audio",
                "media_url": media_url,
                "media_content_type": media_ct,
            }
        # Imágenes, documentos, etc. no soportados de momento
        log.info("twilio media no soportado: content_type=%s", media_ct)
        return None

    # Texto
    body = (form.get("Body") or "").strip()
    if body:
        return {**base, "type": "text", "text": body}

    # Otros eventos (p.ej. status callbacks) — no son mensajes de entrada
    log.info("twilio evento no accionable: MessageType=%s", msg_type or "?")
    return None


# ---------- Descarga de media con Basic Auth ----------

async def download_media(media_url: str) -> bytes:
    """Descarga un archivo de media de Twilio (requiere Basic Auth)."""
    if not settings.twilio_account_sid or not settings.twilio_auth_token:
        raise RuntimeError("TWILIO_ACCOUNT_SID/AUTH_TOKEN no configurados")
    auth = (settings.twilio_account_sid, settings.twilio_auth_token)
    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
        r = await client.get(media_url, auth=auth)
        r.raise_for_status()
        return r.content


# ---------- Envío saliente ----------

async def send_text(to_phone: str, body: str, from_number: str | None = None) -> dict[str, Any]:
    """Envía un mensaje de texto por la Messages API de Twilio.

    to_phone: en formato E.164 (+34600000000). Se le añade 'whatsapp:' delante.
    from_number: override del número sandbox/producción (E.164). Si no se pasa
                 usa settings.twilio_whatsapp_from.
    """
    if not settings.twilio_account_sid or not settings.twilio_auth_token:
        raise RuntimeError("TWILIO_ACCOUNT_SID/AUTH_TOKEN no configurados")

    sender = from_number or settings.twilio_whatsapp_from
    if not sender:
        raise RuntimeError("TWILIO_WHATSAPP_FROM no configurado")

    if not sender.startswith("whatsapp:"):
        sender = f"whatsapp:{sender}"
    to = to_phone if to_phone.startswith("whatsapp:") else f"whatsapp:{to_phone}"

    url = f"{TWILIO_API_BASE}/Accounts/{settings.twilio_account_sid}/Messages.json"
    auth = (settings.twilio_account_sid, settings.twilio_auth_token)
    payload = {
        "From": sender,
        "To": to,
        "Body": body[:1500],  # límite conservador (Twilio permite 1600)
    }

    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.post(url, data=payload, auth=auth)
    if r.status_code >= 400:
        log.error("Twilio send_text error %s: %s", r.status_code, r.text)
    r.raise_for_status()
    return r.json()


async def send_media(to_phone: str, media_url: str, body: str = "", from_number: str | None = None) -> dict[str, Any]:
    """Envía un mensaje con media (imagen/audio) por Twilio. media_url debe ser pública."""
    if not settings.twilio_account_sid or not settings.twilio_auth_token:
        raise RuntimeError("TWILIO_ACCOUNT_SID/AUTH_TOKEN no configurados")
    sender = from_number or settings.twilio_whatsapp_from
    if not sender:
        raise RuntimeError("TWILIO_WHATSAPP_FROM no configurado")
    if not sender.startswith("whatsapp:"):
        sender = f"whatsapp:{sender}"
    to = to_phone if to_phone.startswith("whatsapp:") else f"whatsapp:{to_phone}"

    url = f"{TWILIO_API_BASE}/Accounts/{settings.twilio_account_sid}/Messages.json"
    auth = (settings.twilio_account_sid, settings.twilio_auth_token)
    payload: dict[str, Any] = {"From": sender, "To": to, "MediaUrl": media_url}
    if body:
        payload["Body"] = body[:1500]

    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.post(url, data=payload, auth=auth)
    if r.status_code >= 400:
        log.error("Twilio send_media error %s: %s", r.status_code, r.text)
    r.raise_for_status()
    return r.json()
