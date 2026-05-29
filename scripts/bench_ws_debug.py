"""Versión debug de bench_ws que captura TODOS los agent_responses y
los parametros de tool_calls vía agent_tool_response."""
import asyncio, json, os, pathlib, sys, time
import httpx, websockets

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
for line in (ROOT / ".env").read_text().splitlines():
    if "=" in line and not line.strip().startswith("#"):
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())

KEY = os.environ["ELEVENLABS_API_KEY"]
AID = os.environ["ELEVENLABS_AGENT_ID"]


async def run(first_user_msg: str, follow_ups: list[str]):
    """Conecta WS, drena first_message, envía user_msg, escucha todo.
    Después del primer tool_call envía follow-up si hay."""
    url = f"wss://api.elevenlabs.io/v1/convai/conversation?agent_id={AID}"
    events = []
    async with websockets.connect(url, additional_headers={"xi-api-key": KEY},
                                  open_timeout=10, ping_timeout=30) as ws:
        await ws.send(json.dumps({
            "type": "conversation_initiation_client_data",
            "conversation_config_override": {"conversation": {"text_only": True}},
        }))
        # drena metadata
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=deadline - time.monotonic())
                msg = json.loads(raw)
                if msg.get("type") == "conversation_initiation_metadata":
                    print("METADATA:", json.dumps(msg, indent=2, ensure_ascii=False)[:2000])
                    break
            except asyncio.TimeoutError:
                break
        # drena ~1s de first_message
        drain_end = time.monotonic() + 1.2
        while time.monotonic() < drain_end:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=max(0.05, drain_end - time.monotonic()))
                msg = json.loads(raw)
                if msg.get("type") == "agent_response":
                    ar = ((msg.get("agent_response_event") or {}).get("agent_response")) or ""
                    print(f"[first_message] {ar[:200]}")
            except asyncio.TimeoutError:
                break

        async def send_and_listen(user_msg, wait_s=15.0):
            await ws.send(json.dumps({"type": "user_message", "text": user_msg}))
            t0 = time.monotonic()
            ended = time.monotonic() + wait_s
            while time.monotonic() < ended:
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=max(0.05, ended - time.monotonic()))
                except asyncio.TimeoutError:
                    break
                msg = json.loads(raw)
                elapsed = (time.monotonic() - t0) * 1000
                t = msg.get("type")
                if t == "agent_response":
                    ar = ((msg.get("agent_response_event") or {}).get("agent_response")) or ""
                    print(f"  [{elapsed:.0f}ms] agent_response: {ar}")
                elif t == "agent_tool_response":
                    atr = msg.get("agent_tool_response") or {}
                    print(f"  [{elapsed:.0f}ms] agent_tool_response: name={atr.get('tool_name')} type={atr.get('tool_type')}")
                    # ver result si llega
                    r = atr.get("tool_result") or atr.get("result")
                    if r:
                        print(f"    result: {str(r)[:300]}")
                elif t == "ping":
                    ev = msg.get("ping_event") or {}
                    await ws.send(json.dumps({"type": "pong", "event_id": ev.get("event_id")}))
                elif t == "agent_response_correction":
                    pass
                else:
                    print(f"  [{elapsed:.0f}ms] {t}: {json.dumps(msg, ensure_ascii=False)[:300]}")

        print(f"\n>>> USER: {first_user_msg}")
        await send_and_listen(first_user_msg, wait_s=10)
        for fu in follow_ups:
            print(f"\n>>> USER: {fu}")
            await send_and_listen(fu, wait_s=10)


if __name__ == "__main__":
    asyncio.run(run(
        first_user_msg="Hola, quiero una reserva para mañana",
        follow_ups=[
            "De hombre",
            "Emilio Arizmendi",
        ],
    ))
