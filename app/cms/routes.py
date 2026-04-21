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
    GET  /admin/clientes/{id}/{tab}           Detalle (tab = general|servicios|horarios|personalizacion|conversaciones|metricas)
    POST /admin/clientes/{id}/general         Guardar datos generales
    POST /admin/clientes/{id}/servicios       Guardar servicios
    POST /admin/clientes/{id}/horarios        Guardar horarios
    POST /admin/clientes/{id}/personalizacion Guardar personalización del bot
    POST /admin/clientes/{id}/delete          Borrar tenant
    POST /admin/clientes/{id}/toggle          Pausar/activar

    GET  /admin/conversaciones                Bandeja global
    GET  /admin/conversaciones/{tenant}/{phone} Vista chat (JSON)

    GET  /admin/reservas                      Lista (placeholder)
    GET  /admin/facturacion                   Desglose coste por cliente
    GET  /admin/ajustes                       Ajustes globales
"""
from __future__ import annotations

import json
import pathlib
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Request, Form, Depends, HTTPException, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import db as db_module
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

# Static
router.mount("/admin/static", StaticFiles(directory=str(_BASE / "static")), name="cms_static")


# ==========================================================================
#  HELPERS
# ==========================================================================

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

    # conversaciones distintas en 30d (1 conversación = 1 customer_phone)
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
        d = (datetime.utcnow() - timedelta(days=29 - i)).date().isoformat()
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


def _delta_pct(curr, prev) -> int:
    if not prev:
        return 0
    return round(((curr - prev) / prev) * 100)


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
            d = (datetime.utcnow() - timedelta(days=29 - i)).date().isoformat()
            global_series.append(series_map.get(d, 0))

        # Últimas conversaciones (5)
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
#  CLIENTES — LISTA
# ==========================================================================

@router.get("/admin/clientes", response_class=HTMLResponse)
async def clients_list(request: Request, uid: int = Depends(auth.current_user_id)):
    with Session(db_module.engine) as s:
        tenants = s.query(db_module.Tenant).order_by(db_module.Tenant.name).all()
        rows = []
        for t in tenants:
            m = _metrics_for_tenant(s, t.id)
            rows.append({
                "t": t,
                "m": m,
                "delta_tokens": _delta_pct(m["tokens_30d"], m["tokens_prev"]),
            })
    return templates.TemplateResponse("clients_list.html", {
        "request": request,
        "user_email": auth.current_user_email(uid),
        "active": "clientes",
        "rows": rows,
    })


# ==========================================================================
#  CLIENTE — NUEVO
# ==========================================================================

@router.get("/admin/clientes/new", response_class=HTMLResponse)
async def client_new(request: Request, uid: int = Depends(auth.current_user_id)):
    # Esqueleto de tenant vacío (no persistido hasta que haga POST)
    empty = db_module.Tenant(
        id="", name="", sector="", status="active", plan="Básico",
        phone_number_id="", phone_display="", calendar_id="primary",
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
    phone_number_id: str = Form(""),
    phone_display: str = Form(""),
    calendar_id: str = Form("primary"),
    timezone: str = Form("Europe/Madrid"),
    language: str = Form("Español"),
    uid: int = Depends(auth.current_user_id),
):
    tid = (id or "").strip().lower().replace(" ", "_")
    if not tid or not name:
        raise HTTPException(400, "id y nombre son obligatorios")
    with Session(db_module.engine) as s:
        if s.get(db_module.Tenant, tid) is not None:
            raise HTTPException(400, f"Ya existe un cliente con id={tid}")
        t = db_module.Tenant(
            id=tid, name=name, sector=sector, plan=plan,
            contact_name=contact_name, contact_email=contact_email,
            phone_number_id=phone_number_id, phone_display=phone_display,
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
        s.commit()
    return RedirectResponse(url=f"/admin/clientes/{tid}/general", status_code=303)


# ==========================================================================
#  CLIENTE — DETALLE (por pestaña)
# ==========================================================================

@router.get("/admin/clientes/{tenant_id}", response_class=HTMLResponse)
async def client_detail_root(tenant_id: str, uid: int = Depends(auth.current_user_id)):
    return RedirectResponse(url=f"/admin/clientes/{tenant_id}/general", status_code=303)


@router.get("/admin/clientes/{tenant_id}/{tab}", response_class=HTMLResponse)
async def client_detail(
    tenant_id: str, tab: str, request: Request,
    uid: int = Depends(auth.current_user_id),
):
    if tab not in ("general", "servicios", "horarios", "personalizacion", "conversaciones", "metricas"):
        raise HTTPException(404, "Pestaña desconocida")

    with Session(db_module.engine) as s:
        t = s.get(db_module.Tenant, tenant_id)
        if t is None:
            raise HTTPException(404, "Cliente no encontrado")

        metrics = _metrics_for_tenant(s, tenant_id) if tab in ("metricas", "general", "conversaciones") else None

        conversations = []
        if tab == "conversaciones":
            rows = s.execute(
                select(
                    db_module.Message.customer_phone,
                    func.max(db_module.Message.created_at).label("last_at"),
                    func.count(db_module.Message.id).label("n_msg"),
                )
                .where(db_module.Message.tenant_id == tenant_id)
                .group_by(db_module.Message.customer_phone)
                .order_by(func.max(db_module.Message.created_at).desc())
                .limit(50)
            ).all()
            for row in rows:
                phone, last_at, n_msg = row[0], row[1], row[2]
                last_msg = s.query(db_module.Message).filter(
                    db_module.Message.tenant_id == tenant_id,
                    db_module.Message.customer_phone == phone,
                ).order_by(db_module.Message.created_at.desc()).first()
                conversations.append({
                    "phone": phone,
                    "last_at": last_at,
                    "last_text": (last_msg.content[:120] + "…") if last_msg and len(last_msg.content) > 120 else (last_msg.content if last_msg else ""),
                    "n_messages": n_msg,
                })

        # prompt preview
        prompt_preview = db_module.render_system_prompt(t)

        # Expandir el tenant — el template sigue usando el objeto SQLAlchemy
        # pero necesitamos la lista de servicios también pre-cargada para Jinja.
        _ = t.services  # fuerza carga

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
            "conversations": conversations,
            "prompt_preview": prompt_preview,
        })


# ---- Guardar cambios (una ruta POST por pestaña) ------------------------

@router.post("/admin/clientes/{tenant_id}/general")
async def client_save_general(
    tenant_id: str,
    name: str = Form(...),
    sector: str = Form(""),
    plan: str = Form("Básico"),
    contact_name: str = Form(""),
    contact_email: str = Form(""),
    phone_number_id: str = Form(""),
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
        t.name = name
        t.sector = sector
        t.plan = plan
        t.contact_name = contact_name
        t.contact_email = contact_email
        t.phone_number_id = phone_number_id
        t.phone_display = phone_display
        t.calendar_id = calendar_id or "primary"
        t.timezone = timezone
        t.language = language
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
        s.commit()
    return RedirectResponse(url=f"/admin/clientes/{tenant_id}/servicios", status_code=303)


@router.post("/admin/clientes/{tenant_id}/horarios")
async def client_save_schedule(
    request: Request, tenant_id: str,
    uid: int = Depends(auth.current_user_id),
):
    form = await request.form()
    hours = {}
    for day in ("mon","tue","wed","thu","fri","sat","sun"):
        open_ = form.get(f"{day}_open", "").strip()
        close_ = form.get(f"{day}_close", "").strip()
        enabled = form.get(f"{day}_enabled") == "on"
        if enabled and open_ and close_:
            hours[day] = [open_, close_]
        else:
            hours[day] = ["closed"]
    with Session(db_module.engine) as s:
        t = s.get(db_module.Tenant, tenant_id)
        if t is None:
            raise HTTPException(404)
        t.business_hours = hours
        s.commit()
    return RedirectResponse(url=f"/admin/clientes/{tenant_id}/horarios", status_code=303)


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
        t.assistant_name = assistant_name
        t.assistant_tone = assistant_tone
        t.assistant_formality = assistant_formality
        t.assistant_emoji = bool(assistant_emoji)
        t.assistant_greeting = assistant_greeting
        t.assistant_fallback_phone = assistant_fallback_phone
        t.assistant_rules = rules
        t.system_prompt_override = system_prompt_override
        s.commit()
    return RedirectResponse(url=f"/admin/clientes/{tenant_id}/personalizacion", status_code=303)


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
#  CONVERSACIONES (global)
# ==========================================================================

@router.get("/admin/conversaciones", response_class=HTMLResponse)
async def conversations_list(
    request: Request,
    tenant: Optional[str] = None,
    phone: Optional[str] = None,
    uid: int = Depends(auth.current_user_id),
):
    with Session(db_module.engine) as s:
        # Lista de conversaciones (group by tenant + phone)
        rows = s.execute(
            select(
                db_module.Message.tenant_id,
                db_module.Message.customer_phone,
                func.max(db_module.Message.created_at).label("last_at"),
                func.count(db_module.Message.id).label("n_msg"),
            ).group_by(db_module.Message.tenant_id, db_module.Message.customer_phone)
             .order_by(func.max(db_module.Message.created_at).desc())
             .limit(100)
        ).all()

        tenants_map = {t.id: t for t in s.query(db_module.Tenant).all()}
        convos = []
        for row in rows:
            # OJO: no reasignar `phone` porque es el query param de entrada.
            row_tid, row_phone, row_last = row[0], row[1], row[2]
            last_msg = s.query(db_module.Message).filter(
                db_module.Message.tenant_id == row_tid,
                db_module.Message.customer_phone == row_phone,
            ).order_by(db_module.Message.created_at.desc()).first()
            convos.append({
                "tenant": tenants_map.get(row_tid),
                "tenant_id": row_tid,
                "phone": row_phone,
                "last_at": row_last,
                "last_text": (last_msg.content if last_msg else ""),
            })

        # Conversación seleccionada (si se pasó tenant+phone)
        messages = []
        selected = None
        if tenant and phone:
            messages = s.query(db_module.Message).filter(
                db_module.Message.tenant_id == tenant,
                db_module.Message.customer_phone == phone,
            ).order_by(db_module.Message.created_at.asc()).all()
            selected = {"tenant": tenants_map.get(tenant), "phone": phone}
        elif convos:
            c0 = convos[0]
            messages = s.query(db_module.Message).filter(
                db_module.Message.tenant_id == c0["tenant_id"],
                db_module.Message.customer_phone == c0["phone"],
            ).order_by(db_module.Message.created_at.asc()).all()
            selected = {"tenant": c0["tenant"], "phone": c0["phone"]}

    return templates.TemplateResponse("conversations.html", {
        "request": request,
        "user_email": auth.current_user_email(uid),
        "active": "conversaciones",
        "convos": convos,
        "messages": messages,
        "selected": selected,
    })


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
        "WHATSAPP_APP_SECRET":  _mask(os.getenv("WHATSAPP_APP_SECRET", "")),
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
#  RESERVAS (placeholder hasta que tengamos tabla dedicada)
# ==========================================================================

@router.get("/admin/reservas", response_class=HTMLResponse)
async def bookings_view(request: Request, uid: int = Depends(auth.current_user_id)):
    return templates.TemplateResponse("bookings.html", {
        "request": request,
        "user_email": auth.current_user_email(uid),
        "active": "reservas",
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
