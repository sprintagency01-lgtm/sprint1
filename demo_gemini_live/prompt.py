"""Render del prompt de Ana para Gemini.

ana_prompt_new.txt usa placeholders __HOY_FECHA__, __MANANA_FECHA__, etc. que
en producción rellena el personalization webhook. Aquí los rellenamos en
local con la fecha del día (Europe/Madrid).

Importante (de PROMPT_KNOWLEDGE.md): no tocar la jerarquía del prompt.
Solo sustituimos placeholders, no reordenamos secciones.

Además: como Gemini 3.1 Flash Live native audio NO soporta language_code,
añadimos un refuerzo final al prompt para fijar español de España. La
recomendación de la doc es exactamente esa: forzar idioma vía system instruction.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

# Ruta absoluta al ana_prompt_new.txt en la raíz del repo (un nivel arriba).
_PROMPT_PATH = Path(__file__).parent.parent / "ana_prompt_new.txt"

_DIA_ES = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
_MES_ES = [
    "enero", "febrero", "marzo", "abril", "mayo", "junio",
    "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre",
]

_TZ = ZoneInfo("Europe/Madrid")

# Refuerzo final, NO sustituye nada del prompt original. Se concatena al final
# para que Gemini sepa que TIENE que hablar español de España (la doc dice
# explícitamente que en native audio no se puede fijar language_code y el
# único workaround es vía system instruction).
_REFUERZO_IDIOMA_ES = """

## NOTA TÉCNICA (no recitar)
Modelo: Gemini 3.1 Flash Live native audio. NO uses inglés bajo ningún concepto.
Habla SIEMPRE en español de España, con acento castellano peninsular.
Si por error sale algo en inglés o se cuela un anglicismo, corrige inmediatamente.
"""


def _fecha_natural(d) -> str:
    """'viernes 25 de abril' (sin año, igual que la versión del backend)."""
    return f"{_DIA_ES[d.weekday()]} {d.day} de {_MES_ES[d.month - 1]}"


def render_prompt(now: datetime | None = None) -> str:
    """Lee ana_prompt_new.txt, sustituye placeholders __XXX__ y añade refuerzo idioma."""
    if now is None:
        now = datetime.now(_TZ)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=_TZ)

    hoy = now.date()
    manana = hoy + timedelta(days=1)
    pasado = hoy + timedelta(days=2)

    raw = _PROMPT_PATH.read_text(encoding="utf-8")

    sustituciones = {
        "__HOY_FECHA__": hoy.isoformat(),
        "__MANANA_FECHA__": manana.isoformat(),
        "__PASADO_FECHA__": pasado.isoformat(),
        "__HOY_DIA_NATURAL__": _fecha_natural(hoy),
        "__MANANA_DIA_NATURAL__": _fecha_natural(manana),
        "__PASADO_DIA_NATURAL__": _fecha_natural(pasado),
        "__ANO_ACTUAL__": str(hoy.year),
    }

    out = raw
    for k, v in sustituciones.items():
        out = out.replace(k, v)

    # Sustituciones de variables ElevenLabs ({{system__time}}, {{system__caller_id}})
    # que Gemini no resuelve. Inyectamos valores literales.
    out = out.replace("{{system__time}}", now.strftime("%Y-%m-%d %H:%M %z"))
    # En el demo simulamos un caller_id fijo cargado desde .env.
    # Import perezoso (dentro de la función) para evitar ciclos al importarse
    # prompt.py desde tests sin .env cargado.
    from config import settings  # noqa: WPS433
    out = out.replace("{{system__caller_id}}", settings.caller_id or "unknown")

    return out + _REFUERZO_IDIOMA_ES


if __name__ == "__main__":
    # Smoke test: imprime el prompt renderizado por consola.
    print(render_prompt())
