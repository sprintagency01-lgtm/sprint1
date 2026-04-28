"""Sincronización CMS → Google Sheets (unidireccional, tiempo real).

Idea: cada cambio que el CMS commitea en la tabla `tenants` (o en sus tablas
relacionadas `services` / `equipo`) dispara un push a una pestaña "Tenants" de
un Google Sheet. El Sheet es vista de solo lectura del estado actual.

Mecanismo
─────────
Hookeamos los eventos de SQLAlchemy `before_flush` (para capturar IDs antes de
que la sesión los limpie) y `after_commit` (para hacer el push solo si la
transacción cuajó). Los pushes se ejecutan en un ThreadPoolExecutor para no
bloquear la respuesta HTTP del CMS — si Google está lento, el cliente del CMS
no debería enterarse.

El sync NUNCA debe romper una request del CMS: cualquier excepción se loggea
y se traga.

Configuración (env vars en Railway)
───────────────────────────────────
- GOOGLE_SHEETS_ID            ID del spreadsheet (la parte larga del URL).
- GOOGLE_SERVICE_ACCOUNT_JSON JSON del Service Account, como string. El email
                              del SA debe tener permisos de Editor sobre el
                              Sheet.

Si cualquiera de las dos falta, el sync se queda en no-op silencioso (logs a
INFO). Útil en local para no exigir credenciales solo para correr la app.

Setup paso a paso: ver SHEETS_SYNC_SETUP.md.
"""
from __future__ import annotations

import json
import logging
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

from sqlalchemy import event
from sqlalchemy.orm import Session

log = logging.getLogger(__name__)


# ─── columnas del Sheet (cabecera fija) ──────────────────────────────────
HEADERS = [
    "id",
    "name",
    "sector",
    "status",
    "kind",
    "plan",
    "phone_display",
    "calendar_id",
    "timezone",
    "contact_name",
    "contact_email",
    "assistant_name",
    "assistant_tone",
    "assistant_fallback_phone",
    "n_servicios",
    "n_equipo",
    "voice_agent_id",
    "voice_last_sync_at",
    "voice_last_sync_status",
    "created_at",
    "updated_at",
]


# ─── estado interno (cliente cacheado, executor) ─────────────────────────
_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="sheets-sync")
_client_lock = threading.Lock()
_worksheet_cache = None  # gspread.Worksheet | None
_disabled_logged = False


def _is_configured() -> bool:
    return bool(os.getenv("GOOGLE_SHEETS_ID")) and bool(
        os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")
    )


def _col_letter(n: int) -> str:
    """1 → 'A', 26 → 'Z', 27 → 'AA'."""
    s = ""
    while n > 0:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


