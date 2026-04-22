"""FastAPI app: webhook de WhatsApp + pipeline LLM + Google Calendar + landing."""
from __future__ import annotations

import logging
import pathlib
import re

from fastapi import FastAPI, Request, HTTPException, BackgroundTasks, Response, Form
from fastapi.responses import HTMLResponse, JSONResponse

from .config import settings
from . import whatsapp
from . import db
from . import tenants
from . import agent
from . import voice
from . import eleven_tools
from . import oauth_web
from .cms import router as cms_router
from .cms.auth import ensure_admin_user

logging.basicConfig(
    level=settings.log_level,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
log = logging.getLogger("bot")

app = FastAPI(title="Bot reservas WhatsApp + CMS")

# Bootstrap del usuario admin en arranque (si ADMIN_EMAIL + ADMIN_PASSWORD están
# definidos y no existe todavía). Si algo falla aquí (p.ej. versión de bcrypt
# incompatible) seguimos levantando el servidor para poder diagnosticar — el
# panel /admin avisará con un login fallido, pero / y /whatsapp siguen vivos.
try:
    ensure_admin_user()
    log.info("Bootstrap admin: OK")
except Exception:
    log.exception("Bootstrap del admin falló — el servidor arranca igual, "
                  "pero el login del CMS no funcionará hasta que se arregle.")

# Monta el CMS bajo /admin (las rutas ya incluyen el prefijo).
app.include_router(cms_router)
# Endpoints /tools/* que ElevenLabs Conversational AI llama como server tools
# durante las llamadas de voz (consultar disponibilidad, crear/mover/cancelar
# reserva). Protegidos por X-Tool-Secret.
app.include_router(eleven_tools.router)
# Flujo web OAuth de Google (/oauth/start y /oauth/callback). Necesario para
# autorizar un calendario desde el navegador en producción (Railway), en vez
# del `InstalledAppFlow` que sólo funciona en local.
app.include_router(oauth_web.router)


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
    return {"ok": True, "service": "bot_reservas", "version": "0.3.0"}


@app.get("/_diag/tokens")
async def _diag_tokens(x_tool_secret: str | None = None) -> dict:
    """Diagnóstico rápido de estado de tokens OAuth. Protegido por TOOL_SECRET.

    Usage: GET /_diag/tokens?x_tool_secret=<TOOL_SECRET>
    """
    from .calendar_service import TOKENS_DIR, SCOPES, _load_creds
    from fastapi import Header
    if not settings.tool_secret or x_tool_secret != settings.tool_secret:
        raise HTTPException(status_code=401, detail="Bad x_tool_secret")
    out = {
        "build_marker": "diag_v1_fallback_default",
        "tokens_dir": str(TOKENS_DIR),
        "tokens_dir_exists": TOKENS_DIR.exists(),
        "scopes_requested": SCOPES,
        "files": [],
    }
    if TOKENS_DIR.exists():
        for p in sorted(TOKENS_DIR.iterdir()):
            entry: dict = {"name": p.name, "size": p.stat().st_size}
            try:
                import json as _j
                data = _j.loads(p.read_text())
                entry["scopes_file"] = data.get("scopes")
                entry["has_refresh"] = bool(data.get("refresh_token"))
                entry["client_id_tail"] = (data.get("client_id") or "")[-15:]
            except Exception as e:
                entry["error"] = f"{type(e).__name__}: {str(e)[:100]}"
            out["files"].append(entry)
    # Try loading default tenant creds
    try:
        c = _load_creds("default")
        out["default_load"] = {
            "ok": c is not None,
            "valid": bool(c and c.valid),
            "has_refresh": bool(c and c.refresh_token),
            "scopes": list(c.scopes) if (c and c.scopes) else None,
        }
    except Exception as e:
        out["default_load"] = {"ok": False, "error": f"{type(e).__name__}: {str(e)[:200]}"}
    try:
        c = _load_creds("pelu_demo")
        out["pelu_demo_load"] = {
            "ok": c is not None,
            "valid": bool(c and c.valid),
            "scopes": list(c.scopes) if (c and c.scopes) else None,
        }
    except Exception as e:
        out["pelu_demo_load"] = {"ok": False, "error": f"{type(e).__name__}: {str(e)[:200]}"}

    # Try the actual failing code path
    try:
        from . import tenants as tn
        from . import calendar_service as cal
        from datetime import datetime
        all_t = tn.load_tenants()
        out["tenants_loaded"] = [t.get("id") for t in all_t]
        tenant = next((t for t in all_t if t.get("id") == "pelu_demo"), None)
        out["tenant_found"] = bool(tenant)
        if tenant:
            out["tenant_calendar_id"] = tenant.get("calendar_id")
            out["tenant_peluqueros"] = [{"nombre": p.get("nombre"), "cal": p.get("calendar_id")} for p in (tenant.get("peluqueros") or [])]
            desde = datetime.fromisoformat("2026-04-23T09:30:00")
            hasta = datetime.fromisoformat("2026-04-23T20:30:00")
            try:
                huecos = cal.listar_huecos_por_peluqueros(
                    desde, hasta, 30,
                    peluqueros=tenant.get("peluqueros") or [],
                    tenant_id="pelu_demo",
                )
                out["listar_ok"] = True
                out["listar_count"] = len(huecos)
            except Exception as e:
                import traceback
                out["listar_ok"] = False
                out["listar_error"] = f"{type(e).__name__}: {str(e)[:400]}"
                out["listar_tb"] = traceback.format_exc()[-1500:]
    except Exception as e:
        out["pipeline_error"] = f"{type(e).__name__}: {str(e)[:300]}"

    return out


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


# ---------- Webhook WhatsApp ----------

@app.get("/whatsapp")
async def whatsapp_verify(request: Request):
    """Verificación inicial del webhook (Meta manda hub.challenge)."""
    qp = request.query_params
    mode = qp.get("hub.mode")
    token = qp.get("hub.verify_token")
    challenge = qp.get("hub.challenge")
    if mode == "subscribe" and token == settings.whatsapp_verify_token:
        return Response(content=challenge or "", media_type="text/plain")
    raise HTTPException(status_code=403, detail="Invalid verify token")


@app.post("/whatsapp")
async def whatsapp_webhook(request: Request, background_tasks: BackgroundTasks):
    """Recibe un mensaje de WhatsApp y responde en segundo plano."""
    raw = await request.body()
    sig = request.headers.get("x-hub-signature-256")
    if not whatsapp.verify_signature(raw, sig):
        raise HTTPException(status_code=401, detail="Bad signature")

    payload = await request.json()
    msg = whatsapp.extract_message(payload)
    if not msg:
        # evento no interesante (p. ej. status). Devolvemos 200 igualmente.
        return {"received": True}

    # Procesar en background para devolver 200 rápido a Meta
    if msg["type"] == "text":
        background_tasks.add_task(
            handle_incoming_text,
            msg["from"],
            msg["text"],
            msg["phone_number_id"],
        )
    elif msg["type"] == "audio":
        background_tasks.add_task(
            handle_incoming_audio,
            msg["from"],
            msg["audio_media_id"],
            msg["phone_number_id"],
        )
    return {"received": True}


# ---------- Pipeline de procesamiento ----------

async def handle_incoming_text(from_phone: str, text: str, phone_number_id: str) -> None:
    try:
        tenant = tenants.find_tenant_by_phone_number_id(phone_number_id)
        tenant_id = tenant.get("id", "default")

        log.info("msg in  [%s] %s: %s", tenant_id, from_phone, text)
        db.save_message(tenant_id, from_phone, "user", text)

        history = db.load_history(tenant_id, from_phone)
        # El último user ya está en history; para no duplicar lo quitamos del final
        history = [m for m in history[:-1]] if history and history[-1]["role"] == "user" else history

        reply_text = agent.reply(
            user_message=text,
            history=history,
            tenant=tenant,
            caller_phone=from_phone,
        )

        db.save_message(tenant_id, from_phone, "assistant", reply_text)
        log.info("msg out [%s] %s: %s", tenant_id, from_phone, reply_text)

        await whatsapp.send_text(
            to_phone=from_phone,
            body=reply_text,
            phone_number_id=phone_number_id,
        )
    except Exception:
        log.exception("Error procesando mensaje entrante")
        try:
            await whatsapp.send_text(
                from_phone,
                "Vaya, ha habido un problema técnico. Vuelve a intentarlo en un rato.",
                phone_number_id,
            )
        except Exception:
            pass


async def handle_incoming_audio(from_phone: str, media_id: str, phone_number_id: str) -> None:
    """Procesa una nota de voz entrante: STT → agente → TTS → nota de voz saliente."""
    tenant = tenants.find_tenant_by_phone_number_id(phone_number_id)
    tenant_id = tenant.get("id", "default")

    async def _agent_reply(text_in: str) -> str:
        """Callback que usa voice.handle_incoming_voice para obtener la respuesta.

        Guarda en BBDD el texto transcrito como mensaje del usuario y la respuesta
        del agente, para que el historial de conversación sea coherente entre
        mensajes de texto y notas de voz.
        """
        log.info("audio in [%s] %s: %s", tenant_id, from_phone, text_in)
        db.save_message(tenant_id, from_phone, "user", f"[voz] {text_in}")

        history = db.load_history(tenant_id, from_phone)
        history = [m for m in history[:-1]] if history and history[-1]["role"] == "user" else history

        reply_text = agent.reply(
            user_message=text_in,
            history=history,
            tenant=tenant,
            caller_phone=from_phone,
        )
        db.save_message(tenant_id, from_phone, "assistant", reply_text)
        log.info("audio out [%s] %s: %s", tenant_id, from_phone, reply_text)
        return reply_text

    await voice.handle_incoming_voice(
        from_phone=from_phone,
        media_id=media_id,
        phone_number_id=phone_number_id,
        agent_reply_fn=_agent_reply,
    )
