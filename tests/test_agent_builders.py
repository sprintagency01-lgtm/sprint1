"""Tests de los builders de AgentReply para las tools de oferta.

Estos tests construyen `AgentReply` sin llamar al LLM real — ejercitan el
código que convierte los args de una tool en el spec interactivo que el
backend envía a WhatsApp / Twilio.
"""
from __future__ import annotations

from app import agent as ag


# ---------- _slugify_service ---------------------------------------------

def test_slugify_basic():
    assert ag._slugify_service("Corte hombre") == "corte-hombre"


def test_slugify_accents():
    assert ag._slugify_service("Depilación láser") == "depilacion-laser"


def test_slugify_strips_weird_chars():
    # Slash y símbolos: el "&" desaparece, el "/" se vuelve guion, y los
    # guiones consecutivos se colapsan.
    assert ag._slugify_service("Color & Mechas") == "color-mechas"
    assert ag._slugify_service("Corte/Peinado") == "corte-peinado"


def test_slugify_empty():
    assert ag._slugify_service("") == ""
    assert ag._slugify_service("   ") == ""


# ---------- _build_reply_ofrecer_servicio -------------------------------

_TENANT_PELU = {
    "id": "test",
    "services": [
        {"nombre": "Corte mujer", "duracion_min": 45, "precio": 22},
        {"nombre": "Corte hombre", "duracion_min": 30, "precio": 15},
        {"nombre": "Color", "duracion_min": 90, "precio": 55},
    ],
}


def test_build_servicio_lista_con_otros():
    reply = ag._build_reply_ofrecer_servicio({"body": "¿qué te hacemos?"}, _TENANT_PELU)
    assert reply.has_interactive
    spec = reply.interactive
    assert spec["type"] == "list"
    assert spec["body"] == "¿qué te hacemos?"

    options = spec["options"]
    # 3 servicios + "Otro" al final
    assert len(options) == 4
    # IDs de los 3 primeros deben ser svc:<slug>
    assert options[0]["id"] == "svc:corte-mujer"
    assert options[1]["id"] == "svc:corte-hombre"
    assert options[2]["id"] == "svc:color"
    # Última opción es "Otro"
    assert options[3]["id"] == "other:svc"
    assert options[3]["title"] == "Otro"


def test_build_servicio_incluye_descripcion_duracion_precio():
    reply = ag._build_reply_ofrecer_servicio({"body": "dime"}, _TENANT_PELU)
    first = reply.interactive["options"][0]
    # description = "45 min · 22€"
    assert "description" in first
    assert "45 min" in first["description"]
    assert "22€" in first["description"]


def test_build_servicio_sin_services_devuelve_solo_otros():
    # Tenant sin servicios (edge case): la lista solo trae "Otro".
    empty_tenant = {"id": "empty", "services": []}
    reply = ag._build_reply_ofrecer_servicio({"body": "..."}, empty_tenant)
    options = reply.interactive["options"]
    assert len(options) == 1
    assert options[0]["id"] == "other:svc"


def test_build_servicio_body_default_cuando_vacio():
    reply = ag._build_reply_ofrecer_servicio({}, _TENANT_PELU)
    # Cuando el LLM no pasa body usamos un fallback razonable.
    assert reply.interactive["body"]
    assert reply.text == reply.interactive["body"]


def test_build_servicio_tope_9_mas_otro():
    # Simulamos un tenant con 12 servicios para verificar el corte a 9 + Otro.
    many = {
        "id": "many",
        "services": [
            {"nombre": f"Servicio {i}", "duracion_min": 30, "precio": 10}
            for i in range(12)
        ],
    }
    reply = ag._build_reply_ofrecer_servicio({"body": "bla"}, many)
    options = reply.interactive["options"]
    # 9 primeros servicios + "Otro" al final = 10
    assert len(options) == 10
    assert options[-1]["id"] == "other:svc"
