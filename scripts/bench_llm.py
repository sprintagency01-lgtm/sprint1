"""Harness de evaluación de modelos LLM para el agente Ana.

Objetivo: encontrar el LLM con menor latencia que siga llamando bien a las
tools (consultar_disponibilidad, crear_reserva, mover_reserva, etc.).

Flujo:
  1. GET del agente remoto para guardar el estado actual (rollback).
  2. Para cada modelo candidato:
     a. PATCH conversation_config.agent.prompt.llm = modelo.
     b. Para cada escenario simulado:
        - POST /v1/convai/agents/{id}/simulate-conversation con un
          `simulated_user_config` que actúa como cliente real.
        - Parsear la respuesta: tool_calls emitidos + métricas de latencia
          por turno.
     c. Calcular: tool_success_rate, p50_ttfb_ms, p95_ttfb_ms,
        p50_ttf_sentence_ms, turnos totales, coste de llamada (según
        num tokens si la API lo da).
  3. Volver al modelo original si no se pasa `--apply=<modelo>`.
  4. Imprimir tabla ordenada.

Uso:
  python scripts/bench_llm.py                                  # evalúa todos los candidatos
  python scripts/bench_llm.py --models qwen3-30b-a3b,glm-45-air-fp8
  python scripts/bench_llm.py --apply qwen3-30b-a3b            # deja el ganador aplicado
  python scripts/bench_llm.py --scenarios reserva_simple

Requiere ELEVENLABS_API_KEY + ELEVENLABS_AGENT_ID en el entorno (o .env).
"""
from __future__ import annotations

import argparse
import json
import math
import os
import pathlib
import statistics
import sys
import time
from dataclasses import dataclass, field
from typing import Any

import httpx


HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent

# Carga del .env a mano (dotenv.find_dotenv falla con heredoc)
for line in (ROOT / ".env").read_text().splitlines():
    if "=" in line and not line.strip().startswith("#"):
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())

API = "https://api.elevenlabs.io"
API_KEY = os.environ.get("ELEVENLABS_API_KEY", "").strip()
AGENT_ID = os.environ.get("ELEVENLABS_AGENT_ID", "").strip()

if not API_KEY or not AGENT_ID:
    print("ERROR: faltan ELEVENLABS_API_KEY y/o ELEVENLABS_AGENT_ID", file=sys.stderr)
    sys.exit(1)

HEADERS = {"xi-api-key": API_KEY, "Content-Type": "application/json"}


# ---------------------------------------------------------------------------
#  Candidatos y escenarios
# ---------------------------------------------------------------------------

DEFAULT_CANDIDATES = [
    # ElevenLabs-hosted (prioridad alta, latencia ultrabaja según la UI)
    "qwen3-30b-a3b",         # "Latencia ultrabaja"
    "glm-45-air-fp8",        # "Ideal para casos agenticos"
    "gpt-oss-120b",          # experimental
    # Especialistas en tool calling
    "watt-tool-70b",
    "watt-tool-8b",
    # Google recientes
    "gemini-3-flash-preview",
    "gemini-2.5-flash-lite",
    "gemini-3.1-flash-lite-preview",
    # Baseline actual
    "gemini-2.5-flash",
]


# Cada escenario define:
#  - prompt del cliente simulado (actúa como cliente real por teléfono)
#  - expected_tools: lista de nombres de tool que DEBERÍAN aparecer para pasar
#  - first_message (opcional): qué dice el cliente simulado en su primer turno
@dataclass
class Scenario:
    name: str
    user_prompt: str
    expected_tools: tuple[str, ...]
    first_message: str | None = None
    # turnos máximos del simulador (ElevenLabs permite hasta ~10).
    max_turns: int = 8


