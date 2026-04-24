"""FastAPI app: landing + captura de leads + CMS + portal + tools ElevenLabs.

Nota: el producto ha virado a llamadas de voz únicamente (ElevenLabs
Conversational AI). El webhook de WhatsApp (Meta / Twilio) se eliminó en
2026-04. Las reservas entrantes se crean desde los server tools
`/tools/*` que llama ElevenLabs durante la llamada.

Adicionalmente exponemos un webhook de Telegram (`/telegram/webhook`) para
usar el mismo agente como bot de texto en pruebas / staging. No reemplaza a
voz, solo complementa.
"""
from __future__ import annotations

import logging
import pathlib
import re

from fastapi import FastAPI, Header, Request, Form
from fastapi.responses import HTMLResponse, JSONResponse

from .config import settings
from . import db
from . import eleven_tools
from . import oauth_web
from . import diag
from . import telegram as tg_module
from .cms import router as cms_router
from .cms.auth import ensure_admin_user
from .portal import router as portal_router
from .portal.routes import router_mounts as portal_mounts
from .portal.auth import ensure_portal_users

logging.basicConfig(
    level=settings.log_level,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
log = logging.getLogger("bot")

app = FastAPI(title="Bot reservas voz (ElevenLabs) + CMS")

# Bootstrap del usuario admin en arranque (si ADMIN_EMAIL + ADMIN_PASSWORD están
# definidos y no existe todavía). Si algo falla aquí (p.ej. versión de bcrypt
# incompatible) seguimos levantando el servidor para poder diagnosticar.
try:
    ensure_admin_user()
    log.info("Bootstrap admin: OK")
except Exception:
    log.exception("Bootstrap del admin falló — el servidor arranca igual, "
                  "pero el login del CMS no funcionará hasta que se arregle.")

# Bootstrap de usuarios del portal (1 owner por tenant contracted si el env
# PORTAL_BOOTSTRAP_PASSWORD está definido y no existe ningún usuario todavía).
try:
    ensure_portal_users()
except Exception:
    log.exception("Bootstrap del portal falló — /app seguirá disponible "
                  "pero habrá que crear cuentas a mano.")

# Monta el CMS bajo /admin (las rutas ya incluyen el prefijo).
app.include_router(cms_router)
# Portal del cliente (/app + /api/portal/*).
app.include_router(portal_router)
for mount_path, mount_app in portal_mounts:
    app.mount(mount_path, mount_app, name=f"portal_{mount_path.strip('/').replace('/', '_')}")
# Endpoints /tools/* que ElevenLabs Conversational AI llama como server tools
# durante las llamadas de voz (consultar disponibilidad, crear/mover/cancelar
# reserva). Protegidos por X-Tool-Secret.
app.include_router(eleven_tools.router)
# Flujo web OAuth de Google (/oauth/start y /oauth/callback). Necesario para
# autorizar un calendario desde el navegador en producción (Railway), en vez
# del `InstalledAppFlow` que sólo funciona en local.
app.include_router(oauth_web.router)
# Endpoints /_diag/* de mantenimiento (listar/crear calendarios, verificar ids).
# Protegidos con X-Tool-Secret igual que /tools/*.
app.include_router(diag.router)


# ---------- Landing pública ----------

_LANDING_PATH = pathlib.Path(__file__).parent / "templates" / "landing.html"
_LANDING_CACHE: str | None = None


def _landing_html() -> str:
    """Lee la landing una sola vez (cachea en memoria)."""
    global _LANDING_CACHE
    if _LANDING_CACHE is None:
        try:
            _LANDING_CACHE = _LANDING_PATH.read_text(encoding="utf-8")
        except FileNotFoundError:
            _LANDING_CACHE = (
                "<!doctype html><meta charset=utf-8>"
                "<title>Sprint</title>"
                "<h1>Sprint</h1><p>La landing aún no está desplegada.</p>"
            )
    return _LANDING_CACHE


@app.get("/", response_class=HTMLResponse)
async def landing() -> HTMLResponse:
    return HTMLResponse(_landing_html())


@app.get("/health")
async def health() -> dict:
    """Endpoint de healthcheck para Railway (ligero, no renderiza la landing)."""
    return {"ok": True, "service": "bot_reservas", "version": "0.4.0"}


# ---------- Captura de leads desde la landing ----------

# Valida el teléfono: admite +, espacios, guiones, paréntesis y dígitos.
_PHONE_RE = re.compile(r"^\+?[0-9\s\-\(\)\.]{6,25}$")


@app.post("/api/leads")
async def create_lead(
    request: Request,
    name: str = Form(...),
    phone: str = Form(...),
    email: str = Form(""),
    company: str = Form(""),
    sector: str = Form(""),
    message: str = Form(""),
    consent: str = Form(""),
    source: str = Form(""),
    utm_source: str = Form(""),
    utm_medium: str = Form(""),
    utm_campaign: str = Form(""),
    utm_term: str = Form(""),
    utm_content: str = Form(""),
):
    # Validaciones mínimas
    name = name.strip()
    phone = phone.strip()
    email = email.strip()
    company = company.strip()

    if not name or len(name) < 2:
        return JSONResponse({"error": "Dinos tu nombre."}, status_code=400)
    if not _PHONE_RE.match(phone):
        return JSONResponse({"error": "El teléfono no parece válido."}, status_code=400)
    if not consent:
        return JSONResponse({"error": "Tienes que aceptar que te contactemos."}, status_code=400)
    if email and "@" not in email:
        return JSONResponse({"error": "El email no parece válido."}, status_code=400)

    ip = (request.client.host if request.client else "") or request.headers.get("x-forwarded-for", "").split(",")[0].strip()
    ua = request.headers.get("user-agent", "")[:400]

    try:
        lead_id = db.save_lead(
            name=name, phone=phone, email=email, company=company,
            sector=sector, message=message.strip(),
            source=source or "landing",
            utm_source=utm_source, utm_medium=utm_medium,
            utm_campaign=utm_campaign, utm_term=utm_term, utm_content=utm_content,
            ip=ip, user_agent=ua,
        )
        log.info("lead nuevo id=%s name=%s phone=%s sector=%s source=%s", lead_id, name, phone, sector, source)
    except Exception:
        log.exception("Error guardando lead")
        return JSONResponse({"error": "Error interno. Inténtalo en un momento."}, status_code=500)

    # También crea un tenant en estado 'lead' (paused) con el email de contacto.
    # Este tenant es el que aparecerá en /admin/clientes y podrás promocionar a
    # 'contracted' cuando cierres el deal.
    try:
        db.upsert_tenant_from_lead(
            lead_id=lead_id,
            name=name, phone=phone, email=email,
            company=company, sector=sector,
        )
    except Exception:
        # No rompemos la respuesta al usuario si esto falla
        log.exception("Error creando tenant desde lead")

    return {"ok": True, "id": lead_id}


# ---------- Telegram webhook ----------
#
# Flujo: Telegram → `POST /telegram/webhook` con JSON del update y header
# `X-Telegram-Bot-Api-Secret-Token` que debe coincidir con
# TELEGRAM_WEBHOOK_SECRET. El handler reutiliza el agente canal-agnóstico.
#
# La URL exacta de este webhook se registra una vez tras deploy con
# `scripts/setup_telegram_bot.py`. Si TELEGRAM_BOT_TOKEN no está
# configurado, el endpoint devuelve 501 (Not Implemented) para distinguir
# del 401 de autenticación mal hecha.


@app.post("/telegram/webhook")
async def telegram_webhook(
    request: Request,
    x_telegram_bot_api_secret_token: str | None = Header(None),
):
    if not settings.telegram_bot_token:
        return JSONResponse(
            {"error": "Telegram no está configurado en este despliegue."},
            status_code=501,
        )
    if not settings.telegram_webhook_secret:
        # Sin secreto compartido no exponemos el webhook: cualquiera podría
        # forjar updates y gastar tokens de OpenAI a nuestra costa.
        return JSONResponse(
            {"error": "TELEGRAM_WEBHOOK_SECRET no configurado. Webhook cerrado por seguridad."},
            status_code=501,
        )
    if x_telegram_bot_api_secret_token != settings.telegram_webhook_secret:
        log.warning("Telegram webhook: secret_token no coincide. Ignorado.")
        return JSONResponse({"error": "unauthorized"}, status_code=401)

    try:
        payload = await request.json()
    except Exception:
        log.exception("Telegram webhook: body no es JSON válido")
        return JSONResponse({"error": "body no es JSON"}, status_code=400)

    result = tg_module.handle_update(
        payload,
        bot_token=settings.telegram_bot_token,
        preferred_tenant_id=settings.telegram_default_tenant_id,
    )
    # Telegram solo espera 200; el cuerpo JSON lo usamos nosotros para logs.
    return result
