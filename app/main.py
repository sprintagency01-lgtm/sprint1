"""FastAPI app: webhook de WhatsApp + pipeline LLM + Google Calendar."""
from __future__ import annotations

import logging

from fastapi import FastAPI, Request, HTTPException, BackgroundTasks, Response

from .config import settings
from . import whatsapp
from . import db
from . import tenants
from . import agent
from . import voice
from .cms import router as cms_router
from .cms.auth import ensure_admin_user

logging.basicConfig(
    level=settings.log_level,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
log = logging.getLogger("bot")

app = FastAPI(title="Bot reservas WhatsApp + CMS")

# Bootstrap del usuario admin en arranque (si ADMIN_EMAIL + ADMIN_PASSWORD están
# definidos y no existe todavía). Safe: si faltan vars solo avisa.
ensure_admin_user()

# Monta el CMS bajo /admin (las rutas ya incluyen el prefijo).
app.include_router(cms_router)


@app.get("/")
async def health() -> dict:
    return {"ok": True, "service": "bot_reservas", "version": "0.2.0"}


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
