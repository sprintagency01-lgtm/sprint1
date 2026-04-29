"""Router FastAPI del CMS.

Estructura de URLs (todas bajo /admin):

    GET  /admin/login                         Formulario de login
    POST /admin/login                         Valida y crea sesión
    GET  /admin/logout                        Cierra sesión

    GET  /admin/                              → /admin/dashboard
    GET  /admin/dashboard                     Métricas globales
    GET  /admin/clientes                      Lista de tenants
    GET  /admin/clientes/new                  Formulario nuevo
    POST /admin/clientes                      Crear tenant
    GET  /admin/clientes/{id}                 → /admin/clientes/{id}/general
    GET  /admin/clientes/{id}/{tab}           Detalle (tab = general|servicios|horarios|personalizacion|metricas)
    POST /admin/clientes/{id}/general         Guardar datos generales
    POST /admin/clientes/{id}/servicios       Guardar servicios
    POST /admin/clientes/{id}/horarios        Guardar horarios
    POST /admin/clientes/{id}/personalizacion Guardar personalización del bot
    POST /admin/clientes/{id}/delete          Borrar tenant
    POST /admin/clientes/{id}/toggle          Pausar/activar

    GET  /admin/reservas                      Lista (placeholder)
    GET  /admin/facturacion                   Desglose coste por cliente
    GET  /admin/ajustes                       Ajustes globales
"""
from __future__ import annotations

import json
import pathlib
from datetime import datetime, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Request, Form, Depends, HTTPException, status
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import db as db_module
from .. import elevenlabs_client
from ..config import settings
from . import auth

router = APIRouter()

# Templates + static
_BASE = pathlib.Path(__file__).parent
templates = Jinja2Templates(directory=str(_BASE / "templates"))

# Filtros custom para Jinja
def _fmt_tokens(n: int) -> str:
    if n is None:
        return "0"
    if n >= 1_000_000:
        return f"{n/1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n/1_000:.0f}k" if n >= 100_000 else f"{n/1_000:.1f}k"
    return str(int(n))

def _fmt_eur(n: float) -> str:
    if n is None:
        return "€0,00"
    return "€" + f"{n:.2f}".replace(".", ",")

def _fmt_int(n) -> str:
    if n is None:
        return "0"
    return f"{int(n):,}".replace(",", ".")

def _initials(name: str) -> str:
    if not name:
        return "?"
    parts = [p for p in name.split() if p]
    return "".join(p[0] for p in parts[:2]).upper()

def _avatar_color(name: str) -> str:
    colors = [
        "bg-emerald-100 text-emerald-700",
        "bg-sky-100 text-sky-700",
        "bg-amber-100 text-amber-700",
        "bg-rose-100 text-rose-700",
        "bg-violet-100 text-violet-700",
    ]
    return colors[len(name or "") % len(colors)]

templates.env.filters["fmt_tokens"] = _fmt_tokens
templates.env.filters["fmt_eur"] = _fmt_eur
templates.env.filters["fmt_int"] = _fmt_int
templates.env.filters["initials"] = _initials
templates.env.filters["avatar_color"] = _avatar_color

# Static — montado desde main.py vía router_mounts (un mount declarado
# dentro de un APIRouter no se propaga siempre al hacer include_router en
# función de la versión de Starlette/FastAPI; lo exponemos como lista para
# que main.py lo monte en la app raíz, igual que hace con el portal).
router_mounts: list[tuple[str, "StaticFiles"]] = [
    ("/admin/static", StaticFiles(directory=str(_BASE / "static"))),
]


# ==========================================================================
#  PWA — service worker y manifest
# ==========================================================================
#
# El SW se sirve desde la raíz del scope (/admin/sw.js) para cubrir todas
# las rutas /admin/*. El manifest tiene también un alias en /admin/ por
# si algún navegador prefiere encontrarlo ahí.

@router.get("/admin/sw.js", include_in_schema=False)
async def admin_service_worker():
    return FileResponse(
        _BASE / "static" / "sw.js",
        media_type="application/javascript",
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Service-Worker-Allowed": "/admin/",
        },
    )


@router.get("/admin/manifest.webmanifest", include_in_schema=False)
async def admin_manifest():
    return FileResponse(
        _BASE / "static" / "manifest.json",
        media_type="application/manifest+json",
    )


# ==========================================================================
#  HELPERS
# ==========================================================================

def _today_local() -> datetime:
    """Hoy en la zona horaria del negocio (Europe/Madrid por defecto).

    Útil para series de dashboard tipo "últimos 30 días" donde el corte de
    día tiene que ser local: a las 01:30 AM Madrid, datetime.utcnow() está
    en el día anterior y el dashboard pintaría un día corrido.
    """
    return datetime.now(ZoneInfo(settings.default_timezone))


def _since_30d() -> datetime:
    return datetime.utcnow() - timedelta(days=30)

def _since_60d_30d() -> tuple[datetime, datetime]:
    return datetime.utcnow() - timedelta(days=60), datetime.utcnow() - timedelta(days=30)


def _metrics_for_tenant(s: Session, tenant_id: str) -> dict:
    """Calcula métricas 30d y 60-30d para comparativa."""
    t30 = _since_30d()
    t60, t30_end = _since_60d_30d()

    def _sum(*filters, col=db_module.TokenUsage.input_tokens):
        return s.query(func.coalesce(func.sum(col), 0)).filter(*filters).scalar() or 0

    tokens_in_30  = _sum(db_module.TokenUsage.tenant_id == tenant_id, db_module.TokenUsage.created_at >= t30, col=db_module.TokenUsage.input_tokens)
    tokens_out_30 = _sum(db_module.TokenUsage.tenant_id == tenant_id, db_module.TokenUsage.created_at >= t30, col=db_module.TokenUsage.output_tokens)
    tokens_30 = tokens_in_30 + tokens_out_30

    tokens_prev = (
        _sum(db_module.TokenUsage.tenant_id == tenant_id,
             db_module.TokenUsage.created_at >= t60,
             db_module.TokenUsage.created_at < t30_end,
             col=db_module.TokenUsage.input_tokens) +
        _sum(db_module.TokenUsage.tenant_id == tenant_id,
             db_module.TokenUsage.created_at >= t60,
             db_module.TokenUsage.created_at < t30_end,
             col=db_module.TokenUsage.output_tokens)
    )

    cost_30 = s.query(func.coalesce(func.sum(db_module.TokenUsage.cost_eur), 0.0)).filter(
        db_module.TokenUsage.tenant_id == tenant_id,
        db_module.TokenUsage.created_at >= t30,
    ).scalar() or 0.0

    cost_prev = s.query(func.coalesce(func.sum(db_module.TokenUsage.cost_eur), 0.0)).filter(
        db_module.TokenUsage.tenant_id == tenant_id,
        db_module.TokenUsage.created_at >= t60,
        db_module.TokenUsage.created_at < t30_end,
    ).scalar() or 0.0

    # clientes atendidos distintos en 30d (1 cliente = 1 customer_phone)
    convos_30 = s.query(func.count(func.distinct(db_module.Message.customer_phone))).filter(
        db_module.Message.tenant_id == tenant_id,
        db_module.Message.created_at >= t30,
    ).scalar() or 0

    convos_prev = s.query(func.count(func.distinct(db_module.Message.customer_phone))).filter(
        db_module.Message.tenant_id == tenant_id,
        db_module.Message.created_at >= t60,
        db_module.Message.created_at < t30_end,
    ).scalar() or 0

    # Serie diaria últimos 30d (para sparkline/gráfico)
    date_col = func.date(db_module.TokenUsage.created_at)
    series_rows = s.execute(
        select(
            date_col.label("day"),
            func.coalesce(func.sum(db_module.TokenUsage.input_tokens + db_module.TokenUsage.output_tokens), 0).label("tks"),
        )
        .where(db_module.TokenUsage.tenant_id == tenant_id, db_module.TokenUsage.created_at >= t30)
        .group_by(date_col)
    ).all()
    series_map = {str(row[0]): int(row[1] or 0) for row in series_rows}
    series = []
    for i in range(30):
        d = (_today_local() - timedelta(days=29 - i)).date().isoformat()
        series.append(series_map.get(d, 0))

    return {
        "tokens_30d": int(tokens_30),
        "tokens_prev": int(tokens_prev),
        "cost_30d": float(cost_30),
        "cost_prev": float(cost_prev),
        "convos_30d": int(convos_30),
        "convos_prev": int(convos_prev),
        "series": series,
        # Placeholder: reservas reales vendrían del log de tool calls o Calendar
        "bookings_30d": 0,
        "bookings_prev": 0,
    }


def _conversation_channel(customer_phone: str) -> str:
    raw = (customer_phone or "").strip().lower()
    if raw.startswith("tg:"):
        return "telegram"
    return "voz"


def _conversation_display_phone(customer_phone: str) -> str:
    raw = (customer_phone or "").strip()
    if raw.lower().startswith("tg:"):
        return raw[3:]
    return raw


