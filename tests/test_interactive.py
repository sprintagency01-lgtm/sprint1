"""Tests unitarios del módulo `interactive` (codificación/decodificación de ids
y resolución de menú pendiente por texto).

Cubren los casos críticos del fallback de Twilio (cliente responde "1",
"dos", "otra") y el parseo de slot ids con dos fechas ISO separadas por `:`,
que es el detalle más frágil.
"""
from __future__ import annotations

from app import interactive as ix


# ---------- make_* / parse_id --------------------------------------------

def test_make_slot_id_basic():
    rid = ix.make_slot_id("2026-04-24T10:00", "2026-04-24T10:30")
    assert rid == "slot:2026-04-24T10:00:2026-04-24T10:30"


def test_make_slot_id_with_member():
    rid = ix.make_slot_id("2026-04-24T10:00", "2026-04-24T10:30", "42")
    assert rid.endswith(":42")


def test_parse_slot_id_basic():
    rid = ix.make_slot_id("2026-04-24T10:00", "2026-04-24T10:30")
    parsed = ix.parse_id(rid)
    assert parsed["kind"] == "slot"
    assert parsed["inicio_iso"] == "2026-04-24T10:00"
    assert parsed["fin_iso"] == "2026-04-24T10:30"
    assert parsed["miembro"] is None


def test_parse_slot_id_with_member():
    rid = ix.make_slot_id("2026-04-24T10:00", "2026-04-24T10:30", "42")
    parsed = ix.parse_id(rid)
    assert parsed["kind"] == "slot"
    assert parsed["inicio_iso"] == "2026-04-24T10:00"
    assert parsed["fin_iso"] == "2026-04-24T10:30"
    assert parsed["miembro"] == "42"


def test_parse_team_none():
    parsed = ix.parse_id("team:none")
    assert parsed["kind"] == "team"
    assert parsed["sin_preferencia"] is True
    assert parsed["member_id"] is None


def test_parse_team_with_id():
    parsed = ix.parse_id("team:7")
    assert parsed["kind"] == "team"
    assert parsed["sin_preferencia"] is False
    assert parsed["member_id"] == "7"


def test_parse_confirm_yes_no():
    assert ix.parse_id("confirm:yes")["yes"] is True
    assert ix.parse_id("confirm:no")["yes"] is False


def test_parse_other_slot():
    parsed = ix.parse_id("other:slot")
    assert parsed["kind"] == "other"
    assert parsed["target"] == "slot"


def test_make_and_parse_service_id():
    rid = ix.make_service_id("corte-hombre")
    assert rid == "svc:corte-hombre"
    parsed = ix.parse_id(rid)
    assert parsed["kind"] == "svc"
    assert parsed["slug"] == "corte-hombre"


def test_parse_other_svc():
    parsed = ix.parse_id("other:svc")
    assert parsed["kind"] == "other"
    assert parsed["target"] == "svc"


def test_parse_unknown_does_not_raise():
    parsed = ix.parse_id("garbage:stuff:123")
    # no reconocemos el prefijo → kind="unknown", raw intacto
    assert parsed["kind"] == "unknown"
    assert parsed["raw"] == "garbage:stuff:123"


# ---------- resolve_from_pending_menu ------------------------------------

_OPTIONS = [
    {"id": "slot:2026-04-24T10:00:2026-04-24T10:30", "title": "vie 24 abr, 10:00"},
    {"id": "slot:2026-04-24T11:00:2026-04-24T11:30", "title": "vie 24 abr, 11:00"},
    {"id": "slot:2026-04-24T12:00:2026-04-24T12:30", "title": "vie 24 abr, 12:00"},
    {"id": "other:slot", "title": "Otra hora"},
]


def _pending():
    return {"kind": "slot", "options": _OPTIONS}


def test_resolve_digit():
    opt = ix.resolve_from_pending_menu(_pending(), "1")
    assert opt is not None and opt["id"] == _OPTIONS[0]["id"]


def test_resolve_spanish_number():
    opt = ix.resolve_from_pending_menu(_pending(), "dos")
    assert opt is not None and opt["id"] == _OPTIONS[1]["id"]


def test_resolve_ordinal():
    opt = ix.resolve_from_pending_menu(_pending(), "tercera")
    assert opt is not None and opt["id"] == _OPTIONS[2]["id"]


def test_resolve_otra_matches_other():
    opt = ix.resolve_from_pending_menu(_pending(), "otra")
    assert opt is not None and opt["id"] == "other:slot"


def test_resolve_title_contains():
    opt = ix.resolve_from_pending_menu(_pending(), "10:00")
    assert opt is not None and opt["id"] == _OPTIONS[0]["id"]


def test_resolve_unmatched_returns_none():
    opt = ix.resolve_from_pending_menu(_pending(), "jugar al parchís")
    assert opt is None


def test_resolve_empty_pending():
    assert ix.resolve_from_pending_menu(None, "1") is None
    assert ix.resolve_from_pending_menu({"options": []}, "1") is None
