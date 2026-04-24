"""Evalúa UN modelo + UN escenario, escribe resultado JSON a /tmp/bench_results.jsonl.

Uso:
    python scripts/bench_one.py <model> <scenario_name>

scenario_name ∈ {reserva_simple, reserva_con_peluquero, mover_cita, cancelar_cita}.

Cada ejecución:
  1. PATCH al agente con el modelo.
  2. Espera 1s de propagación.
  3. POST /simulate-conversation con el prompt del escenario.
  4. Parseа turnos, tool_calls, métricas TTFB / TTF_sentence.
  5. Append a /tmp/bench_results.jsonl (una línea por ejecución).

No restaura el modelo original; el orquestador lo hace al final.
"""
from __future__ import annotations

import json
import math
import os
import pathlib
import sys
import time

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

SCENARIOS = {
    "reserva_simple": {
        "prompt": (
            "Eres Juan García, cliente llamando por teléfono. Respuestas BREVÍSIMAS "
            "(1 frase). Cuando te ofrezcan horas, acepta la PRIMERA sin dudar. "
            "Confirma con 'sí, confírmala'. Despídete con 'gracias, hasta luego'. "
            "No eres IA."
        ),
        # Mensaje inicial condensado: nombre + servicio + fecha → el agente debería
        # llamar consultar_disponibilidad directamente sin preguntar nombre.
        "first_message": "Hola soy Juan, quiero cita mañana por la tarde para corte de hombre, me da igual el peluquero",
        "expected": ["consultar_disponibilidad", "crear_reserva"],
    },
    "reserva_con_peluquero": {
        "prompt": (
            "Eres Ana López. Cita de CORTE MUJER con MARIO el VIERNES mañana. "
            "Si Mario no tiene hueco, acepta otra hora el viernes. Responde nombre "
            "'Ana' si preguntan. Acepta primera hora ofrecida. Confirma con 'sí'. "
            "Despídete. No eres IA, eres clienta real."
        ),
        "first_message": "Hola, cita con Mario el viernes",
        "expected": ["consultar_disponibilidad", "crear_reserva"],
    },
    "mover_cita": {
        "prompt": (
            "Eres Marta Sánchez. Ya tienes una cita reservada y quieres MOVERLA "
            "porque te ha surgido algo. Prefieres 'el jueves por la tarde'. "
            "Acepta la primera hora ofrecida. Confirma con 'sí'. Eres clienta real."
        ),
        "first_message": "Hola, llamaba para cambiar mi cita",
        "expected": ["buscar_reserva_cliente", "mover_reserva"],
    },
    "cancelar_cita": {
        "prompt": (
            "Eres Luis Romero. Tienes una cita reservada que quieres CANCELAR "
            "por un imprevisto. Cuando te lean los datos, confírmalo como tuyo. "
            "Cuando pregunten si cancelar di 'sí, cancélala'. Cliente real."
        ),
        "first_message": "Hola, necesito cancelar mi cita",
        "expected": ["buscar_reserva_cliente", "cancelar_reserva"],
    },
}


def _percentile(xs, q):
    if not xs:
        return float("nan")
    xs = sorted(xs)
    k = (len(xs) - 1) * q
    f = int(k)
    c = min(f + 1, len(xs) - 1)
    if f == c:
        return xs[f]
    return xs[f] * (c - k) + xs[c] * (k - f)