SCENARIOS: list[Scenario] = [
    Scenario(
        name="reserva_simple",
        user_prompt=(
            "Eres Juan García. Llamas a la peluquería para reservar un CORTE DE HOMBRE "
            "para MAÑANA por la tarde. Te da igual el peluquero. "
            "Cuando te ofrezcan horas disponibles, acepta la PRIMERA que propongan. "
            "Si te preguntan tu nombre responde 'Juan'. "
            "Cuando te pidan confirmar la reserva, di 'sí, confírmalo' claramente. "
            "Nunca digas que eres una IA. Eres una persona real llamando. "
            "Frases cortas, coloquiales, como por teléfono. "
            "Tras la confirmación, despídete con 'gracias, hasta luego' y cuelga."
        ),
        expected_tools=("consultar_disponibilidad", "crear_reserva"),
        first_message="Hola, quería pedir cita",
    ),
    Scenario(
        name="reserva_con_peluquero",
        user_prompt=(
            "Eres Ana López. Llamas para cita de CORTE MUJER con MARIO el VIERNES "
            "por la mañana. Si Mario no tiene hueco, acepta sábado. "
            "Cuando te pregunten nombre responde 'Ana'. "
            "Acepta la primera hora que te ofrezcan. "
            "Confirma con 'sí' cuando te pidan confirmación. "
            "No eres IA, eres clienta. Responde breve, tono coloquial."
        ),
        expected_tools=("consultar_disponibilidad", "crear_reserva"),
        first_message="Hola, ¿podría pedir cita con Mario?",
    ),
    Scenario(
        name="mover_cita",
        user_prompt=(
            "Eres Marta Sánchez. Llamaste antes y tienes una cita. "
            "Quieres MOVERLA a otro día porque te ha surgido algo. "
            "Si te piden el día, di 'mejor el jueves por la tarde'. "
            "Acepta la primera hora que te ofrezcan. "
            "Confirma el cambio con 'sí, perfecto'. "
            "No eres IA, eres la clienta. Breve y natural."
        ),
        expected_tools=("buscar_reserva_cliente", "mover_reserva"),
        first_message="Hola, llamaba para cambiar mi cita",
    ),
    Scenario(
        name="cancelar_cita",
        user_prompt=(
            "Eres Luis Romero. Tienes una cita que quieres CANCELAR porque te ha "
            "salido un imprevisto. Cuando te lean los datos de la cita confirma "
            "que es esa. Cuando te pregunten si cancelar di 'sí, cancélala'. "
            "No eres IA, cliente real. Tono normal por teléfono."
        ),
        expected_tools=("buscar_reserva_cliente", "cancelar_reserva"),
        first_message="Hola, necesito cancelar la cita que tengo",
    ),
]


# ---------------------------------------------------------------------------
#  API helpers
# ---------------------------------------------------------------------------

def get_agent() -> dict[str, Any]:
    r = httpx.get(f"{API}/v1/convai/agents/{AGENT_ID}", headers=HEADERS, timeout=20.0)
    r.raise_for_status()
    return r.json()


def set_llm(model: str) -> None:
    """PATCH al agente con el modelo dado. Lanza si la API responde !=200."""
    payload = {"conversation_config": {"agent": {"prompt": {"llm": model}}}}
    r = httpx.patch(f"{API}/v1/convai/agents/{AGENT_ID}", headers=HEADERS, json=payload, timeout=30.0)
    if r.status_code >= 400:
        raise RuntimeError(f"set_llm({model}) → {r.status_code}: {r.text[:300]}")


def simulate(scenario: Scenario) -> dict[str, Any]:
    body: dict[str, Any] = {
        "simulation_specification": {
            "simulated_user_config": {
                "prompt": {"prompt": scenario.user_prompt, "llm": "gemini-2.5-flash"},
                "first_message": scenario.first_message,
                "language": "es",
            },
        },
    }
    r = httpx.post(
        f"{API}/v1/convai/agents/{AGENT_ID}/simulate-conversation",
        headers=HEADERS, json=body, timeout=120.0,
    )
    if r.status_code >= 400:
        raise RuntimeError(f"simulate → {r.status_code}: {r.text[:400]}")
    return r.json()


# ---------------------------------------------------------------------------
#  Análisis
# ---------------------------------------------------------------------------

def _percentile(values: list[float], q: float) -> float:
    if not values:
        return math.nan
    values = sorted(values)
    k = (len(values) - 1) * q
    f = int(k)
    c = min(f + 1, len(values) - 1)
    if f == c:
        return values[f]
    return values[f] * (c - k) + values[c] * (k - f)


