"""Harness WS-text-only: 1 modelo × 1 mensaje, mide TTFB real + tool_call.

Uso:
    python scripts/bench_ws.py <model> "<user_message>"

Arranca un WebSocket text-only contra el agente, envía 1 mensaje del user,
mide:
  - ms hasta primer `agent_response` (texto completo, no token).
  - ms hasta primer `client_tool_call` (si llega).
  - nombre de la tool llamada y su parameters.

Escribe resultado a /tmp/bench_ws.jsonl (append, una línea JSON por run).

Este enfoque es mucho más rápido que simulate-conversation (~3-6s por run vs
30-40s), cabe de sobra en los 45s del sandbox.

Requiere ELEVENLABS_API_KEY + ELEVENLABS_AGENT_ID en el entorno.
"""
from __future__ import annotations

import asyncio
import json
import os
import pathlib
import sys
import time

import httpx
import websockets

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
for line in (ROOT / ".env").read_text().splitlines():
    if "=" in line and not line.strip().startswith("#"):
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())

API = "https://api.elevenlabs.io"
WS_API = "wss://api.elevenlabs.io"
KEY = os.environ["ELEVENLABS_API_KEY"]
AID = os.environ["ELEVENLABS_AGENT_ID"]
H = {"xi-api-key": KEY, "Content-Type": "application/json"}


def patch_model(model: str) -> None:
    r = httpx.patch(f"{API}/v1/convai/agents/{AID}", headers=H,
                    json={"conversation_config": {"agent": {"prompt": {"llm": model}}}},
                    timeout=20.0)
    if r.status_code >= 400:
        raise RuntimeError(f"PATCH {r.status_code}: {r.text[:200]}")


