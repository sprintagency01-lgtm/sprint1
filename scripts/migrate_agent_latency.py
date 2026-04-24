"""Migra un agente existente de ElevenLabs a la config de baja latencia.

Cambios que aplica:
  1. TTS `model_id` → `eleven_flash_v2_5` (antes: `eleven_v3_conversational`).
     Ahorra ~150-400ms al primer audio.
  2. `force_pre_tool_speech: true` en las 5 tools del agente. Arranca el TTS
     del filler en paralelo a la tool call, enmascarando la latencia del
     backend auditivamente.
  3. Añade `calendar_id` (opcional) al request_body_schema de mover_reserva
     y cancelar_reserva, para que Ana reenvíe al backend el calendar_id que
     obtuvo de buscar_reserva_cliente y evite la iteración de peluqueros en
     el backend.

Uso:
    # Migrar el agente apuntado por ELEVENLABS_AGENT_ID (global del .env)
    python scripts/migrate_agent_latency.py

    # Migrar un agente concreto
    python scripts/migrate_agent_latency.py agent_3901kprqemrger3rsgky0csea6g0

    # Dry run: imprime el payload sin hacer PATCH
    python scripts/migrate_agent_latency.py --dry-run

Requiere ELEVENLABS_API_KEY en el entorno (o en .env).
"""
from __future__ import annotations

import os
import pathlib
import sys
from typing import Any

import httpx
from dotenv import load_dotenv

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
load_dotenv(ROOT / ".env")

API_BASE = "https://api.elevenlabs.io"


def _headers(api_key: str) -> dict[str, str]:
    return {"xi-api-key": api_key, "Content-Type": "application/json"}


def get_agent(api_key: str, agent_id: str) -> dict[str, Any]:
    r = httpx.get(
        f"{API_BASE}/v1/convai/agents/{agent_id}",
        headers=_headers(api_key),
        timeout=30.0,
    )
    if r.status_code >= 400:
        raise RuntimeError(f"GET agent {agent_id} → HTTP {r.status_code}: {r.text[:400]}")
    return r.json()


def build_patch_payload(remote_agent: dict[str, Any]) -> dict[str, Any]:
    """Construye el payload de PATCH sin pisar campos que no tocamos.

    Estrategia: tomamos las `tools` remotas, mutamos lo necesario y las
    devolvemos completas, igual con `tts`.
    """
    conv = remote_agent.get("conversation_config") or {}
    agent_block = conv.get("agent") or {}
    prompt_block = (agent_block.get("prompt") or {}).copy()
    tools = list(prompt_block.get("tools") or [])

    tools_patched: list[dict[str, Any]] = []
    for t in tools:
        name = t.get("name") or ""
        t = dict(t)
        # (2) force_pre_tool_speech en todas las tools: arranca el filler TTS
        #     en paralelo a la HTTP call.
        t["force_pre_tool_speech"] = True

        # (3) calendar_id opcional en mover_reserva y cancelar_reserva.
        if name in ("mover_reserva", "cancelar_reserva"):
            api_schema = dict(t.get("api_schema") or {})
            body = dict(api_schema.get("request_body_schema") or {})
            props = dict(body.get("properties") or {})
            if "calendar_id" not in props:
                props["calendar_id"] = {
                    "type": "string",
                    "description": (
                        "calendar_id devuelto por buscar_reserva_cliente. "
                        "Si lo pasas, el backend " +
                        ("mueve" if name == "mover_reserva" else "cancela") +
                        " directo sin iterar peluqueros — MÁS RÁPIDO."
                    ),
                    "enum": None,
                    "is_system_provided": False,
                    "dynamic_variable": "",
                    "constant_value": "",
                }
                body["properties"] = props
                api_schema["request_body_schema"] = body
                t["api_schema"] = api_schema
        tools_patched.append(t)

    # (1) TTS → eleven_flash_v2_5
    tts_block = dict(conv.get("tts") or {})
    tts_block["model_id"] = "eleven_flash_v2_5"

    return {
        "conversation_config": {
            "agent": {"prompt": {"tools": tools_patched}},
            "tts": tts_block,
        },
    }


def patch_agent(api_key: str, agent_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    r = httpx.patch(
        f"{API_BASE}/v1/convai/agents/{agent_id}",
        headers=_headers(api_key),
        json=payload,
        timeout=30.0,
    )
    if r.status_code >= 400:
        raise RuntimeError(f"PATCH agent {agent_id} → HTTP {r.status_code}: {r.text[:400]}")
    return r.json()


def main() -> None:
    args = sys.argv[1:]
    dry_run = "--dry-run" in args
    args = [a for a in args if a != "--dry-run"]

    api_key = os.getenv("ELEVENLABS_API_KEY", "").strip()
    if not api_key:
        print("ERROR: ELEVENLABS_API_KEY no está en el entorno ni en .env")
        sys.exit(1)

    agent_id = (args[0] if args else os.getenv("ELEVENLABS_AGENT_ID", "")).strip()
    if not agent_id:
        print("ERROR: pasa un agent_id como argumento o define ELEVENLABS_AGENT_ID")
        sys.exit(1)

    print(f"→ GET agent {agent_id}")
    remote = get_agent(api_key, agent_id)
    name = remote.get("name")
    conv = remote.get("conversation_config") or {}
    current_tts_model = ((conv.get("tts") or {}).get("model_id")) or "(none)"
    tools_count = len(((conv.get("agent") or {}).get("prompt") or {}).get("tools") or [])
    print(f"  nombre: {name}")
    print(f"  TTS model actual: {current_tts_model}")
    print(f"  tools registradas: {tools_count}")

    payload = build_patch_payload(remote)

    if dry_run:
        import json as _json
        print("\n=== DRY RUN — no hago PATCH. Payload:\n")
        print(_json.dumps(payload, ensure_ascii=False, indent=2))
        print("\nRe-ejecuta sin --dry-run para aplicar.")
        return

    print(f"→ PATCH agent {agent_id} (TTS=flash, force_pre_tool_speech, calendar_id)")
    resp = patch_agent(api_key, agent_id, payload)
    new_tts = ((resp.get("conversation_config") or {}).get("tts") or {}).get("model_id") or "(none)"
    new_tools = ((resp.get("conversation_config") or {}).get("agent") or {}).get("prompt", {}).get("tools") or []
    n_with_fpts = sum(1 for t in new_tools if t.get("force_pre_tool_speech") is True)
    print(f"  OK. TTS model ahora: {new_tts}")
    print(f"  force_pre_tool_speech activo en: {n_with_fpts}/{len(new_tools)} tools")
    print("  calendar_id presente en mover/cancelar:",
          all(
              "calendar_id" in (((t.get("api_schema") or {}).get("request_body_schema") or {}).get("properties") or {})
              for t in new_tools
              if t.get("name") in ("mover_reserva", "cancelar_reserva")
          ))


if __name__ == "__main__":
    main()
