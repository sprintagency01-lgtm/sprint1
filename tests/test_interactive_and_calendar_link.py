"""Tests de los cambios introducidos tras la prueba real de Mario (2026-04-24):

- `ofrecer_equipo` acepta `modo_preferencia=True` y añade "Me da igual"
  como opción extra (en vez de "Otro miembro") — para usar al principio
  del flujo cuando preguntamos por preferencia.
- El flujo del prompt obliga a usar `ofrecer_equipo` y `ofrecer_huecos`
  con botones en vez de listar en texto.
- `_build_google_add_to_calendar_url` genera un URL de "añadir a mi
  Google Calendar" correcto, y el prompt instruye al agente a mandarlo.
"""
from __future__ import annotations

from datetime import datetime
from urllib.parse import parse_qs, urlparse

from app import agent as ag


# ---------------------------------------------------------------------------
#  ofrecer_equipo modo_preferencia
# ---------------------------------------------------------------------------

def test_ofrecer_equipo_modo_preferencia_incluye_me_da_igual():
    reply = ag._build_reply_ofrecer_equipo(
        {
            "body": "¿Tienes preferencia?",
            "miembros": [
                {"id": "1", "nombre": "Mario"},
                {"id": "2", "nombre": "Marcos"},
            ],
            "modo_preferencia": True,
        },
        tenant={"id": "pelu_demo"},
    )
    titles = [o["title"] for o in reply.interactive["options"]]
    ids = [o["id"] for o in reply.interactive["options"]]
    assert "Mario" in titles
    assert "Marcos" in titles
    assert "Me da igual" in titles
    # El callback_data del botón "Me da igual" debe resolver a sin preferencia.
    assert "team:none" in ids
    # NO debe haber "Otro miembro" en este modo.
    assert "Otro miembro" not in titles


def test_ofrecer_equipo_modo_normal_incluye_otro_miembro():
    """Por defecto (modo sin preferencia) el extra sigue siendo 'Otro
    miembro' — preserva el comportamiento original cuando se usa tras
    equipo_disponible_en."""
    reply = ag._build_reply_ofrecer_equipo(
        {
            "body": "¿Con quién?",
            "miembros": [{"id": "1", "nombre": "Mario"}],
            # sin modo_preferencia → False
        },
        tenant={"id": "pelu_demo"},
    )
    titles = [o["title"] for o in reply.interactive["options"]]
    assert "Otro miembro" in titles
    assert "Me da igual" not in titles


def test_ofrecer_equipo_modo_false_explicito():
    reply = ag._build_reply_ofrecer_equipo(
        {"body": "x", "miembros": [{"id": "1", "nombre": "A"}], "modo_preferencia": False},
        tenant={"id": "t"},
    )
    titles = [o["title"] for o in reply.interactive["options"]]
    assert "Otro miembro" in titles
    assert "Me da igual" not in titles


# ---------------------------------------------------------------------------
#  Flujo del prompt obliga a usar tools interactivas
# ---------------------------------------------------------------------------

def test_flujo_instruye_ofrecer_equipo_con_modo_preferencia():
    from app.db import _build_flujo_reserva, _professional_word_for

    flujo = _build_flujo_reserva(has_team=True, professional_word=_professional_word_for("peluquería"))
    assert "ofrecer_equipo" in flujo
    assert "modo_preferencia" in flujo
    assert "Me da igual" in flujo


def test_flujo_instruye_ofrecer_huecos_no_listar_texto():
    from app.db import _build_flujo_reserva, _professional_word_for

    flujo = _build_flujo_reserva(has_team=True, professional_word=_professional_word_for("peluquería"))
    assert "ofrecer_huecos" in flujo
    # Debe prohibir explícitamente listar en texto.
    low = flujo.lower()
    assert "no listes" in low or "no los escribas" in low


def test_flujo_incluye_instruccion_add_to_calendar_url():
    from app.db import _build_flujo_reserva, _professional_word_for

    flujo = _build_flujo_reserva(has_team=True, professional_word=_professional_word_for("peluquería"))
    assert "add_to_calendar_url" in flujo
    # Debe decir que se incluya en el mensaje de confirmación.
    low = flujo.lower()
    assert "google calendar" in low
    assert "incluye" in low or "incluir" in low


# ---------------------------------------------------------------------------
#  _build_google_add_to_calendar_url
# ---------------------------------------------------------------------------

def test_add_to_calendar_url_basico():
    url = ag._build_google_add_to_calendar_url(
        titulo="Javier — Corte hombre (Mario)",
        inicio=datetime(2026, 4, 25, 11, 0),
        fin=datetime(2026, 4, 25, 11, 30),
        descripcion="",
        ubicacion="Peluquería Demo",
        tz="Europe/Madrid",
    )
    parsed = urlparse(url)
    assert parsed.scheme == "https"
    assert parsed.netloc == "calendar.google.com"
    assert parsed.path == "/calendar/render"
    qs = parse_qs(parsed.query)
    assert qs["action"] == ["TEMPLATE"]
    assert qs["text"] == ["Javier — Corte hombre (Mario)"]
    assert qs["dates"] == ["20260425T110000/20260425T113000"]
    assert qs["ctz"] == ["Europe/Madrid"]
    assert qs["location"] == ["Peluquería Demo"]
    # Si descripcion es vacío, no se incluye el parámetro.
    assert "details" not in qs


def test_add_to_calendar_url_con_tz_aware_convierte_a_tz_tenant():
    """Si inicio/fin traen tzinfo (p.ej. UTC), convertimos a la TZ del
    tenant antes de serializar, para que el cliente vea la hora correcta."""
    from datetime import timezone
    # 10:00 UTC = 12:00 Europe/Madrid (CEST, +02:00 en abril).
    inicio_utc = datetime(2026, 4, 25, 10, 0, tzinfo=timezone.utc)
    fin_utc = datetime(2026, 4, 25, 10, 30, tzinfo=timezone.utc)
    url = ag._build_google_add_to_calendar_url(
        titulo="X",
        inicio=inicio_utc,
        fin=fin_utc,
        tz="Europe/Madrid",
    )
    qs = parse_qs(urlparse(url).query)
    assert qs["dates"] == ["20260425T120000/20260425T123000"]
    assert qs["ctz"] == ["Europe/Madrid"]


def test_add_to_calendar_url_incluye_details_si_hay():
    url = ag._build_google_add_to_calendar_url(
        titulo="x",
        inicio=datetime(2026, 4, 25, 11, 0),
        fin=datetime(2026, 4, 25, 11, 30),
        descripcion="Cliente Juan, tel +34123",
        tz="Europe/Madrid",
    )
    qs = parse_qs(urlparse(url).query)
    assert qs["details"] == ["Cliente Juan, tel +34123"]


def test_add_to_calendar_url_tz_invalida_no_lanza():
    """Una TZ invalida debe caer a Europe/Madrid sin reventar."""
    url = ag._build_google_add_to_calendar_url(
        titulo="x",
        inicio=datetime(2026, 4, 25, 11, 0),
        fin=datetime(2026, 4, 25, 11, 30),
        tz="Atlantis/Center",
    )
    # No crash + algo utilizable como URL.
    assert url.startswith("https://calendar.google.com/calendar/render?")
