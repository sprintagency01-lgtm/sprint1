"""Migra tenants.yaml a la tabla `tenants` de SQLite.

Uso:
    python -m app.migrate_yaml           # migra ./tenants.yaml
    python -m app.migrate_yaml path.yaml # migra otro fichero

Es idempotente: si el tenant ya existe con el mismo id, actualiza sus campos
(upsert). No borra servicios preexistentes del mismo tenant — los reemplaza
por los del YAML.
"""
from __future__ import annotations

import pathlib
import sys
from typing import Any

import yaml
from sqlalchemy.orm import Session

from . import db as db_module
from .config import settings


def _infer_business_hours(raw: Any) -> dict[str, list[str]]:
    """Acepta tanto el formato antiguo {open, close} como el nuevo por día."""
    if not raw:
        return {}
    if "open" in raw and "close" in raw:
        # Formato antiguo: aplicarlo L–S; domingo cerrado.
        default = [raw["open"], raw["close"]]
        return {
            "mon": default, "tue": default, "wed": default,
            "thu": default, "fri": default, "sat": default, "sun": ["closed"],
        }
    return raw


def _extract_assistant(t: dict) -> dict:
    """Soporta dos formatos:
      - Nuevo: campo `assistant: {name, tone, ...}`
      - Antiguo: solo `system_prompt`. Valores por defecto para los demás.
    """
    a = t.get("assistant") or {}
    return {
        "name":            a.get("name", "Asistente"),
        "tone":            a.get("tone", "cercano"),
        "formality":       a.get("formality", "tu"),
        "emoji":           bool(a.get("emoji", True)),
        "greeting":        a.get("greeting", ""),
        "fallback_phone":  a.get("fallback_phone", ""),
        "rules":           a.get("rules", []),
    }


def migrate(yaml_path: str) -> None:
    path = pathlib.Path(yaml_path)
    if not path.exists():
        print(f"[!] No existe {yaml_path}. Nada que migrar.")
        return

    data = yaml.safe_load(path.read_text()) or {}
    tenants = data.get("tenants") or []
    if not tenants:
        print(f"[!] {yaml_path} no contiene tenants.")
        return

    inserted, updated = 0, 0
    with Session(db_module.engine) as s:
        for raw in tenants:
            tid = str(raw.get("id", "")).strip()
            if not tid:
                print(f"[!] Saltando tenant sin id: {raw.get('name')}")
                continue

            tenant = s.get(db_module.Tenant, tid)
            is_new = tenant is None
            if is_new:
                tenant = db_module.Tenant(id=tid)
                s.add(tenant)

            tenant.name = raw.get("name", tid)
            tenant.sector = raw.get("sector", "")
            tenant.status = raw.get("status", "active")
            tenant.plan = raw.get("plan", "Básico")
            tenant.phone_number_id = str(raw.get("phone_number_id") or "")
            tenant.phone_display = raw.get("phone_display", "")
            tenant.calendar_id = raw.get("calendar_id") or "primary"
            tenant.timezone = raw.get("timezone", "Europe/Madrid")
            tenant.language = raw.get("language", "Español")
            tenant.contact_name = raw.get("contact_name", "")
            tenant.contact_email = raw.get("contact_email", "")
            tenant.business_hours = _infer_business_hours(raw.get("business_hours"))

            a = _extract_assistant(raw)
            tenant.assistant_name = a["name"]
            tenant.assistant_tone = a["tone"]
            tenant.assistant_formality = a["formality"]
            tenant.assistant_emoji = a["emoji"]
            tenant.assistant_greeting = a["greeting"]
            tenant.assistant_fallback_phone = a["fallback_phone"]
            tenant.assistant_rules = a["rules"]

            # Si el YAML trae system_prompt completo, lo guardamos como override
            # (el bot antiguo ya funcionaba así).
            if raw.get("system_prompt"):
                tenant.system_prompt_override = raw["system_prompt"].strip()

            # Reemplazar servicios
            tenant.services.clear()
            for idx, sv in enumerate(raw.get("services") or []):
                tenant.services.append(db_module.Service(
                    nombre=sv.get("nombre", f"Servicio {idx+1}"),
                    duracion_min=int(sv.get("duracion_min", 30)),
                    precio=float(sv.get("precio", 0) or 0),
                    orden=idx,
                ))

            if is_new:
                inserted += 1
            else:
                updated += 1

        s.commit()

    print(f"[ok] Tenants insertados: {inserted}  actualizados: {updated}")


if __name__ == "__main__":
    yaml_file = sys.argv[1] if len(sys.argv) > 1 else settings.tenants_file
    migrate(yaml_file)
