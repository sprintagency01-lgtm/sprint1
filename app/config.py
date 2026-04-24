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


settings = Settings()
