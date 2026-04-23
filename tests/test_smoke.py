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


def test_landing_ok():
    # Tras retirar el webhook de WhatsApp, el endpoint público que nos
    # interesa comprobar es la landing pública en `/`.
    client = TestClient(app)
    r = client.get("/")
    assert r.status_code == 200