def analyze_turns(convo: dict[str, Any]) -> dict[str, Any]:
    """Extrae métricas y tool_calls de una simulación."""
    # Distintos shapes observados: envuelve bajo "simulated_conversation"
    # o devuelve directamente la lista.
    if isinstance(convo, dict):
        if "simulated_conversation" in convo:
            turns = convo.get("simulated_conversation") or []
        else:
            turns = convo.get("transcript") or convo.get("conversation") or []
    else:
        turns = convo

    ttfb_ms: list[float] = []
    ttf_sentence_ms: list[float] = []
    tt_last_ms: list[float] = []
    tool_calls: list[str] = []
    agent_turns = 0

    for t in turns:
        if not isinstance(t, dict):
            continue
        if t.get("role") == "agent":
            agent_turns += 1
            metrics = (t.get("conversation_turn_metrics") or {}).get("metrics") or {}
            def _get(k):
                m = metrics.get(k)
                return (m.get("elapsed_time") if isinstance(m, dict) else None)
            if (v := _get("convai_llm_service_ttfb")) is not None:
                ttfb_ms.append(v * 1000)
            if (v := _get("convai_llm_service_ttf_sentence")) is not None:
                ttf_sentence_ms.append(v * 1000)
            if (v := _get("convai_llm_service_tt_last_sentence")) is not None:
                tt_last_ms.append(v * 1000)
            for call in t.get("tool_calls") or []:
                name = call.get("tool_name") or call.get("name") or ""
                if name:
                    tool_calls.append(name)

    return {
        "agent_turns": agent_turns,
        "ttfb_p50": _percentile(ttfb_ms, 0.50),
        "ttfb_p95": _percentile(ttfb_ms, 0.95),
        "ttfs_p50": _percentile(ttf_sentence_ms, 0.50),
        "ttfs_p95": _percentile(ttf_sentence_ms, 0.95),
        "tt_last_p50": _percentile(tt_last_ms, 0.50),
        "tool_calls": tool_calls,
    }


@dataclass
class ScenarioResult:
    scenario: str
    tool_calls: list[str]
    expected: tuple[str, ...]
    ok: bool
    ttfb_p50: float
    ttfb_p95: float
    ttfs_p50: float
    tt_last_p50: float
    agent_turns: int
    error: str | None = None


@dataclass
class ModelResult:
    model: str
    scenarios: list[ScenarioResult] = field(default_factory=list)

    @property
    def tool_success_rate(self) -> float:
        if not self.scenarios:
            return 0.0
        ok = sum(1 for s in self.scenarios if s.ok)
        return ok / len(self.scenarios)

    @property
    def mean_ttfb_p50(self) -> float:
        xs = [s.ttfb_p50 for s in self.scenarios if not math.isnan(s.ttfb_p50)]
        return statistics.mean(xs) if xs else math.nan


# ---------------------------------------------------------------------------
#  Runner
# ---------------------------------------------------------------------------

def _expected_matches_calls(expected: tuple[str, ...], actual: list[str]) -> bool:
    """True si las tools esperadas aparecieron EN ORDEN (permite extras intermedios)."""
    i = 0
    for call in actual:
        if i < len(expected) and call == expected[i]:
            i += 1
    return i == len(expected)


