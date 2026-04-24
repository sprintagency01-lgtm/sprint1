"""Test de diálogo contra el agente Ana real.

Ejecuta una conversación completa con un usuario simulado, captura todo el
transcript y verifica que se cumplen las reglas del prompt:

  1. Nombre se pide DESPUÉS de ofrecer huecos (no antes de consultar).
  2. UNA pregunta por turno — ningún mensaje del agente con dos "?".
  3. El año de las fechas ISO enviadas a las tools coincide con hoy.
  4. Las tools correctas se llaman en el orden correcto (consultar_disponibilidad
     antes de crear_reserva; buscar_reserva_cliente antes de mover/cancelar).
  5. Ana no alucina huecos: si el backend devuelve `huecos: []` y sin error,
     Ana NO inventa horas concretas.
  6. `peluquero_preferido` queda vacío cuando el usuario no lo menciona.
  7. Al crear_reserva, `telefono_cliente` NO es "None"/"null".

Uso:
    python scripts/test_dialog.py                # corre todos los escenarios
    python scripts/test_dialog.py reserva_simple # solo uno

Cada test abre una conversation real vía /v1/convai/agents/{id}/simulate-conversation
(con un simulated_user con guion fijo), descarga el transcript vía
/v1/convai/conversations/{id} y evalúa los checks. Output: PASS/FAIL por check.

Es lento (30-50s por escenario). Diseñado para iterar de forma defensiva
cuando tocamos el prompt.
"""
from __future__ import annotations

import json
import os
import pathlib
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable
from zoneinfo import ZoneInfo

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

TZ = ZoneInfo("Europe/Madrid")
CURRENT_YEAR = datetime.now(TZ).year


# ---------------------------------------------------------------------------
#  Checks — cada uno recibe el transcript completo y devuelve (ok, mensaje)
# ---------------------------------------------------------------------------

Check = Callable[[list[dict]], tuple[bool, str]]


def _agent_turns(turns: list[dict]) -> list[dict]:
    return [t for t in turns if t.get("role") == "agent"]


def _tool_calls(turns: list[dict]) -> list[dict]:
    out = []
    for t in _agent_turns(turns):
        for tc in t.get("tool_calls") or []:
            out.append(tc)
    return out


def _tool_call_args(tc: dict) -> dict:
    """Args normalizados de un tool_call."""
    a = tc.get("params_as_json") or tc.get("parameters") or tc.get("params") or {}
    if isinstance(a, str):
        try:
            a = json.loads(a)
        except Exception:
            a = {}
    # Limpiar campos system__* que añade ElevenLabs
    return {k: v for k, v in (a or {}).items() if not k.startswith("system__")}


def check_nombre_al_final(turns: list[dict]) -> tuple[bool, str]:
    """No se pregunta el nombre hasta después de ofrecer huecos.

    Detecta la primera vez que el agente dice "a qué nombre" / "tu nombre" /
    "cómo te llamas", y confirma que hubo al menos un `consultar_disponibilidad`
    antes.
    """
    first_name_ask_turn = None
    rx = re.compile(r"(a qué nombre|tu nombre|cómo te llamas|para qué nombre|a nombre de quién)", re.IGNORECASE)
    for i, t in enumerate(turns):
        if t.get("role") == "agent":
            msg = t.get("message") or ""
            if rx.search(msg):
                first_name_ask_turn = i
                break
    if first_name_ask_turn is None:
        return True, "agente no preguntó nombre (puede que el user lo dijo solo — OK)"
    # ¿Hubo consultar_disponibilidad antes?
    for j in range(first_name_ask_turn):
        for tc in turns[j].get("tool_calls") or []:
            if (tc.get("tool_name") or tc.get("name")) == "consultar_disponibilidad":
                return True, f"OK — nombre preguntado en turno #{first_name_ask_turn} tras consultar_disponibilidad"
    return False, f"FAIL — agente pregunta nombre en turno #{first_name_ask_turn} ANTES de consultar_disponibilidad"


