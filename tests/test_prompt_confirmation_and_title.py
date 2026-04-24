"""Tests de regresión de dos bugs observados en producción el 2026-04-24:

1) Tras preguntar "¿lo confirmo?", cuando el cliente respondía "sí" en
   texto libre (no pulsando botón interactive), el agente reconsultaba
   disponibilidad en vez de llamar a `crear_reserva`. Verificamos que el
   prompt generado por `render_system_prompt` contiene la regla dura que
   evita esto.

2) La tool `crear_reserva` en `app.agent.TOOLS` tenía el título al revés:
   `'Servicio — Nombre'` en vez de `'Nombre — Servicio'` como ya espera
   el canal voz (ElevenLabs). Verificamos que la description de la tool
   ahora insiste en "NOMBRE PRIMERO" con ejemplo incorrecto explícito.
"""
from __future__ import annotations

from app import agent as agent_mod


# ---------------------------------------------------------------------------
#  Fix 1 — regla de cierre tras pedir_confirmacion
# ---------------------------------------------------------------------------

def _render_prompt_for_pelu_mock():
    """Construye un prompt como haría render_system_prompt, pero sin pasar
    por la BD — usamos directamente `_build_flujo_reserva` que es la pieza
    donde vive la regla.
    """
    from app.db import _build_flujo_reserva, _professional_word_for
    return _build_flujo_reserva(has_team=True, professional_word=_professional_word_for("peluquería"))


def test_flujo_incluye_regla_de_cierre_tras_confirmacion():
    flujo = _render_prompt_for_pelu_mock()
    # La regla debe mencionar explícitamente las variantes afirmativas
    # comunes que un cliente español usaría.
    assert "REGLA DE CIERRE" in flujo
    for affirmation in ("sí", "confirma", "ok", "dale", "perfecto", "adelante"):
        assert affirmation in flujo.lower(), f"falta variante afirmativa: {affirmation}"


def test_flujo_prohibe_reconsultar_tras_si():
    flujo = _render_prompt_for_pelu_mock()
    # No solo tiene que decir "sí → crear_reserva", también tiene que decir
    # explícitamente que NO reconsulte disponibilidad. El bug de hoy fue
    # exactamente ese.
    low = flujo.lower()
    assert "no vuelvas a consultar" in low or "no reconsultes" in low
    assert "no reofrerezcas" in low or "no ofrezcas" in low or "no reofrezcas" in low
    # Debe mencionar la tool concreta (crear_reserva) para que el LLM la mapee.
    assert "crear_reserva" in flujo


def test_flujo_habla_de_ok_true_tras_ejecutar():
    """Una vez ejecutada crear_reserva, la regla dice que confirmes al
    cliente SOLO tras ok:true. Eso evita que el agente diga 'reservado'
    cuando en realidad la tool falló (p. ej. Google Calendar devolvió
    retryable:true).
    """
    flujo = _render_prompt_for_pelu_mock()
    assert "ok:true" in flujo or "ok: true" in flujo


def test_flujo_tiene_regla_anti_alucinacion():
    """Alucinación observada en producción: con flujos largos tras
    rechazos de hora, el modelo decía "reservado" sin ejecutar la tool.
    El prompt lo prohíbe explícitamente con "NUNCA digas reservado si no
    ejecutaste crear_reserva".
    """
    flujo = _render_prompt_for_pelu_mock()
    low = flujo.lower()
    assert "anti-alucin" in low or "nunca digas" in low
    # Debe mencionar palabras de cierre concretas que no se deben decir
    # sin tool call real.
    for key in ("reservado", "confirmado", "hecho", "listo"):
        assert key in low, f"falta referencia a '{key}' en regla anti-alucinación"


def test_flujo_menciona_retryable_manejo():
    flujo = _render_prompt_for_pelu_mock()
    assert "retryable" in flujo


# ---------------------------------------------------------------------------
#  Fix 2 — título en formato Nombre — Servicio
# ---------------------------------------------------------------------------

def _crear_reserva_tool() -> dict:
    """Devuelve el dict de la tool crear_reserva desde agent.TOOLS."""
    for t in agent_mod.TOOLS:
        if t.get("function", {}).get("name") == "crear_reserva":
            return t
    raise AssertionError("No encuentro la tool crear_reserva en agent.TOOLS")


def test_crear_reserva_description_pone_nombre_primero():
    tool = _crear_reserva_tool()
    titulo_desc = tool["function"]["parameters"]["properties"]["titulo"]["description"]
    # La description debe indicar el formato 'Nombre — Servicio' (con guion
    # largo u ordinario) y debe DESACONSEJAR la inversa con ejemplo.
    assert "Nombre" in titulo_desc and "Servicio" in titulo_desc
    assert "PRIMERO" in titulo_desc or "primero" in titulo_desc
    # Debe incluir un ejemplo incorrecto explícito para que el LLM no caiga
    # en la trampa (el bug de hoy: "Corte hombre — Marcos").
    assert "INCORRECTO" in titulo_desc or "NO hagas" in titulo_desc


def test_crear_reserva_description_incluye_ejemplo_realista():
    tool = _crear_reserva_tool()
    titulo_desc = tool["function"]["parameters"]["properties"]["titulo"]["description"]
    # Un ejemplo concreto con nombre primero en el texto.
    assert any(
        sample in titulo_desc
        for sample in (
            "Marcos — Corte hombre",
            "Javier Test — Corte hombre",
            "Lucía — Corte mujer",
        )
    )


def test_crear_reserva_description_refuerza_llamar_tras_confirmacion():
    """Además del prompt global, la description de la tool también debe
    avisar al modelo de que 'sí/confirma' es el momento de ejecutar."""
    tool = _crear_reserva_tool()
    desc = tool["function"]["description"]
    low = desc.lower()
    assert "confirma" in low or "confirme" in low
    assert (
        "no reconsultes" in low
        or "no reofrerezcas" in low
        or "no reofrezcas" in low
        or "no consultes" in low
    ), desc
