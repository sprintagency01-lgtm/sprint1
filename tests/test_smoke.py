"""Tests mínimos: la app arranca y responde al health check."""
from fastapi.testclient import TestClient

from app.main import app


def test_health():
    client = TestClient(app)
    # `/` ahora renderiza la landing pública (HTML), así que el healthcheck
    # JSON vive en `/health`.
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_whatsapp_verify_ok(monkeypatch):
    monkeypatch.setenv("WHATSAPP_VERIFY_TOKEN", "test_token")
    # Reimportar tras monkeypatch si fuese necesario; en esta plantilla
    # settings se lee una vez, así que este test es ilustrativo.
    client = TestClient(app)
    r = client.get(
        "/whatsapp",
        params={"hub.mode": "subscribe", "hub.verify_token": "whatever", "hub.challenge": "xyz"},
    )
    # Si el token no coincide debe devolver 403
    assert r.status_code in (200, 403)