def _load_conversation_summaries(
    s: Session,
    *,
    tenant_id: str | None = None,
    channel: str | None = None,
    limit: int = 300,
) -> list[dict]:
    """Agrupa `messages` por (tenant, customer_phone) para pintar el inbox."""
    q = s.query(db_module.Message)
    if tenant_id:
        q = q.filter(db_module.Message.tenant_id == tenant_id)
    rows = q.order_by(db_module.Message.created_at.desc()).limit(2000).all()

    grouped: dict[tuple[str, str], dict] = {}
    for m in rows:
        conv_channel = _conversation_channel(m.customer_phone)
        if channel in ("voz", "telegram") and conv_channel != channel:
            continue
        key = (m.tenant_id, m.customer_phone)
        item = grouped.get(key)
        if item is None:
            item = {
                "tenant_id": m.tenant_id,
                "phone": m.customer_phone,
                "display_phone": _conversation_display_phone(m.customer_phone),
                "channel": conv_channel,
                "last_at": m.created_at,
                "last_text": (m.content or "").strip(),
                "n_messages": 0,
                "tenant": s.get(db_module.Tenant, m.tenant_id),
            }
            grouped[key] = item
        item["n_messages"] += 1

    summaries = sorted(
        grouped.values(),
        key=lambda x: x["last_at"] or datetime.min,
        reverse=True,
    )
    return summaries[:limit]


def _load_conversation_messages(
    s: Session,
    *,
    tenant_id: str,
    customer_phone: str,
    limit: int = 300,
) -> list[db_module.Message]:
    return (
        s.query(db_module.Message)
        .filter(
            db_module.Message.tenant_id == tenant_id,
            db_module.Message.customer_phone == customer_phone,
        )
        .order_by(db_module.Message.created_at.asc())
        .limit(limit)
        .all()
    )


def _services_map(tenant_id: str) -> dict[str, dict]:
    with Session(db_module.engine) as s:
        rows = (
            s.query(db_module.Service)
            .filter(db_module.Service.tenant_id == tenant_id)
            .all()
        )
        return {
            str(sv.id): {
                "nombre": sv.nombre,
                "precio": sv.precio,
                "duracion": sv.duracion_min,
            }
            for sv in rows
        }


def _members_by_calendar(tenant_id: str) -> dict[str, str]:
    with Session(db_module.engine) as s:
        rows = (
            s.query(db_module.MiembroEquipo)
            .filter(db_module.MiembroEquipo.tenant_id == tenant_id)
            .all()
        )
        return {m.calendar_id: str(m.id) for m in rows if m.calendar_id}


def _parse_event_dt(ev: dict, key: str) -> datetime | None:
    raw = ((ev.get(key) or {}).get("dateTime") or "").strip()
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