def check_una_pregunta_por_turno(turns: list[dict]) -> tuple[bool, str]:
    """Cada mensaje del agente tiene como mucho una frase interrogativa real.

    Heurística: contar "?" finales en el mensaje tras quitar muletillas
    conocidas. Además detectar patrones tipo "X. Y." cuando X es pregunta
    (por la inversión implícita del español: "dime qué prefieres, X o Y").
    """
    offenders = []
    for i, t in enumerate(turns):
        if t.get("role") != "agent":
            continue
        msg = (t.get("message") or "").strip()
        if not msg:
            continue
        # Ignorar el first_message — no es parte del flujo iterativo.
        if i == 0:
            continue
        # Primero quitamos muletillas interrogativas al final: "¿vale?", "¿ok?",
        # "¿te parece bien?", "¿de acuerdo?", "¿sí?".
        msg_core = msg
        for _ in range(3):
            new = re.sub(r"(.*?)[¿¡]\s*(vale|ok|sí|te parece(?: bien)?|de acuerdo)\s*\?+\s*$",
                          r"\1", msg_core, flags=re.IGNORECASE).strip()
            if new == msg_core:
                break
            msg_core = new
        # Contar número de "?"
        n_q = msg_core.count("?")
        if n_q >= 2:
            offenders.append((i, msg[:180]))
    if offenders:
        s = "; ".join(f"t#{i}: {m!r}" for i, m in offenders[:3])
        return False, f"FAIL — {len(offenders)} turno(s) con 2+ preguntas: {s}"
    return True, "OK — ningún turno con 2+ preguntas reales"


def check_año_correcto(turns: list[dict]) -> tuple[bool, str]:
    """Todos los args ISO a tools tienen el año actual."""
    bad = []
    for tc in _tool_calls(turns):
        args = _tool_call_args(tc)
        for k, v in args.items():
            if not isinstance(v, str):
                continue
            m = re.match(r"^(\d{4})-\d{2}-\d{2}T", v)
            if m:
                year = int(m.group(1))
                if year != CURRENT_YEAR:
                    bad.append((tc.get("tool_name") or tc.get("name"), k, v))
    if bad:
        s = "; ".join(f"{n}.{k}={v}" for n, k, v in bad[:3])
        return False, f"FAIL — {len(bad)} fecha(s) con año != {CURRENT_YEAR}: {s}"
    return True, f"OK — todas las fechas ISO con año {CURRENT_YEAR}"


def check_orden_tools(turns: list[dict]) -> tuple[bool, str]:
    """consultar_disponibilidad antes de crear_reserva; buscar_reserva_cliente antes de mover/cancelar."""
    seen = set()
    errors = []
    for tc in _tool_calls(turns):
        name = tc.get("tool_name") or tc.get("name") or ""
        if name == "crear_reserva" and "consultar_disponibilidad" not in seen:
            errors.append("crear_reserva llamado sin consultar_disponibilidad previo")
        if name == "mover_reserva" and "buscar_reserva_cliente" not in seen:
            errors.append("mover_reserva sin buscar_reserva_cliente previo")
        if name == "cancelar_reserva" and "buscar_reserva_cliente" not in seen:
            errors.append("cancelar_reserva sin buscar_reserva_cliente previo")
        seen.add(name)
    if errors:
        return False, f"FAIL — {'; '.join(errors)}"
    return True, "OK — orden de tools correcto"


def check_peluquero_vacio_si_no_dicho(user_msgs_lower: str) -> Check:
    """Si el cliente NO menciona un peluquero, `peluquero_preferido` debe ir vacío."""
    def fn(turns: list[dict]) -> tuple[bool, str]:
        mentioned = any(name in user_msgs_lower for name in ("mario", "marcos"))
        for tc in _tool_calls(turns):
            if (tc.get("tool_name") or tc.get("name")) != "consultar_disponibilidad":
                continue
            pref = _tool_call_args(tc).get("peluquero_preferido", "")
            pref = (pref or "").strip().lower()
            if pref and pref not in ("", "sin preferencia"):
                if not mentioned:
                    return False, f"FAIL — peluquero_preferido={pref!r} pero cliente NO lo mencionó"
        return True, "OK — peluquero_preferido respeta regla 8"
    return fn


def check_telefono_no_none(turns: list[dict]) -> tuple[bool, str]:
    """En crear_reserva, telefono_cliente no es literal 'None'/'null'."""
    for tc in _tool_calls(turns):
        if (tc.get("tool_name") or tc.get("name")) != "crear_reserva":
            continue
        tel = _tool_call_args(tc).get("telefono_cliente", "")
        if isinstance(tel, str) and tel.strip().lower() in ("none", "null"):
            return False, f"FAIL — crear_reserva con telefono_cliente={tel!r}"
    return True, "OK — telefono_cliente limpio en crear_reserva"


