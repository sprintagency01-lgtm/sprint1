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


def _load_yaml_by_id() -> dict[str, dict[str, Any]]:
    """Lee tenants.yaml y devuelve un dict indexado por id.

    Se usa como fuente complementaria: hay campos operativos (peluqueros,
    calendarios por peluquero, dias_trabajo) que todavía no existen como
    columna en la BD del CMS. Si falla la lectura, devolvemos vacío para no
    tumbar el arranque.
    """
    path = pathlib.Path(settings.tenants_file)
    if not path.exists():
        return {}
    try:
        data = yaml.safe_load(path.read_text()) or {}
    except Exception:  # pragma: no cover - YAML mal formado no puede romper
        return {}
    result: dict[str, dict[str, Any]] = {}
    for t in (data.get("tenants") or []):
        tid = t.get("id")
        if tid:
            result[tid] = t
    return result


# Campos que sólo existen en el YAML de momento y que mergeamos sobre los
# tenants de la BD cuando comparten id.
_YAML_ONLY_FIELDS = ("peluqueros",)


def _merge_yaml_into_db(db_tenant: dict[str, Any], yaml_tenant: dict[str, Any]) -> dict[str, Any]:
    """Añade al dict de BD los campos que sólo viven en el YAML.

    No pisa nada que la BD ya tenga con valor; sólo rellena los vacíos.
    """
    for key in _YAML_ONLY_FIELDS:
        if not db_tenant.get(key) and yaml_tenant.get(key):
            db_tenant[key] = yaml_tenant[key]
    return db_tenant


def load_tenants() -> list[dict[str, Any]]:
    """Devuelve todos los tenants como lista de dicts.

    La BD (panel CMS) es la fuente principal; el YAML sirve para dos cosas:
    - Fallback cuando la tabla está vacía (primer arranque sin CMS).
    - Fuente complementaria de los campos operativos que aún no existen como
      columna (p.ej. `peluqueros`).
    """
    yaml_by_id = _load_yaml_by_id()

    # 1) Intenta la BD (enriquecida con YAML)
    with Session(db_module.engine) as session:
        rows = session.query(db_module.Tenant).all()
        if rows:
            result = []
            for t in rows:
                td = t.to_dict()
                yt = yaml_by_id.get(td.get("id"))
                if yt:
                    _merge_yaml_into_db(td, yt)
                result.append(td)
            return result

    # 2) Fallback al YAML si la tabla está vacía
    if yaml_by_id:
        return list(yaml_by_id.values())

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
    """Busca un tenant por id. Devuelve dict o None (enriquecido con YAML)."""
    yaml_by_id = _load_yaml_by_id()
    with Session(db_module.engine) as session:
        t = session.get(db_module.Tenant, tenant_id)
        if t is not None:
            td = t.to_dict()
            yt = yaml_by_id.get(tenant_id)
            if yt:
                _merge_yaml_into_db(td, yt)
            return td
    for t in load_tenants():
        if t.get("id") == tenant_id:
            return t
    return None
