"""Rutas del portal del cliente.

- /app             → SPA (HTML)
- /app/login       → Formulario
- /app/logout      → POST
- /app/static/*    → Ficheros JSX/CSS del SPA
- /api/portal/*    → JSON endpoints que consume el SPA

Todos los /api/portal/* están protegidos por sesión del portal (cookie
`reservabot_portal`). La sesión lleva además el `tenant_id` para que
cualquier consulta/mutación esté acotada al tenant del usuario.
"""
from __future__ import annotations

import json
import logging
import pathlib
from datetime import date, datetime, time, timedelta
from time import time as unix_time
from typing import Any

from fastapi import (
    APIRouter, Depends, Form, HTTPException, Request, Response, status,
)
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from sqlalchemy.orm import Session

from .. import calendar_service, db as db_module
from ..config import settings  # noqa: F401  (futuro: flags de entorno)
from . import auth as portal_auth

log = logging.getLogger(__name__)

_BASE = pathlib.Path(__file__).parent
_TEMPLATES = Jinja2Templates(directory=str(_BASE / "templates"))

router = APIRouter()

# Ficheros estáticos (data.jsx, shell.jsx, screen_*.jsx)
router_mounts: list[tuple[str, Any]] = [
    ("/app/static", StaticFiles(directory=str(_BASE / "static"))),
]


# ===========================================================================
#  PWA — service worker y manifest
# ===========================================================================
#
# El SW se sirve desde la raíz del scope (/app/sw.js, no /app/static/sw.js)
# para que el scope por defecto sea /app/ y pueda interceptar todas las
# navegaciones del portal. Cache-Control: no-cache para que un cambio en
# sw.js se propague rápido — el navegador siempre revalida.

@router.get("/app/sw.js", include_in_schema=False)
async def portal_service_worker():
    return FileResponse(
        _BASE / "static" / "sw.js",
        media_type="application/javascript",
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Service-Worker-Allowed": "/app/",
        },
    )


@router.get("/app/manifest.webmanifest", include_in_schema=False)
async def portal_manifest():
    """Alias del manifest en la raíz del scope (algunos navegadores lo
    prefieren así; el HTML linka a /app/static/manifest.json igualmente)."""
    return FileResponse(
        _BASE / "static" / "manifest.json",
        media_type="application/manifest+json",
    )


# ===========================================================================
#  HTML — login, portal, logout
# ===========================================================================

@router.get("/app/login", response_class=HTMLResponse)
async def portal_login_get(request: Request):
    # Si ya tiene sesión válida, pasa directamente al portal.
    token = request.cookies.get(portal_auth.COOKIE_NAME)
    if token and portal_auth.read_session(token):
        return RedirectResponse("/app", status_code=302)
    return _TEMPLATES.TemplateResponse(
        "login.html",
        {"request": request, "error": None, "email": ""},
    )


@router.post("/app/login", response_class=HTMLResponse)
async def portal_login_post(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
):
    result = portal_auth.verify_credentials(email, password)
    if result is None:
        return _TEMPLATES.TemplateResponse(
            "login.html",
            {"request": request, "error": "Email o contraseña incorrectos.", "email": email},
            status_code=401,
        )
    uid, tid = result
    resp = RedirectResponse("/app", status_code=302)
    resp.set_cookie(
        key=portal_auth.COOKIE_NAME,
        value=portal_auth.sign_session(uid, tid),
        max_age=portal_auth.SESSION_TTL_SECONDS,
        httponly=True,
        samesite="lax",
        path="/",
    )
    return resp


@router.post("/app/logout")
async def portal_logout():
    resp = RedirectResponse("/app/login", status_code=302)
    resp.delete_cookie(portal_auth.COOKIE_NAME, path="/")
    return resp


@router.get("/app", response_class=HTMLResponse)
async def portal_index(request: Request, session=Depends(portal_auth.current_session)):
    uid, tid = session
    data = _build_initial_data(uid, tid)
    negocio_nombre = (data.get("negocio") or {}).get("nombre") or "Panel"
    return _TEMPLATES.TemplateResponse(
        "portal.html",
        {
            "request": request,
            "negocio_nombre": negocio_nombre,
            "asset_version": str(int(unix_time())),
            # json.dumps con default=str para datetimes; el SPA sólo espera
            # strings ISO en fechas.
            "portal_data_json": json.dumps(data, ensure_ascii=False, default=str),
        },
    )