def _event_to_booking(
    ev: dict,
    *,
    tenant: db_module.Tenant,
    services_by_id: dict[str, dict],
    members_by_calendar: dict[str, str],
) -> dict | None:
    start = _parse_event_dt(ev, "start")
    end = _parse_event_dt(ev, "end")
    if not start or not end:
        return None

    priv = (ev.get("extendedProperties") or {}).get("private") or {}
    phone = priv.get("phone") or ""
    client = priv.get("client_name") or ""
    service_id = str(priv.get("service_id") or "")
    member_id = str(priv.get("member_id") or "")
    channel = (priv.get("channel") or priv.get("created_by") or "").lower()
    canal = "voz" if channel in ("voice", "voz") else "manual"

    if not client:
        summary = (ev.get("summary") or "").strip()
        client = summary or "(sin nombre)"

    service_name = ""
    if service_id and service_id in services_by_id:
        service_name = services_by_id[service_id]["nombre"]
    else:
        summary_lower = (ev.get("summary") or "").lower()
        for sid, svc in services_by_id.items():
            if svc["nombre"].lower() in summary_lower:
                service_id = sid
                service_name = svc["nombre"]
                break

    organizer_email = (ev.get("organizer") or {}).get("email") or ""
    if not member_id and organizer_email in members_by_calendar:
        member_id = members_by_calendar[organizer_email]

    duracion = max(1, int((end - start).total_seconds() // 60))

    return {
        "id": ev.get("id") or "",
        "tenant_id": tenant.id,
        "tenant_name": tenant.name,
        "fecha": start.strftime("%Y-%m-%d"),
        "hora": start.strftime("%H:%M"),
        "inicio": start,
        "duracion": duracion,
        "cliente": client,
        "telefono": phone,
        "servicio": service_name,
        "servicio_id": service_id,
        "equipo": member_id,
        "canal": canal,
        "estado": "cancelada" if ev.get("status") == "cancelled" else "confirmada",
        "calendar_id": ev.get("_calendar_id") or tenant.calendar_id or "",
        "summary": ev.get("summary") or "",
    }


def _load_all_bookings(*, days_back: int = 30, days_forward: int = 365) -> tuple[list[dict], list[dict]]:
    """Agrega reservas de todos los tenants activos desde Google Calendar."""
    from .. import calendar_service
    from datetime import time as _time

    warnings: list[dict] = []
    bookings: list[dict] = []
    today = datetime.now(calendar_service.TZ).date()
    desde = datetime.combine(today - timedelta(days=days_back), _time(0, 0))
    hasta = datetime.combine(today + timedelta(days=days_forward), _time(23, 59))

    with Session(db_module.engine) as s:
        tenants = (
            s.query(db_module.Tenant)
            .filter(db_module.Tenant.kind == "contracted")
            .order_by(db_module.Tenant.name.asc())
            .all()
        )

    for tenant in tenants:
        try:
            events = calendar_service.listar_eventos(
                desde=desde,
                hasta=hasta,
                calendar_id=tenant.calendar_id,
                tenant_id=tenant.id,
            )
            services_by_id = _services_map(tenant.id)
            members_by_calendar = _members_by_calendar(tenant.id)
            for ev in events:
                booking = _event_to_booking(
                    ev,
                    tenant=tenant,
                    services_by_id=services_by_id,
                    members_by_calendar=members_by_calendar,
                )
                if booking is not None:
                    bookings.append(booking)
        except Exception as exc:
            warnings.append({
                "tenant_id": tenant.id,
                "tenant_name": tenant.name,
                "error": str(exc)[:220],
            })

    bookings.sort(key=lambda x: x["inicio"], reverse=True)
    return bookings, warnings


def _delta_pct(curr, prev) -> int:
    if not prev:
        return 0
    return round(((curr - prev) / prev) * 100)


# --------------------------------------------------------------------------
#  Seed perezoso del prompt de voz
# --------------------------------------------------------------------------

# Voz del snapshot actual de ElevenLabs (ver ELEVENLABS.md). Solo se usa como
# valor por defecto si el tenant no tiene uno.
_DEFAULT_VOICE_ID = "1eHrpOW5l98cxiSRjbzJ"


def _diag_calendar_connection(tenant_id: str, calendar_id: str) -> dict:
    """Ejecuta una batería de chequeos contra Google Calendar para un tenant
    y devuelve un dict con el resultado de cada paso. Útil para depurar sin
    tener que mirar logs de Railway.
    """
    from .. import calendar_service as _cal
    from datetime import datetime, timedelta

    result: dict = {"steps": [], "ok": True, "cause": None}

    def add(name: str, ok: bool, detail: str = ""):
        result["steps"].append({"name": name, "ok": ok, "detail": detail})
        if not ok and result["ok"]:
            result["ok"] = False
            result["cause"] = name

    # 1) ¿Hay archivo de token para este tenant?
    path = _cal.TOKENS_DIR / f"{tenant_id}.json"
    token_exists = path.exists()
    add("Token file", token_exists,
        str(path) if token_exists else f"No existe {path}. Pulsa 'Conectar' en General.")

    # 2) ¿Se puede cargar y refrescar?
    try:
        _cal._invalidate_service_cache(tenant_id)  # forzar recarga
        svc = _cal._service(tenant_id)
        add("Load service", True, "Credenciales cargadas y service construido")
    except Exception as e:
        add("Load service", False, f"{type(e).__name__}: {str(e)[:240]}")
        return result

    # 3) calendarList().list — confirma que el token tiene scope suficiente
    try:
        cl = svc.calendarList().list(maxResults=50, showHidden=False).execute()
        cals = cl.get("items", [])
        add("calendarList", True, f"{len(cals)} calendarios accesibles")
        result["calendar_options"] = [
            {"id": c.get("id"), "summary": c.get("summary"), "primary": bool(c.get("primary"))}
            for c in cals
        ]
    except Exception as e:
        add("calendarList", False, f"{type(e).__name__}: {str(e)[:240]}")

    # 4) ¿El calendar_id objetivo responde?
    target = calendar_id or "primary"
    try:
        got = svc.calendars().get(calendarId=target).execute()
        add(f"calendars.get({target})", True, f"summary='{got.get('summary')}'")
    except Exception as e:
        add(f"calendars.get({target})", False, f"{type(e).__name__}: {str(e)[:240]}")

    # 5) Una freeBusy.query de verdad
    try:
        now = datetime.utcnow()
        tmin = now.replace(microsecond=0).isoformat() + "Z"
        tmax = (now + timedelta(days=1)).replace(microsecond=0).isoformat() + "Z"
        fb = svc.freebusy().query(body={
            "timeMin": tmin, "timeMax": tmax, "items": [{"id": target}],
        }).execute()
        cal_entry = (fb.get("calendars") or {}).get(target) or {}
        if "errors" in cal_entry:
            add("freeBusy", False, f"Google devolvió errors: {cal_entry['errors']}")
        else:
            busy = cal_entry.get("busy", [])
            add("freeBusy", True, f"{len(busy)} periodos ocupados en las próximas 24h")
    except Exception as e:
        add("freeBusy", False, f"{type(e).__name__}: {str(e)[:240]}")

    # 6) listar_huecos_libres con los parámetros de una llamada REAL de Ana.
    #    Esto replica exactamente el camino que `consultar_disponibilidad`
    #    ejecuta cuando el tenant no tiene equipo: loop intra-día + ranges_for_weekday
    #    + generación de slots. Si algo peta aquí y no en los pasos 1-5, el fallo
    #    está en el loop, no en la conexión Google.
    try:
        import traceback as _tb
        from .. import tenants as _tn
        tenant_dict = _tn.get_tenant(tenant_id) or {}
        bh = tenant_dict.get("business_hours") or {}
        # Ventana: próximo día laborable desde ahora (24h-48h adelante)
        desde = datetime.now(_cal.TZ).replace(hour=9, minute=0, second=0, microsecond=0) + timedelta(days=1)
        hasta = desde.replace(hour=20, minute=0)
        slots = _cal.listar_huecos_libres(
            desde, hasta, 30,
            calendar_id=target,
            tenant_id=tenant_id,
            business_hours=bh,
        )
        detail = (
            f"{len(slots)} huecos de 30min entre "
            f"{desde.strftime('%a %d/%m %H:%M')} y {hasta.strftime('%H:%M')}. "
            f"business_hours del día: {bh.get(['mon','tue','wed','thu','fri','sat','sun'][desde.weekday()])}"
        )
        add("listar_huecos_libres", True, detail)
    except Exception as e:
        tb_lines = _tb.format_exc().splitlines()
        add(
            "listar_huecos_libres",
            False,
            f"{type(e).__name__}: {str(e)[:200]} | último frame: {tb_lines[-3] if len(tb_lines) >= 3 else ''}",
        )

    return result


def _google_calendar_connected(tenant_id: str) -> bool:
    """True si hay un token OAuth guardado para este tenant.

    `TOKENS_DIR` lo resuelve el env var (Railway usa `/app/data/.tokens`) —
    importamos calendar_service para que el path sea siempre el mismo que usa
    el backend en runtime.
    """
    try:
        from .. import calendar_service as _cal
        return (_cal.TOKENS_DIR / f"{tenant_id}.json").exists()
    except Exception:
        return False


def _member_token_path(tenant_id: str, member_id: int):
    """Wrapper fino sobre `calendar_service.member_token_path` (compat).

    Antes esta función vivía aquí; ahora la lógica está en
    `app/calendar_service.py` para que el portal del cliente la reuse.
    """
    from .. import calendar_service as _cal
    return _cal.member_token_path(tenant_id, member_id)


def _google_member_connected(tenant_id: str, member_id: int) -> bool:
    from .. import calendar_service as _cal
    return _cal.member_is_connected(tenant_id, member_id)


def _member_google_service(tenant_id: str, member_id: int):
    """Wrapper que traduce `RuntimeError("miembro_no_conectado")` del helper
    compartido a un `HTTPException(400)` legible para los endpoints del CMS.
    """
    from .. import calendar_service as _cal
    try:
        return _cal.member_google_service(tenant_id, member_id)
    except RuntimeError as e:
        if "miembro_no_conectado" in str(e):
            raise HTTPException(
                400,
                "Este miembro aún no ha conectado su cuenta Google. "
                "Pulsa 'Conectar Google' en la pestaña Equipo.",
            )
        raise
    except Exception as e:  # pragma: no cover
        raise HTTPException(500, f"No se pudo construir el cliente Google del miembro: {e}")


def _seed_voice_defaults_if_empty(s: Session, t: db_module.Tenant) -> None:
    """Rellena voice_prompt y voice_voice_id la primera vez si están vacíos.

    El prompt se compone con `render_voice_prompt(tenant_dict)` a partir de los
    datos del tenant (nombre, servicios, horario, peluqueros, fallback phone),
    así que el onboarding de un cliente nuevo no requiere editar el prompt a
    mano. Idempotente: si ya hay valor, no toca nada.
    """
    touched = False
    if not (t.voice_prompt or "").strip():
        try:
            t.voice_prompt = db_module.render_voice_prompt(t.to_dict())
            touched = True
        except Exception:
            # Si falla la composición por cualquier motivo (datos raros),
            # preferimos no romper la vista; el usuario podrá escribirlo a mano.
            pass
    if not (t.voice_voice_id or "").strip():
        t.voice_voice_id = _DEFAULT_VOICE_ID
        touched = True
    if touched:
        s.commit()


def _render_voice_prompt_safe(t: db_module.Tenant) -> str:
    return db_module.render_voice_prompt(t.to_dict(include_system_prompt=False))


def _refresh_voice_prompt_if_autogenerated(
    t: db_module.Tenant,
    previous_rendered_prompt: str | None,
) -> None:
    """Refresca `voice_prompt` si aún sigue el template autogenerado.

    Objetivo: cuando cambian servicios/horarios/equipo/datos del negocio, el
    prompt de voz no debe quedarse desfasado. Pero si un humano lo editó a mano,
    no debemos pisarlo. Regla: solo lo regeneramos si estaba vacío o si seguía
    coincidiendo exactamente con la versión autogenerada anterior.
    """
    current_prompt = (t.voice_prompt or "").strip()
    if current_prompt and previous_rendered_prompt and current_prompt != previous_rendered_prompt:
        return
    t.voice_prompt = _render_voice_prompt_safe(t)
    if not (t.voice_voice_id or "").strip():
        t.voice_voice_id = _DEFAULT_VOICE_ID


# ==========================================================================
#  AUTH ENDPOINTS
# ==========================================================================

@router.get("/admin/login", response_class=HTMLResponse)
async def login_get(request: Request):
    return templates.TemplateResponse("login.html", {"request": request, "error": None})


@router.post("/admin/login", response_class=HTMLResponse)
async def login_post(request: Request, email: str = Form(...), password: str = Form(...)):
    uid = auth.verify_credentials(email, password)
    if not uid:
        return templates.TemplateResponse(
            "login.html", {"request": request, "error": "Credenciales incorrectas."}, status_code=401
        )
    resp = RedirectResponse(url="/admin/dashboard", status_code=303)
    resp.set_cookie(
        auth.COOKIE_NAME, auth.sign_session(uid),
        max_age=auth.SESSION_TTL_SECONDS, httponly=True, samesite="lax",
    )
    return resp


@router.get("/admin/logout")
async def logout():
    resp = RedirectResponse(url="/admin/login", status_code=303)
    resp.delete_cookie(auth.COOKIE_NAME)
    return resp


# ==========================================================================
#  ROOT  →  DASHBOARD
# ==========================================================================

@router.get("/admin/")
async def admin_root(uid: int = Depends(auth.current_user_id)):
    return RedirectResponse(url="/admin/dashboard", status_code=303)


@router.get("/admin/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request, uid: int = Depends(auth.current_user_id)):
    with Session(db_module.engine) as s:
        tenants = s.query(db_module.Tenant).all()

        # Métricas globales
        t30 = _since_30d()
        t60, t30_end = _since_60d_30d()

        total_tokens = s.query(func.coalesce(func.sum(db_module.TokenUsage.input_tokens + db_module.TokenUsage.output_tokens), 0)).filter(
            db_module.TokenUsage.created_at >= t30
        ).scalar() or 0
        total_tokens_prev = s.query(func.coalesce(func.sum(db_module.TokenUsage.input_tokens + db_module.TokenUsage.output_tokens), 0)).filter(
            db_module.TokenUsage.created_at >= t60,
            db_module.TokenUsage.created_at < t30_end,
        ).scalar() or 0

        total_cost = s.query(func.coalesce(func.sum(db_module.TokenUsage.cost_eur), 0.0)).filter(
            db_module.TokenUsage.created_at >= t30
        ).scalar() or 0.0

        total_convos = s.query(func.count(func.distinct(db_module.Message.customer_phone))).filter(
            db_module.Message.created_at >= t30
        ).scalar() or 0

        # Consumo por tenant (ranking)
        ranking = []
        for t in tenants:
            m = _metrics_for_tenant(s, t.id)
            ranking.append({"tenant": t, "m": m})
        ranking.sort(key=lambda x: x["m"]["tokens_30d"], reverse=True)

        # Serie diaria agregada
        date_col = func.date(db_module.TokenUsage.created_at)
        series_rows = s.execute(
            select(
                date_col.label("day"),
                func.coalesce(func.sum(db_module.TokenUsage.input_tokens + db_module.TokenUsage.output_tokens), 0).label("tks"),
            ).where(db_module.TokenUsage.created_at >= t30).group_by(date_col)
        ).all()
        series_map = {str(row[0]): int(row[1] or 0) for row in series_rows}
        global_series = []
        for i in range(30):
            d = (_today_local() - timedelta(days=29 - i)).date().isoformat()
            global_series.append(series_map.get(d, 0))

        # Últimas llamadas (5) — cada una es 1 customer_phone distinto
        recent_rows = s.execute(
            select(
                db_module.Message.tenant_id,
                db_module.Message.customer_phone,
                func.max(db_module.Message.created_at).label("last_at"),
            ).group_by(db_module.Message.tenant_id, db_module.Message.customer_phone)
             .order_by(func.max(db_module.Message.created_at).desc())
             .limit(5)
        ).all()
        recent = []
        for row in recent_rows:
            tid, phone, last = row[0], row[1], row[2]
            t = s.get(db_module.Tenant, tid)
            recent.append({"tenant": t, "phone": phone, "last": last})

    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "user_email": auth.current_user_email(uid),
        "active": "dashboard",
        "tenants": tenants,
        "active_tenants": [t for t in tenants if t.status == "active"],
        "total_tokens": int(total_tokens),
        "total_tokens_prev": int(total_tokens_prev),
        "total_tokens_delta": _delta_pct(total_tokens, total_tokens_prev),
        "total_cost": float(total_cost),
        "total_convos": int(total_convos),
        "ranking": ranking,
        "max_rank_tokens": max([r["m"]["tokens_30d"] for r in ranking], default=1) or 1,
        "global_series": global_series,
        "recent": recent,
    })


# ==========================================================================
#  CONVERSACIONES / LLAMADAS — BANDEJA GLOBAL
# ==========================================================================

@router.get("/admin/conversaciones", response_class=HTMLResponse)
async def conversations_inbox(
    request: Request,
    tenant: str | None = None,
    phone: str | None = None,
    channel: str | None = None,
    uid: int = Depends(auth.current_user_id),
):
    active_channel = channel if channel in ("voz", "telegram") else "voz"
    selected = None
    messages: list[db_module.Message] = []
    with Session(db_module.engine) as s:
        convos = _load_conversation_summaries(s, tenant_id=tenant, channel=active_channel)
        counts = {
            "voz": len(_load_conversation_summaries(s, tenant_id=tenant, channel="voz", limit=1000)),
            "telegram": len(_load_conversation_summaries(s, tenant_id=tenant, channel="telegram", limit=1000)),
        }
        if phone:
            selected = next(
                (
                    c for c in convos
                    if c["phone"] == phone and (tenant is None or c["tenant_id"] == tenant)
                ),
                None,
            )
            if selected is None and tenant:
                t = s.get(db_module.Tenant, tenant)
                selected = {
                    "tenant_id": tenant,
                    "phone": phone,
                    "display_phone": _conversation_display_phone(phone),
                    "channel": _conversation_channel(phone),
                    "tenant": t,
                    "last_at": None,
                    "last_text": "",
                    "n_messages": 0,
                }
            if selected is not None:
                messages = _load_conversation_messages(
                    s,
                    tenant_id=selected["tenant_id"],
                    customer_phone=selected["phone"],
                )

    return templates.TemplateResponse("conversations.html", {
        "request": request,
        "user_email": auth.current_user_email(uid),
        "active": "conversaciones",
        "convos": convos,
        "selected": selected,
        "messages": messages,
        "active_channel": active_channel,
        "channel_counts": counts,
    })


# ==========================================================================
#  CLIENTES — LISTA
# ==========================================================================

@router.get("/admin/clientes", response_class=HTMLResponse)
async def clients_list(
    request: Request,
    kind: Optional[str] = None,
    uid: int = Depends(auth.current_user_id),
):
    with Session(db_module.engine) as s:
        q = s.query(db_module.Tenant)
        if kind in ("lead", "contracted"):
            q = q.filter(db_module.Tenant.kind == kind)
        tenants = q.order_by(db_module.Tenant.kind.desc(), db_module.Tenant.name).all()
        rows = []
        for t in tenants:
            m = _metrics_for_tenant(s, t.id)
            rows.append({
                "t": t,
                "m": m,
                "delta_tokens": _delta_pct(m["tokens_30d"], m["tokens_prev"]),
            })

        # Contadores para pestañas de filtro
        counts = {
            "all":        s.query(db_module.Tenant).count(),
            "lead":       s.query(db_module.Tenant).filter(db_module.Tenant.kind == "lead").count(),
            "contracted": s.query(db_module.Tenant).filter(db_module.Tenant.kind == "contracted").count(),
        }
    return templates.TemplateResponse("clients_list.html", {
        "request": request,
        "user_email": auth.current_user_email(uid),
        "active": "clientes",
        "rows": rows,
        "kind_filter": kind,
        "counts": counts,
    })


# ==========================================================================
#  CLIENTE — NUEVO
# ==========================================================================

@router.get("/admin/clientes/new", response_class=HTMLResponse)
async def client_new(request: Request, uid: int = Depends(auth.current_user_id)):
    # Esqueleto de tenant vacío (no persistido hasta que haga POST)
    empty = db_module.Tenant(
        id="", name="", sector="", status="active", plan="Básico",
        phone_display="", calendar_id="primary",
        timezone="Europe/Madrid", language="Español",
        contact_name="", contact_email="",
        business_hours_json=json.dumps({
            "mon": ["09:00","20:00"], "tue": ["09:00","20:00"], "wed": ["09:00","20:00"],
            "thu": ["09:00","20:00"], "fri": ["09:00","20:00"], "sat": ["closed"], "sun": ["closed"],
        }),
        assistant_name="Asistente", assistant_tone="cercano", assistant_formality="tu",
        assistant_emoji=True, assistant_greeting="",
        assistant_fallback_phone="", assistant_rules_json="[]",
        system_prompt_override="",
    )
    return templates.TemplateResponse("client_detail.html", {
        "request": request,
        "user_email": auth.current_user_email(uid),
        "active": "clientes",
        "t": empty,
        "tab": "general",
        "is_new": True,
        "metrics": None,
        "conversations": [],
    })


@router.post("/admin/clientes")
async def client_create(
    request: Request,
    id: str = Form(...),
    name: str = Form(...),
    sector: str = Form(""),
    plan: str = Form("Básico"),
    contact_name: str = Form(""),
    contact_email: str = Form(""),
    phone_display: str = Form(""),
    calendar_id: str = Form("primary"),
    timezone: str = Form("Europe/Madrid"),
    language: str = Form("Español"),
    portal_email: str = Form(""),
    portal_password: str = Form(""),
    uid: int = Depends(auth.current_user_id),
):
    tid = (id or "").strip().lower().replace(" ", "_")
    if not tid or not name:
        raise HTTPException(400, "id y nombre son obligatorios")

    # Validación del acceso al portal: si ponen uno, tienen que poner los dos.
    portal_email_norm = (portal_email or "").strip().lower()
    portal_pwd = portal_password or ""
    if portal_email_norm and not portal_pwd:
        raise HTTPException(400, "Si indicas email de portal, también debes indicar contraseña inicial.")
    if portal_pwd and not portal_email_norm:
        raise HTTPException(400, "Si indicas contraseña de portal, también debes indicar el email.")
    if portal_pwd and len(portal_pwd) < 8:
        raise HTTPException(400, "La contraseña del portal debe tener al menos 8 caracteres.")

    with Session(db_module.engine) as s:
        if s.get(db_module.Tenant, tid) is not None:
            raise HTTPException(400, f"Ya existe un cliente con id={tid}")
        t = db_module.Tenant(
            id=tid, name=name, sector=sector, plan=plan,
            contact_name=contact_name, contact_email=contact_email,
            phone_display=phone_display,
            calendar_id=calendar_id or "primary",
            timezone=timezone, language=language,
            status="active",
        )
        t.business_hours = {
            "mon": ["09:00","20:00"], "tue": ["09:00","20:00"], "wed": ["09:00","20:00"],
            "thu": ["09:00","20:00"], "fri": ["09:00","20:00"], "sat": ["closed"], "sun": ["closed"],
        }
        t.assistant_rules = []
        s.add(t)
        s.flush()  # para tener el tenant disponible antes de añadir el owner

        # Crea el owner del portal si se han proporcionado credenciales.
        # (Si no, ensure_portal_users() generará uno por defecto en el próximo arranque.)
        if portal_email_norm and portal_pwd:
            from passlib.hash import bcrypt
            owner = db_module.TenantUser(
                tenant_id=tid,
                email=portal_email_norm,
                password_hash=bcrypt.hash(portal_pwd),
                nombre=(contact_name or name),
                role="owner",
            )
            s.add(owner)
        s.commit()
    return RedirectResponse(url=f"/admin/clientes/{tid}/general", status_code=303)


# ==========================================================================
#  CLIENTE — DETALLE (por pestaña)
# ==========================================================================

@router.get("/admin/clientes/{tenant_id}", response_class=HTMLResponse)
async def client_detail_root(tenant_id: str, uid: int = Depends(auth.current_user_id)):
    return RedirectResponse(url=f"/admin/clientes/{tenant_id}/general", status_code=303)


# OJO: esta ruta DEBE ir antes que `/admin/clientes/{tenant_id}/{tab}` para
# que FastAPI no intente matchear "calendar_test" como el parámetro `tab` y
# devuelva 404 "Pestaña desconocida".
@router.get("/admin/clientes/{tenant_id}/calendar_test", response_class=HTMLResponse)
async def client_calendar_test(
    tenant_id: str, request: Request,
    uid: int = Depends(auth.current_user_id),
):
    """Página de diagnóstico de la integración Google Calendar para un tenant.

    Muestra paso a paso qué funciona y qué no (token, scopes, calendar_id
    objetivo, freeBusy). Pensado para depurar problemas del tipo "Ana dice
    que tiene problemas del sistema" sin tener que mirar los logs de Railway.
    """
    with Session(db_module.engine) as s:
        t = s.get(db_module.Tenant, tenant_id)
        if t is None:
            # Lista los ids existentes para orientar al usuario si escribió
            # mal el slug o si está esperando otro id.
            existing = [row.id for row in s.query(db_module.Tenant).all()]
            raise HTTPException(
                404,
                detail=(
                    f"No existe tenant con id '{tenant_id}'. "
                    f"Tenants en BD: {existing}"
                ),
            )
        calendar_id = t.calendar_id or "primary"
        tenant_name = t.name

    diag = _diag_calendar_connection(tenant_id, calendar_id)

    rows_html = []
    for step in diag["steps"]:
        color = "#16a34a" if step["ok"] else "#dc2626"
        icon = "✓" if step["ok"] else "✗"
        rows_html.append(
            f'<tr><td style="padding:8px;color:{color};font-weight:bold">{icon}</td>'
            f'<td style="padding:8px"><b>{step["name"]}</b></td>'
            f'<td style="padding:8px;font-family:monospace;font-size:13px">{step["detail"]}</td></tr>'
        )

    cals_html = ""
    if diag.get("calendar_options"):
        items = "".join(
            f'<li><code>{c["id"]}</code> — {c["summary"]}{" <b>(primary)</b>" if c["primary"] else ""}</li>'
            for c in diag["calendar_options"]
        )
        cals_html = f"<h3>Calendarios accesibles con este token</h3><ul>{items}</ul>"

    summary_color = "#16a34a" if diag["ok"] else "#dc2626"
    summary_text = "Todo bien" if diag["ok"] else f"Falló en: {diag['cause']}"

    body = f"""<!doctype html><html lang="es"><head><meta charset="utf-8">
<title>Diagnóstico calendar · {tenant_name}</title>
<style>
  body {{ font-family: system-ui, sans-serif; max-width: 900px; margin: 2rem auto;
          padding: 2rem; background: #f8fafc; color: #0f172a; }}
  .card {{ background: white; border: 1px solid #e2e8f0; border-radius: 12px;
           padding: 24px; margin-bottom: 16px; }}
  h1 {{ margin: 0 0 8px; font-size: 20px; }}
  h3 {{ margin: 16px 0 8px; font-size: 14px; }}
  table {{ width: 100%; border-collapse: collapse; }}
  td {{ border-bottom: 1px solid #f1f5f9; vertical-align: top; }}
  code {{ background: #f1f5f9; padding: 2px 6px; border-radius: 4px; font-size: 12px; }}
  .summary {{ color: {summary_color}; font-weight: 600; }}
  a {{ color: #2563eb; }}
</style></head>
<body>
  <div class="card">
    <h1>Diagnóstico Google Calendar — {tenant_name}</h1>
    <p>Tenant <code>{tenant_id}</code> · calendar_id objetivo: <code>{calendar_id}</code></p>
    <p class="summary">{summary_text}</p>
    <table>{"".join(rows_html)}</table>
    {cals_html}
    <p style="margin-top:24px"><a href="/admin/clientes/{tenant_id}/general">← Volver al panel</a></p>
  </div>
</body></html>"""
    return HTMLResponse(body)


@router.get("/admin/clientes/{tenant_id}/{tab}", response_class=HTMLResponse)
async def client_detail(
    tenant_id: str, tab: str, request: Request,
    uid: int = Depends(auth.current_user_id),
):
    if tab not in ("general", "servicios", "horarios", "equipo", "personalizacion", "voz", "conversaciones", "metricas"):
        raise HTTPException(404, "Pestaña desconocida")

    with Session(db_module.engine) as s:
        t = s.get(db_module.Tenant, tenant_id)
        if t is None:
            raise HTTPException(404, "Cliente no encontrado")

        # Seed perezoso del prompt de voz la primera vez que se abre la pestaña:
        # si el tenant aún no tiene voice_prompt guardado, se carga el
        # `ana_prompt_new.txt` del repo como punto de partida editable. A
        # partir de ahí, la fuente de verdad es la BD.
        if tab == "voz":
            _seed_voice_defaults_if_empty(s, t)

        metrics = _metrics_for_tenant(s, tenant_id) if tab in ("metricas", "general") else None
        conversations = _load_conversation_summaries(s, tenant_id=tenant_id, channel="voz") if tab == "conversaciones" else []

        # prompt preview
        prompt_preview = db_module.render_system_prompt(t)

        # Expandir el tenant — el template sigue usando el objeto SQLAlchemy
        # pero necesitamos la lista de servicios también pre-cargada para Jinja.
        _ = t.services  # fuerza carga
        # Estado de conexión Google por miembro (solo relevante en tab=equipo)
        equipo_conectados: dict[int, bool] = {}
        if tab == "equipo":
            for m in t.equipo:
                equipo_conectados[m.id] = _google_member_connected(tenant_id, m.id)

        # Usuarios del portal del cliente (sólo relevante en tab=general)
        portal_users: list[dict] = []
        if tab == "general":
            rows = (
                s.query(db_module.TenantUser)
                .filter(db_module.TenantUser.tenant_id == tenant_id)
                .order_by(db_module.TenantUser.id.asc())
                .all()
            )
            portal_users = [
                {"id": u.id, "email": u.email, "nombre": u.nombre or "", "role": u.role}
                for u in rows
            ]

        return templates.TemplateResponse("client_detail.html", {
            "request": request,
            "user_email": auth.current_user_email(uid),
            "active": "clientes",
            "t": t,
            "tab": tab,
            "is_new": False,
            "metrics": metrics,
            "metrics_deltas": {
                "tokens": _delta_pct(metrics["tokens_30d"], metrics["tokens_prev"]) if metrics else 0,
                "cost":   _delta_pct(metrics["cost_30d"],   metrics["cost_prev"])   if metrics else 0,
                "convos": _delta_pct(metrics["convos_30d"], metrics["convos_prev"]) if metrics else 0,
            } if metrics else None,
            "prompt_preview": prompt_preview,
            "google_connected": _google_calendar_connected(tenant_id),
            "equipo_conectados": equipo_conectados,
            "portal_users": portal_users,
            "conversations": conversations,
        })


# ---- Guardar cambios (una ruta POST por pestaña) ------------------------

@router.post("/admin/clientes/{tenant_id}/general")
async def client_save_general(
    tenant_id: str,
    name: str = Form(...),
    sector: str = Form(""),
    plan: str = Form("Básico"),
    kind: str = Form("contracted"),
    contact_name: str = Form(""),
    contact_email: str = Form(""),
    phone_display: str = Form(""),
    calendar_id: str = Form("primary"),
    timezone: str = Form("Europe/Madrid"),
    language: str = Form("Español"),
    uid: int = Depends(auth.current_user_id),
):
    with Session(db_module.engine) as s:
        t = s.get(db_module.Tenant, tenant_id)
        if t is None:
            raise HTTPException(404)
        prev_voice_prompt = _render_voice_prompt_safe(t)
        t.name = name
        t.sector = sector
        t.plan = plan
        if kind in ("lead", "contracted"):
            t.kind = kind
        t.contact_name = contact_name
        t.contact_email = contact_email
        t.phone_display = phone_display
        t.calendar_id = calendar_id or "primary"
        t.timezone = timezone
        t.language = language
        _refresh_voice_prompt_if_autogenerated(t, prev_voice_prompt)
        s.commit()
    return RedirectResponse(url=f"/admin/clientes/{tenant_id}/general", status_code=303)


# ---- Accesos al portal del cliente (tenant_users) -----------------------

@router.post("/admin/clientes/{tenant_id}/portal_access")
async def client_portal_access_upsert(
    tenant_id: str,
    user_id: str = Form(""),            # vacío = crear nuevo; con valor = editar ese
    email: str = Form(...),
    password: str = Form(""),           # opcional al editar
    role: str = Form("owner"),
    nombre: str = Form(""),
    uid: int = Depends(auth.current_user_id),
):
    email_norm = (email or "").strip().lower()
    if not email_norm:
        raise HTTPException(400, "El email es obligatorio.")
    if role not in ("owner", "manager", "readonly"):
        role = "manager"

    with Session(db_module.engine) as s:
        if s.get(db_module.Tenant, tenant_id) is None:
            raise HTTPException(404, "tenant no encontrado")

        target = None
        if user_id:
            try:
                target = s.get(db_module.TenantUser, int(user_id))
            except ValueError:
                target = None
            if target is None or target.tenant_id != tenant_id:
                raise HTTPException(404, "usuario no encontrado")

        # Si el email cambió (o es nuevo), comprueba que no colisione con otro
        # usuario del mismo tenant.
        conflict = (
            s.query(db_module.TenantUser)
            .filter(
                db_module.TenantUser.tenant_id == tenant_id,
                db_module.TenantUser.email == email_norm,
            ).first()
        )
        if conflict is not None and (target is None or conflict.id != target.id):
            raise HTTPException(400, f"Ya hay otro usuario con email {email_norm} en este cliente.")

        if target is None:
            # Crear — la contraseña es obligatoria
            if len(password) < 8:
                raise HTTPException(400, "Contraseña mínimo 8 caracteres para crear un usuario nuevo.")
            from passlib.hash import bcrypt
            target = db_module.TenantUser(
                tenant_id=tenant_id,
                email=email_norm,
                password_hash=bcrypt.hash(password),
                nombre=nombre or "",
                role=role,
            )
            s.add(target)
        else:
            # Editar — contraseña opcional
            target.email = email_norm
            target.nombre = nombre or target.nombre
            target.role = role
            if password:
                if len(password) < 8:
                    raise HTTPException(400, "Contraseña mínimo 8 caracteres.")
                from passlib.hash import bcrypt
                target.password_hash = bcrypt.hash(password)
        s.commit()
    return RedirectResponse(url=f"/admin/clientes/{tenant_id}/general", status_code=303)


@router.post("/admin/clientes/{tenant_id}/portal_access/{user_id}/delete")
async def client_portal_access_delete(
    tenant_id: str,
    user_id: int,
    uid: int = Depends(auth.current_user_id),
):
    with Session(db_module.engine) as s:
        row = s.get(db_module.TenantUser, user_id)
        if row is None or row.tenant_id != tenant_id:
            raise HTTPException(404, "usuario no encontrado")
        # Proteger al último owner del tenant
        if row.role == "owner":
            n_owners = (
                s.query(db_module.TenantUser)
                .filter(
                    db_module.TenantUser.tenant_id == tenant_id,
                    db_module.TenantUser.role == "owner",
                ).count()
            )
            if n_owners <= 1:
                raise HTTPException(400, "No puedes quitar al último propietario. Crea otro antes.")
        s.delete(row)
        s.commit()
    return RedirectResponse(url=f"/admin/clientes/{tenant_id}/general", status_code=303)


@router.post("/admin/clientes/{tenant_id}/servicios")
async def client_save_services(
    request: Request, tenant_id: str,
    uid: int = Depends(auth.current_user_id),
):
    form = await request.form()
    # Campos vienen como listas: nombre[], duracion[], precio[]
    names = form.getlist("nombre")
    durs = form.getlist("duracion_min")
    prices = form.getlist("precio")

    with Session(db_module.engine) as s:
        t = s.get(db_module.Tenant, tenant_id)
        if t is None:
            raise HTTPException(404)
        prev_voice_prompt = _render_voice_prompt_safe(t)
        t.services.clear()
        s.flush()
        for i, n in enumerate(names):
            if not n or not n.strip():
                continue
            try:
                d = int(durs[i]) if i < len(durs) else 30
                p = float(prices[i]) if i < len(prices) else 0.0
            except ValueError:
                continue
            t.services.append(db_module.Service(nombre=n.strip(), duracion_min=d, precio=p, orden=i))
        _refresh_voice_prompt_if_autogenerated(t, prev_voice_prompt)
        s.commit()
    return RedirectResponse(url=f"/admin/clientes/{tenant_id}/servicios", status_code=303)


@router.post("/admin/clientes/{tenant_id}/horarios")
async def client_save_schedule(
    request: Request, tenant_id: str,
    uid: int = Depends(auth.current_user_id),
):
    """Guarda el horario admitiendo múltiples franjas por día.

    Formato del form:
      - `{day}_enabled` = "on" si el día está abierto.
      - `{day}_open` y `{day}_close` son LISTAS paralelas (usar getlist): una
        entrada por franja, ordenadas. UI recorta/valida antes de enviar.

    Se descartan franjas con open/close vacíos o inválidos. Si después de
    limpiar no queda ninguna franja para un día abierto, el día cae a
    ["closed"].
    """
    form = await request.form()
    hours: dict[str, list[str]] = {}
    for day in ("mon", "tue", "wed", "thu", "fri", "sat", "sun"):
        enabled = form.get(f"{day}_enabled") == "on"
        if not enabled:
            hours[day] = ["closed"]
            continue
        opens = [x.strip() for x in form.getlist(f"{day}_open")]
        closes = [x.strip() for x in form.getlist(f"{day}_close")]
        flat: list[str] = []
        for o, c in zip(opens, closes):
            if not o or not c:
                continue
            # Validación mínima de formato HH:MM — si es inválido, salta.
            try:
                oh, om = o.split(":"); ch, cm = c.split(":")
                int(oh); int(om); int(ch); int(cm)
            except ValueError:
                continue
            if o >= c:  # ignora rangos no crecientes
                continue
            flat.extend([o, c])
        hours[day] = flat if flat else ["closed"]

    with Session(db_module.engine) as s:
        t = s.get(db_module.Tenant, tenant_id)
        if t is None:
            raise HTTPException(404)
        prev_voice_prompt = _render_voice_prompt_safe(t)
        t.business_hours = hours
        _refresh_voice_prompt_if_autogenerated(t, prev_voice_prompt)
        s.commit()
    return RedirectResponse(url=f"/admin/clientes/{tenant_id}/horarios", status_code=303)


@router.get("/admin/clientes/{tenant_id}/equipo/{member_id}/calendars")
async def member_list_calendars(
    tenant_id: str, member_id: int,
    uid: int = Depends(auth.current_user_id),
):
    """JSON: calendarios accesibles desde el token OAuth del miembro.

    La UI llama aquí al abrir la fila del miembro para poblar el desplegable.
    Devuelve 400 si el miembro no está conectado todavía.
    """
    from fastapi.responses import JSONResponse
    svc = _member_google_service(tenant_id, member_id)
    try:
        res = svc.calendarList().list(maxResults=250, showHidden=False).execute()
    except Exception as e:
        raise HTTPException(502, f"Google Calendar: {e}")
    items = []
    for c in res.get("items", []):
        items.append({
            "id": c.get("id"),
            "summary": c.get("summary") or "(sin nombre)",
            "primary": bool(c.get("primary")),
            "accessRole": c.get("accessRole"),
        })
    # Primario arriba, resto por nombre.
    items.sort(key=lambda x: (0 if x["primary"] else 1, x["summary"].lower()))
    return JSONResponse({"calendars": items, "connected": True})


@router.post("/admin/clientes/{tenant_id}/equipo/{member_id}/calendars/create")
async def member_create_calendar(
    tenant_id: str, member_id: int,
    summary: str = Form(""),
    uid: int = Depends(auth.current_user_id),
):
    """Crea un calendario nuevo en la cuenta del miembro y lo asigna a él.

    Por defecto el summary es "Trabajo — <nombre_miembro>" si no se pasa uno.
    Devuelve el id del calendario recién creado (ya guardado en
    MiembroEquipo.calendar_id).
    """
    from fastapi.responses import JSONResponse
    summary = (summary or "").strip()

    with Session(db_module.engine) as s:
        m = s.get(db_module.MiembroEquipo, member_id)
        if m is None or m.tenant_id != tenant_id:
            raise HTTPException(404, "Miembro no encontrado en este tenant")
        if not summary:
            summary = f"Trabajo — {m.nombre or 'Miembro'}"

        svc = _member_google_service(tenant_id, member_id)
        try:
            body = {"summary": summary, "timeZone": "Europe/Madrid"}
            created = svc.calendars().insert(body=body).execute()
        except Exception as e:
            raise HTTPException(502, f"Google Calendar: {e}")

        cal_id = created.get("id") or ""
        m.calendar_id = cal_id
        s.commit()

    return JSONResponse({
        "ok": True,
        "id": cal_id,
        "summary": created.get("summary", summary),
    })


@router.post("/admin/clientes/{tenant_id}/equipo/{member_id}/disconnect")
async def member_disconnect_google(
    tenant_id: str, member_id: int,
    uid: int = Depends(auth.current_user_id),
):
    """Borra el token del miembro (no revoca en Google, solo olvida local)."""
    path = _member_token_path(tenant_id, member_id)
    try:
        if path.exists():
            path.unlink()
    except Exception:  # pragma: no cover
        pass
    return RedirectResponse(url=f"/admin/clientes/{tenant_id}/equipo", status_code=303)


@router.post("/admin/clientes/{tenant_id}/equipo")
async def client_save_equipo(
    request: Request, tenant_id: str,
    uid: int = Depends(auth.current_user_id),
):
    """Reescribe el equipo del tenant de forma atómica.

    El form envía listas paralelas `nombre[]` y `calendar_id[]` (tantas como
    miembros haya en la UI), más un campo `dias_trabajo_{i}` por cada índice
    con los días seleccionados (0-6). Si el nombre está vacío la fila se
    descarta — así se "quita" un miembro sin botón extra.
    """
    form = await request.form()
    nombres = form.getlist("nombre")
    calendar_ids = form.getlist("calendar_id")

    with Session(db_module.engine) as s:
        t = s.get(db_module.Tenant, tenant_id)
        if t is None:
            raise HTTPException(404)
        prev_voice_prompt = _render_voice_prompt_safe(t)
        # Reemplazo completo: borramos y reinsertamos. Es sencillo y coherente
        # con cómo tab_services maneja el catálogo.
        t.equipo.clear()
        s.flush()
        for i, nombre in enumerate(nombres):
            nombre = (nombre or "").strip()
            if not nombre:
                continue
            calendar_id = (calendar_ids[i] if i < len(calendar_ids) else "").strip()
            dias_raw = form.getlist(f"dias_trabajo_{i}")
            try:
                dias = [int(d) for d in dias_raw if str(d).isdigit()]
            except ValueError:
                dias = [0, 1, 2, 3, 4, 5]
            row = db_module.MiembroEquipo(
                tenant_id=tenant_id,
                nombre=nombre,
                calendar_id=calendar_id,
                orden=i,
            )
            row.dias_trabajo = dias
            t.equipo.append(row)
        _refresh_voice_prompt_if_autogenerated(t, prev_voice_prompt)
        s.commit()
    return RedirectResponse(url=f"/admin/clientes/{tenant_id}/equipo", status_code=303)


@router.post("/admin/clientes/{tenant_id}/personalizacion")
async def client_save_personalization(
    tenant_id: str,
    assistant_name: str = Form("Asistente"),
    assistant_tone: str = Form("cercano"),
    assistant_formality: str = Form("tu"),
    assistant_emoji: Optional[str] = Form(None),
    assistant_greeting: str = Form(""),
    assistant_fallback_phone: str = Form(""),
    assistant_rules: str = Form(""),
    system_prompt_override: str = Form(""),
    uid: int = Depends(auth.current_user_id),
):
    # rules viene como textarea; una regla por línea (no vacía)
    rules = [r.strip() for r in (assistant_rules or "").splitlines() if r.strip()]
    with Session(db_module.engine) as s:
        t = s.get(db_module.Tenant, tenant_id)
        if t is None:
            raise HTTPException(404)
        prev_voice_prompt = _render_voice_prompt_safe(t)
        t.assistant_name = assistant_name
        t.assistant_tone = assistant_tone
        t.assistant_formality = assistant_formality
        t.assistant_emoji = bool(assistant_emoji)
        t.assistant_greeting = assistant_greeting
        t.assistant_fallback_phone = assistant_fallback_phone
        t.assistant_rules = rules
        t.system_prompt_override = system_prompt_override
        _refresh_voice_prompt_if_autogenerated(t, prev_voice_prompt)
        s.commit()
    return RedirectResponse(url=f"/admin/clientes/{tenant_id}/personalizacion", status_code=303)


# --------------------------------------------------------------------------
#  VOZ  — edición y sincronización del agente ElevenLabs
# --------------------------------------------------------------------------

def _save_voice_fields(
    s: Session, t: db_module.Tenant, *,
    voice_agent_id: str,
    voice_prompt: str,
    voice_voice_id: str,
    voice_stability: float,
    voice_similarity_boost: float,
    voice_speed: float,
) -> None:
    """Escribe los campos de voz en el tenant. No sincroniza con ElevenLabs."""
    t.voice_agent_id = (voice_agent_id or "").strip()
    t.voice_prompt = voice_prompt or ""
    t.voice_voice_id = (voice_voice_id or "").strip()
    # Clamps defensivos por si el navegador envía algo fuera de rango.
    t.voice_stability = max(0.0, min(1.0, float(voice_stability or 0.0)))
    t.voice_similarity_boost = max(0.0, min(1.0, float(voice_similarity_boost or 0.0)))
    t.voice_speed = max(0.5, min(1.5, float(voice_speed or 1.0)))
    s.commit()


@router.post("/admin/clientes/{tenant_id}/voz")
async def client_save_voice(
    tenant_id: str,
    voice_agent_id: str = Form(""),
    voice_prompt: str = Form(""),
    voice_voice_id: str = Form(""),
    voice_stability: float = Form(0.67),
    voice_similarity_boost: float = Form(0.8),
    voice_speed: float = Form(1.04),
    uid: int = Depends(auth.current_user_id),
):
    """Guarda los campos de voz en la BD. No toca ElevenLabs."""
    with Session(db_module.engine) as s:
        t = s.get(db_module.Tenant, tenant_id)
        if t is None:
            raise HTTPException(404)
        _save_voice_fields(
            s, t,
            voice_agent_id=voice_agent_id,
            voice_prompt=voice_prompt,
            voice_voice_id=voice_voice_id,
            voice_stability=voice_stability,
            voice_similarity_boost=voice_similarity_boost,
            voice_speed=voice_speed,
        )
    return RedirectResponse(
        url=f"/admin/clientes/{tenant_id}/voz?saved=1", status_code=303,
    )


@router.post("/admin/clientes/{tenant_id}/voz/create_agent")
async def client_create_voice_agent(
    tenant_id: str,
    request: Request,
    uid: int = Depends(auth.current_user_id),
):
    """Crea un agente nuevo en ElevenLabs asociado a este tenant y guarda su id.

    No toca el `voice_prompt` ni los parámetros TTS: parte de lo que ya hay en
    BD (o de los defaults sembrados al abrir la pestaña). Tras la creación, el
    usuario todavía puede editar el prompt/voz y pulsar Sincronizar.
    """
    from datetime import datetime as _dt
    from urllib.parse import quote

    # La URL pública para registrar las tools = host desde el que nos
    # llamaron. En Railway esto vale https://<dominio>.up.railway.app.
    # Si quieres forzar otra (ngrok, dominio custom), define TOOL_BASE_URL.
    tool_base_url = os.getenv("TOOL_BASE_URL", "").strip() or str(request.base_url).rstrip("/")

    with Session(db_module.engine) as s:
        t = s.get(db_module.Tenant, tenant_id)
        if t is None:
            raise HTTPException(404)

        if (t.voice_agent_id or "").strip():
            # Ya tiene agente — no creamos uno nuevo sin permiso. El usuario
            # puede limpiar el campo desde el form y volver a pulsar.
            msg = "Este tenant ya tiene agent_id. Vacíalo antes de crear uno nuevo."
            t.voice_last_sync_at = _dt.utcnow()
            t.voice_last_sync_status = msg
            s.commit()
            return RedirectResponse(
                url=f"/admin/clientes/{tenant_id}/voz?sync=err&msg={quote(msg)}",
                status_code=303,
            )

        # Asegura que hay prompt y voice_id (sembrando si hace falta)
        _seed_voice_defaults_if_empty(s, t)

        try:
            agent_id = elevenlabs_client.create_agent_for_tenant(
                tenant=t.to_dict(),
                tool_base_url=tool_base_url,
                prompt=t.voice_prompt,
                voice=elevenlabs_client.VoiceParams(
                    voice_id=t.voice_voice_id,
                    stability=t.voice_stability,
                    similarity_boost=t.voice_similarity_boost,
                    speed=t.voice_speed,
                ),
            )
            t.voice_agent_id = agent_id
            t.voice_last_sync_at = _dt.utcnow()
            t.voice_last_sync_status = "ok"
            s.commit()
            return RedirectResponse(
                url=f"/admin/clientes/{tenant_id}/voz?sync=ok",
                status_code=303,
            )
        except elevenlabs_client.ElevenLabsError as e:
            msg = str(e)[:380]
            t.voice_last_sync_at = _dt.utcnow()
            t.voice_last_sync_status = msg
            s.commit()
            return RedirectResponse(
                url=f"/admin/clientes/{tenant_id}/voz?sync=err&msg={quote(msg)}",
                status_code=303,
            )


@router.post("/admin/clientes/{tenant_id}/voz/regenerate")
async def client_regenerate_voice(
    tenant_id: str,
    sync_now: str = Form("0"),
    uid: int = Depends(auth.current_user_id),
):
    """Regenera el prompt desde los datos del tenant y opcionalmente lo sincroniza.

    Sirve para corregir prompts que se quedaron viejos respecto a servicios,
    horarios, equipo o fallback phone sin tener que reescribirlos a mano.
    """
    from datetime import datetime as _dt
    from urllib.parse import quote

    with Session(db_module.engine) as s:
        t = s.get(db_module.Tenant, tenant_id)
        if t is None:
            raise HTTPException(404)
        t.voice_prompt = _render_voice_prompt_safe(t)
        if not (t.voice_voice_id or "").strip():
            t.voice_voice_id = _DEFAULT_VOICE_ID
        s.commit()

        if sync_now == "1":
            agent_id = (t.voice_agent_id or os.getenv("ELEVENLABS_AGENT_ID", "")).strip()
            if not agent_id:
                msg = "Sin voice_agent_id en el tenant ni ELEVENLABS_AGENT_ID global."
                t.voice_last_sync_at = _dt.utcnow()
                t.voice_last_sync_status = msg
                s.commit()
                return RedirectResponse(
                    url=f"/admin/clientes/{tenant_id}/voz?sync=err&msg={quote(msg)}",
                    status_code=303,
                )
            try:
                elevenlabs_client.sync_agent(
                    agent_id,
                    prompt=t.voice_prompt,
                    voice=elevenlabs_client.VoiceParams(
                        voice_id=t.voice_voice_id,
                        stability=t.voice_stability,
                        similarity_boost=t.voice_similarity_boost,
                        speed=t.voice_speed,
                    ),
                )
                t.voice_last_sync_at = _dt.utcnow()
                t.voice_last_sync_status = "ok"
                s.commit()
                return RedirectResponse(
                    url=f"/admin/clientes/{tenant_id}/voz?sync=ok",
                    status_code=303,
                )
            except elevenlabs_client.ElevenLabsError as e:
                msg = str(e)[:380]
                t.voice_last_sync_at = _dt.utcnow()
                t.voice_last_sync_status = msg
                s.commit()
                return RedirectResponse(
                    url=f"/admin/clientes/{tenant_id}/voz?sync=err&msg={quote(msg)}",
                    status_code=303,
                )

    return RedirectResponse(
        url=f"/admin/clientes/{tenant_id}/voz?saved=1",
        status_code=303,
    )


@router.post("/admin/clientes/{tenant_id}/voz/sync")
async def client_sync_voice(
    tenant_id: str,
    voice_agent_id: str = Form(""),
    voice_prompt: str = Form(""),
    voice_voice_id: str = Form(""),
    voice_stability: float = Form(0.67),
    voice_similarity_boost: float = Form(0.8),
    voice_speed: float = Form(1.04),
    uid: int = Depends(auth.current_user_id),
):
    """Guarda + envía PATCH al agente remoto en ElevenLabs.

    La estrategia es guardar primero (así el usuario no pierde sus cambios si
    ElevenLabs falla) y solo después intentar la sincronización. El resultado
    (ok o mensaje de error) queda persistido en voice_last_sync_* y se muestra
    en la UI vía query params.
    """
    from datetime import datetime as _dt

    with Session(db_module.engine) as s:
        t = s.get(db_module.Tenant, tenant_id)
        if t is None:
            raise HTTPException(404)
        _save_voice_fields(
            s, t,
            voice_agent_id=voice_agent_id,
            voice_prompt=voice_prompt,
            voice_voice_id=voice_voice_id,
            voice_stability=voice_stability,
            voice_similarity_boost=voice_similarity_boost,
            voice_speed=voice_speed,
        )

        # Intento de sincronización. Capturamos cualquier error del cliente
        # ElevenLabs (HTTP, red, validación) para presentarlo al usuario sin
        # reventar la request.
        try:
            elevenlabs_client.sync_agent(
                t.voice_agent_id,
                prompt=t.voice_prompt,
                voice=elevenlabs_client.VoiceParams(
                    voice_id=t.voice_voice_id,
                    stability=t.voice_stability,
                    similarity_boost=t.voice_similarity_boost,
                    speed=t.voice_speed,
                ),
            )
            t.voice_last_sync_at = _dt.utcnow()
            t.voice_last_sync_status = "ok"
            s.commit()
            return RedirectResponse(
                url=f"/admin/clientes/{tenant_id}/voz?sync=ok",
                status_code=303,
            )
        except elevenlabs_client.ElevenLabsError as e:
            msg = str(e)[:380]
            t.voice_last_sync_at = _dt.utcnow()
            t.voice_last_sync_status = msg
            s.commit()
        except Exception as e:  # pragma: no cover - red bajo control del cliente
            msg = f"Error inesperado: {e!r}"[:380]
            t.voice_last_sync_at = _dt.utcnow()
            t.voice_last_sync_status = msg
            s.commit()

    # URL-encodear el mensaje de error para que pase limpio por la query string.
    from urllib.parse import quote
    return RedirectResponse(
        url=f"/admin/clientes/{tenant_id}/voz?sync=err&msg={quote(msg)}",
        status_code=303,
    )


@router.post("/admin/clientes/{tenant_id}/toggle")
async def client_toggle(tenant_id: str, uid: int = Depends(auth.current_user_id)):
    with Session(db_module.engine) as s:
        t = s.get(db_module.Tenant, tenant_id)
        if t is None:
            raise HTTPException(404)
        t.status = "paused" if t.status == "active" else "active"
        s.commit()
    return RedirectResponse(url=f"/admin/clientes/{tenant_id}/general", status_code=303)


@router.post("/admin/clientes/{tenant_id}/delete")
async def client_delete(tenant_id: str, uid: int = Depends(auth.current_user_id)):
    with Session(db_module.engine) as s:
        t = s.get(db_module.Tenant, tenant_id)
        if t is None:
            raise HTTPException(404)
        s.delete(t)
        s.commit()
    return RedirectResponse(url="/admin/clientes", status_code=303)


# ==========================================================================
#  FACTURACIÓN
# ==========================================================================

PLAN_PRICES = {"Básico": 29.0, "Profesional": 49.0, "Premium": 99.0}


@router.get("/admin/facturacion", response_class=HTMLResponse)
async def billing(request: Request, uid: int = Depends(auth.current_user_id)):
    with Session(db_module.engine) as s:
        tenants = s.query(db_module.Tenant).order_by(db_module.Tenant.name).all()
        t30 = _since_30d()

        total_cost = s.query(func.coalesce(func.sum(db_module.TokenUsage.cost_eur), 0.0)).filter(
            db_module.TokenUsage.created_at >= t30
        ).scalar() or 0.0
        total_tokens = s.query(func.coalesce(func.sum(db_module.TokenUsage.input_tokens + db_module.TokenUsage.output_tokens), 0)).filter(
            db_module.TokenUsage.created_at >= t30
        ).scalar() or 0

        rows = []
        for t in tenants:
            tokens = s.query(func.coalesce(func.sum(db_module.TokenUsage.input_tokens + db_module.TokenUsage.output_tokens), 0)).filter(
                db_module.TokenUsage.tenant_id == t.id, db_module.TokenUsage.created_at >= t30
            ).scalar() or 0
            cost = s.query(func.coalesce(func.sum(db_module.TokenUsage.cost_eur), 0.0)).filter(
                db_module.TokenUsage.tenant_id == t.id, db_module.TokenUsage.created_at >= t30
            ).scalar() or 0.0
            plan_price = PLAN_PRICES.get(t.plan, 29.0)
            rows.append({
                "t": t,
                "tokens": int(tokens),
                "cost": float(cost),
                "plan_price": plan_price,
                "margin": plan_price - float(cost),
            })

    return templates.TemplateResponse("billing.html", {
        "request": request,
        "user_email": auth.current_user_email(uid),
        "active": "facturacion",
        "rows": rows,
        "total_cost": float(total_cost),
        "total_tokens": int(total_tokens),
    })


# ==========================================================================
#  AJUSTES
# ==========================================================================

import os


@router.get("/admin/ajustes", response_class=HTMLResponse)
async def settings_view(request: Request, uid: int = Depends(auth.current_user_id)):
    def _mask(v: str) -> str:
        if not v:
            return ""
        if len(v) <= 8:
            return "•" * len(v)
        return v[:4] + "•" * 20 + v[-4:]

    keys = {
        "OPENAI_API_KEY":       _mask(os.getenv("OPENAI_API_KEY", "")),
        "ANTHROPIC_API_KEY":    _mask(os.getenv("ANTHROPIC_API_KEY", "")),
        "ELEVENLABS_API_KEY":   _mask(os.getenv("ELEVENLABS_API_KEY", "")),
        "TOOL_SECRET":          _mask(os.getenv("TOOL_SECRET", "")),
        "GOOGLE_CLIENT_SECRET": _mask(os.getenv("GOOGLE_CLIENT_SECRET", "")),
    }

    return templates.TemplateResponse("settings.html", {
        "request": request,
        "user_email": auth.current_user_email(uid),
        "active": "ajustes",
        "keys": keys,
        "model": os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
        "timezone": os.getenv("DEFAULT_TIMEZONE", "Europe/Madrid"),
    })


# ==========================================================================
#  RESERVAS (vista agregada desde Google Calendar)
# ==========================================================================

@router.get("/admin/reservas", response_class=HTMLResponse)
async def bookings_view(request: Request, uid: int = Depends(auth.current_user_id)):
    bookings, warnings = _load_all_bookings()
    stats = {
        "total": len(bookings),
        "voz": sum(1 for b in bookings if b["canal"] == "voz"),
        "manual": sum(1 for b in bookings if b["canal"] != "voz"),
        "canceladas": sum(1 for b in bookings if b["estado"] == "cancelada"),
    }
    return templates.TemplateResponse("bookings.html", {
        "request": request,
        "user_email": auth.current_user_email(uid),
        "active": "reservas",
        "bookings": bookings,
        "warnings": warnings,
        "stats": stats,
    })


# ==========================================================================
#  LEADS  (capturados desde la landing pública)
# ==========================================================================

_STATUS_LABELS = {
    "new":       "Nuevo",
    "contacted": "Contactado",
    "qualified": "Cualificado",
    "converted": "Convertido",
    "lost":      "Perdido",
}


@router.get("/admin/leads", response_class=HTMLResponse)
async def leads_view(
    request: Request,
    status: Optional[str] = None,
    uid: int = Depends(auth.current_user_id),
):
    with Session(db_module.engine) as s:
        q = s.query(db_module.Lead)
        if status and status in _STATUS_LABELS:
            q = q.filter(db_module.Lead.status == status)
        leads = q.order_by(db_module.Lead.created_at.desc()).limit(500).all()

        # KPIs
        total = s.query(func.count(db_module.Lead.id)).scalar() or 0
        new_count = s.query(func.count(db_module.Lead.id)).filter(
            db_module.Lead.status == "new"
        ).scalar() or 0
        last_7d = s.query(func.count(db_module.Lead.id)).filter(
            db_module.Lead.created_at >= datetime.utcnow() - timedelta(days=7)
        ).scalar() or 0

    return templates.TemplateResponse("leads.html", {
        "request": request,
        "user_email": auth.current_user_email(uid),
        "active": "leads",
        "leads": leads,
        "status_filter": status,
        "status_labels": _STATUS_LABELS,
        "total_leads": total,
        "new_leads": new_count,
        "last_7d": last_7d,
    })


@router.post("/admin/leads/{lead_id}/status")
async def leads_update_status(
    lead_id: int,
    status: str = Form(...),
    uid: int = Depends(auth.current_user_id),
):
    if status not in _STATUS_LABELS:
        raise HTTPException(400, "Estado inválido")
    with Session(db_module.engine) as s:
        lead = s.get(db_module.Lead, lead_id)
        if lead is None:
            raise HTTPException(404)
        lead.status = status
        s.commit()
    return RedirectResponse(url="/admin/leads", status_code=303)


@router.post("/admin/leads/{lead_id}/delete")
async def leads_delete(lead_id: int, uid: int = Depends(auth.current_user_id)):
    with Session(db_module.engine) as s:
        lead = s.get(db_module.Lead, lead_id)
        if lead is None:
            raise HTTPException(404)
        s.delete(lead)
        s.commit()
    return RedirectResponse(url="/admin/leads", status_code=303)
