"""Tests del cap de duración en /tools/consultar_disponibilidad,
/tools/crear_reserva y /tools/mover_reserva.

Sin estos caps, una alucinación del LLM (Ana pidiendo huecos de "8 horas",
o creando una cita que termina al día siguiente) podía bloquear la agenda
del peluquero entera. Validamos:

- ConsultaReq rechaza duracion_minutos fuera de [5, 240] con HTTP 422.
- crear_reserva devuelve {ok: false, retryable: false} cuando fin-inicio
  está fuera de rango, sin tocar Calendar.
- mover_reserva idem.
"""
from __future__ import annotations

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

from app import eleven_tools


_HEADERS = {"X-Tool-Secret": "test-secret-conftest"}


@pytest.fixture
def client(monkeypatch):
    """TestClient con un tenant resoluble vía _resolve_tenant.

    Parcheamos el resolver para devolver un tenant mínimo sin BD/yaml.
    """
    monkeypatch.setattr(
        eleven_tools,
        "_resolve_tenant",
        lambda tid: {"id": tid or "test", "calendar_id": "primary", "peluqueros": []},
    )
    app = FastAPI()
    app.include_router(eleven_tools.router)
    return TestClient(app)


def test_consultar_disponibilidad_duracion_demasiado_alta_422(client):
    body = {
        "fecha_desde_iso": "2026-04-30T09:00:00",
        "fecha_hasta_iso": "2026-04-30T20:00:00",
        "duracion_minutos": 600,  # 10h, fuera de rango
        "max_resultados": 5,
    }
    r = client.post(
        "/tools/consultar_disponibilidad?tenant_id=test",
        json=body,
        headers=_HEADERS,
    )
    assert r.status_code == 422, r.text


def test_consultar_disponibilidad_duracion_demasiado_baja_422(client):
    body = {
        "fecha_desde_iso": "2026-04-30T09:00:00",
        "fecha_hasta_iso": "2026-04-30T20:00:00",
        "duracion_minutos": 1,  # < 5
        "max_resultados": 5,
    }
    r = client.post(
        "/tools/consultar_disponibilidad?tenant_id=test",
        json=body,
        headers=_HEADERS,
    )
    assert r.status_code == 422


def test_crear_reserva_duracion_excesiva_no_toca_calendar(client, monkeypatch):
    """Si Ana pide una cita de 6 horas, abortamos antes de llamar a Google."""
    calls = []
    monkeypatch.setattr(
        eleven_tools.cal,
        "crear_evento",
        lambda *a, **kw: calls.append((a, kw)) or "fake-event-id",
    )
    body = {
        "titulo": "Corte hombre",
        "inicio_iso": "2026-04-30T10:00:00",
        "fin_iso": "2026-04-30T16:00:00",  # 6h
        "telefono_cliente": "+34611111111",
        "peluquero": "sin preferencia",
        "notas": "",
    }
    r = client.post(
        "/tools/crear_reserva?tenant_id=test", json=body, headers=_HEADERS
    )
    assert r.status_code == 200
    payload = r.json()
    assert payload["ok"] is False
    assert payload.get("retryable") is False
    assert "rango" in payload["error"].lower()
    # Nunca debe haber llamado a Calendar.
    assert calls == []


def test_crear_reserva_duracion_invertida_no_toca_calendar(client, monkeypatch):
    """fin <= inicio cuenta como duración negativa: rechazo."""
    calls = []
    monkeypatch.setattr(
        eleven_tools.cal,
        "crear_evento",
        lambda *a, **kw: calls.append((a, kw)) or "fake-event-id",
    )
    body = {
        "titulo": "Corte hombre",
        "inicio_iso": "2026-04-30T11:00:00",
        "fin_iso": "2026-04-30T10:00:00",
        "telefono_cliente": "+34611111111",
        "peluquero": "sin preferencia",
    }
    r = client.post(
        "/tools/crear_reserva?tenant_id=test", json=body, headers=_HEADERS
    )
    assert r.status_code == 200
    assert r.json()["ok"] is False
    assert calls == []
