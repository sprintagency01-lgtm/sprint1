"""Notificaciones best-effort para leads de la landing.

El guardado del lead es la fuente de verdad. Las notificaciones nunca deben
romper la respuesta pública de `/api/leads`: si Slack/Make/Resend falla,
registramos el error y seguimos.
"""
from __future__ import annotations

import html
import logging
from dataclasses import dataclass

import httpx

from .brevo import BrevoLead, sync_lead_contact
from .config import settings

log = logging.getLogger(__name__)

_TIMEOUT = httpx.Timeout(10.0, connect=4.0)
_RESEND_URL = "https://api.resend.com/emails"


@dataclass(frozen=True)
class LeadNotification:
    lead_id: int
    name: str
    phone: str
    email: str = ""
    company: str = ""
    sector: str = ""
    message: str = ""
    source: str = ""
    utm_source: str = ""
    utm_medium: str = ""
    utm_campaign: str = ""


def notify_new_lead(lead: LeadNotification) -> None:
    """Dispara todas las salidas configuradas para un lead nuevo."""
    _sync_brevo(lead)
    _post_webhook(lead)
    _send_internal_email(lead)
    _send_autoreply(lead)


def _sync_brevo(lead: LeadNotification) -> None:
    sync_lead_contact(
        BrevoLead(
            lead_id=lead.lead_id,
            name=lead.name,
            phone=lead.phone,
            email=lead.email,
            company=lead.company,
            sector=lead.sector,
        )
    )


def _post_webhook(lead: LeadNotification) -> None:
    url = settings.lead_notify_webhook_url.strip()
    if not url:
        return
    text = _internal_text(lead)
    payload = {
        "text": text,
        "lead": {
            "id": lead.lead_id,
            "name": lead.name,
            "phone": lead.phone,
            "email": lead.email,
            "company": lead.company,
            "sector": lead.sector,
            "source": lead.source,
            "utm_source": lead.utm_source,
            "utm_medium": lead.utm_medium,
            "utm_campaign": lead.utm_campaign,
        },
    }
    try:
        r = httpx.post(url, json=payload, timeout=_TIMEOUT)
        r.raise_for_status()
    except httpx.HTTPStatusError:
        # Algunos Incoming Webhooks estrictos (p.ej. Slack) sólo aceptan
        # `text`. Reintentamos con payload mínimo antes de rendirnos.
        try:
            r = httpx.post(url, json={"text": text}, timeout=_TIMEOUT)
            r.raise_for_status()
        except Exception:
            log.exception("No se pudo enviar webhook de lead id=%s", lead.lead_id)
    except Exception:
        log.exception("No se pudo enviar webhook de lead id=%s", lead.lead_id)


def _send_internal_email(lead: LeadNotification) -> None:
    to = settings.lead_notify_email_to.strip()
    if not to:
        return
    subject = f"Nuevo lead Sprintia: {lead.company or lead.name}"
    _send_email(
        to=to,
        subject=subject,
        text=_internal_text(lead),
        html_body=_internal_html(lead),
        log_label=f"email interno lead id={lead.lead_id}",
    )


def _send_autoreply(lead: LeadNotification) -> None:
    if not settings.lead_autoreply_enabled or not lead.email:
        return
    name = lead.name.split()[0] if lead.name else "ahí"
    text = (
        f"Hola {name},\n\n"
        "Gracias por contactar con Sprintia. Hemos recibido tu solicitud y "
        "te responderemos lo antes posible para entender tu caso y ver cómo "
        "podemos ayudarte con el asistente de reservas por voz.\n\n"
        "Si necesitas añadir algo, puedes responder directamente a este email.\n\n"
        "Un saludo,\n"
        "Equipo Sprintia"
    )
    html_body = (
        f"<p>Hola {html.escape(name)},</p>"
        "<p>Gracias por contactar con Sprintia. Hemos recibido tu solicitud y "
        "te responderemos lo antes posible para entender tu caso y ver cómo "
        "podemos ayudarte con el asistente de reservas por voz.</p>"
        "<p>Si necesitas añadir algo, puedes responder directamente a este email.</p>"
        "<p>Un saludo,<br>Equipo Sprintia</p>"
    )
    _send_email(
        to=lead.email,
        subject=settings.lead_autoreply_subject,
        text=text,
        html_body=html_body,
        log_label=f"autorespuesta lead id={lead.lead_id}",
    )


def _send_email(*, to: str, subject: str, text: str, html_body: str, log_label: str) -> None:
    api_key = settings.resend_api_key.strip()
    sender = settings.lead_email_from.strip()
    if not api_key or not sender:
        log.warning("%s no enviado: falta RESEND_API_KEY o LEAD_EMAIL_FROM", log_label)
        return
    payload = {
        "from": sender,
        "to": [to],
        "subject": subject,
        "text": text,
        "html": html_body,
    }
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    try:
        r = httpx.post(_RESEND_URL, json=payload, headers=headers, timeout=_TIMEOUT)
        r.raise_for_status()
    except Exception:
        log.exception("No se pudo enviar %s", log_label)


def _internal_text(lead: LeadNotification) -> str:
    lines = [
        f"Nuevo lead en Sprintia #{lead.lead_id}",
        f"Nombre: {lead.name}",
        f"Teléfono: {lead.phone}",
    ]
    if lead.email:
        lines.append(f"Email: {lead.email}")
    if lead.company:
        lines.append(f"Empresa: {lead.company}")
    if lead.sector:
        lines.append(f"Sector: {lead.sector}")
    if lead.source:
        lines.append(f"Origen: {lead.source}")
    utm = " / ".join(x for x in (lead.utm_source, lead.utm_medium, lead.utm_campaign) if x)
    if utm:
        lines.append(f"UTM: {utm}")
    if lead.message:
        lines.append("")
        lines.append(f"Mensaje: {lead.message}")
    lines.append("")
    lines.append("Entrar al CMS: https://sprintiasolutions.com/admin/clientes?kind=lead")
    return "\n".join(lines)


def _internal_html(lead: LeadNotification) -> str:
    text = html.escape(_internal_text(lead)).replace("\n", "<br>")
    return f"<p>{text}</p>"
