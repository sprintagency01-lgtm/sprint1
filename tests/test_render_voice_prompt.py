"""Tests de regresión para `render_voice_prompt`.

Garantizan que cualquier tenant nuevo nazca con la jerarquía y reglas
optimizadas de Ana — fuente de verdad: `ana_prompt_new.txt` (ver
PROMPT_KNOWLEDGE.md).

Si en el futuro se mejora algo del prompt y se rompe alguno de estos
checks, la pregunta es: "¿hemos cambiado intencionadamente la jerarquía?"
Si no, fallar es la señal de que se rompió algo. Si sí, actualizar el
test al mismo tiempo.
"""
from __future__ import annotations

import pytest

from app.db import render_voice_prompt


# ---------------------------------------------------------------------
#  Fixtures: tenants representativos
# ---------------------------------------------------------------------

PELU_DEMO = {
    "id": "pelu_demo",
    "name": "Peluquería Demo",
    "timezone": "Europe/Madrid",
    "assistant": {"name": "Ana", "fallback_phone": "910 000 000"},
    "business_hours": {
        "mon": ["09:30", "20:30"],
        "tue": ["09:30", "20:30"],
        "wed": ["09:30", "20:30"],
        "thu": ["09:30", "20:30"],
        "fri": ["09:30", "20:30"],
        "sat": ["09:30", "20:30"],
        "sun": [],
    },
    "services": [
        {"nombre": "Corte mujer", "duracion_min": 45, "precio": 22},
        {"nombre": "Corte hombre", "duracion_min": 30, "precio": 15},
        {"nombre": "Color", "duracion_min": 90, "precio": 55},
    ],
    "peluqueros": [
        {"nombre": "Mario", "dias_trabajo": [0, 1, 2, 3, 4, 5]},
        {"nombre": "Marcos", "dias_trabajo": [2]},
    ],
}


ABOGADO_SIN_EQUIPO = {
    "id": "test_abogado",
    "name": "Despacho Test Abogado",
    "timezone": "Europe/Madrid",
    "assistant": {"name": "Asistente", "fallback_phone": ""},
    "business_hours": {
        "mon": ["09:00", "20:00"],
        "tue": [],
        "wed": ["09:00", "20:00"],
        "thu": [],
        "fri": ["09:00", "20:00"],
        "sat": [],
        "sun": [],
    },
    "services": [
        {"nombre": "Consulta", "duracion_min": 60, "precio": 80},
    ],
    "peluqueros": [],
}


# ---------------------------------------------------------------------
#  Marcas canónicas que deben aparecer en TODOS los prompts renderizados
# ---------------------------------------------------------------------
#  Estas son las reglas duras del producto; si alguna desaparece, hay
#  regresión. Ver PROMPT_KNOWLEDGE.md.

CANONICAL_MARKS = [
    # Bloque de fechas con macros (refresh_agent_prompt.py las rellena
    # antes de subir el prompt a ElevenLabs).
    "<!-- REFRESH_BLOCK -->",
    "<!-- /REFRESH_BLOCK -->",
    "__HOY_FECHA__",
    "__ANO_ACTUAL__",
    # Variables ElevenLabs literales (las pone el runtime).
    "{{system__time}}",
    "{{system__caller_id}}",
    # Jerarquía RESERVA correcta — nombre al FINAL.
    "servicio → cuándo → consultar → ofrecer → elegir → NOMBRE → crear",
    # Regla crítica de UNA pregunta por turno (sección dedicada).
    "## UNA pregunta por turno (regla crítica)",
    # Fillers obligatorios.
    "## Fillers antes de tool calls",
    # Sección de cierre con end_call (ronda 9).
    "## Cierre y colgar",
    "end_call",
    # Búsqueda por nombre tras fallo de teléfono (ronda 9).
    "## Flujo MOVER / CANCELAR — si no encuentras por teléfono",
    "nombre_cliente",
    # Regla anti-encadenar preguntas con ejemplos BIEN/MAL.
    "BIEN:",
    "MAL:",
]


REGRESSIONS_FORBIDDEN = [
    # Jerarquía vieja que se cazó en ronda 8 — el nombre NO va antes de
    # consultar. Si esta cadena aparece, hay regresión.
    "servicio → cuándo → NOMBRE → consultar",
    "Nombre — OBLIGATORIO antes de consultar",
    # La regla 5 nueva dice "Nombre al FINAL"; la vieja "Nombre al final,"
    # (minúscula) es la versión obsoleta — no debe aparecer.
    "5. Nombre al final, antes de crear_reserva.",
]


# ---------------------------------------------------------------------
#  Tests
# ---------------------------------------------------------------------


@pytest.mark.parametrize("tenant", [PELU_DEMO, ABOGADO_SIN_EQUIPO])
def test_canonical_marks_present(tenant):
    """Todo prompt renderizado debe contener las marcas canónicas."""
    prompt = render_voice_prompt(tenant)
    missing = [m for m in CANONICAL_MARKS if m not in prompt]
    assert not missing, (
        f"Faltan marcas canónicas en el prompt de '{tenant['id']}': {missing}\n"
        "Probable causa: se editó ana_prompt_new.txt y se borró una sección "
        "obligatoria. Ver PROMPT_KNOWLEDGE.md."
    )


