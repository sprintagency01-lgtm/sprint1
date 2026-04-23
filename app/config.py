"""Configuración desde variables de entorno."""
from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Settings:
    # WhatsApp
    whatsapp_verify_token: str = os.getenv("WHATSAPP_VERIFY_TOKEN", "")
    whatsapp_access_token: str = os.getenv("WHATSAPP_ACCESS_TOKEN", "")
    whatsapp_phone_number_id: str = os.getenv("WHATSAPP_PHONE_NUMBER_ID", "")
    whatsapp_app_secret: str = os.getenv("WHATSAPP_APP_SECRET", "")

    # Provider del LLM del agente: "openai" | "anthropic".
    # Por defecto openai para no romper instalaciones existentes.
    llm_provider: str = os.getenv("LLM_PROVIDER", "openai").strip().lower()

    # OpenAI
    openai_api_key: str = os.getenv("OPENAI_API_KEY", "")
    openai_model: str = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

    # Anthropic (alternativa a OpenAI para el agente)
    anthropic_api_key: str = os.getenv("ANTHROPIC_API_KEY", "")
    anthropic_model: str = os.getenv("ANTHROPIC_MODEL", "claude-haiku-4-5-20251001")

    # ElevenLabs (voz)
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

    # Twilio (WhatsApp sandbox / producción) — adaptador alternativo a Meta
    twilio_account_sid: str = os.getenv("TWILIO_ACCOUNT_SID", "")
    twilio_auth_token: str = os.getenv("TWILIO_AUTH_TOKEN", "")
    # Número saliente en E.164 (ej. "+14155238886" del sandbox, o tu número real).
    twilio_whatsapp_from: str = os.getenv("TWILIO_WHATSAPP_FROM", "")
    # Tenant por defecto para tráfico que entra por el sandbox compartido.
    # En producción, cada tenant tendrá su propio número y routeamos por To.
    twilio_default_tenant_id: str = os.getenv("TWILIO_DEFAULT_TENANT_ID", "pelu_demo")


settings = Settings()
