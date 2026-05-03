"""clone_tools_for_sandbox.py — duplica las 5 tools del agente Ana en ElevenLabs.

Las tools son entidades independientes de los agentes. Por defecto, cuando
clonamos un agente reusamos los mismos tool_ids: cualquier cambio sobre las
tools (p.ej. activar `pre_tool_speech: force`) afectaría a producción.

Este script:
  1. Lee tool_ids del agente PROD (ELEVENLABS_AGENT_ID).
  2. Para cada tool: GET, aplica mutate_tool_for_latency (pre_tool_speech=force,
     force_pre_tool_speech=True, calendar_id schema en mover/cancelar).
  3. POST /v1/convai/tools  → crea copia.
  4. PATCH al agente SANDBOX (id en /tmp/sandbox_agent_id.txt) con la nueva
     lista de tool_ids.
  5. Guarda el mapping {old_tid: new_tid} en /tmp/sandbox_tools_mapping.json.

Uso:
    python scripts/clone_tools_for_sandbox.py [--dry-run]

Para deshacer (borrar las tools clonadas):
    python scripts/clone_tools_for_sandbox.py --cleanup
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys
from typing import Any

import httpx

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
for line in (ROOT / ".env").read_text().splitlines():
    if "=" in line and not line.strip().startswith("#"):
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())

sys.path.insert(0, str(ROOT))
from scripts.migrate_agent_latency import mutate_tool_for_latency  # type: ignore

API = "https://api.elevenlabs.io"
KEY = os.environ["ELEVENLABS_API_KEY"]
PROD_AID = os.environ["ELEVENLABS_AGENT_ID"]
H = {"xi-api-key": KEY, "Content-Type": "application/json"}
MAPPING_FILE = pathlib.Path("/tmp/sandbox_tools_mapping.json")


def get_agent(aid: str) -> dict:
    r = httpx.get(f"{API}/v1/convai/agents/{aid}", headers=H, timeout=30.0)
    r.raise_for_status()
    return r.json()


def get_tool(tid: str) -> dict:
    r = httpx.get(f"{API}/v1/convai/tools/{tid}", headers=H, timeout=20.0)
    r.raise_for_status()
    return r.json()


def create_tool(tool_config: dict) -> dict:
    # ElevenLabs envuelve el body en {"tool_config": ...} para PATCH;
    # para POST CREATE el wrapper es el mismo según docs.
    r = httpx.post(
        f"{API}/v1/convai/tools",
        headers=H,
        json={"tool_config": tool_config},
        timeout=30.0,
    )
    if r.status_code >= 400:
        raise RuntimeError(f"POST /tools → HTTP {r.status_code}: {r.text[:600]}")
    return r.json()


def delete_tool(tid: str) -> None:
    r = httpx.delete(f"{API}/v1/convai/tools/{tid}", headers=H, timeout=15.0)
    if r.status_code not in (200, 204, 404):
        print(f"  [warn] DELETE tool {tid} → HTTP {r.status_code}: {r.text[:200]}")


def patch_agent_tool_ids(aid: str, tool_ids: list[str]) -> None:
    payload = {
        "conversation_config": {
            "agent": {"prompt": {"tool_ids": tool_ids}}
        }
    }
    r = httpx.patch(
        f"{API}/v1/convai/agents/{aid}",
        headers=H,
        json=payload,
        timeout=30.0,
    )
    if r.status_code >= 400:
        raise RuntimeError(f"PATCH agent {aid} → HTTP {r.status_code}: {r.text[:400]}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--cleanup", action="store_true",
                    help="Borra las tools clonadas previamente (lee mapping de /tmp).")
    args = ap.parse_args()

    if args.cleanup:
        if not MAPPING_FILE.exists():
            print(f"No mapping en {MAPPING_FILE}. Nada que limpiar.")
            return
        mapping = json.loads(MAPPING_FILE.read_text())
        print(f"→ Borrando {len(mapping)} tools clonadas")
        for old, new in mapping.items():
            print(f"  - {new}")
            delete_tool(new)
        MAPPING_FILE.unlink()
        print("OK.")
        return

    sandbox_id_file = pathlib.Path("/tmp/sandbox_agent_id.txt")
    if not sandbox_id_file.exists():
        print(f"ERROR: no encuentro {sandbox_id_file}. ¿Ejecutaste clone_agent_sandbox.py?")
        sys.exit(1)
    SANDBOX_AID = sandbox_id_file.read_text().strip()

    print(f"→ GET agente PROD {PROD_AID}")
    prod = get_agent(PROD_AID)
    prompt = ((prod.get("conversation_config") or {}).get("agent") or {}).get("prompt") or {}
    old_tool_ids = list(prompt.get("tool_ids") or [])
    print(f"  {len(old_tool_ids)} tools a clonar")

    new_tool_ids: list[str] = []
    mapping: dict[str, str] = {}

    for old_tid in old_tool_ids:
        body = get_tool(old_tid)
        tc = body.get("tool_config") or body
        tname = tc.get("name") or "(noname)"
        # Limpia campos read-only/identidad
        tc.pop("id", None)
        tc.pop("dependent_agents", None)
        tc.pop("usage_stats", None)
        tc.pop("created_at_unix_secs", None)
        # Aplica mejoras de latencia (pre_tool_speech force + calendar_id schema)
        tc = mutate_tool_for_latency(tc)
        # Renombra para identificar como copia sandbox
        tc["name"] = tc.get("name")  # mantiene el nombre — el agente ID ya lo distingue
        # Description tag
        if tc.get("description") and "[SANDBOX]" not in tc["description"]:
            tc["description"] = "[SANDBOX] " + tc["description"]

        if args.dry_run:
            print(f"  [dry] would create: {tname:30}  pre_tool_speech={tc.get('pre_tool_speech')!r}")
            continue

        created = create_tool(tc)
        new_tid = (created.get("tool_config") or created).get("id") or created.get("id")
        if not new_tid:
            # Fallback: ElevenLabs a veces devuelve id en raíz
            new_tid = created.get("tool_id")
        if not new_tid:
            raise RuntimeError(f"No id devuelto al crear {tname}: {created}")
        print(f"  ✓ {tname:30} {old_tid} → {new_tid}")
        new_tool_ids.append(new_tid)
        mapping[old_tid] = new_tid

    if args.dry_run:
        return

    MAPPING_FILE.write_text(json.dumps(mapping, indent=2))
    print(f"\n→ PATCH agente SANDBOX {SANDBOX_AID} con {len(new_tool_ids)} tool_ids nuevos")
    patch_agent_tool_ids(SANDBOX_AID, new_tool_ids)
    print("  ✓ sandbox usa tools propias.")
    print(f"  mapping guardado en {MAPPING_FILE}")


if __name__ == "__main__":
    main()
