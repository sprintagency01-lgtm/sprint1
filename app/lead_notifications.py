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

from .brevo import BrevoLead, send_template_email, send_transactional_email, sync_lead_contact
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
    country: str = ""
    landing_language: str = "es"
    marketing_consent: bool = False
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
            country=lead.country,
            landing_language=lead.landing_language,
            marketing_consent=lead.marketing_consent,
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
            "country": lead.country,
            "landing_language": lead.landing_language,
            "marketing_consent": lead.marketing_consent,
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
    subject, text, html_body = _autoreply_content(lead)
    template_id = _int_or_none(settings.brevo_autoreply_template_id)
    if template_id and send_template_email(
        to_email=lead.email,
        to_name=lead.name or lead.email,
        template_id=template_id,
        params=_autoreply_params(lead),
        tag="lead-autoreply",
    ):
        return
    _send_email(
        to=lead.email,
        subject=subject,
        text=text,
        html_body=html_body,
        log_label=f"autorespuesta lead id={lead.lead_id}",
    )


def _send_email(*, to: str, subject: str, text: str, html_body: str, log_label: str) -> None:
    recipients = _split_recipients(to)
    if not recipients:
        return

    remaining: list[str] = []
    for recipient in recipients:
        if not send_transactional_email(
            to_email=recipient,
            subject=subject,
            text=text,
            html_body=html_body,
            tag="lead",
        ):
            remaining.append(recipient)
    if not remaining:
        return

    api_key = settings.resend_api_key.strip()
    sender = settings.lead_email_from.strip()
    if not api_key or not sender:
        log.warning("%s no enviado: falta RESEND_API_KEY o LEAD_EMAIL_FROM", log_label)
        return
    payload = {
        "from": sender,
        "to": remaining,
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
    if lead.country:
        lines.append(f"País: {lead.country}")
    if lead.landing_language:
        lines.append(f"Idioma landing: {lead.landing_language}")
    lines.append(f"Consentimiento marketing: {'sí' if lead.marketing_consent else 'no'}")
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


def _split_recipients(raw: str) -> list[str]:
    return [p.strip() for p in (raw or "").replace(";", ",").split(",") if p.strip()]


def _language_family(raw: str) -> str:
    lang = (raw or "es").strip().lower().replace("_", "-")
    return (lang.split("-", 1)[0] or "es")[:2]


def _int_or_none(raw: str) -> int | None:
    try:
        return int(str(raw or "").strip())
    except (TypeError, ValueError):
        return None


def _autoreply_params(lead: LeadNotification) -> dict[str, str]:
    subject, text, html_body = _autoreply_content(lead)
    _, greeting, body, extra, signoff = _autoreply_parts(lead)
    signoff_lines = signoff.split("\n", 1)
    first_name = lead.name.split()[0] if lead.name else ""
    return {
        "FIRSTNAME": first_name,
        "NAME": lead.name or "",
        "COMPANY": lead.company or "",
        "COUNTRY": lead.country or "",
        "LANGUAGE": lead.landing_language or "es",
        "SUBJECT": subject,
        "GREETING": greeting,
        "BODY": body,
        "EXTRA": extra,
        "SIGNOFF_LINE1": signoff_lines[0],
        "SIGNOFF_LINE2": signoff_lines[1] if len(signoff_lines) > 1 else "",
        "TEXT": text,
        "HTML": html_body,
    }


def _autoreply_content(lead: LeadNotification) -> tuple[str, str, str]:
    subject, greeting, body, extra, signoff = _autoreply_parts(lead)

    text = f"{greeting}\n\n{body}\n\n{extra}\n\n{signoff}"
    html_body = (
        f"<p>{html.escape(greeting)}</p>"
        f"<p>{html.escape(body)}</p>"
        f"<p>{html.escape(extra)}</p>"
        f"<p>{html.escape(signoff).replace(chr(10), '<br>')}</p>"
    )
    return subject, text, html_body


def _autoreply_parts(lead: LeadNotification) -> tuple[str, str, str, str, str]:
    name = lead.name.split()[0] if lead.name else ""
    family = _language_family(lead.landing_language)

    if family == "en":
        greeting = f"Hi {name}," if name else "Hi,"
        subject = "We received your request at Sprintia"
        body = (
            "Thanks for contacting Sprintia. We have received your request and "
            "will get back to you as soon as possible to understand your case "
            "and see how we can help with your voice booking assistant."
        )
        extra = "If you need to add anything, you can reply directly to this email."
        signoff = "Best,\nSprintia Team"
    elif family == "pt":
        greeting = f"Olá {name}," if name else "Olá,"
        subject = "Recebemos o seu pedido na Sprintia"
        body = (
            "Obrigado por contactar a Sprintia. Recebemos o seu pedido e "
            "responderemos o mais breve possível para entender o seu caso e ver "
            "como podemos ajudar com o assistente de reservas por voz."
        )
        extra = "Se precisar de acrescentar algo, pode responder diretamente a este email."
        signoff = "Cumprimentos,\nEquipa Sprintia"
    elif family == "fr":
        greeting = f"Bonjour {name}," if name else "Bonjour,"
        subject = "Nous avons bien reçu votre demande chez Sprintia"
        body = (
            "Merci d’avoir contacté Sprintia. Nous avons bien reçu votre demande "
            "et nous vous répondrons dès que possible afin de comprendre votre "
            "besoin et voir comment notre assistant vocal de réservation peut vous aider."
        )
        extra = "Si vous souhaitez ajouter une précision, vous pouvez répondre directement à cet email."
        signoff = "Cordialement,\nL’équipe Sprintia"
    else:
        greeting = f"Hola {name}," if name else "Hola,"
        subject = settings.lead_autoreply_subject or "Hemos recibido tu solicitud en Sprintia"
        body = (
            "Gracias por contactar con Sprintia. Hemos recibido tu solicitud y "
            "te responderemos lo antes posible para entender tu caso y ver cómo "
            "podemos ayudarte con el asistente de reservas por voz."
        )
        extra = "Si necesitas añadir algo, puedes responder directamente a este email."
        signoff = "Un saludo,\nEquipo Sprintia"

    return subject, greeting, body, extra, signoff
