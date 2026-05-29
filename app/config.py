"""Configuración desde variables de entorno."""
from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Settings:
    # Provider del LLM del agente (CLI/diag): "openai" | "anthropic".
    # El canal productivo es voz por ElevenLabs; OpenAI/Anthropic se usan
    # sólo para el CLI de pruebas y el asistente de reservas del portal.
    llm_provider: str = os.getenv("LLM_PROVIDER", "openai").strip().lower()

    # OpenAI
    openai_api_key: str = os.getenv("OPENAI_API_KEY", "")
    openai_model: str = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

    # Anthropic (alternativa a OpenAI para el agente)
    anthropic_api_key: str = os.getenv("ANTHROPIC_API_KEY", "")
    anthropic_model: str = os.getenv("ANTHROPIC_MODEL", "claude-haiku-4-5-20251001")

    # ElevenLabs (voz — canal productivo)
    elevenlabs_api_key: str = os.getenv("ELEVENLABS_API_KEY", "")
    elevenlabs_voice_id: str = os.getenv("ELEVENLABS_VOICE_ID", "")

    # Google
    google_client_id: str = os.getenv("GOOGLE_CLIENT_ID", "")
    google_client_secret: str = os.getenv("GOOGLE_CLIENT_SECRET", "")
    google_redirect_uri: str = os.getenv(
        "GOOGLE_REDIRECT_URI", "http://localhost:8000/oauth/callback"
    )
    default_calendar_id: str = os.getenv("DEFAULT_CALENDAR_ID", "primary")
    default_timezone: str = os.getenv("DEFAULT_TIMEZONE", "Europe/Madrid")

    # App
    database_url: str = os.getenv("DATABASE_URL", "sqlite:///./data.db")
    tenants_file: str = os.getenv("TENANTS_FILE", "./tenants.yaml")
    log_level: str = os.getenv("LOG_LEVEL", "INFO")

    # ElevenLabs Conversational AI
    # Secreto compartido que ElevenLabs envía en X-Tool-Secret al llamar a
    # /tools/*. Si está vacío los endpoints devuelven 500 como seguro.
    tool_secret: str = os.getenv("TOOL_SECRET", "")
    # ID del agente en ElevenLabs (se guarda tras crear con scripts/setup_elevenlabs_agent.py).
    elevenlabs_agent_id: str = os.getenv("ELEVENLABS_AGENT_ID", "")

    # ---- Telegram Bot (canal de staging / texto) ----------------------------
    # Token que da @BotFather al crear el bot. Gratuito.
    telegram_bot_token: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
    # Secreto compartido con Telegram para autenticar webhook entrantes. Se pasa
    # a `setWebhook` como `secret_token` y Telegram lo devuelve en cada update
    # en el header `X-Telegram-Bot-Api-Secret-Token`. Si está vacío el endpoint
    # rechaza (no es aceptable exponer el webhook abierto).
    telegram_webhook_secret: str = os.getenv("TELEGRAM_WEBHOOK_SECRET", "")
    # Tenant al que van los mensajes entrantes del bot. Si vacío se usa el
    # primer tenant contracted+active como fallback (ver app/telegram.py).
    telegram_default_tenant_id: str = os.getenv("TELEGRAM_DEFAULT_TENANT_ID", "")

    # ---- Leads landing: alertas internas y autorespuesta --------------------
    # Webhook genérico tipo Slack/Make/Zapier. Si está configurado, cada lead
    # nuevo dispara un POST best-effort con un resumen legible.
    lead_notify_webhook_url: str = os.getenv("LEAD_NOTIFY_WEBHOOK_URL", "")
    # Email interno para avisar de leads nuevos. Requiere RESEND_API_KEY y
    # LEAD_EMAIL_FROM.
    lead_notify_email_to: str = os.getenv("LEAD_NOTIFY_EMAIL_TO", "")
    # Proveedor de email transaccional. Usamos HTTP para evitar SMTP stateful.
    resend_api_key: str = os.getenv("RESEND_API_KEY", "")
    lead_email_from: str = os.getenv("LEAD_EMAIL_FROM", "")
    # Autorespuesta al lead: desactivada por defecto hasta validar copy/dominio.
    lead_autoreply_enabled: bool = os.getenv("LEAD_AUTOREPLY_ENABLED", "").strip().lower() in {
        "1", "true", "yes", "on", "si", "sí",
    }
    lead_autoreply_subject: str = os.getenv(
        "LEAD_AUTOREPLY_SUBJECT",
        "Hemos recibido tu solicitud en Sprintia",
    )
    # ---- Brevo CRM / email marketing ----------------------------------------
    # Si BREVO_API_KEY está definido, cada lead se sincroniza como contacto.
    # BREVO_LIST_IDS acepta una lista separada por comas, p.ej. "12,18".
    brevo_api_key: str = os.getenv("BREVO_API_KEY", "")
    brevo_list_ids: str = os.getenv("BREVO_LIST_IDS", "")
    brevo_update_enabled: bool = os.getenv("BREVO_UPDATE_ENABLED", "true").strip().lower() in {
        "1", "true", "yes", "on", "si", "sí",
    }
    # Atributos creados en Brevo para enriquecer los contactos de la landing.
    brevo_company_attribute: str = os.getenv("BREVO_COMPANY_ATTRIBUTE", "COMPANY")
    brevo_sector_attribute: str = os.getenv("BREVO_SECTOR_ATTRIBUTE", "SECTOR")
    brevo_country_attribute: str = os.getenv("BREVO_COUNTRY_ATTRIBUTE", "COUNTRY")
    brevo_lead_id_attribute: str = os.getenv("BREVO_LEAD_ID_ATTRIBUTE", "LEAD_ID")
    # Sender transaccional verificado en Brevo. Si está configurado, las
    # alertas/autorespuestas salen por Brevo; si no, se conserva fallback Resend.
    brevo_sender_email: str = os.getenv("BREVO_SENDER_EMAIL", "")
    brevo_sender_name: str = os.getenv("BREVO_SENDER_NAME", "Sprintia")


settings = Settings()
