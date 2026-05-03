"""bench_audio_ttfa.py — mide TTFA (time-to-first-audio) en agente real.

A diferencia de bench_ws.py (que usa text_only=True y solo mide LLM), este
harness conecta con audio activado y mide:

  - ms al primer agent_response (texto)            -> TTFR (LLM)
  - ms al primer chunk de audio del agente         -> TTFA (LLM + TTS)
  - delta TTFA - TTFR                              -> latencia pura del TTS
  - ms al primer audio TRAS la tool                -> TT_post_tool_audio
  - tool_responses + tools llamadas

Uso:
    AGENT_ID=agent_xxx python scripts/bench_audio_ttfa.py "<mensaje>" [N_runs]

  - AGENT_ID (opcional) sobrescribe ELEVENLABS_AGENT_ID del .env.
  - N_runs por defecto = 3. Calcula media + min + max.
  - Escribe cada run a /tmp/bench_audio_ttfa.jsonl.

Requiere ELEVENLABS_API_KEY en .env.
"""
from __future__ import annotations

import asyncio
import json
import os
import pathlib
import statistics
import sys
import time
from typing import Any

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
AID = os.environ.get("AGENT_ID") or os.environ["ELEVENLABS_AGENT_ID"]
H = {"xi-api-key": KEY, "Content-Type": "application/json"}


def get_agent_summary() -> dict[str, Any]:
    r = httpx.get(f"{API}/v1/convai/agents/{AID}", headers=H, timeout=15.0)
    r.raise_for_status()
    d = r.json()
    conv = d.get("conversation_config") or {}
    tts = conv.get("tts") or {}
    prompt = (conv.get("agent") or {}).get("prompt") or {}
    return {
        "agent_id": d.get("agent_id"),
        "name": d.get("name"),
        "llm": prompt.get("llm"),
        "tts_model": tts.get("model_id"),
        "voice_id": tts.get("voice_id"),
        "stability": tts.get("stability"),
        "similarity_boost": tts.get("similarity_boost"),
        "speed": tts.get("speed"),
        "opt_streaming_latency": tts.get("optimize_streaming_latency"),
        "audio_format": tts.get("agent_output_audio_format"),
    }


async def run_single_turn(user_text: str, overall_timeout_s: float = 25.0) -> dict[str, Any]:
    """Conecta WS con audio activado, envía 1 mensaje, mide TTFR y TTFA."""
    url = f"{WS_API}/v1/convai/conversation?agent_id={AID}"
    out: dict[str, Any] = {
        "user_text": user_text,
        "ms_to_first_text": None,
        "ms_to_first_audio": None,
        "ms_to_post_tool_audio": None,
        "ms_to_tool_response": None,
        "tts_overhead_ms": None,
        "tools": [],
        "first_text": None,
        "error": None,
    }
    t_sent: float | None = None

    async def _session():
        nonlocal t_sent
        async with websockets.connect(
            url,
            additional_headers={"xi-api-key": KEY},
            open_timeout=10,
            close_timeout=5,
            ping_timeout=20,
            max_size=8 * 1024 * 1024,
        ) as ws:
            init = {
                "type": "conversation_initiation_client_data",
                # NO ponemos text_only:True — queremos que TTS produzca audio.
                # El first_message no se puede sobrescribir si workspace no permite override,
                # así que lo drenamos activamente abajo antes de cronometrar.
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
                if msg.get("type") == "ping":
                    ev = msg.get("ping_event") or {}
                    await ws.send(json.dumps({"type": "pong", "event_id": ev.get("event_id")}))
            if not metadata_received:
                out["error"] = "no metadata"
                return

            # Drenar el first_message (texto + audio chunks) hasta que haya 700ms de silencio.
            # Sin esto, los chunks de audio del saludo contaminan la medición de TTFA.
            silence_window = 0.7
            last_event_at = time.monotonic()
            hard_stop = time.monotonic() + 6.0  # nunca drenar más de 6s
            while time.monotonic() < hard_stop:
                remaining = max(0.05, last_event_at + silence_window - time.monotonic())
                if remaining <= 0:
                    break
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=remaining)
                except asyncio.TimeoutError:
                    break
                last_event_at = time.monotonic()
                try:
                    msg = json.loads(raw)
                except Exception:
                    continue
                if msg.get("type") == "ping":
                    ev = msg.get("ping_event") or {}
                    await ws.send(json.dumps({"type": "pong", "event_id": ev.get("event_id")}))

            # Disparar timer y enviar mensaje del user
            t_sent = time.monotonic()
            await ws.send(json.dumps({"type": "user_message", "text": user_text}))

            got_text = False
            got_audio = False
            got_tool = False
            got_post_tool_audio = False
            turn_deadline = t_sent + 18.0

            while time.monotonic() < turn_deadline:
                try:
                    raw = await asyncio.wait_for(
                        ws.recv(), timeout=turn_deadline - time.monotonic()
                    )
                except asyncio.TimeoutError:
                    break
                try:
                    msg = json.loads(raw)
                except Exception:
                    continue
                mtype = msg.get("type")
                now_ms = (time.monotonic() - t_sent) * 1000.0

                if mtype == "ping":
                    ev = msg.get("ping_event") or {}
                    await ws.send(json.dumps({"type": "pong", "event_id": ev.get("event_id")}))
                    continue

                if mtype == "agent_response":
                    ar = ((msg.get("agent_response_event") or {}).get("agent_response")) or ""
                    if not got_text:
                        out["ms_to_first_text"] = round(now_ms, 1)
                        out["first_text"] = ar[:120]
                        got_text = True

                elif mtype == "audio":
                    if not got_audio:
                        out["ms_to_first_audio"] = round(now_ms, 1)
                        got_audio = True
                    elif got_tool and not got_post_tool_audio:
                        out["ms_to_post_tool_audio"] = round(now_ms, 1)
                        got_post_tool_audio = True

                elif mtype == "agent_tool_response":
                    atr = msg.get("agent_tool_response") or {}
                    if not got_tool:
                        out["ms_to_tool_response"] = round(now_ms, 1)
                        got_tool = True
                    out["tools"].append(atr.get("tool_name"))

                # Salida: tenemos TTFA + (si aplica) post_tool_audio.
                if got_audio and (got_post_tool_audio or now_ms > 12000):
                    break

            if (
                out["ms_to_first_audio"] is not None
                and out["ms_to_first_text"] is not None
            ):
                out["tts_overhead_ms"] = round(
                    out["ms_to_first_audio"] - out["ms_to_first_text"], 1
                )

    try:
        await asyncio.wait_for(_session(), timeout=overall_timeout_s + 5)
    except Exception as e:
        out["error"] = str(e)[:200]
    return out