def main():
    if len(sys.argv) < 3:
        print("Uso: bench_one.py <model> <scenario>")
        sys.exit(1)
    model = sys.argv[1]
    scenario = sys.argv[2]
    if scenario not in SCENARIOS:
        print(f"ERROR: escenario desconocido {scenario}. Opciones: {list(SCENARIOS)}")
        sys.exit(2)
    sc = SCENARIOS[scenario]

    result = {"model": model, "scenario": scenario, "error": None}

    # 1) PATCH al modelo
    t_start = time.monotonic()
    try:
        r = httpx.patch(f"{API}/v1/convai/agents/{AID}", headers=H,
                        json={"conversation_config": {"agent": {"prompt": {"llm": model}}}},
                        timeout=20.0)
        if r.status_code >= 400:
            result["error"] = f"PATCH {r.status_code}: {r.text[:200]}"
            _write(result); print(json.dumps(result)); return
    except Exception as e:
        result["error"] = f"PATCH exception: {e}"
        _write(result); print(json.dumps(result)); return
    time.sleep(1.0)

    # 2) Simulate
    # User simulator con gemini-2.5-flash-lite para respuestas más breves y
    # rápidas (menos turnos totales).
    body = {
        "simulation_specification": {
            "simulated_user_config": {
                "prompt": {"prompt": sc["prompt"], "llm": "gemini-2.5-flash-lite"},
                "first_message": sc["first_message"],
                "language": "es",
            },
        },
    }
    try:
        t_sim = time.monotonic()
        # Timeout ajustado al bash call de 45s (deja margen para analyze+write).
        r2 = httpx.post(f"{API}/v1/convai/agents/{AID}/simulate-conversation",
                        headers=H, json=body, timeout=42.0)
        sim_wall = time.monotonic() - t_sim
        if r2.status_code >= 400:
            result["error"] = f"SIM {r2.status_code}: {r2.text[:200]}"
            result["sim_wall_s"] = round(sim_wall, 1)
            _write(result); print(json.dumps(result)); return
        d = r2.json()
    except httpx.ReadTimeout:
        result["error"] = "SIM timeout (>38s)"
        result["sim_wall_s"] = 38.0
        _write(result); print(json.dumps(result)); return
    except Exception as e:
        result["error"] = f"SIM exception: {e}"
        _write(result); print(json.dumps(result)); return

    # 3) Analyze
    turns = d.get("simulated_conversation") or []
    ttfbs, ttf_sents, tt_lasts = [], [], []
    tool_calls = []
    agent_turns = 0
    for t in turns:
        if t.get("role") == "agent":
            agent_turns += 1
            metrics = (t.get("conversation_turn_metrics") or {}).get("metrics") or {}
            def g(k):
                m = metrics.get(k)
                return (m.get("elapsed_time") if isinstance(m, dict) else None)
            if (v := g("convai_llm_service_ttfb")) is not None: ttfbs.append(v * 1000)
            if (v := g("convai_llm_service_ttf_sentence")) is not None: ttf_sents.append(v * 1000)
            if (v := g("convai_llm_service_tt_last_sentence")) is not None: tt_lasts.append(v * 1000)
            for tc in t.get("tool_calls") or []:
                nm = tc.get("tool_name") or tc.get("name") or ""
                if nm:
                    tool_calls.append(nm)

    # 4) Match esperados en orden (permite extras intermedios)
    expected = sc["expected"]
    i = 0
    for c in tool_calls:
        if i < len(expected) and c == expected[i]:
            i += 1
    ok = (i == len(expected))

    result.update({
        "ok": ok,
        "tool_calls": tool_calls,
        "expected": expected,
        "agent_turns": agent_turns,
        "total_turns": len(turns),
        "ttfb_p50": _percentile(ttfbs, 0.5),
        "ttfb_p95": _percentile(ttfbs, 0.95),
        "ttfs_p50": _percentile(ttf_sents, 0.5),
        "tt_last_p50": _percentile(tt_lasts, 0.5),
        "sim_wall_s": round(sim_wall, 1),
        "total_wall_s": round(time.monotonic() - t_start, 1),
    })
    _write(result)
    print(json.dumps({k: v for k, v in result.items() if k not in ("tool_calls", "expected")}))


def _write(r):
    with open("/tmp/bench_results.jsonl", "a") as f:
        f.write(json.dumps(r) + "\n")


if __name__ == "__main__":
    main()
