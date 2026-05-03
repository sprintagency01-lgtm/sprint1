"""clone_agent_sandbox.py — duplica el agente Ana actual en un sandbox.

Crea un agente nuevo en ElevenLabs con la misma config que el de prod, pero:
  - nombre: "Ana - SANDBOX v3 (NO TOCAR)"
  - tts.model_id: forzado al valor pasado por --tts-model (default: eleven_v3_conversational)
  - reusa los mismos tool_ids del agente original (no se clonan tools)
  - mantiene el mismo prompt, voice_id, dynamic_variables, turn config, ASR, audio format

Uso:
    python scripts/clone_agent_sandbox.py [--tts-model eleven_v3_conversational]

Imprime el nuevo agent_id en stdout y a /tmp/sandbox_agent_id.txt.

Requiere ELEVENLABS_API_KEY + ELEVENLABS_AGENT_ID en .env.

Para borrar el sandbox después:
    curl -X DELETE -H "xi-api-key: $ELEVENLABS_API_KEY" \\
        https://api.elevenlabs.io/v1/convai/agents/<sandbox_id>
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

API = "https://api.elevenlabs.io"
KEY = os.environ["ELEVENLABS_API_KEY"]
AID = os.environ["ELEVENLABS_AGENT_ID"]
H = {"xi-api-key": KEY, "Content-Type": "application/json"}


def get_agent() -> dict[str, Any]:
    r = httpx.get(f"{API}/v1/convai/agents/{AID}", headers=H, timeout=30.0)
    r.raise_for_status()
    return r.json()


def build_clone_payload(remote: dict[str, Any], tts_model: str, name: str) -> dict[str, Any]:
    conv = remote.get("conversation_config") or {}
    tts = dict(conv.get("tts") or {})
    tts["model_id"] = tts_model

    # Quitar campos que no debemos enviar en POST (son devueltos por GET pero rechazados en CREATE)
    agent_block = dict(conv.get("agent") or {})
    prompt_block = dict(agent_block.get("prompt") or {})
    # Mantenemos tool_ids del agente original — apuntan al mismo backend.
    # Quitamos `tools` (versión resuelta) si está, dejamos solo tool_ids.
    prompt_block.pop("tools", None)

    new_conv = {
        "agent": {
            **agent_block,
            "prompt": prompt_block,
        },
        "tts": tts,
        "turn": conv.get("turn") or {},
        "asr": conv.get("asr") or {},
        "conversation": conv.get("conversation") or {},
        "language_presets": conv.get("language_presets") or {},
    }

    payload = {
        "name": name,
        "conversation_config": new_conv,
    }
    return payload


def create_agent(payload: dict[str, Any]) -> dict[str, Any]:
    r = httpx.post(
        f"{API}/v1/convai/agents/create",
        headers=H,
        json=payload,
        timeout=60.0,
    )
    if r.status_code >= 400:
        raise RuntimeError(f"POST agents/create → HTTP {r.status_code}: {r.text[:600]}")
    return r.json()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tts-model", default="eleven_v3_conversational",
                    help="modelo TTS a poner en el sandbox (default: eleven_v3_conversational)")
    ap.add_argument("--name", default="Ana - SANDBOX v3 (NO TOCAR)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    print(f"→ GET agente actual {AID}")
    remote = get_agent()
    cur_tts = ((remote.get("conversation_config") or {}).get("tts") or {}).get("model_id")
    print(f"  TTS actual: {cur_tts}")

    payload = build_clone_payload(remote, args.tts_model, args.name)

    if args.dry_run:
        print("\n=== DRY RUN — payload del POST:\n")
        print(json.dumps({
            "name": payload["name"],
            "conversation_config.tts": payload["conversation_config"]["tts"],
            "conversation_config.agent.prompt.llm": (
                payload["conversation_config"]["agent"]["prompt"].get("llm")
            ),
            "tool_ids": (
                payload["conversation_config"]["agent"]["prompt"].get("tool_ids")
            ),
        }, ensure_ascii=False, indent=2))
        return

    print(f"→ POST /v1/convai/agents/create  (TTS={args.tts_model})")
    created = create_agent(payload)
    new_id = created.get("agent_id")
    print(f"  ✓ Sandbox creado: {new_id}")
    pathlib.Path("/tmp/sandbox_agent_id.txt").write_text(new_id + "\n")
    print(f"  agent_id guardado en /tmp/sandbox_agent_id.txt")
    print()
    print("Para benchmarkear:")
    print(f"  AGENT_ID={new_id} python3 scripts/bench_audio_ttfa.py \"<mensaje>\" 3")
    print()
    print("Para borrar el sandbox cuando termines:")
    print(f"  curl -X DELETE -H \"xi-api-key: $ELEVENLABS_API_KEY\" {API}/v1/convai/agents/{new_id}")


if __name__ == "__main__":
    main()