async def run_single_turn(user_text: str, overall_timeout_s: float = 18.0) -> dict:
    """Conecta WS text-only, envía 1 mensaje, devuelve métricas.

    TTFR real = tiempo hasta el primer `agent_response` que NO sea el
    `first_message` pregrabado del agente. El first_message llega del server
    antes o justo después del handshake y no es representativo de la
    latencia de generación del LLM.
    """
    url = f"{WS_API}/v1/convai/conversation?agent_id={AID}"
    out: dict = {
        "user_text": user_text,
        "ms_to_first_agent_response": None,        # primer texto del agente tras mensaje user
        "ms_to_tool_response": None,                # cuando la tool (webhook) terminó y vino respuesta
        "ms_to_final_agent_response": None,         # primer agent_response tras la tool (contenido útil)
        "tool_responses": [],                       # lo que el webhook devolvió
        "agent_responses": [],                      # textos que emitió el agente
        "first_message_suppressed": False,
        "error": None,
    }
    t_sent = None
    got_first_response = False
    got_tool_response = False
    got_final_response = False

    async def _session():
        async with websockets.connect(
            url, additional_headers={"xi-api-key": KEY},
            open_timeout=10, close_timeout=5, ping_timeout=15,
        ) as ws:
            nonlocal t_sent, got_first_response, got_tool_response, got_final_response
            init = {
                "type": "conversation_initiation_client_data",
                "conversation_config_override": {
                    "conversation": {"text_only": True},
                },
            }
            await ws.send(json.dumps(init))

            # Espera al metadata
            deadline = time.monotonic() + overall_timeout_s
            metadata_received = False
            while time.monotonic() < deadline and not metadata_received:
                raw = await asyncio.wait_for(ws.recv(), timeout=deadline - time.monotonic())
                try:
                    msg = json.loads(raw)
                except Exception:
                    continue
                if msg.get("type") == "conversation_initiation_metadata":
                    metadata_received = True
                    break

            if not metadata_received:
                out["error"] = "no metadata"
                return

            # Drenar eventos pendientes ~800ms (typically el first_message).
            drain_until = time.monotonic() + 0.8
            while time.monotonic() < drain_until:
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=max(0.05, drain_until - time.monotonic()))
                except asyncio.TimeoutError:
                    break
                try:
                    msg = json.loads(raw)
                except Exception:
                    continue
                # Cualquier agent_response aquí es el first_message pregrabado.
                if msg.get("type") == "agent_response":
                    out["first_message_suppressed"] = True

            # Envía mensaje del usuario y cronometra desde aquí.
            t_sent = time.monotonic()
            await ws.send(json.dumps({"type": "user_message", "text": user_text}))

            # Límite estricto post-envío: 15s para primer response + tool.
            # El pre_tool_speech llega ~500ms y el tool_call a los 2-5s después.
            turn_deadline = t_sent + 15.0
            while time.monotonic() < turn_deadline:
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=turn_deadline - time.monotonic())
                except asyncio.TimeoutError:
                    break
                try:
                    msg = json.loads(raw)
                except Exception:
                    continue
                mtype = msg.get("type")
                now_ms = (time.monotonic() - t_sent) * 1000.0

                if mtype == "agent_response":
                    ar = ((msg.get("agent_response_event") or {}).get("agent_response")) or ""
                    if not got_first_response:
                        out["ms_to_first_agent_response"] = round(now_ms, 1)
                        got_first_response = True
                    elif got_tool_response and not got_final_response:
                        # Primera respuesta del agente TRAS la tool = info útil
                        out["ms_to_final_agent_response"] = round(now_ms, 1)
                        got_final_response = True
                    out["agent_responses"].append(ar)
                elif mtype == "agent_tool_response":
                    # La tool webhook terminó y el server nos notifica.
                    atr = msg.get("agent_tool_response") or {}
                    if not got_tool_response:
                        out["ms_to_tool_response"] = round(now_ms, 1)
                        got_tool_response = True
                    out["tool_responses"].append({
                        "tool_name": atr.get("tool_name"),
                        "tool_type": atr.get("tool_type"),
                        "is_error": atr.get("is_error"),
                    })
                elif mtype == "ping":
                    ev = (msg.get("ping_event") or {})
                    await ws.send(json.dumps({"type": "pong", "event_id": ev.get("event_id")}))

                # Condición de salida: tenemos respuesta final (post-tool) → listo.
                if got_final_response:
                    break
                # Si pasa >10s tras primer response sin tool, probablemente
                # el modelo no está llamando — cortamos.
                if got_first_response and now_ms > 10000 and not got_tool_response:
                    break

    try:
        await asyncio.wait_for(_session(), timeout=overall_timeout_s + 5)
    except Exception as e:
        out["error"] = str(e)[:200]
    return out


def main():
    if len(sys.argv) < 3:
        print('Uso: bench_ws.py <model> "<mensaje>"')
        sys.exit(1)
    model = sys.argv[1]
    user_text = sys.argv[2]

    t0 = time.monotonic()
    try:
        patch_model(model)
    except Exception as e:
        out = {"model": model, "error": f"PATCH: {e}"}
        with open("/tmp/bench_ws.jsonl", "a") as f:
            f.write(json.dumps(out) + "\n")
        print(json.dumps(out))
        return

    time.sleep(1.0)
    result = asyncio.run(run_single_turn(user_text))
    result["model"] = model
    result["wall_s"] = round(time.monotonic() - t0, 1)
    with open("/tmp/bench_ws.jsonl", "a") as f:
        f.write(json.dumps(result) + "\n")
    # Print compacto
    tool_names = [tr.get("tool_name") for tr in result.get("tool_responses", [])]
    print(f"{model:<32} TTFR={result.get('ms_to_first_agent_response')}ms "
          f"TT_tool_resp={result.get('ms_to_tool_response')}ms "
          f"TT_final={result.get('ms_to_final_agent_response')}ms "
          f"tools={tool_names} wall={result['wall_s']}s "
          f"err={result.get('error')}")


if __name__ == "__main__":
    main()