async def main_async(user_text: str, n_runs: int):
    summary = get_agent_summary()
    print(f"=== Agente: {summary['name']} ({summary['agent_id']})")
    print(
        f"    LLM={summary['llm']} | TTS={summary['tts_model']} | "
        f"voice={summary['voice_id']} | stab={summary['stability']} | "
        f"speed={summary['speed']} | opt_streaming={summary['opt_streaming_latency']}"
    )
    print(f"=== Runs: {n_runs}, mensaje: {user_text!r}")
    print()

    results: list[dict[str, Any]] = []
    for i in range(1, n_runs + 1):
        t0 = time.monotonic()
        r = await run_single_turn(user_text)
        wall = round(time.monotonic() - t0, 1)
        r["run"] = i
        r["wall_s"] = wall
        r["agent_id"] = summary["agent_id"]
        r["tts_model"] = summary["tts_model"]
        results.append(r)
        with open("/tmp/bench_audio_ttfa.jsonl", "a") as f:
            f.write(json.dumps(r) + "\n")
        print(
            f"  run {i}: TTFR_text={r.get('ms_to_first_text')}ms "
            f"TTFA={r.get('ms_to_first_audio')}ms "
            f"TTS_overhead={r.get('tts_overhead_ms')}ms "
            f"TT_tool={r.get('ms_to_tool_response')}ms "
            f"PostToolAudio={r.get('ms_to_post_tool_audio')}ms "
            f"tools={r.get('tools')} wall={wall}s "
            f"err={r.get('error')}"
        )
        # pausa entre runs para evitar rate-limit
        await asyncio.sleep(1.5)

    print()

    def stats(key: str) -> str:
        vals = [r[key] for r in results if r.get(key) is not None]
        if not vals:
            return "n/a"
        return (
            f"mean={round(statistics.mean(vals), 1)} "
            f"min={min(vals)} max={max(vals)} "
            f"n={len(vals)}/{len(results)}"
        )

    print(f"  TTFR (text):        {stats('ms_to_first_text')}")
    print(f"  TTFA (first audio): {stats('ms_to_first_audio')}")
    print(f"  TTS_overhead:       {stats('tts_overhead_ms')}")
    print(f"  TT_tool_response:   {stats('ms_to_tool_response')}")
    print(f"  PostToolAudio:      {stats('ms_to_post_tool_audio')}")


def main():
    if len(sys.argv) < 2:
        print('Uso: bench_audio_ttfa.py "<mensaje>" [N_runs]')
        print('  AGENT_ID=agent_xxx puede sobrescribir ELEVENLABS_AGENT_ID')
        sys.exit(1)
    user_text = sys.argv[1]
    n_runs = int(sys.argv[2]) if len(sys.argv) > 2 else 3
    asyncio.run(main_async(user_text, n_runs))


if __name__ == "__main__":
    main()
