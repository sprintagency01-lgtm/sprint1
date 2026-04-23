"""FastAPI app: webhook de WhatsApp + pipeline LLM + Google Calendar + landing."""
from __future__ import annotations

import logging
import pathlib
import re

from fastapi import FastAPI, Request, HTTPException, BackgroundTasks, Response, Form
from fastapi.responses import HTMLResponse, JSONResponse

from .config import settings
from . import whatsapp
from . import twilio_wa
from . import db
from . import tenants
from . import agent
from . import interactive as interactive_ids
from . import voice
from . import eleven_tools
from . import oauth_web
from . import diag
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
    return {"ok": True, "service": "bot_reservas", "version": "0.3.0"}


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
    elif msg["type"] == "interactive_reply":
        # El cliente pulsó un botón/fila. Traducimos el reply_id al texto
        # sintético correspondiente y reutilizamos el pipeline de texto.
        synthetic = _synthetic_text_from_interactive_reply(
            reply_id=msg.get("reply_id", ""),
            reply_title=msg.get("reply_title", ""),
        )
        background_tasks.add_task(
            handle_incoming_text,
            msg["from"],
            synthetic,
            msg["phone_number_id"],
        )
    return {"received": True}


# ---------- Helpers de interactive → texto sintético ----------

def _synthetic_text_from_interactive_reply(reply_id: str, reply_title: str) -> str:
    """Convierte un id codificado a un string que el LLM interpreta como
    elección clara del cliente. El objetivo es que el agente no tenga que
    saber nada de ids: le llega "El cliente elige la hora 2026-04-24T10:00" y
    sigue el flujo con normalidad.
    """
    parsed = interactive_ids.parse_id(reply_id)
    kind = parsed.get("kind")

    if kind == "slot":
        inicio = parsed.get("inicio_iso", "")
        fin = parsed.get("fin_iso", "")
        miembro = parsed.get("miembro") or ""
        base = f"El cliente elige el hueco de {inicio} a {fin}."
        if miembro:
            base += f" Con el miembro {miembro}."
        return base

    if kind == "team":
        if parsed.get("sin_preferencia"):
            return "El cliente dice que le da igual el miembro del equipo."
        return f"El cliente elige al miembro del equipo con id {parsed.get('member_id')}."

    if kind == "svc":
        # Preferimos el título humano ("Corte hombre") sobre el slug, porque
        # es lo que el agente ya conoce del catálogo en su system_prompt.
        if reply_title:
            return f"El cliente elige el servicio: {reply_title}."
        return f"El cliente elige el servicio con slug {parsed.get('slug')}."

    if kind == "confirm":
        return "Sí, confírmalo." if parsed.get("yes") else "No, vamos a cambiar algo."

    if kind == "other":
        target = parsed.get("target", "")
        if target == "slot":
            return (
                "El cliente pide OTRA hora distinta a las ofrecidas. "
                "Pregúntale qué franja le va mejor (día/hora aproximada) "
                "en texto libre."
            )
        if target == "team":
            # Vuelve al paso anterior: re-ofrecer huecos.
            return (
                "El cliente quiere OTRO miembro del equipo. Vuelve a "
                "ofrecerle huecos disponibles con el resto del equipo."
            )
        if target == "svc":
            return "El cliente quiere OTRO servicio distinto a los ofrecidos."
        return "El cliente pide otra opción distinta a las ofrecidas."

    # Desconocido: nos apoyamos en el título que WhatsApp nos devuelve.
    if reply_title:
        return reply_title
    return reply_id or ""


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

        reply = agent.reply(
            user_message=text,
            history=history,
            tenant=tenant,
            caller_phone=from_phone,
        )

        db.save_message(tenant_id, from_phone, "assistant", reply.text)
        log.info("msg out [%s] %s: %s", tenant_id, from_phone, reply.text)

        if reply.has_interactive:
            # Guardamos el menú (Meta no lo necesita para el dispatch, pero
            # así mantenemos estado consistente entre canales y habilita
            # resolución por texto si el cliente escribe libre en vez de
            # pulsar una opción).
            db.save_pending_menu(
                tenant_id=tenant_id,
                customer_phone=from_phone,
                kind=(reply.interactive or {}).get("type", "list"),
                options=(reply.interactive or {}).get("options", []),
            )
            await whatsapp.send_interactive(
                to_phone=from_phone,
                spec=reply.interactive or {},
                phone_number_id=phone_number_id,
            )
        else:
            db.clear_pending_menu(tenant_id, from_phone)
            await whatsapp.send_text(
                to_phone=from_phone,
                body=reply.text,
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


# ---------- Webhook WhatsApp via Twilio (sandbox o número real) ----------
#
# Convive con /whatsapp (Meta). Útil mientras Meta for Developers no está
# verificada: el sandbox de Twilio permite probar el bot sin Meta Business.

@app.post("/whatsapp/twilio")
async def whatsapp_twilio_webhook(request: Request, background_tasks: BackgroundTasks):
    """Recibe un mensaje de WhatsApp vía Twilio (form-encoded) y responde async."""
    raw = await request.body()
    form_raw = await request.form()
    form: dict[str, str] = {k: str(v) for k, v in form_raw.items()}

    # Valida firma de Twilio (X-Twilio-Signature es HMAC-SHA1 de URL+params).
    # Reconstruye la URL tal y como Twilio la firma (incluye query string si hay).
    sig = request.headers.get("x-twilio-signature")
    # Twilio firma con la URL *pública* tal y como la tiene configurada; si está
    # detrás de proxy/Railway, respetamos el header X-Forwarded-Proto.
    proto = request.headers.get("x-forwarded-proto") or request.url.scheme
    host = request.headers.get("x-forwarded-host") or request.url.netloc
    url = f"{proto}://{host}{request.url.path}"
    if request.url.query:
        url += f"?{request.url.query}"

    if not twilio_wa.verify_signature(url, form, sig):
        raise HTTPException(status_code=401, detail="Bad Twilio signature")

    msg = twilio_wa.extract_message(form)
    if not msg:
        return Response(status_code=204)

    if msg["type"] == "text":
        background_tasks.add_task(
            handle_incoming_text_twilio,
            msg["from"],
            msg["text"],
            msg["to"],
            msg.get("profile_name", ""),
        )
    elif msg["type"] == "audio":
        background_tasks.add_task(
            handle_incoming_audio_twilio,
            msg["from"],
            msg["media_url"],
            msg.get("media_content_type", "audio/ogg"),
            msg["to"],
            msg.get("profile_name", ""),
        )
    return Response(status_code=204)


async def handle_incoming_text_twilio(
    from_phone: str,
    text: str,
    to_number: str,
    profile_name: str,
) -> None:
    """Pipeline de texto para mensajes entrantes por Twilio."""
    try:
        tenant = tenants.find_tenant_for_twilio(to_number)
        tenant_id = tenant.get("id", "default")

        log.info("twilio in  [%s] %s (%s): %s", tenant_id, from_phone, profile_name or "-", text)

        # Fallback de interactivos en Twilio: si hay un menú pendiente y el
        # cliente responde "1", "dos", "otra", etc., lo resolvemos a su id
        # y lo convertimos a texto sintético para que el agente lo interprete
        # igual que una pulsación nativa en Meta.
        effective_text = text
        pending = db.get_pending_menu(tenant_id, from_phone)
        if pending:
            opt = interactive_ids.resolve_from_pending_menu(pending, text)
            if opt is not None:
                rid = opt.get("id") or ""
                effective_text = _synthetic_text_from_interactive_reply(
                    reply_id=rid,
                    reply_title=opt.get("title", ""),
                )
                log.info(
                    "twilio [%s] %s resuelve menú: '%s' → %s",
                    tenant_id, from_phone, text, rid,
                )

        db.save_message(tenant_id, from_phone, "user", text)

        history = db.load_history(tenant_id, from_phone)
        history = [m for m in history[:-1]] if history and history[-1]["role"] == "user" else history

        reply = agent.reply(
            user_message=effective_text,
            history=history,
            tenant=tenant,
            caller_phone=from_phone,
        )

        db.save_message(tenant_id, from_phone, "assistant", reply.text)
        log.info("twilio out [%s] %s: %s", tenant_id, from_phone, reply.text)

        if reply.has_interactive:
            db.save_pending_menu(
                tenant_id=tenant_id,
                customer_phone=from_phone,
                kind=(reply.interactive or {}).get("type", "list"),
                options=(reply.interactive or {}).get("options", []),
            )
            await twilio_wa.send_interactive(
                to_phone=from_phone,
                spec=reply.interactive or {},
            )
        else:
            db.clear_pending_menu(tenant_id, from_phone)
            await twilio_wa.send_text(to_phone=from_phone, body=reply.text)
    except Exception:
        log.exception("Error procesando mensaje Twilio")
        try:
            await twilio_wa.send_text(
                to_phone=from_phone,
                body="Vaya, ha habido un problema técnico. Vuelve a intentarlo en un rato.",
            )
        except Exception:
            pass


async def handle_incoming_audio_twilio(
    from_phone: str,
    media_url: str,
    media_content_type: str,
    to_number: str,
    profile_name: str,
) -> None:
    """Procesa una nota de voz entrante por Twilio: descarga → STT → agente → texto.

    Nota: por ahora respondemos en texto (no subimos TTS a un hosting público).
    Fase siguiente: servir el mp3 generado desde un endpoint público de Railway.
    """
    try:
        tenant = tenants.find_tenant_for_twilio(to_number)
        tenant_id = tenant.get("id", "default")

        log.info("twilio voz in [%s] %s: %s", tenant_id, from_phone, media_url)
        audio_in = await twilio_wa.download_media(media_url)
        text_in = await voice.transcribe(audio_in, mime_type=media_content_type or "audio/ogg")
        log.info("twilio voz transcrita: %s", text_in)

        if not text_in:
            await twilio_wa.send_text(
                to_phone=from_phone,
                body="Perdona, no he podido entender el audio. ¿Puedes escribirlo o volver a grabarlo?",
            )
            return

        db.save_message(tenant_id, from_phone, "user", f"[voz] {text_in}")
        history = db.load_history(tenant_id, from_phone)
        history = [m for m in history[:-1]] if history and history[-1]["role"] == "user" else history

        reply = agent.reply(
            user_message=text_in,
            history=history,
            tenant=tenant,
            caller_phone=from_phone,
        )
        db.save_message(tenant_id, from_phone, "assistant", reply.text)
        log.info("twilio voz out [%s] %s: %s", tenant_id, from_phone, reply.text)

        if reply.has_interactive:
            db.save_pending_menu(
                tenant_id=tenant_id,
                customer_phone=from_phone,
                kind=(reply.interactive or {}).get("type", "list"),
                options=(reply.interactive or {}).get("options", []),
            )
            await twilio_wa.send_interactive(
                to_phone=from_phone,
                spec=reply.interactive or {},
            )
        else:
            db.clear_pending_menu(tenant_id, from_phone)
            await twilio_wa.send_text(to_phone=from_phone, body=reply.text)
    except Exception:
        log.exception("Error procesando voz Twilio")
        try:
            await twilio_wa.send_text(
                to_phone=from_phone,
                body="Ha habido un problema procesando tu audio. Vuelve a intentarlo en un rato.",
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

        Nota: para notas de voz SIEMPRE devolvemos texto plano (TTS). Si el
        agente produjo un mensaje interactivo, mantenemos el body como texto
        para el audio y el usuario podrá seguir escribiendo. Mandamos también
        el interactivo por chat en paralelo para no perder la UI clicable.
        """
        log.info("audio in [%s] %s: %s", tenant_id, from_phone, text_in)
        db.save_message(tenant_id, from_phone, "user", f"[voz] {text_in}")

        history = db.load_history(tenant_id, from_phone)
        history = [m for m in history[:-1]] if history and history[-1]["role"] == "user" else history

        reply = agent.reply(
            user_message=text_in,
            history=history,
            tenant=tenant,
            caller_phone=from_phone,
        )
        db.save_message(tenant_id, from_phone, "assistant", reply.text)
        log.info("audio out [%s] %s: %s", tenant_id, from_phone, reply.text)

        if reply.has_interactive:
            # Además del TTS, mandamos el mensaje interactivo por chat para
            # que el cliente pueda pulsar la opción.
            try:
                db.save_pending_menu(
                    tenant_id=tenant_id,
                    customer_phone=from_phone,
                    kind=(reply.interactive or {}).get("type", "list"),
                    options=(reply.interactive or {}).get("options", []),
                )
                await whatsapp.send_interactive(
                    to_phone=from_phone,
                    spec=reply.interactive or {},
                    phone_number_id=phone_number_id,
                )
            except Exception:
                log.exception("Error enviando interactive tras voz (Meta)")
        else:
            db.clear_pending_menu(tenant_id, from_phone)

        return reply.text

    await voice.handle_incoming_voice(
        from_phone=from_phone,
        media_id=media_id,
        phone_number_id=phone_number_id,
        agent_reply_fn=_agent_reply,
    )
