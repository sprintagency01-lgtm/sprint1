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

    # OpenAI
    openai_api_key: str = os.getenv("OPENAI_API_KEY", "")
    openai_model: str = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

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


settings = Settings()
