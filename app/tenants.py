"""Carga de tenants.

Lee de la BD (tabla `tenants`). Si la tabla está vacía, cae al YAML antiguo
para no romper el bot en instalaciones existentes. Devuelve siempre dicts en
el formato que espera `agent.py`.

Incluye un caché in-memory (TTL corto) para el hot path de voz: cada tool call
de ElevenLabs resolvía el tenant leyendo BD + YAML + renderizando el prompt
de todos los tenants. Con el caché, el coste amortizado baja a ~0 ms en
llamadas consecutivas dentro de la misma ventana de TTL.
"""
from __future__ import annotations

import pathlib
import time
from typing import Any

import yaml
from sqlalchemy.orm import Session

from .config import settings
from . import db as db_module

# Caché por tenant_id. Clave "__all__" = load_tenants() completo; clave
# "__first__" = get_first(); resto = get_tenant(tid).
#
# TTL pequeño (30s) para limitar latencia entre "edito en el CMS" y "se
# refleja en el bot". El CMS, tras guardar, debe llamar a
# `invalidate_tenant_cache(tid)` para propagar cambios al instante.
_TENANT_CACHE: dict[str, tuple[float, Any]] = {}
_TENANT_CACHE_TTL = 30.0


def invalidate_tenant_cache(tenant_id: str | None = None) -> None:
    """Invalida el caché.

    - `tenant_id=None` → borra TODO el caché (incluido `__all__`/`__first__`).
    - `tenant_id="foo"` → borra solo ese tenant + los alias de lista (que
      podrían haberlo incluido).
    """
    if tenant_id is None:
        _TENANT_CACHE.clear()
        return
    # Borrar todas las variantes del tenant (con y sin system_prompt) y los
    # alias de lista.
    for key in [k for k in _TENANT_CACHE if k.startswith(f"{tenant_id}::")]:
        _TENANT_CACHE.pop(key, None)
    _TENANT_CACHE.pop(tenant_id, None)  # compat con invalidaciones antiguas
    _TENANT_CACHE.pop("__all__", None)
    _TENANT_CACHE.pop("__first__", None)


# --- Invalidación automática on-commit -------------------------------------
# Escuchamos commits de SQLAlchemy y si afectaron a Tenant/Service/
# MiembroEquipo, tiramos el caché. Así evitamos salpicar `invalidate_*`
# por cada ruta del CMS. Es selectivo: saves de Message/TokenUsage/Lead no
# invalidan (son el 99% de commits en el hot path texto) para no destrozar
# el ratio de cache hit.
def _register_cache_invalidation_listener() -> None:
    from sqlalchemy import event
    from sqlalchemy.orm import Session as _Session

    def _before_commit(session):  # type: ignore[no-redef]
        try:
            affected: set[str | None] = set()
            for inst in list(session.new) + list(session.dirty) + list(session.deleted):
                cls_name = type(inst).__name__
                if cls_name == "Tenant":
                    affected.add(getattr(inst, "id", None))
                elif cls_name in ("Service", "MiembroEquipo"):
                    affected.add(getattr(inst, "tenant_id", None))
            if affected:
                session.info["_tenants_to_invalidate"] = affected
        except Exception:
            # No rompemos el commit por un bug del listener.
            pass

    def _after_commit(session):  # type: ignore[no-redef]
        ids = session.info.pop("_tenants_to_invalidate", None)
        if not ids:
            return
        for tid in ids:
            if tid:
                invalidate_tenant_cache(tid)
            else:
                invalidate_tenant_cache(None)
                return

    event.listen(_Session, "before_commit", _before_commit)
    event.listen(_Session, "after_commit", _after_commit)


_register_cache_invalidation_listener()


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

    Histórico: se usaba para mergear peluqueros sobre los tenants de la BD.
    Desde que la tabla `peluqueros` existe, esto ya no se usa por defecto
    (`_YAML_ONLY_FIELDS` está vacío) pero se deja como fallback por si algún
    día vuelve a hacer falta. Si falla la lectura, devolvemos vacío para no
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


# Campos que sólo existen en el YAML y se mergean sobre los tenants de la BD
# cuando comparten id. Los peluqueros vivían aquí hasta abril 2026; ahora están
# en la tabla `peluqueros` de la BD (editable desde el CMS). El mecanismo se
# mantiene por si vuelve a hacer falta para otros campos operativos.
_YAML_ONLY_FIELDS: tuple[str, ...] = ()


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

    Usa el caché si hay entrada viva bajo la clave `__all__`.
    """
    now = time.monotonic()
    hit = _TENANT_CACHE.get("__all__")
    if hit and (now - hit[0]) < _TENANT_CACHE_TTL:
        return hit[1]

    yaml_by_id = _load_yaml_by_id()

    # 1) Intenta la BD (enriquecida con YAML)
    with Session(db_module.engine) as session:
        rows = session.query(db_module.Tenant).all()
        if rows:
            result = []
            for t in rows:
                # Hot path de voz no usa `system_prompt` (usa voice.prompt).
                # Omitimos el render para ahorrar ~1-3ms por tenant.
                td = t.to_dict(include_system_prompt=False)
                yt = yaml_by_id.get(td.get("id"))
                if yt:
                    _merge_yaml_into_db(td, yt)
                result.append(td)
            _TENANT_CACHE["__all__"] = (now, result)
            return result

    # 2) Fallback al YAML si la tabla está vacía
    if yaml_by_id:
        result = list(yaml_by_id.values())
        _TENANT_CACHE["__all__"] = (now, result)
        return result

    # 3) Último recurso: tenant demo
    return [_DEFAULT_TENANT_TEMPLATE]


def get_tenant(tenant_id: str, *, include_system_prompt: bool = True) -> dict[str, Any] | None:
    """Busca un tenant por id. Devuelve dict o None (enriquecido con YAML).

    `include_system_prompt=False` evita renderizar el prompt de texto (canal
    voz no lo usa). Un tenant cacheado con `system_prompt` se devuelve igual;
    para garantizar la versión ligera desde caché, llamadores del hot path
    pasan `include_system_prompt=False` y obtienen un dict sin esa clave.
    """
    now = time.monotonic()
    cache_key = f"{tenant_id}::sp={int(include_system_prompt)}"
    hit = _TENANT_CACHE.get(cache_key)
    if hit and (now - hit[0]) < _TENANT_CACHE_TTL:
        return hit[1]

    yaml_by_id = _load_yaml_by_id()
    with Session(db_module.engine) as session:
        t = session.get(db_module.Tenant, tenant_id)
        if t is not None:
            td = t.to_dict(include_system_prompt=include_system_prompt)
            yt = yaml_by_id.get(tenant_id)
            if yt:
                _merge_yaml_into_db(td, yt)
            _TENANT_CACHE[cache_key] = (now, td)
            return td
    for t in load_tenants():
        if t.get("id") == tenant_id:
            _TENANT_CACHE[cache_key] = (now, t)
            return t
    return None
