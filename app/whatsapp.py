"""Cliente mínimo de la WhatsApp Cloud API de Meta.

Cubre:
- Verificación del webhook (GET).
- Validación de firma del webhook (X-Hub-Signature-256).
- Envío de mensajes de texto.
- Envío de mensajes interactivos (botones y listas).
- Detección de audios entrantes (el envío de audios vive en voice.py).
- Detección de respuestas a mensajes interactivos (button_reply / list_reply).

Mensajes interactivos soportados (dentro de la ventana de 24h
customer-initiated, no requieren templates pre-aprobados):

- `button`: hasta 3 botones de respuesta rápida. Título ≤ 20 chars.
- `list`:   hasta 10 filas agrupadas en secciones. Título ≤ 24 chars,
            descripción opcional ≤ 72 chars.

Referencias:
- https://developers.facebook.com/docs/whatsapp/cloud-api/guides/send-message-templates/interactive-message-templates
- https://developers.facebook.com/docs/whatsapp/cloud-api/reference/messages
"""
from __future__ import annotations

import hashlib
import hmac
import logging
from typing import Any, Iterable

import httpx

from .config import settings

log = logging.getLogger(__name__)

GRAPH_API_BASE = "https://graph.facebook.com/v21.0"

# Límites de la API (truncamos en el backend antes de enviar para no
# recibir 400s de Meta con textos que casi siempre llegan ajustados).
_BTN_TITLE_MAX = 20
_LIST_ROW_TITLE_MAX = 24
_LIST_ROW_DESC_MAX = 72
_LIST_BODY_MAX = 1024
_BTN_BODY_MAX = 1024


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


# ---------- Mensajes interactivos: botones ----------

async def send_buttons(
    to_phone: str,
    body: str,
    buttons: list[dict[str, str]],
    phone_number_id: str | None = None,
    header: str | None = None,
    footer: str | None = None,
) -> dict[str, Any]:
    """Envía un mensaje con hasta 3 botones de respuesta rápida.

    buttons: [{"id": "confirm:yes", "title": "Sí"}, ...] — máx 3.
    El id es el `button_reply.id` que recibiremos al hacer clic.
    """
    if not buttons:
        raise ValueError("send_buttons requiere al menos 1 botón")
    if len(buttons) > 3:
        log.warning("send_buttons recibió %d botones, recortando a 3", len(buttons))
        buttons = buttons[:3]

    action_buttons = []
    for b in buttons:
        title = (b.get("title") or "").strip()[:_BTN_TITLE_MAX] or "OK"
        bid = (b.get("id") or "").strip() or title
        action_buttons.append({
            "type": "reply",
            "reply": {"id": bid, "title": title},
        })

    interactive: dict[str, Any] = {
        "type": "button",
        "body": {"text": (body or "")[:_BTN_BODY_MAX]},
        "action": {"buttons": action_buttons},
    }
    if header:
        interactive["header"] = {"type": "text", "text": header[:60]}
    if footer:
        interactive["footer"] = {"text": footer[:60]}

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
        "type": "interactive",
        "interactive": interactive,
    }
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.post(url, json=payload, headers=headers)
    if r.status_code >= 400:
        log.error("WhatsApp send_buttons error %s: %s", r.status_code, r.text)
    r.raise_for_status()
    return r.json()


# ---------- Mensajes interactivos: listas ----------