def check_no_alucina_huecos(turns: list[dict]) -> tuple[bool, str]:
    """Si consultar_disponibilidad devuelve huecos=[] sin error, el siguiente
    mensaje del agente NO debe proponer horas concretas.

    OJO: `simulate-conversation` de ElevenLabs NO ejecuta realmente las tools
    — devuelve `result_value="Tool Called."` como string literal, y deja que
    el agente improvise la respuesta. En ese modo no podemos decidir si
    alucina, así que devolvemos SKIP. En llamadas reales el tool sí se
    ejecuta y devuelve JSON con huecos reales.
    """
    rx_hora = re.compile(r"\b([01]?\d|2[0-3])[:\.]\d{2}\b|\ba las (?:una|dos|tres|cuatro|cinco|seis|siete|ocho|nueve|diez|once|doce)\b", re.IGNORECASE)
    offenders = []
    parseable_result_found = False
    for i, t in enumerate(turns):
        if t.get("role") != "agent":
            continue
        for tr in t.get("tool_results") or []:
            if (tr.get("tool_name") or tr.get("name")) != "consultar_disponibilidad":
                continue
            res = tr.get("result_value") or tr.get("result") or ""
            # En simulate-conversation el result es "Tool Called." (string
            # no-JSON). En ese caso no podemos validar alucinación.
            if isinstance(res, str) and not res.strip().startswith("{"):
                continue
            try:
                parsed = json.loads(res) if isinstance(res, str) else res
            except Exception:
                continue
            parseable_result_found = True
            if parsed.get("error"):
                continue
            if (parsed.get("huecos") or []) != []:
                continue
            for k in range(i + 1, min(i + 4, len(turns))):
                if turns[k].get("role") == "agent":
                    nxt = turns[k].get("message") or ""
                    if rx_hora.search(nxt):
                        offenders.append(f"t#{i}→t#{k}: {nxt[:100]!r}")
                    break
    if not parseable_result_found:
        return True, "SKIP — simulate-conversation no ejecuta tools (resultado 'Tool Called.'); solo verificable en llamada real"
    if offenders:
        return False, f"FAIL — Ana propuso horas tras huecos=[]: {'; '.join(offenders[:2])}"
    return True, "OK — no alucina horas tras huecos vacíos"


# ---------------------------------------------------------------------------
#  Escenarios
# ---------------------------------------------------------------------------

@dataclass
class Scenario:
    name: str
    user_prompt: str
    first_message: str
    expected_tools: tuple[str, ...]
    extra_checks: list[Check] = field(default_factory=list)
    user_mentioned_lower: str = ""  # para check_peluquero_vacio


SCENARIOS = [
    Scenario(
        name="reserva_sin_peluquero",
        user_prompt=(
            "Eres Marcos, cliente de la peluquería, llamando por teléfono. "
            "Quieres CORTE DE HOMBRE para MAÑANA por la tarde. Sin peluquero "
            "preferido (te da igual). Acepta la primera hora que te ofrezcan. "
            "Si te preguntan el nombre responde 'Marcos'. Confirma con 'sí' "
            "cuando te pidan. Despídete con 'gracias, hasta luego'. NO eres IA, "
            "eres cliente real. Frases cortas, UNA respuesta por turno."
        ),
        first_message="Hola, quería cita para mañana por la tarde, corte de hombre",
        expected_tools=("consultar_disponibilidad", "crear_reserva"),
        user_mentioned_lower="",
    ),
    Scenario(
        name="reserva_con_peluquero",
        user_prompt=(
            "Eres Ana, clienta. Quieres CORTE MUJER con MARIO el VIERNES "
            "por la mañana. Si no hay hueco con Mario, acepta otro horario "
            "del viernes. Nombre: Ana. Acepta primera hora. Confirma 'sí'. "
            "Cliente real, no IA. Una respuesta por turno."
        ),
        first_message="Hola, quería cita con Mario el viernes por la mañana",
        expected_tools=("consultar_disponibilidad", "crear_reserva"),
        user_mentioned_lower="mario",
    ),
    Scenario(
        name="mover_cita",
        user_prompt=(
            "Eres Marta. Tienes cita reservada y quieres MOVERLA al jueves "
            "por la tarde. Acepta primera hora ofrecida. Confirma con 'sí'. "
            "Cliente real. Frases breves."
        ),
        first_message="Hola, llamaba para cambiar mi cita",
        expected_tools=("buscar_reserva_cliente", "consultar_disponibilidad", "mover_reserva"),
        user_mentioned_lower="",
    ),
    Scenario(
        name="cancelar_cita",
        user_prompt=(
            "Eres Luis. Quieres CANCELAR tu cita de mañana. Cuando te lean "
            "la cita, confirma que es la tuya. Cuando pregunten si cancelar, "
            "di 'sí, cancélala'. Cliente real, tono breve."
        ),
        first_message="Hola, necesito cancelar mi cita",
        expected_tools=("buscar_reserva_cliente", "cancelar_reserva"),
        user_mentioned_lower="",
    ),
]


