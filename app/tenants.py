"""Carga de tenants.

Lee de la BD (tabla `tenants`). Si la tabla está vacía, cae al YAML antiguo
para no romper el bot en instalaciones existentes. Devuelve siempre dicts en
el formato que espera `agent.py`.
"""
from __future__ import annotations

import pathlib
from typing import Any

import yaml
from sqlalchemy.orm import Session

from .config import settings
from . import db as db_module


_DEFAULT_TENANT_TEMPLATE = {
    "id": "default",
    "name": "Negocio demo",
    "phone_number_id": None,
    "calendar_id": None,
    "services": [{"nombre": "Consulta", "duracion_min": 30, "precio": 0}],
    "business_hours": {
        "mon": ["09:00", "20:00"], "tue": ["09:00", "20:00"],
        "wed": ["09:00", "20:00"], "thu": ["09:00", "20:00"],
        "fri": ["09:00", "20:00"], "sat": ["closed"], "sun": ["closed"],
    },
    "system_prompt": (
        "Eres el asistente virtual de reservas del negocio. "
        "Tu objetivo es ayudar a clientes a consultar disponibilidad, reservar, "
        "reagendar o cancelar citas. Sé breve, amable y directo en español. "
        "Nunca inventes servicios ni horarios; usa las herramientas. "
        "Confirma SIEMPRE la hora elegida antes de crear la reserva. "
        "No preguntes ni almacenes datos financieros. "
        "Si el cliente pide algo que no puedes hacer, ofrécele hablar con una persona."
    ),
}


def load_tenants() -> list[dict[str, Any]]:
    """Devuelve todos los tenants como lista de dicts."""
    # 1) Intenta la BD
    with Session(db_module.engine) as session:
        rows = session.query(db_module.Tenant).all()
        if rows:
            return [t.to_dict() for t in rows]

    # 2) Fallback al YAML si la tabla está vacía
    path = pathlib.Path(settings.tenants_file)
    if path.exists():
        data = yaml.safe_load(path.read_text()) or {}
        tenants = data.get("tenants")
        if tenants:
            return tenants

    # 3) Último recurso: tenant demo
    return [_DEFAULT_TENANT_TEMPLATE]


def find_tenant_by_phone_number_id(phone_number_id: str) -> dict[str, Any]:
    """Busca el tenant asociado a un phone_number_id de WhatsApp.
    Si no lo encuentra devuelve el primero (modo monotenant)."""
    with Session(db_module.engine) as session:
        t = (
            session.query(db_module.Tenant)
            .filter(db_module.Tenant.phone_number_id == phone_number_id)
            .first()
        )
        if t is not None:
            return t.to_dict()

    tenants = load_tenants()
    for t in tenants:
        if t.get("phone_number_id") == phone_number_id:
            return t
    return tenants[0]


def get_tenant(tenant_id: str) -> dict[str, Any] | None:
    """Busca un tenant por id. Devuelve dict o None."""
    with Session(db_module.engine) as session:
        t = session.get(db_module.Tenant, tenant_id)
        if t is not None:
            return t.to_dict()
    for t in load_tenants():
        if t.get("id") == tenant_id:
            return t
    return None