# ===========================================================================
#  API — helpers comunes
# ===========================================================================

def _tenant_or_404(tid: str) -> db_module.Tenant:
    with Session(db_module.engine) as s:
        t = s.get(db_module.Tenant, tid)
    if t is None:
        raise HTTPException(status_code=404, detail="tenant no encontrado")
    return t


def _current_tenant(session=Depends(portal_auth.current_api_session)) -> db_module.Tenant:
    _uid, tid = session
    return _tenant_or_404(tid)


def _current_user(session=Depends(portal_auth.current_api_session)) -> db_module.TenantUser:
    uid, _tid = session
    u = portal_auth.get_user(uid)
    if u is None:
        raise HTTPException(status_code=401, detail="sesión inválida")
    return u


def _member_id_str(mid: int | str | None) -> str:
    return str(mid) if mid is not None else ""


def _service_id_str(sid: int | str | None) -> str:
    return str(sid) if sid is not None else ""


# ===========================================================================
#  DATA — payload inicial del SPA
# ===========================================================================

def _build_initial_data(user_id: int, tenant_id: str) -> dict[str, Any]:
    """Construye el bloque __PORTAL_DATA__ que se inyecta en portal.html."""
    tenant = _tenant_or_404(tenant_id)
    user = portal_auth.get_user(user_id)

    negocio = {
        "id": tenant.id,
        "nombre": tenant.name,
        "sector": tenant.sector,
        "direccion": "",  # TODO: tenant no tiene dirección aún
        "tz": tenant.timezone or "Europe/Madrid",
        "telefono": tenant.phone_display,
    }

    with Session(db_module.engine) as s:
        miembros = (
            s.query(db_module.MiembroEquipo)
            .filter(db_module.MiembroEquipo.tenant_id == tenant_id)
            .order_by(db_module.MiembroEquipo.orden.asc(), db_module.MiembroEquipo.id.asc())
            .all()
        )
        equipo = [
            {
                "id": str(m.id),
                "nombre": m.nombre,
                "color": m.color or "#059669",
                "dias": m.dias_trabajo,
                "turnos": m.turnos,
                "vacaciones": m.vacaciones,
                "calendar_id": m.calendar_id or "",
            }
            for m in miembros
        ]

        servicios_rows = (
            s.query(db_module.Service)
            .filter(db_module.Service.tenant_id == tenant_id)
            .order_by(db_module.Service.orden.asc(), db_module.Service.id.asc())
            .all()
        )
        servicios = [
            {
                "id": str(sv.id),
                "nombre": sv.nombre,
                "duracion": sv.duracion_min,
                "precio": sv.precio,
                "activo": bool(sv.activo),
                # equipo_ids se guarda como int; la UI compara con strings, así que
                # los pasamos a str para que coincidan con m.id arriba.
                "equipo": [str(x) for x in sv.equipo_ids],
            }
            for sv in servicios_rows
        ]

    hoy = date.today().isoformat()
    reservas = _load_reservas_for_window(tenant, dias=7)
    ingresos_30d = _compute_ingresos_30d(tenant, servicios)
    llamadas = _load_llamadas(tenant_id, reservas=reservas)

    return {
        "user": {
            "id": user.id if user else None,
            "nombre": (user.nombre if user else "") or (user.email if user else ""),
            "email": user.email if user else "",
            "role": user.role if user else "owner",
        },
        "bot": {
            "voz": tenant.status == "active",
        },
        "negocio": negocio,
        "equipo": equipo,
        "servicios": servicios,
        "reservas": reservas,
        "hoy_iso": hoy,
        "ingresos_30d": ingresos_30d,
        "llamadas": llamadas,
    }


# ---------- Reservas (desde Google Calendar) --------------------------------

def _parse_event_dt(ev: dict, key: str) -> datetime | None:
    v = (ev.get(key) or {}).get("dateTime") or (ev.get(key) or {}).get("date")
    if not v:
        return None
    try:
        # Maneja "YYYY-MM-DDTHH:MM:SS±TZ" y "YYYY-MM-DD".
        if "T" in v:
            return datetime.fromisoformat(v.replace("Z", "+00:00"))
        return datetime.fromisoformat(v + "T00:00:00")
    except ValueError:
        return None


