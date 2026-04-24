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


def build_agent_patch_payload(remote_agent: dict[str, Any]) -> dict[str, Any]:
    """Construye el payload de PATCH para el agente (solo TTS).

    NOTA: las `tools` que devuelve GET /agents/{id} son un reflejo resuelto
    de `tool_ids`, pero PATCH sobre `prompt.tools` NO las modifica — las
    tools son entidades separadas en `/v1/convai/tools/{tool_id}`. Por eso
    este payload solo toca `tts`; las tools se patchean aparte en
    `patch_tools`.
    """
    conv = remote_agent.get("conversation_config") or {}
    tts_block = dict(conv.get("tts") or {})
    tts_block["model_id"] = "eleven_flash_v2_5"
    return {"conversation_config": {"tts": tts_block}}


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


def get_tool(api_key: str, tool_id: str) -> dict[str, Any]:
    r = httpx.get(
        f"{API_BASE}/v1/convai/tools/{tool_id}",
        headers=_headers(api_key),
        timeout=15.0,
    )
    if r.status_code >= 400:
        raise RuntimeError(f"GET tool {tool_id} → HTTP {r.status_code}: {r.text[:400]}")
    return r.json()


def patch_tool(api_key: str, tool_id: str, tool_config: dict[str, Any]) -> dict[str, Any]:
    """PATCH a una tool. ElevenLabs envuelve el body en `tool_config`."""
    r = httpx.patch(
        f"{API_BASE}/v1/convai/tools/{tool_id}",
        headers=_headers(api_key),
        json={"tool_config": tool_config},
        timeout=20.0,
    )
    if r.status_code >= 400:
        raise RuntimeError(f"PATCH tool {tool_id} → HTTP {r.status_code}: {r.text[:400]}")
    return r.json()


def mutate_tool_for_latency(tool_config: dict[str, Any]) -> dict[str, Any]:
    """Devuelve un tool_config mutado con las mejoras de latencia aplicadas.

    Aplica:
      - `pre_tool_speech: "force"` (enum: 'auto'|'force'|'off') — sin esto,
        el booleano `force_pre_tool_speech` es ignorado por la API.
      - `force_pre_tool_speech: True` (para que refleje el estado deseado).
      - `calendar_id` opcional en el schema de mover_reserva/cancelar_reserva.
    """
    tc = dict(tool_config)
    tc["pre_tool_speech"] = "force"
    tc["force_pre_tool_speech"] = True

    name = tc.get("name") or ""
    if name in ("mover_reserva", "cancelar_reserva"):
        api_schema = dict(tc.get("api_schema") or {})
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
            tc["api_schema"] = api_schema
    return tc


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
    prompt_block = (conv.get("agent") or {}).get("prompt") or {}
    tool_ids: list[str] = list(prompt_block.get("tool_ids") or [])
    print(f"  nombre: {name}")
    print(f"  TTS model actual: {current_tts_model}")
    print(f"  tool_ids: {len(tool_ids)}")

    agent_payload = build_agent_patch_payload(remote)

    if dry_run:
        import json as _json
        print("\n=== DRY RUN — no hago PATCH. Payload del agente:\n")
        print(_json.dumps(agent_payload, ensure_ascii=False, indent=2))
        print("\nTools a patchear (una a una):")
        for tid in tool_ids:
            try:
                tool_body = get_tool(api_key, tid)
                tc = tool_body.get("tool_config") or tool_body
                mutated = mutate_tool_for_latency(tc)
                changed = {
                    "pre_tool_speech": mutated.get("pre_tool_speech"),
                    "force_pre_tool_speech": mutated.get("force_pre_tool_speech"),
                    "has_calendar_id": "calendar_id" in (
                        ((mutated.get("api_schema") or {}).get("request_body_schema") or {}).get("properties") or {}
                    ),
                }
                print(f"  {tid} ({tc.get('name')}): {changed}")
            except Exception as e:
                print(f"  {tid}: GET falló: {e}")
        print("\nRe-ejecuta sin --dry-run para aplicar.")
        return

    print(f"→ PATCH agent {agent_id} (TTS=flash)")
    resp = patch_agent(api_key, agent_id, agent_payload)
    new_tts = ((resp.get("conversation_config") or {}).get("tts") or {}).get("model_id") or "(none)"
    print(f"  OK. TTS model ahora: {new_tts}")

    print(f"→ PATCH {len(tool_ids)} tool(s) individualmente (pre_tool_speech=force, calendar_id donde aplique)")
    for tid in tool_ids:
        try:
            tool_body = get_tool(api_key, tid)
            tc = tool_body.get("tool_config") or tool_body
            mutated = mutate_tool_for_latency(tc)
            after = patch_tool(api_key, tid, mutated)
            after_tc = after.get("tool_config") or after
            tname = after_tc.get("name") or tid
            pts = after_tc.get("pre_tool_speech")
            fpts = after_tc.get("force_pre_tool_speech")
            has_cal = "calendar_id" in (
                ((after_tc.get("api_schema") or {}).get("request_body_schema") or {}).get("properties") or {}
            )
            print(f"  [{tname}] OK pre_tool_speech={pts} force={fpts} calendar_id_in_schema={has_cal}")
        except Exception as e:
            print(f"  [{tid}] ERROR: {e}")


if __name__ == "__main__":
    main()
