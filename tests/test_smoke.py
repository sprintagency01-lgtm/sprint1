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


def test_english_landing_ok():
    client = TestClient(app)
    r = client.get("/en")
    assert r.status_code == 200
    assert '<html lang="en">' in r.text
    assert 'https://sprintiasolutions.com/en' in r.text
    assert 'Your <em>bookings</em>' in r.text
    assert 'name="landing_language" value="en"' in r.text
    assert "Try Sprintia" in r.text
    assert "lang: landingLanguage().slice(0, 2).toLowerCase()" in r.text


def test_english_demo_copy_ok():
    client = TestClient(app)
    r = client.get("/gemini-demo?embed=1&lang=en")
    assert r.status_code == 200
    assert "Live demo · Talk to Ana" in r.text
    assert "Start call" in r.text
    assert "She answers with a natural English voice." in r.text
    assert "&lang=${encodeURIComponent(LANG)}" in r.text