def _event_to_reserva(ev: dict, services_by_id: dict[str, dict], miembros_by_name: dict[str, str]) -> dict | None:
    start = _parse_event_dt(ev, "start")
    end = _parse_event_dt(ev, "end")
    if not start or not end:
        return None

    priv = (ev.get("extendedProperties") or {}).get("private") or {}
    phone = priv.get("phone") or ""
    cliente = priv.get("client_name") or ""
    service_id = priv.get("service_id") or ""
    member_id = priv.get("member_id") or ""
    # Canal: `channel` nuevo o legacy `created_by`.
    created_by = (priv.get("channel") or priv.get("created_by") or "").lower()
    if created_by in ("voice", "voz"):
        canal = "voz"
    else:
        canal = "manual"

    if not cliente:
        # Extrae nombre del summary "Servicio — Nombre".
        summary = ev.get("summary") or ""
        if " — " in summary:
            cliente = summary.split(" — ", 1)[1].strip()
        else:
            cliente = summary.strip() or "(sin nombre)"

    # Si no hay service_id en extendedProperties, intentamos matchear el summary
    # contra el catálogo del tenant.
    if not service_id:
        summary_lower = (ev.get("summary") or "").lower()
        for sid, s in services_by_id.items():
            if s["nombre"].lower() in summary_lower:
                service_id = sid
                break

    # El member_id puede venir implícito vía el calendar_id del evento: si el
    # evento vive en el calendario secundario de un miembro, ése es.
    if not member_id and ev.get("organizer", {}).get("email") in miembros_by_name:
        member_id = miembros_by_name[ev["organizer"]["email"]]

    duracion = max(1, int((end - start).total_seconds() // 60))

    estado = "cancelada" if (ev.get("status") == "cancelled") else "confirmada"

    return {
        "id": ev.get("id") or "",
        "fecha": start.strftime("%Y-%m-%d"),
        "hora": start.strftime("%H:%M"),
        "duracion": duracion,
        "cliente": cliente,
        "telefono": phone,
        "servicio": str(service_id) if service_id else "",
        "equipo":   str(member_id) if member_id else "",
        "canal": canal,
        "estado": estado,
        "_calendar_id": ev.get("_calendar_id") or "",
    }


def _load_reservas_for_window(tenant: db_module.Tenant, *, dias: int = 7) -> list[dict]:
    """Lee eventos del calendario principal desde hoy-1 hasta hoy+dias.

    Si la llamada a Google falla (por ejemplo no hay credencial), devuelve
    lista vacía en vez de crashear el portal — la UI sigue siendo navegable.
    """
    try:
        svcs = _services_map(tenant.id)
        miembros_map = _members_by_calendar(tenant.id)
        today = datetime.now(calendar_service.TZ).date()
        desde = datetime.combine(today - timedelta(days=1), time(0, 0))
        hasta = datetime.combine(today + timedelta(days=dias), time(23, 59))
        events = calendar_service.listar_eventos(
            desde=desde, hasta=hasta,
            calendar_id=tenant.calendar_id,
            tenant_id=tenant.id,
        )
        out: list[dict] = []
        for ev in events:
            r = _event_to_reserva(ev, svcs, miembros_map)
            if r is not None:
                out.append(r)
        return out
    except Exception as exc:
        log.warning("listar_eventos falló para tenant=%s: %s", tenant.id, exc)
        return []


def _services_map(tenant_id: str) -> dict[str, dict]:
    with Session(db_module.engine) as s:
        rows = (
            s.query(db_module.Service)
            .filter(db_module.Service.tenant_id == tenant_id)
            .all()
        )
        return {str(sv.id): {"nombre": sv.nombre, "precio": sv.precio, "duracion": sv.duracion_min} for sv in rows}


def _members_by_calendar(tenant_id: str) -> dict[str, str]:
    with Session(db_module.engine) as s:
        rows = (
            s.query(db_module.MiembroEquipo)
            .filter(db_module.MiembroEquipo.tenant_id == tenant_id)
            .all()
        )
        return {m.calendar_id: str(m.id) for m in rows if m.calendar_id}


def _compute_ingresos_30d(tenant: db_module.Tenant, servicios: list[dict]) -> list[dict]:
    """Ingresos por día en los últimos 30 días, agrupados por canal.

    Se calculan desde el calendario principal del tenant: precio del servicio
    de cada reserva, bucketizado por canal (voz vs manual desde el portal/CMS).
    """
    try:
        today = datetime.now(calendar_service.TZ).date()
        desde = datetime.combine(today - timedelta(days=29), time(0, 0))
        hasta = datetime.combine(today, time(23, 59))
        events = calendar_service.listar_eventos(
            desde=desde, hasta=hasta,
            calendar_id=tenant.calendar_id, tenant_id=tenant.id,
        )
        precio_por_id = {s["id"]: s["precio"] for s in servicios}
        # Inicializa buckets
        buckets: dict[str, dict] = {}
        for i in range(30):
            d = (today - timedelta(days=29 - i)).isoformat()
            buckets[d] = {"d": i, "fecha": d, "voz": 0, "man": 0, "total": 0}
        for ev in events:
            if ev.get("status") == "cancelled":
                continue
            start = _parse_event_dt(ev, "start")
            if not start:
                continue
            d = start.date().isoformat()
            if d not in buckets:
                continue
            priv = (ev.get("extendedProperties") or {}).get("private") or {}
            sid = str(priv.get("service_id") or "")
            precio = precio_por_id.get(sid, 0) or 0
            ch = (priv.get("channel") or priv.get("created_by") or "").lower()
            if ch in ("voice", "voz"):
                buckets[d]["voz"] += precio
            else:
                buckets[d]["man"] += precio
            buckets[d]["total"] = buckets[d]["voz"] + buckets[d]["man"]
        return list(buckets.values())
    except Exception as exc:
        log.warning("ingresos_30d falló para tenant=%s: %s", tenant.id, exc)
        return []


def _conversation_channel(customer_phone: str) -> str:
    raw = (customer_phone or "").strip().lower()
    if raw.startswith("tg:"):
        return "telegram"
    return "voz"



def _display_phone(customer_phone: str) -> str:
    raw = (customer_phone or "").strip()
    if raw.lower().startswith("tg:"):
        return raw[3:]
    return raw



def _load_llamadas(tenant_id: str, reservas: list[dict] | None = None) -> list[dict]:
    """Agrupa los mensajes del tenant por conversación para el portal cliente.

    Aunque la pantalla histórica se llama `llamadas` en el portal, aquí ya
    devolvemos tanto voz como Telegram para reutilizar una sola vista de
    historial de conversaciones.
    """
    try:
        reservas = reservas or []
        phones_with_booking = {str((r.get("telefono") or "")).strip() for r in reservas if r.get("telefono")}
        with Session(db_module.engine) as s:
            rows = (
                s.query(db_module.Message)
                .filter(db_module.Message.tenant_id == tenant_id)
                .order_by(db_module.Message.created_at.desc())
                .limit(800)
                .all()
            )
        conv_by_phone: dict[str, list[db_module.Message]] = {}
        for m in rows:
            conv_by_phone.setdefault(m.customer_phone, []).append(m)
        llamadas: list[dict] = []
        for phone, msgs in conv_by_phone.items():
            msgs_sorted = sorted(msgs, key=lambda x: x.created_at)
            last = msgs_sorted[-1]
            preview = (last.content or "")[:160]
            channel = _conversation_channel(phone)
            display_phone = _display_phone(phone)
            has_booking = phone in phones_with_booking or display_phone in phones_with_booking
            llamadas.append({
                "id": f"c_{phone}",
                "telefono": phone,
                "display_phone": display_phone,
                "nombre": "—",
                "channel": channel,
                "ultimoAt": last.created_at.isoformat(timespec="minutes"),
                "reserva": has_booking,
                "preview": preview,
                "duracion": None,
                "tools": [],
                "turnos": [
                    {
                        "role": m.role,
                        "at": m.created_at.strftime("%H:%M"),
                        "text": m.content or "",
                    } for m in msgs_sorted
                ],
            })
        llamadas.sort(key=lambda x: x["ultimoAt"], reverse=True)
        return llamadas
    except Exception as exc:
        log.warning("llamadas falló para tenant=%s: %s", tenant_id, exc)
        return []


# ===========================================================================
#  API — Pydantic schemas
# ===========================================================================

class BotToggle(BaseModel):
    voz: bool | None = None


class ReservaCreate(BaseModel):
    fecha: str
    hora: str
    duracion: int | None = None
    cliente: str
    telefono: str
    servicio: str
    equipo: str | None = None
    canal: str = "manual"


class ReservaUpdate(BaseModel):
    fecha: str | None = None
    hora: str | None = None
    duracion: int | None = None
    cliente: str | None = None
    telefono: str | None = None
    servicio: str | None = None
    equipo: str | None = None


class ServicioCreate(BaseModel):
    nombre: str
    duracion: int = 30
    precio: float = 0.0
    equipo: list[str] = []
    activo: bool = True


class ServicioUpdate(BaseModel):
    nombre: str | None = None
    duracion: int | None = None
    precio: float | None = None
    equipo: list[str] | None = None
    activo: bool | None = None


class MiembroCreate(BaseModel):
    nombre: str
    color: str | None = None
    dias: list[int] | None = None
    turnos: list[list[str]] | None = None
    vacaciones: list[dict[str, str]] | None = None
    calendar_id: str | None = None


class MiembroUpdate(BaseModel):
    nombre: str | None = None
    color: str | None = None
    dias: list[int] | None = None
    turnos: list[list[str]] | None = None
    vacaciones: list[dict[str, str]] | None = None
    calendar_id: str | None = None


class NegocioUpdate(BaseModel):
    nombre: str | None = None
    sector: str | None = None
    telefono: str | None = None
    timezone: str | None = None


class IAParse(BaseModel):
    prompt: str


# ===========================================================================
#  API — /me, /bot
# ===========================================================================

@router.get("/api/portal/me")
async def api_me(
    u: db_module.TenantUser = Depends(_current_user),
    t: db_module.Tenant = Depends(_current_tenant),
):
    return {
        "user": {"id": u.id, "email": u.email, "nombre": u.nombre, "role": u.role},
        "tenant": {"id": t.id, "nombre": t.name, "status": t.status},
    }


@router.patch("/api/portal/bot")
async def api_bot_toggle(
    body: BotToggle,
    t: db_module.Tenant = Depends(_current_tenant),
):
    # Producto de voz único (ElevenLabs): `voz` mapea directamente al
    # `status` del tenant. Si en el futuro sumamos otro canal, volveremos
    # a un desglose por canal.
    with Session(db_module.engine) as s:
        tenant = s.get(db_module.Tenant, t.id)
        if tenant is None:
            raise HTTPException(404, "tenant no encontrado")
        if body.voz is not None:
            tenant.status = "active" if body.voz else "paused"
        s.commit()
        return {"ok": True, "status": tenant.status}


# ===========================================================================
#  API — Servicios
# ===========================================================================

@router.get("/api/portal/servicios")
async def api_servicios_list(t: db_module.Tenant = Depends(_current_tenant)):
    with Session(db_module.engine) as s:
        rows = (
            s.query(db_module.Service)
            .filter(db_module.Service.tenant_id == t.id)
            .order_by(db_module.Service.orden.asc(), db_module.Service.id.asc())
            .all()
        )
        return [
            {
                "id": str(sv.id),
                "nombre": sv.nombre,
                "duracion": sv.duracion_min,
                "precio": sv.precio,
                "activo": bool(sv.activo),
                "equipo": [str(x) for x in sv.equipo_ids],
            } for sv in rows
        ]


@router.post("/api/portal/servicios")
async def api_servicios_create(
    body: ServicioCreate,
    t: db_module.Tenant = Depends(_current_tenant),
):
    with Session(db_module.engine) as s:
        row = db_module.Service(
            tenant_id=t.id,
            nombre=body.nombre.strip(),
            duracion_min=int(body.duracion or 30),
            precio=float(body.precio or 0.0),
            activo=bool(body.activo),
        )
        row.equipo_ids = [int(x) for x in (body.equipo or []) if str(x).isdigit()]
        s.add(row)
        s.commit()
        s.refresh(row)
        return {"ok": True, "id": str(row.id)}


@router.patch("/api/portal/servicios/{sid}")
async def api_servicios_update(
    sid: int,
    body: ServicioUpdate,
    t: db_module.Tenant = Depends(_current_tenant),
):
    with Session(db_module.engine) as s:
        row = s.get(db_module.Service, sid)
        if row is None or row.tenant_id != t.id:
            raise HTTPException(404, "servicio no encontrado")
        if body.nombre is not None:   row.nombre = body.nombre.strip()
        if body.duracion is not None: row.duracion_min = int(body.duracion)
        if body.precio is not None:   row.precio = float(body.precio)
        if body.activo is not None:   row.activo = bool(body.activo)
        if body.equipo is not None:   row.equipo_ids = [int(x) for x in body.equipo if str(x).isdigit()]
        s.commit()
        return {"ok": True}


@router.delete("/api/portal/servicios/{sid}")
async def api_servicios_delete(
    sid: int,
    t: db_module.Tenant = Depends(_current_tenant),
):
    with Session(db_module.engine) as s:
        row = s.get(db_module.Service, sid)
        if row is None or row.tenant_id != t.id:
            raise HTTPException(404, "servicio no encontrado")
        s.delete(row)
        s.commit()
        return {"ok": True}


# ===========================================================================
#  API — Equipo
# ===========================================================================

@router.get("/api/portal/equipo")
async def api_equipo_list(t: db_module.Tenant = Depends(_current_tenant)):
    with Session(db_module.engine) as s:
        rows = (
            s.query(db_module.MiembroEquipo)
            .filter(db_module.MiembroEquipo.tenant_id == t.id)
            .order_by(db_module.MiembroEquipo.orden.asc(), db_module.MiembroEquipo.id.asc())
            .all()
        )
        return [
            {
                "id": str(m.id),
                "nombre": m.nombre,
                "color": m.color,
                "dias": m.dias_trabajo,
                "turnos": m.turnos,
                "vacaciones": m.vacaciones,
                "calendar_id": m.calendar_id or "",
            } for m in rows
        ]


@router.post("/api/portal/equipo")
async def api_equipo_create(
    body: MiembroCreate,
    t: db_module.Tenant = Depends(_current_tenant),
):
    with Session(db_module.engine) as s:
        row = db_module.MiembroEquipo(
            tenant_id=t.id,
            nombre=body.nombre.strip(),
            color=(body.color or "#059669"),
            calendar_id=(body.calendar_id or ""),
        )
        if body.dias is not None: row.dias_trabajo = body.dias
        if body.turnos is not None: row.turnos = body.turnos
        if body.vacaciones is not None: row.vacaciones = body.vacaciones
        s.add(row)
        s.commit()
        s.refresh(row)
        return {"ok": True, "id": str(row.id)}


@router.patch("/api/portal/equipo/{mid}")
async def api_equipo_update(
    mid: int,
    body: MiembroUpdate,
    t: db_module.Tenant = Depends(_current_tenant),
):
    with Session(db_module.engine) as s:
        row = s.get(db_module.MiembroEquipo, mid)
        if row is None or row.tenant_id != t.id:
            raise HTTPException(404, "miembro no encontrado")
        if body.nombre is not None: row.nombre = body.nombre.strip()
        if body.color is not None:  row.color = body.color
        if body.calendar_id is not None: row.calendar_id = body.calendar_id
        if body.dias is not None:   row.dias_trabajo = body.dias
        if body.turnos is not None: row.turnos = body.turnos
        if body.vacaciones is not None: row.vacaciones = body.vacaciones
        s.commit()
        return {"ok": True}


@router.delete("/api/portal/equipo/{mid}")
async def api_equipo_delete(
    mid: int,
    t: db_module.Tenant = Depends(_current_tenant),
):
    with Session(db_module.engine) as s:
        row = s.get(db_module.MiembroEquipo, mid)
        if row is None or row.tenant_id != t.id:
            raise HTTPException(404, "miembro no encontrado")
        s.delete(row)
        s.commit()
        return {"ok": True}


# ===========================================================================
#  API — Reservas (Google Calendar)
# ===========================================================================

@router.get("/api/portal/reservas")
async def api_reservas_list(
    t: db_module.Tenant = Depends(_current_tenant),
    dias: int = 14,
):
    return _load_reservas_for_window(t, dias=dias)


@router.post("/api/portal/reservas")
async def api_reservas_create(
    body: ReservaCreate,
    t: db_module.Tenant = Depends(_current_tenant),
):
    # Resuelve el servicio para duración/nombre por defecto
    with Session(db_module.engine) as s:
        svc_row = None
        if body.servicio and body.servicio.isdigit():
            svc_row = s.get(db_module.Service, int(body.servicio))
            if svc_row and svc_row.tenant_id != t.id:
                svc_row = None
        mid = None
        if body.equipo and body.equipo.isdigit():
            m = s.get(db_module.MiembroEquipo, int(body.equipo))
            if m and m.tenant_id == t.id:
                mid = m.id
    titulo = svc_row.nombre if svc_row else "Reserva"
    duracion = body.duracion or (svc_row.duracion_min if svc_row else 30)

    try:
        inicio = datetime.fromisoformat(f"{body.fecha}T{body.hora}:00")
    except ValueError:
        raise HTTPException(400, "fecha/hora inválida")
    fin = inicio + timedelta(minutes=int(duracion))

    try:
        ev = calendar_service.crear_evento(
            titulo=titulo,
            inicio=inicio, fin=fin,
            telefono_cliente=body.telefono,
            nombre_cliente=body.cliente,
            calendar_id=t.calendar_id,
            tenant_id=t.id,
            service_id=(svc_row.id if svc_row else None),
            member_id=mid,
            channel=body.canal or "manual",
        )
        return {"ok": True, "id": ev.get("id")}
    except Exception as exc:
        log.exception("crear_evento falló (portal)")
        raise HTTPException(500, f"no se pudo crear la reserva: {exc}")


@router.patch("/api/portal/reservas/{event_id}")
async def api_reservas_update(
    event_id: str,
    body: ReservaUpdate,
    t: db_module.Tenant = Depends(_current_tenant),
):
    if body.fecha and body.hora:
        try:
            inicio = datetime.fromisoformat(f"{body.fecha}T{body.hora}:00")
        except ValueError:
            raise HTTPException(400, "fecha/hora inválida")
        dur = body.duracion or 30
        fin = inicio + timedelta(minutes=int(dur))
        try:
            calendar_service.mover_evento(
                event_id=event_id,
                nuevo_inicio=inicio, nuevo_fin=fin,
                calendar_id=t.calendar_id, tenant_id=t.id,
            )
        except Exception as exc:
            log.exception("mover_evento falló (portal)")
            raise HTTPException(500, f"no se pudo mover la reserva: {exc}")
    return {"ok": True}


@router.delete("/api/portal/reservas/{event_id}")
async def api_reservas_delete(
    event_id: str,
    t: db_module.Tenant = Depends(_current_tenant),
):
    try:
        calendar_service.cancelar_evento(
            event_id=event_id,
            calendar_id=t.calendar_id, tenant_id=t.id,
        )
        return {"ok": True}
    except Exception as exc:
        log.exception("cancelar_evento falló (portal)")
        raise HTTPException(500, f"no se pudo cancelar: {exc}")


# ===========================================================================
#  API — IA parse (OpenAI) para dar de alta reservas por voz/texto
# ===========================================================================

_IA_SYSTEM_PROMPT = (
    "Eres un extractor de datos para reservar en una peluquería. "
    "Dado un texto libre en español (voz transcrita o texto escrito), "
    "devuelve SÓLO un JSON válido sin markdown, sin comentarios, con estas "
    "claves (todas opcionales): cliente (string), telefono (string), "
    "servicio_nombre (string), equipo_nombre (string), fecha (YYYY-MM-DD), "
    "hora (HH:MM). Si la fecha es relativa ('mañana', 'el viernes'), "
    "calcúlala respecto a hoy."
)


@router.post("/api/portal/reservas/ia_parse")
async def api_reservas_ia_parse(
    body: IAParse,
    t: db_module.Tenant = Depends(_current_tenant),
):
    prompt = (body.prompt or "").strip()
    if not prompt:
        return {}
    try:
        from openai import OpenAI
        client = OpenAI(api_key=settings.openai_api_key)
        hoy = date.today().isoformat()
        resp = client.chat.completions.create(
            model=settings.openai_model or "gpt-4o-mini",
            messages=[
                {"role": "system", "content": _IA_SYSTEM_PROMPT + f" Hoy es {hoy}."},
                {"role": "user", "content": prompt},
            ],
            response_format={"type": "json_object"},
            temperature=0,
        )
        content = resp.choices[0].message.content or "{}"
        # Devolvemos el string tal cual (el cliente hace JSON.parse)
        return Response(content=content, media_type="application/json")
    except Exception as exc:
        log.exception("ia_parse falló")
        raise HTTPException(500, f"IA parse error: {exc}")


# ===========================================================================
#  API — Negocio / ajustes básicos
# ===========================================================================

@router.get("/api/portal/negocio")
async def api_negocio_get(t: db_module.Tenant = Depends(_current_tenant)):
    return {
        "id": t.id,
        "nombre": t.name,
        "sector": t.sector,
        "telefono": t.phone_display,
        "timezone": t.timezone,
    }


@router.patch("/api/portal/negocio")
async def api_negocio_update(
    body: NegocioUpdate,
    t: db_module.Tenant = Depends(_current_tenant),
):
    with Session(db_module.engine) as s:
        row = s.get(db_module.Tenant, t.id)
        if row is None:
            raise HTTPException(404, "tenant no encontrado")
        if body.nombre is not None:    row.name = body.nombre.strip()
        if body.sector is not None:    row.sector = body.sector.strip()
        if body.telefono is not None:  row.phone_display = body.telefono.strip()
        if body.timezone is not None:  row.timezone = body.timezone.strip()
        s.commit()
        return {"ok": True}


# ===========================================================================
#  API — Usuarios del tenant (Ajustes / Usuarios)
# ===========================================================================

class UserInvite(BaseModel):
    email: str
    nombre: str = ""
    role: str = "manager"
    password: str


class UserUpdate(BaseModel):
    nombre: str | None = None
    role: str | None = None
    password: str | None = None


@router.get("/api/portal/usuarios")
async def api_usuarios_list(
    t: db_module.Tenant = Depends(_current_tenant),
    _u: db_module.TenantUser = Depends(_current_user),
):
    with Session(db_module.engine) as s:
        rows = (
            s.query(db_module.TenantUser)
            .filter(db_module.TenantUser.tenant_id == t.id)
            .order_by(db_module.TenantUser.id.asc())
            .all()
        )
        return [
            {"id": u.id, "email": u.email, "nombre": u.nombre, "role": u.role}
            for u in rows
        ]


@router.post("/api/portal/usuarios")
async def api_usuarios_invite(
    body: UserInvite,
    t: db_module.Tenant = Depends(_current_tenant),
    me: db_module.TenantUser = Depends(_current_user),
):
    if me.role != "owner":
        raise HTTPException(403, "solo el propietario puede invitar")
    from passlib.hash import bcrypt
    with Session(db_module.engine) as s:
        dup = (
            s.query(db_module.TenantUser)
            .filter(
                db_module.TenantUser.tenant_id == t.id,
                db_module.TenantUser.email == body.email.strip().lower(),
            ).first()
        )
        if dup is not None:
            raise HTTPException(409, "ese email ya tiene acceso")
        row = db_module.TenantUser(
            tenant_id=t.id,
            email=body.email.strip().lower(),
            password_hash=bcrypt.hash(body.password),
            nombre=body.nombre or "",
            role=body.role if body.role in ("owner", "manager", "readonly") else "manager",
        )
        s.add(row)
        s.commit()
        s.refresh(row)
        return {"ok": True, "id": row.id}


@router.delete("/api/portal/usuarios/{uid}")
async def api_usuarios_remove(
    uid: int,
    t: db_module.Tenant = Depends(_current_tenant),
    me: db_module.TenantUser = Depends(_current_user),
):
    if me.role != "owner":
        raise HTTPException(403, "solo el propietario puede quitar usuarios")
    if uid == me.id:
        raise HTTPException(400, "no puedes quitarte a ti mismo")
    with Session(db_module.engine) as s:
        row = s.get(db_module.TenantUser, uid)
        if row is None or row.tenant_id != t.id:
            raise HTTPException(404, "usuario no encontrado")
        s.delete(row)
        s.commit()
        return {"ok": True}
