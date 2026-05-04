"""Configuración del demo: carga .env y centraliza settings.

Mantenemos esto separado para que el archivo principal (gemini_live_demo.py)
quede solo con la lógica de la sesión Live, y los demás módulos puedan
importar `settings` sin tocar variables de entorno.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

# Cargamos .env del directorio del demo (no el del repo raíz).
_HERE = Path(__file__).parent
load_dotenv(_HERE / ".env")


@dataclass(frozen=True)
class Settings:
    # Gemini Live
    gemini_api_key: str
    gemini_model: str
    gemini_voice: str

    # Backend Sprintia (las tools de Ana)
    backend_url: str
    tenant_id: str
    tool_secret: str
    caller_id: str

    # Audio
    audio_input_sr: int
    audio_output_sr: int
    audio_chunk_ms: int

    @property
    def audio_chunk_samples_in(self) -> int:
        """Samples por chunk de captura del micro a sample rate de entrada."""
        return int(self.audio_input_sr * self.audio_chunk_ms / 1000)


def _env(name: str, default: str | None = None, required: bool = False) -> str:
    val = os.environ.get(name, default)
    if required and not val:
        raise RuntimeError(
            f"Falta la variable de entorno {name}. Copia .env.example a .env "
            f"y rellena los valores."
        )
    return val or ""


def load_settings() -> Settings:
    return Settings(
        gemini_api_key=_env("GEMINI_API_KEY", required=True),
        gemini_model=_env("GEMINI_MODEL", "gemini-3.1-flash-live-preview"),
        gemini_voice=_env("GEMINI_VOICE", "Kore"),
        backend_url=_env("BACKEND_URL", "https://sprintiasolutions.com").rstrip("/"),
        tenant_id=_env("TENANT_ID", "pelu_demo"),
        tool_secret=_env("TOOL_SECRET", required=True),
        caller_id=_env("CALLER_ID", "+34600000000"),
        audio_input_sr=int(_env("AUDIO_INPUT_SAMPLE_RATE", "16000")),
        audio_output_sr=int(_env("AUDIO_OUTPUT_SAMPLE_RATE", "24000")),
        audio_chunk_ms=int(_env("AUDIO_CHUNK_MS", "50")),
    )


settings = load_settings()
