"""Cerrojos anti-spam del formulario público (`POST /api/leads`).

Contexto: el 2026-09-20 entró por la landing un lead falso (`Ashley` /
madamtaisia@mail.ru, dirección en lista negra con miles de webs atacadas).
Llegó con `source` vacío, cosa imposible desde el navegador porque el JS del
modal siempre hace `fd.set('source', ...)`: fue un POST directo al endpoint.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import main as main_module
from app.main import app


@pytest.fixture(autouse=True)
def _clean_rate_limit_state():
    """Cada test arranca con la ventana de rate limit vacía."""
    main_module._LEAD_HITS.clear()
    yield
    main_module._LEAD_HITS.clear()


def _payload(**overrides) -> dict:
    base = {
        "name": "Laura",
        "phone": "+34611111111",
        "email": "laura@example.com",
        "country": "España",
        "consent": "1",
        "source": "hero",
    }
    base.update(overrides)
    return base


def test_honeypot_descarta_el_lead_en_silencio(monkeypatch):
    sent = []
    monkeypatch.setattr("app.main.notify_new_lead", lambda lead: sent.append(lead))

    client = TestClient(app)
    r = client.post(
        "/api/leads",
        data=_payload(name="Ashley", email="madamtaisia@mail.ru", website="http://spam.example"),
        headers={"X-Forwarded-For": "203.0.113.10"},
    )

    # Respuesta indistinguible de un envío correcto: el bot no debe saberlo.
    assert r.status_code == 200
    assert r.json() == {"ok": True, "id": 0}
    # Pero no se guarda, ni se notifica, ni se sincroniza a Brevo.
    assert sent == []


def test_honeypot_vacio_deja_pasar_el_lead(monkeypatch):
    sent = []
    monkeypatch.setattr("app.main.notify_new_lead", lambda lead: sent.append(lead))

    client = TestClient(app)
    r = client.post(
        "/api/leads",
        data=_payload(website=""),
        headers={"X-Forwarded-For": "203.0.113.11"},
    )

    assert r.status_code == 200
    assert r.json()["ok"] is True
    assert len(sent) == 1


def test_rate_limit_corta_a_partir_del_maximo(monkeypatch):
    monkeypatch.setattr("app.main.notify_new_lead", lambda lead: None)

    client = TestClient(app)
    headers = {"X-Forwarded-For": "203.0.113.12"}
    maximo = main_module.settings.lead_rate_limit_max

    for i in range(maximo):
        r = client.post("/api/leads", data=_payload(email=f"ok{i}@example.com"), headers=headers)
        assert r.status_code == 200, f"el envío {i + 1} no debería estar limitado"

    r = client.post("/api/leads", data=_payload(email="uno-de-mas@example.com"), headers=headers)
    assert r.status_code == 429
    assert "varias solicitudes" in r.json()["error"]


def test_rate_limit_es_por_ip(monkeypatch):
    monkeypatch.setattr("app.main.notify_new_lead", lambda lead: None)

    client = TestClient(app)
    maximo = main_module.settings.lead_rate_limit_max
    for i in range(maximo):
        client.post(
            "/api/leads",
            data=_payload(email=f"vecino{i}@example.com"),
            headers={"X-Forwarded-For": "203.0.113.13"},
        )

    # Otra IP no hereda la cuota agotada del vecino.
    r = client.post(
        "/api/leads",
        data=_payload(email="otra-ip@example.com"),
        headers={"X-Forwarded-For": "203.0.113.14"},
    )
    assert r.status_code == 200


def test_los_errores_de_validacion_no_gastan_cuota(monkeypatch):
    monkeypatch.setattr("app.main.notify_new_lead", lambda lead: None)

    client = TestClient(app)
    headers = {"X-Forwarded-For": "203.0.113.15"}

    # Un humano que se equivoca tecleando el teléfono varias veces...
    for _ in range(5):
        r = client.post("/api/leads", data=_payload(phone="no-es-un-telefono"), headers=headers)
        assert r.status_code == 400

    # ...sigue pudiendo enviar el formulario cuando lo corrige.
    r = client.post("/api/leads", data=_payload(), headers=headers)
    assert r.status_code == 200


def test_client_ip_prefiere_x_forwarded_for():
    """Detrás del proxy de Railway, `request.client.host` es el edge."""
    class _Req:
        def __init__(self, headers, host):
            self.headers = headers
            self.client = type("C", (), {"host": host})()

    proxy = _Req({"x-forwarded-for": "198.51.100.7, 10.0.0.1"}, "10.0.0.1")
    assert main_module._client_ip(proxy) == "198.51.100.7"

    directo = _Req({}, "198.51.100.8")
    assert main_module._client_ip(directo) == "198.51.100.8"