def evaluate_model(model: str, scenarios: list[Scenario]) -> ModelResult:
    print(f"\n▶ Probando modelo: {model}")
    try:
        set_llm(model)
    except Exception as e:
        print(f"  ✗ set_llm falló: {e}")
        return ModelResult(model=model, scenarios=[
            ScenarioResult(scenario=s.name, tool_calls=[], expected=s.expected_tools,
                            ok=False, ttfb_p50=math.nan, ttfb_p95=math.nan,
                            ttfs_p50=math.nan, tt_last_p50=math.nan, agent_turns=0,
                            error=str(e)[:200])
            for s in scenarios
        ])
    # Pequeña pausa para que el cambio se propague dentro de ElevenLabs.
    time.sleep(1.5)

    result = ModelResult(model=model)
    for sc in scenarios:
        try:
            t0 = time.monotonic()
            convo = simulate(sc)
            wall_s = time.monotonic() - t0
            stats = analyze_turns(convo)
            tc = stats["tool_calls"]
            ok = _expected_matches_calls(sc.expected_tools, tc)
            result.scenarios.append(ScenarioResult(
                scenario=sc.name,
                tool_calls=tc,
                expected=sc.expected_tools,
                ok=ok,
                ttfb_p50=stats["ttfb_p50"],
                ttfb_p95=stats["ttfb_p95"],
                ttfs_p50=stats["ttfs_p50"],
                tt_last_p50=stats["tt_last_p50"],
                agent_turns=stats["agent_turns"],
            ))
            mark = "✓" if ok else "✗"
            print(f"  {mark} {sc.name}: tools={tc} (esperado {list(sc.expected_tools)}) "
                  f"ttfb_p50={stats['ttfb_p50']:.0f}ms turns={stats['agent_turns']} wall={wall_s:.1f}s")
        except Exception as e:
            print(f"  ✗ {sc.name}: ERROR {e}")
            result.scenarios.append(ScenarioResult(
                scenario=sc.name, tool_calls=[], expected=sc.expected_tools,
                ok=False, ttfb_p50=math.nan, ttfb_p95=math.nan,
                ttfs_p50=math.nan, tt_last_p50=math.nan, agent_turns=0,
                error=str(e)[:200],
            ))
    return result


def print_summary(results: list[ModelResult]) -> None:
    print("\n" + "=" * 80)
    print(f"{'MODELO':<35} {'TOOLS OK':>10} {'TTFB p50':>10} {'TURNOS':>8}")
    print("-" * 80)
    # Ordenar: primero los que pasan tools, luego por TTFB medio
    results_sorted = sorted(
        results,
        key=lambda r: (-r.tool_success_rate, r.mean_ttfb_p50 if not math.isnan(r.mean_ttfb_p50) else 1e9),
    )
    for r in results_sorted:
        turns = sum(s.agent_turns for s in r.scenarios)
        mtb = r.mean_ttfb_p50
        ttfb_txt = f"{mtb:.0f}ms" if not math.isnan(mtb) else "n/a"
        ok_txt = f"{int(r.tool_success_rate * 100)}% ({sum(1 for s in r.scenarios if s.ok)}/{len(r.scenarios)})"
        print(f"{r.model:<35} {ok_txt:>10} {ttfb_txt:>10} {turns:>8}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--models", help="lista de modelos separados por coma",
                        default=",".join(DEFAULT_CANDIDATES))
    parser.add_argument("--scenarios", help="nombres de escenarios separados por coma",
                        default=",".join(s.name for s in SCENARIOS))
    parser.add_argument("--apply", help="si se pasa, aplica este modelo al agente al terminar")
    parser.add_argument("--no-restore", action="store_true",
                        help="no restaura el modelo original al terminar")
    args = parser.parse_args()

    models = [m.strip() for m in args.models.split(",") if m.strip()]
    scenario_names = set(s.strip() for s in args.scenarios.split(","))
    scenarios = [s for s in SCENARIOS if s.name in scenario_names]

    # Estado actual (para rollback)
    original = get_agent()
    original_llm = (((original.get("conversation_config") or {}).get("agent") or {})
                    .get("prompt", {}).get("llm")) or "gemini-2.5-flash"
    print(f"LLM original del agente: {original_llm}")
    print(f"Modelos a probar: {models}")
    print(f"Escenarios: {[s.name for s in scenarios]}")

    results: list[ModelResult] = []
    for m in models:
        results.append(evaluate_model(m, scenarios))

    print_summary(results)

    # Aplicar ganador si se pidió
    if args.apply:
        print(f"\n→ APLICANDO {args.apply}")
        set_llm(args.apply)
        print("OK")
    elif not args.no_restore:
        print(f"\n→ Restaurando modelo original: {original_llm}")
        set_llm(original_llm)
        print("OK")


if __name__ == "__main__":
    main()