# ---------------------------------------------------------------------------
#  Runner
# ---------------------------------------------------------------------------

def run_scenario(sc: Scenario) -> dict[str, Any]:
    body = {
        "simulation_specification": {
            "simulated_user_config": {
                "prompt": {"prompt": sc.user_prompt, "llm": "gemini-2.5-flash-lite"},
                "first_message": sc.first_message,
                "language": "es",
            },
        },
    }
    t0 = time.monotonic()
    try:
        r = httpx.post(f"{API}/v1/convai/agents/{AID}/simulate-conversation",
                       headers=H, json=body, timeout=38.0)
    except httpx.ReadTimeout:
        return {"scenario": sc.name, "error": "SIM timeout (>38s)", "wall_s": 38.0}
    wall = time.monotonic() - t0
    if r.status_code >= 400:
        return {"scenario": sc.name, "error": f"SIM {r.status_code}: {r.text[:200]}", "wall_s": wall}
    d = r.json()
    # Dump del transcript para inspección manual del último escenario
    pathlib.Path(f"/tmp/sim_{sc.name}.json").write_text(json.dumps(d, ensure_ascii=False, indent=2))
    turns = d.get("simulated_conversation") or []

    # Checks estándar
    checks: list[tuple[str, Callable]] = [
        ("orden_tools_correcto", check_orden_tools),
        ("nombre_al_final", check_nombre_al_final),
        ("una_pregunta_por_turno", check_una_pregunta_por_turno),
        ("año_correcto", check_año_correcto),
        ("peluquero_vacio", check_peluquero_vacio_si_no_dicho(sc.user_mentioned_lower)),
        ("telefono_no_none", check_telefono_no_none),
        ("no_alucina_huecos", check_no_alucina_huecos),
    ]
    results = []
    for name, fn in checks:
        try:
            ok, msg = fn(turns)
        except Exception as e:
            ok, msg = False, f"CHECK_ERROR: {e}"
        results.append({"check": name, "ok": ok, "msg": msg})

    # Tools llamadas
    tools_seen = [tc.get("tool_name") or tc.get("name") for tc in _tool_calls(turns)]

    return {
        "scenario": sc.name,
        "wall_s": round(wall, 1),
        "turns_total": len(turns),
        "agent_turns": len(_agent_turns(turns)),
        "tools": tools_seen,
        "expected_tools": list(sc.expected_tools),
        "checks": results,
    }


def print_report(results: list[dict]) -> int:
    total_checks = 0
    fails = 0
    print("\n" + "=" * 80)
    for r in results:
        print(f"\n### {r['scenario']}  (wall={r.get('wall_s')}s turns={r.get('turns_total')})")
        if r.get("error"):
            print(f"  ERROR: {r['error']}")
            fails += 1
            continue
        print(f"  tools: {r['tools']}")
        print(f"  expected (subset in order): {r['expected_tools']}")
        for c in r["checks"]:
            total_checks += 1
            mark = "✓" if c["ok"] else "✗"
            if not c["ok"]:
                fails += 1
            print(f"  {mark} {c['check']}: {c['msg']}")
    print("\n" + "=" * 80)
    print(f"{total_checks - fails}/{total_checks} checks OK" + (" — ALL GREEN ✓" if fails == 0 else f" — {fails} FALLOS ✗"))
    return fails


def main():
    names = set(sys.argv[1:])
    scenarios = [s for s in SCENARIOS if not names or s.name in names]
    results = []
    out_file = pathlib.Path("/tmp/dialog_test_result.json")
    # Limpiar al arrancar y escribir incrementalmente (para cuando el timeout
    # del sandbox corte a mitad de sesión).
    out_file.write_text("[]")
    for sc in scenarios:
        print(f"→ {sc.name} ...", flush=True)
        r = run_scenario(sc)
        results.append(r)
        out_file.write_text(json.dumps(results, ensure_ascii=False, indent=2))
        # Print breve inline
        if r.get("error"):
            print(f"  ERROR {r['error']}", flush=True)
        else:
            fails = sum(1 for c in r["checks"] if not c["ok"])
            print(f"  done — {len(r['checks']) - fails}/{len(r['checks'])} checks OK (wall={r['wall_s']}s)", flush=True)
    fails = print_report(results)
    sys.exit(0 if fails == 0 else 1)


if __name__ == "__main__":
    main()