def _get_worksheet():
    """Lazy + cached. Devuelve la worksheet "Tenants" o None si no configurado."""
    global _worksheet_cache, _disabled_logged

    if _worksheet_cache is not None:
        return _worksheet_cache

    with _client_lock:
        if _worksheet_cache is not None:
            return _worksheet_cache

        if not _is_configured():
            if not _disabled_logged:
                log.info(
                    "Sheets sync deshabilitado (faltan GOOGLE_SHEETS_ID y/o "
                    "GOOGLE_SERVICE_ACCOUNT_JSON)"
                )
                _disabled_logged = True
            return None

        try:
            import gspread
            from google.oauth2.service_account import Credentials
        except ImportError:
            log.warning("gspread o google-auth no instalados; sync deshabilitado")
            return None

        try:
            info = json.loads(os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"])
        except json.JSONDecodeError:
            log.error("GOOGLE_SERVICE_ACCOUNT_JSON no es JSON válido")
            return None

        try:
            creds = Credentials.from_service_account_info(
                info,
                scopes=["https://www.googleapis.com/auth/spreadsheets"],
            )
            client = gspread.authorize(creds)
            spreadsheet = client.open_by_key(os.environ["GOOGLE_SHEETS_ID"])
        except Exception as exc:
            log.error("No se pudo abrir el Sheet: %s", exc)
            return None

        # Pestaña "Tenants": crearla si no existe.
        try:
            ws = spreadsheet.worksheet("Tenants")
        except Exception:
            ws = spreadsheet.add_worksheet(
                title="Tenants", rows=200, cols=len(HEADERS) + 2
            )

        # Cabecera correcta — escribirla si está vacía o desactualizada.
        try:
            first_row = ws.row_values(1)
        except Exception:
            first_row = []
        if first_row != HEADERS:
            try:
                ws.update(
                    range_name=f"A1:{_col_letter(len(HEADERS))}1",
                    values=[HEADERS],
                )
            except Exception:
                log.exception("No se pudo escribir cabecera en el Sheet")

        _worksheet_cache = ws
        return ws


# ─── conversión Tenant → fila ────────────────────────────────────────────

def _tenant_to_row(d: dict, updated_at: str = "") -> list:
    a = d.get("assistant") or {}
    v = d.get("voice") or {}
    return [
        d.get("id", "") or "",
        d.get("name", "") or "",
        d.get("sector", "") or "",
        d.get("status", "") or "",
        d.get("kind", "") or "",
        d.get("plan", "") or "",
        d.get("phone_display", "") or "",
        d.get("calendar_id", "") or "",
        d.get("timezone", "") or "",
        d.get("contact_name", "") or "",
        d.get("contact_email", "") or "",
        a.get("name", "") or "",
        a.get("tone", "") or "",
        a.get("fallback_phone", "") or "",
        len(d.get("services") or []),
        len(d.get("equipo") or []),
        v.get("agent_id", "") or "",
        v.get("last_sync_at", "") or "",
        v.get("last_sync_status", "") or "",
        d.get("created_at", "") or "",
        updated_at or "",
    ]


# ─── operaciones bloqueantes (corren en threadpool) ──────────────────────

def _upsert_row(tenant_id: str, row: list) -> None:
    ws = _get_worksheet()
    if ws is None:
        return
    try:
        ids = ws.col_values(1)
        if tenant_id in ids[1:]:  # skip cabecera
            row_idx = ids.index(tenant_id, 1) + 1  # 1-indexed
            ws.update(
                range_name=f"A{row_idx}:{_col_letter(len(HEADERS))}{row_idx}",
                values=[row],
            )
        else:
            ws.append_row(row)
    except Exception:
        log.exception("Error sincronizando tenant %s al Sheet", tenant_id)


def _delete_row(tenant_id: str) -> None:
    ws = _get_worksheet()
    if ws is None:
        return
    try:
        ids = ws.col_values(1)
        if tenant_id in ids[1:]:
            row_idx = ids.index(tenant_id, 1) + 1
            ws.delete_rows(row_idx)
    except Exception:
        log.exception("Error borrando tenant %s del Sheet", tenant_id)


def _push_blocking(tenant_id: str) -> None:
    """Lee el tenant de la BD y empuja al Sheet. Si no existe, lo borra del Sheet."""
    if not _is_configured():
        return
    try:
        # Import perezoso para evitar ciclos en el arranque.
        from . import db as dbmod

        with Session(dbmod.engine) as s:
            t = s.get(dbmod.Tenant, tenant_id)
            if t is None:
                _delete_row(tenant_id)
                return
            d = t.to_dict(include_system_prompt=False)
            updated = (
                t.updated_at.isoformat() if t.updated_at else ""
            )
            _upsert_row(tenant_id, _tenant_to_row(d, updated))
    except Exception:
        log.exception("Error en _push_blocking(%s)", tenant_id)


# ─── API pública ─────────────────────────────────────────────────────────

def push_tenant(tenant_id: Optional[str]) -> None:
    """Encola un push del tenant indicado. No bloquea."""
    if not tenant_id:
        return
    if not _is_configured():
        return
    _executor.submit(_push_blocking, tenant_id)


def delete_tenant(tenant_id: Optional[str]) -> None:
    if not tenant_id:
        return
    if not _is_configured():
        return
    _executor.submit(_delete_row, tenant_id)


def push_all_tenants() -> int:
    """Vuelca todos los tenants al Sheet de forma síncrona. Devuelve el count.

    Útil para un endpoint de sync manual o un test de humo desde shell.
    """
    if not _is_configured():
        return 0
    from . import db as dbmod

    n = 0
    with Session(dbmod.engine) as s:
        tenants = s.query(dbmod.Tenant).all()
        for t in tenants:
            d = t.to_dict(include_system_prompt=False)
            updated = t.updated_at.isoformat() if t.updated_at else ""
            _upsert_row(t.id, _tenant_to_row(d, updated))
            n += 1
    log.info("Sheets sync: full push (%d tenants)", n)
    return n


# ─── registro de listeners SQLAlchemy ────────────────────────────────────

def register_listeners() -> None:
    """Registra los hooks que detectan cambios en Tenant/Service/MiembroEquipo
    y disparan push al Sheet tras commit. Llamar UNA vez al arranque.
    """
    from . import db as dbmod

    @event.listens_for(Session, "before_flush")
    def _before_flush(session, flush_context, instances):
        # Capturamos IDs ANTES de que la sesión limpie session.new/dirty/deleted.
        bag_dirty: set[str] = session.info.setdefault("_sheets_dirty", set())
        bag_deleted: set[str] = session.info.setdefault("_sheets_deleted", set())

        for obj in list(session.new) + list(session.dirty):
            if isinstance(obj, dbmod.Tenant):
                bag_dirty.add(obj.id)
            else:
                # Service y MiembroEquipo afectan a counts en el Sheet.
                tid = getattr(obj, "tenant_id", None)
                if tid:
                    bag_dirty.add(tid)

        for obj in session.deleted:
            if isinstance(obj, dbmod.Tenant):
                bag_deleted.add(obj.id)
                bag_dirty.discard(obj.id)
            else:
                tid = getattr(obj, "tenant_id", None)
                if tid:
                    bag_dirty.add(tid)

    @event.listens_for(Session, "after_commit")
    def _after_commit(session):
        bag_dirty = session.info.pop("_sheets_dirty", set())
        bag_deleted = session.info.pop("_sheets_deleted", set())
        for tid in bag_dirty:
            push_tenant(tid)
        for tid in bag_deleted:
            delete_tenant(tid)

    @event.listens_for(Session, "after_rollback")
    def _after_rollback(session):
        session.info.pop("_sheets_dirty", None)
        session.info.pop("_sheets_deleted", None)

    log.info("Sheets sync: listeners SQLAlchemy registrados")