async def send_list(
    to_phone: str,
    body: str,
    button_label: str,
    rows: list[dict[str, str]],
    phone_number_id: str | None = None,
    section_title: str = "Opciones",
    header: str | None = None,
    footer: str | None = None,
) -> dict[str, Any]:
    """Envía un mensaje tipo lista con hasta 10 filas.

    rows: [{"id": "slot:...", "title": "vie 24, 10:00", "description": "con Mario"}, ...]
    button_label: texto del botón que abre la hoja (≤20 chars).
    """
    if not rows:
        raise ValueError("send_list requiere al menos 1 fila")
    if len(rows) > 10:
        log.warning("send_list recibió %d filas, recortando a 10", len(rows))
        rows = rows[:10]

    api_rows = []
    for r_ in rows:
        title = (r_.get("title") or "").strip()[:_LIST_ROW_TITLE_MAX] or "Opción"
        rid = (r_.get("id") or "").strip() or title
        row: dict[str, str] = {"id": rid, "title": title}
        desc = (r_.get("description") or "").strip()
        if desc:
            row["description"] = desc[:_LIST_ROW_DESC_MAX]
        api_rows.append(row)

    interactive: dict[str, Any] = {
        "type": "list",
        "body": {"text": (body or "")[:_LIST_BODY_MAX]},
        "action": {
            "button": (button_label or "Elegir").strip()[:_BTN_TITLE_MAX] or "Elegir",
            "sections": [
                {"title": (section_title or "Opciones")[:24], "rows": api_rows},
            ],
        },
    }
    if header:
        interactive["header"] = {"type": "text", "text": header[:60]}
    if footer:
        interactive["footer"] = {"text": footer[:60]}

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
        "type": "interactive",
        "interactive": interactive,
    }
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.post(url, json=payload, headers=headers)
    if r.status_code >= 400:
        log.error("WhatsApp send_list error %s: %s", r.status_code, r.text)
    r.raise_for_status()
    return r.json()


async def send_interactive(
    to_phone: str,
    spec: dict[str, Any],
    phone_number_id: str | None = None,
) -> dict[str, Any]:
    """Dispatcher genérico: recibe un dict del formato estándar interno
    (el que produce el agente) y llama a send_list o send_buttons según type.

    spec = {
        "type": "list" | "buttons",
        "body": str,
        "button": str (opcional, default "Elegir"),   # sólo list
        "section_title": str (opcional),              # sólo list
        "options": [{"id": str, "title": str, "description"?: str}, ...],
    }
    """
    kind = (spec or {}).get("type") or "list"
    body = (spec or {}).get("body") or ""
    options = (spec or {}).get("options") or []
    if kind == "buttons":
        return await send_buttons(
            to_phone=to_phone, body=body, buttons=options,
            phone_number_id=phone_number_id,
        )
    # default → list
    return await send_list(
        to_phone=to_phone,
        body=body,
        button_label=spec.get("button") or "Elegir",
        section_title=spec.get("section_title") or "Opciones",
        rows=options,
        phone_number_id=phone_number_id,
    )


# ---------- Parser de webhook entrante ----------

def extract_message(payload: dict[str, Any]) -> dict[str, Any] | None:
    """Extrae los campos interesantes del webhook de Meta.

    Devuelve un dict:
      - Texto:  {type: "text",  from, text, message_id, phone_number_id}
      - Audio:  {type: "audio", from, audio_media_id, message_id, phone_number_id}
      - Click:  {type: "interactive_reply", from, reply_id, reply_title,
                 reply_kind ("button"|"list"), message_id, phone_number_id}
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
        if msg_type == "interactive":
            inter = msg.get("interactive") or {}
            itype = inter.get("type")
            if itype == "button_reply":
                br = inter.get("button_reply") or {}
                return {
                    **base,
                    "type": "interactive_reply",
                    "reply_kind": "button",
                    "reply_id": br.get("id", ""),
                    "reply_title": br.get("title", ""),
                }
            if itype == "list_reply":
                lr = inter.get("list_reply") or {}
                return {
                    **base,
                    "type": "interactive_reply",
                    "reply_kind": "list",
                    "reply_id": lr.get("id", ""),
                    "reply_title": lr.get("title", ""),
                }
            log.info("interactive type no soportado: %s", itype)
            return None
        # Otros tipos (image, document, location...) ignorados.
        log.info("Tipo de mensaje no soportado: %s", msg_type)
        return None
    except (KeyError, IndexError, TypeError):
        log.warning("Payload inesperado: %s", payload)
        return None
