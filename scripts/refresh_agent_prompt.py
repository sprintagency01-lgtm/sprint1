"""Renderiza `ana_prompt_new.txt` con la fecha de hoy como texto literal y
sincroniza el prompt del agente en ElevenLabs.

Problema que resuelve: Gemini 3 Flash Preview (y otros LLMs con training
cutoff reciente) IGNORA la variable {{system__time}} del contexto y usa su
conocimiento pre-entrenado como "hoy". Eso hace que invente fechas ISO con
el año del cutoff (p.ej. 2025) en lugar del año real (2026), y las tools
devuelven huecos vacíos porque todo queda en el pasado.

Solución: en vez de confiar en placeholders, hardcodear la fecha dentro del
prompt antes de subirlo. El prompt en disco tiene un bloque marcado con
`<!-- REFRESH_BLOCK -->` y macros __HOY_FECHA__, __ANO_ACTUAL__, etc. Este
script las reemplaza por valores reales calculados ahora y hace PATCH al
agente.

Ejecutar:
  - En local, a mano: `python scripts/refresh_agent_prompt.py`
  - Idealmente con cron / scheduled task al menos 1 vez al día (00:05).
  - Se puede ejecutar tantas veces como se quiera (idempotente).

Opcional: --print para ver el prompt renderizado sin hacer PATCH.
"""
from __future__ import annotations

import os
import pathlib
import re
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
for line in (ROOT / ".env").read_text().splitlines():
    if "=" in line and not line.strip().startswith("#"):
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())

import httpx

TZ = ZoneInfo(os.environ.get("DEFAULT_TIMEZONE", "Europe/Madrid"))
DIAS_ES = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
MESES_ES = ["enero", "febrero", "marzo", "abril", "mayo", "junio",
            "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"]


def _fecha_natural(d) -> str:
    return f"{DIAS_ES[d.weekday()]} {d.day} de {MESES_ES[d.month - 1]} de {d.year}"


def render_prompt(prompt_raw: str, now_dt: datetime | None = None) -> str:
    now = (now_dt or datetime.now(TZ)).astimezone(TZ)
    hoy = now.date()
    manana = hoy + timedelta(days=1)
    pasado = hoy + timedelta(days=2)
    macros = {
        "__HOY_FECHA__": hoy.isoformat(),
        "__HOY_DIA_NATURAL__": _fecha_natural(hoy),
        "__MANANA_FECHA__": manana.isoformat(),
        "__MANANA_DIA_NATURAL__": _fecha_natural(manana),
        "__PASADO_FECHA__": pasado.isoformat(),
        "__PASADO_DIA_NATURAL__": _fecha_natural(pasado),
        "__ANO_ACTUAL__": str(hoy.year),
    }
    out = prompt_raw
    for k, v in macros.items():
        out = out.replace(k, v)
    return out


def sync_to_agent(rendered: str) -> dict:
    aid = os.environ["ELEVENLABS_AGENT_ID"]
    key = os.environ["ELEVENLABS_API_KEY"]
    r = httpx.patch(
        f"https://api.elevenlabs.io/v1/convai/agents/{aid}",
        headers={"xi-api-key": key, "Content-Type": "application/json"},
        json={"conversation_config": {"agent": {"prompt": {"prompt": rendered}}}},
        timeout=20.0,
    )
    if r.status_code >= 400:
        raise RuntimeError(f"PATCH {r.status_code}: {r.text[:300]}")
    return {"ok": True, "status": r.status_code, "bytes": len(rendered)}


def main():
    prompt_path = ROOT / "ana_prompt_new.txt"
    raw = prompt_path.read_text()
    rendered = render_prompt(raw)

    if "--print" in sys.argv:
        # Extraer solo el bloque renderizado para inspección
        m = re.search(r"<!-- REFRESH_BLOCK -->(.*?)<!-- /REFRESH_BLOCK -->",
                      rendered, flags=re.DOTALL)
        if m:
            print("=== REFRESH_BLOCK renderizado ===")
            print(m.group(1).strip())
        print(f"\n(total bytes: {len(rendered)})")
        return

    res = sync_to_agent(rendered)
    print(f"PATCH OK status={res['status']} bytes={res['bytes']}")
    print(f"Fechas renderizadas: hoy={datetime.now(TZ).date().isoformat()}")


if __name__ == "__main__":
    main()
