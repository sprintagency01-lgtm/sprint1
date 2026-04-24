"""Tests de los fixes 2026-04-24 (parche pm 4):

- Filtro de huecos en el pasado: Anabel pidió cita hoy a las 12h y el bot
  le ofreció un hueco a las 9h. Ahora consultar_disponibilidad descarta
  huecos con inicio < now + 10 min.
- Generación de archivo .ics RFC 5545 válido para adjuntarse como
  documento en Telegram (el móvil del cliente abre el calendario nativo).
- AgentReply.calendar_event se rellena al crear_reserva exitosamente
  (probado a través del __init__ — el test end-to-end con Anthropic
  sería integración real).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from app import agent as ag
from app import eleven_tools as et


# ---------------------------------------------------------------------------
#  eleven_tools: filtros de huecos en el pasado
# ---------------------------------------------------------------------------

def test_descartar_huecos_pasados_drop_inicio_pasado():
    tz = ZoneInfo("Europe/Madrid")
    now = datetime.now(tz)
    past = now - timedelta(hours=3)
    future = now + timedelta(hours=1)
    huecos = [
        {"inicio": past, "fin": past + timedelta(minutes=30), "peluquero": "x"},
        {"inicio": future, "fin": future + timedelta(minutes=30), "peluquero": "y"},
    ]
    out = et._descartar_huecos_pasados(huecos)
    assert len(out) == 1
    assert out[0]["peluquero"] == "y"


def test_descartar_huecos_pasados_respeta_buffer_10min():
    tz = ZoneInfo("Europe/Madrid")
    now = datetime.now(tz)
    # Slot que empieza en 5 min — dentro del buffer → se descarta.
    inminente = {"inicio": now + timedelta(minutes=5), "fin": now + timedelta(minutes=35)}
    # Slot que empieza en 20 min — fuera del buffer → se mantiene.
    futuro = {"inicio": now + timedelta(minutes=20), "fin": now + timedelta(minutes=50)}
    out = et._descartar_huecos_pasados([inminente, futuro])
    assert len(out) == 1
    assert out[0] is futuro


def test_descartar_huecos_pasados_tolera_naive():
    """Los datetimes naive se asumen en la TZ del tenant."""
    tz = ZoneInfo("Europe/Madrid")
    now_local = datetime.now(tz).replace(tzinfo=None)
    past_naive = now_local - timedelta(hours=2)
    future_naive = now_local + timedelta(hours=2)
    out = et._descartar_huecos_pasados([
        {"inicio": past_naive, "fin": past_naive + timedelta(minutes=30)},
        {"inicio": future_naive, "fin": future_naive + timedelta(minutes=30)},
    ])
    assert len(out) == 1
    assert out[0]["inicio"] == future_naive


def test_descartar_slots_pasados_con_objetos_slot():
    """Versión para slots (objetos con .start/.end) del fallback single-cal."""
    from types import SimpleNamespace
    tz = ZoneInfo("Europe/Madrid")
    now = datetime.now(tz)
    past = SimpleNamespace(
        start=now - timedelta(hours=2),
        end=now - timedelta(hours=2) + timedelta(minutes=30),
    )
    future = SimpleNamespace(
        start=now + timedelta(hours=1),
        end=now + timedelta(hours=1, minutes=30),
    )
    out = et._descartar_slots_pasados([past, future])
    assert out == [future]


# ---------------------------------------------------------------------------
#  agent._tz_now y filtro en _execute_tool (lo probamos indirecto con mock)
# ---------------------------------------------------------------------------

def test_tz_now_devuelve_aware_en_tz_configurada():
    t = ag._tz_now()
    assert t.tzinfo is not None
    # Debería coincidir con la zona por defecto (Europe/Madrid salvo override).
    assert str(t.tzinfo).startswith(("Europe/", "UTC", "Etc/"))


def test_execute_tool_consultar_disponibilidad_filtra_pasados(monkeypatch):
    """Mock listar_huecos_libres para que devuelva un slot pasado + uno
    futuro, y confirma que _execute_tool filtra el pasado."""
    from types import SimpleNamespace
    tz = ZoneInfo("Europe/Madrid")
    now = datetime.now(tz)
    past = SimpleNamespace(
        start=now - timedelta(hours=5),
        end=now - timedelta(hours=5) + timedelta(minutes=30),
    )
    future = SimpleNamespace(
        start=now + timedelta(hours=2),
        end=now + timedelta(hours=2, minutes=30),
    )

    monkeypatch.setattr(
        "app.calendar_service.listar_huecos_libres",
        lambda *a, **kw: [past, future],
    )

    args = {
        "peluquero_preferido": "sin preferencia",
        "fecha_desde_iso": (now - timedelta(hours=6)).replace(tzinfo=None).isoformat(),
        "fecha_hasta_iso": (now + timedelta(hours=6)).replace(tzinfo=None).isoformat(),
        "duracion_minutos": 30,
        "max_resultados": 10,
    }
    import json
    result_json = ag._execute_tool(
        name="consultar_disponibilidad",
        args=args,
        tenant={"id": "t", "calendar_id": "x"},
        caller_phone="p",
    )
    result = json.loads(result_json)
    huecos = result.get("huecos", [])
    assert len(huecos) == 1
    assert huecos[0]["inicio"] == future.start.isoformat()


# ---------------------------------------------------------------------------
#  ICS generation
# ---------------------------------------------------------------------------

def test_build_ics_structure_valida():
    ics = ag._build_ics_content(
        titulo="Javier Test — Corte hombre (Mario)",
        inicio=datetime(2026, 4, 25, 11, 0),
        fin=datetime(2026, 4, 25, 11, 30),
        descripcion="Cliente Juan",
        ubicacion="Peluquería Demo",
        tz="Europe/Madrid",
        organizer_name="Peluquería Demo",
    )
    # Estructura RFC 5545 minimal.
    assert "BEGIN:VCALENDAR" in ics
    assert "END:VCALENDAR" in ics
    assert "BEGIN:VEVENT" in ics
    assert "END:VEVENT" in ics
    assert "VERSION:2.0" in ics
    assert "DTSTART;TZID=Europe/Madrid:20260425T110000" in ics
    assert "DTEND;TZID=Europe/Madrid:20260425T113000" in ics
    # Título con caracteres — preservado tras escape.
    assert "Javier Test \\— Corte hombre (Mario)" not in ics  # nothing weird
    assert "Javier Test — Corte hombre (Mario)" in ics
    assert "LOCATION:Peluquería Demo" in ics
    # CRLF
    assert "\r\n" in ics


def test_build_ics_escapa_caracteres_especiales():
    ics = ag._build_ics_content(
        titulo="Cliente; con coma, y ñ",
        inicio=datetime(2026, 4, 25, 11, 0),
        fin=datetime(2026, 4, 25, 11, 30),
    )
    # ; y , deben ir escapados con \
    assert "SUMMARY:Cliente\\; con coma\\, y ñ" in ics


def test_build_ics_tz_aware_convierte_a_tz_tenant():
    """Si inicio/fin traen tzinfo UTC, debemos guardarlos en local del
    tenant + TZID."""
    ics = ag._build_ics_content(
        titulo="x",
        inicio=datetime(2026, 4, 25, 9, 0, tzinfo=timezone.utc),
        fin=datetime(2026, 4, 25, 9, 30, tzinfo=timezone.utc),
        tz="Europe/Madrid",
    )
    # 9:00 UTC = 11:00 Europe/Madrid en abril (CEST).
    assert "DTSTART;TZID=Europe/Madrid:20260425T110000" in ics


def test_build_ics_tz_invalida_fallback_madrid():
    ics = ag._build_ics_content(
        titulo="x",
        inicio=datetime(2026, 4, 25, 11, 0),
        fin=datetime(2026, 4, 25, 11, 30),
        tz="Atlantis/Center",
    )
    assert "TZID=Europe/Madrid" in ics


def test_build_ics_omite_campos_vacios():
    ics = ag._build_ics_content(
        titulo="x",
        inicio=datetime(2026, 4, 25, 11, 0),
        fin=datetime(2026, 4, 25, 11, 30),
    )
    assert "DESCRIPTION" not in ics
    assert "LOCATION" not in ics


# ---------------------------------------------------------------------------
#  AgentReply.calendar_event
# ---------------------------------------------------------------------------

def test_agent_reply_has_calendar_attachment_property():
    r1 = ag.AgentReply(text="hola")
    assert r1.has_calendar_attachment is False
    r2 = ag.AgentReply(
        text="reservado",
        calendar_event={"inicio_iso": "2026-04-25T11:00:00"},
    )
    assert r2.has_calendar_attachment is True
    r3 = ag.AgentReply(text="x", calendar_event={})  # dict vacío → no válido
    assert r3.has_calendar_attachment is False
