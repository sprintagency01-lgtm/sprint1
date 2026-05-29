"""Sincronización de leads con Brevo Contacts.

Usamos la API HTTP directamente para mantener la dependencia ligera. Brevo
permite crear un contacto por email o por teléfono (`SMS`) y añadirlo a listas
con `listIds`.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx

from .config import settings

log = logging.getLogger(__name__)

_CONTACTS_URL = "https://api.brevo.com/v3/contacts"
_TIMEOUT = httpx.Timeout(10.0, connect=4.0)


@dataclass(frozen=True)
class BrevoLead:
    lead_id: int
    name: str
    phone: str
    email: str = ""
    company: str = ""
    sector: str = ""


def sync_lead_contact(lead: BrevoLead) -> None:
    """Crea o actualiza un contacto en Brevo si está configurado."""
    api_key = settings.brevo_api_key.strip()
    if not api_key:
        return

    payload = _contact_payload(lead)
    if not payload.get("email") and not payload.get("attributes", {}).get("SMS"):
        log.warning("Lead id=%s no sincronizado con Brevo: falta email o teléfono", lead.lead_id)
        return

    headers = {
        "accept": "application/json",
        "api-key": api_key,
        "content-type": "application/json",
    }
    try:
        r = httpx.post(_CONTACTS_URL, json=payload, headers=headers, timeout=_TIMEOUT)
        r.raise_for_status()
    except Exception:
        log.exception("No se pudo sincronizar lead id=%s con Brevo", lead.lead_id)


def _contact_payload(lead: BrevoLead) -> dict:
    attributes: dict[str, str] = {}
    first_name, last_name = _split_name(lead.name)
    if first_name:
        attributes["FNAME"] = first_name
    if last_name:
        attributes["LNAME"] = last_name
    phone = _normalize_phone(lead.phone)
    if phone:
        attributes["SMS"] = phone
    _set_optional_attribute(attributes, settings.brevo_company_attribute, lead.company, 200)
    _set_optional_attribute(attributes, settings.brevo_sector_attribute, lead.sector, 80)
    _set_optional_attribute(attributes, settings.brevo_lead_id_attribute, str(lead.lead_id), 40)

    payload: dict = {
        "attributes": attributes,
        "updateEnabled": settings.brevo_update_enabled,
    }
    if lead.email:
        payload["email"] = lead.email
    list_ids = _parse_list_ids(settings.brevo_list_ids)
    if list_ids:
        payload["listIds"] = list_ids
    return payload


def _parse_list_ids(raw: str) -> list[int]:
    ids: list[int] = []
    for part in (raw or "").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            ids.append(int(part))
        except ValueError:
            log.warning("BREVO_LIST_IDS contiene un id inválido: %r", part)
    return ids


def _set_optional_attribute(
    attributes: dict[str, str],
    name: str,
    value: str,
    maxlen: int,
) -> None:
    attr = (name or "").strip().upper()
    val = (value or "").strip()
    if attr and val:
        attributes[attr] = val[:maxlen]


def _split_name(name: str) -> tuple[str, str]:
    parts = (name or "").strip().split()
    if not parts:
        return "", ""
    if len(parts) == 1:
        return parts[0][:80], ""
    return parts[0][:80], " ".join(parts[1:])[:120]


def _normalize_phone(phone: str) -> str:
    # Brevo acepta formatos con + y dígitos. Quitamos separadores visuales para
    # evitar rechazos por espacios/paréntesis que el form público sí permite.
    raw = (phone or "").strip()
    if not raw:
        return ""
    prefix = "+" if raw.startswith("+") else ""
    digits = "".join(ch for ch in raw if ch.isdigit())
    if len(digits) < 6:
        return ""
    return f"{prefix}{digits}"