@pytest.mark.parametrize("tenant", [PELU_DEMO, ABOGADO_SIN_EQUIPO])
def test_no_regression_old_hierarchy(tenant):
    """El prompt no debe tener la jerarquía vieja "nombre antes de consultar"."""
    prompt = render_voice_prompt(tenant)
    found = [r for r in REGRESSIONS_FORBIDDEN if r in prompt]
    assert not found, (
        f"Regresión detectada en el prompt de '{tenant['id']}': {found}\n"
        "Es la jerarquía vieja que se cazó en ronda 8. El nombre va al "
        "FINAL antes de crear_reserva, no antes de consultar."
    )


def test_pelu_demo_intro_y_negocio():
    """Sustitución correcta de la línea de intro y de la sección Negocio."""
    prompt = render_voice_prompt(PELU_DEMO)

    # Intro con nombre real del negocio y asistente
    assert "Eres Ana, recepcionista de Peluquería Demo." in prompt
    assert 'Soy Ana, trabajo aquí' in prompt

    # Sección Negocio con datos reales
    assert "## Negocio" in prompt
    assert "Horario lun-sáb 09:30-20:30. Domingo cerrado." in prompt
    assert "Corte mujer 45min 22€" in prompt
    assert "Color 90min 55€" in prompt
    assert "Peluqueros: Mario (lun-sáb), Marcos (solo miércoles)." in prompt
    assert "Solo recitas precios/horarios si preguntan." in prompt


def test_pelu_demo_fallback_hablado():
    """El número de fallback se renderiza dígito a dígito en la regla 4."""
    prompt = render_voice_prompt(PELU_DEMO)
    assert (
        '"me da problemas el sistema, ¿puedes llamar al '
        '9 1 0 0 0 0 0 0 0?"'
    ) in prompt


def test_pelu_demo_pregunta_corte_presente():
    """Si hay corte mujer/hombre, el paso 1 incluye la diferenciación."""
    prompt = render_voice_prompt(PELU_DEMO)
    assert '1. Servicio (si "corte" → "¿mujer o hombre?")' in prompt


def test_abogado_sin_equipo_no_tiene_linea_peluqueros():
    """Tenant sin equipo: la línea 'Peluqueros: ...' debe desaparecer."""
    prompt = render_voice_prompt(ABOGADO_SIN_EQUIPO)
    assert "Peluqueros:" not in prompt
    # Pero el resto de la sección Negocio sigue ahí.
    assert "## Negocio" in prompt
    assert "Servicios: Consulta 60min 80€." in prompt
    assert "Solo recitas precios/horarios si preguntan." in prompt


def test_abogado_intro_con_nombre_asistente_genérico():
    """Si el tenant llama a su asistente 'Asistente', se respeta."""
    prompt = render_voice_prompt(ABOGADO_SIN_EQUIPO)
    assert "Eres Asistente, recepcionista de Despacho Test Abogado." in prompt
    assert 'Soy Asistente, trabajo aquí' in prompt


def test_abogado_sin_corte_no_pregunta_corte():
    """Sin servicio 'corte mujer/hombre', el paso 1 va sin paréntesis."""
    prompt = render_voice_prompt(ABOGADO_SIN_EQUIPO)
    assert "  1. Servicio. Ese turno SOLO pregunta el servicio." in prompt
    assert '1. Servicio (si "corte"' not in prompt


def test_abogado_fallback_genérico_sin_telefono():
    """Sin teléfono de fallback, frase genérica (no 'llamar al X')."""
    prompt = render_voice_prompt(ABOGADO_SIN_EQUIPO)
    assert (
        '"me da problemas el sistema, ¿puedes volver a intentarlo en un rato?"'
    ) in prompt
    assert "llamar al" not in prompt or "puedes volver a intentarlo" in prompt


def test_timezone_se_propaga():
    """Si el tenant declara timezone, aparece en la sección Contexto."""
    custom = dict(PELU_DEMO)
    custom["timezone"] = "Atlantic/Canary"
    prompt = render_voice_prompt(custom)
    assert "(zona Atlantic/Canary)" in prompt


def test_anchors_match_o_levanta_runtime_error(monkeypatch, tmp_path):
    """Si la plantilla se rompe, render levanta RuntimeError explícito.

    Garantiza que un cambio descuidado en ana_prompt_new.txt no devuelva
    silenciosamente un prompt con datos hardcoded de pelu_demo.
    """
    from app import db as db_module

    fake_template = tmp_path / "ana_prompt_new.txt"
    # Plantilla minimalista que NO contiene los anchors.
    fake_template.write_text("Esto no es la plantilla de Ana.\n", encoding="utf-8")
    monkeypatch.setattr(db_module, "_VOICE_PROMPT_TEMPLATE_PATH", fake_template)

    with pytest.raises(RuntimeError, match="anchors de sustitución no encontrados"):
        render_voice_prompt(PELU_DEMO)
