"""Tests del endpoint `/_diag/telegram/status` — en concreto los 5 estados
categóricos que introdujimos para que el diagnóstico sea rápido y legible:

- `not_configured`: sin TELEGRAM_BOT_TOKEN.
- `token_invalid`: getMe devuelve 401.
- `webhook_missing`: token OK pero getWebhookInfo devuelve url vacío
  (escenario típico cuando otro servicio hace getUpdates contra el bot).
- `webhook_mismatched`: webhook apunta a otra URL distinta de la esperada.
- `webhook_errors`: hay last_error_* reciente.
- `healthy`: todo OK.

Los tests parchean `app.diag.tg_module.TelegramClient.get_me` y `httpx.get`
(que es como el endpoint pide `getWebhookInfo`).
"""
from __future__ import annotations

import time
import types
from typing import Any

import pytest


_BASE = "/_diag/telegram/status"
_HEADERS_OK = {"X-Tool-Secret": "test-secret"}


def _fake_settings(**overrides):
    base = dict(
        tool_secret="test-secret",
        telegram_bot_token="",
        telegram_webhook_secret="",
        telegram_default_tenant_id="",
    )
    base.update(overrides)
    return types.SimpleNamespace(**base)


@pytest.fixture
def client_with_settings(monkeypatch):
    """Devuelve un helper que crea un TestClient con settings parcheados.

    Se parchean `app.diag.settings` (el endpoint lee de ahí) y, cuando el
    test lo requiere, también el TelegramClient y httpx.get de diag.
    """
    from fastapi.testclient import TestClient
    from app.main import app
    import app.diag as diag_mod
    import app.telegram as tg_mod

    def _build(*, tg_client=None, http_get=None, **settings_overrides):
        monkeypatch.setattr(diag_mod, "settings", _fake_settings(**settings_overrides))
        if tg_client is not None:
            monkeypatch.setattr(tg_mod, "TelegramClient", tg_client)
        if http_get is not None:
            # El endpoint hace `import httpx; httpx.get(...)` dentro de la
            # función; parcheamos el módulo global.
            import httpx
            monkeypatch.setattr(httpx, "get", http_get)
        return TestClient(app)

    return _build


def test_status_not_configured(client_with_settings):
    client = client_with_settings()  # sin token
    r = client.get(_BASE, headers=_HEADERS_OK)
    assert r.status_code == 200
    body = r.json()
    assert body == {
        "configured": False,
        "status": "not_configured",
        "hint": "Añade TELEGRAM_BOT_TOKEN en Railway para activar el canal.",
    }


def test_status_token_invalid(client_with_settings):
    import app.telegram as tg_mod

    class _BadClient:
        def __init__(self, token, timeout=None): pass
        def get_me(self):
            raise tg_mod.TelegramError("getMe → HTTP 401 — Unauthorized")

    client = client_with_settings(
        telegram_bot_token="bad_token",
        tg_client=_BadClient,
    )
    r = client.get(_BASE, headers=_HEADERS_OK)
    body = r.json()
    assert body["status"] == "token_invalid"
    assert body["ok"] is False
    assert "BotFather" in body["hint"]


def test_status_webhook_missing(client_with_settings):
    """Token OK pero getWebhookInfo devuelve url vacío."""

    class _OKClient:
        def __init__(self, token, timeout=None): pass
        def get_me(self):
            return {"id": 42, "username": "some_bot", "first_name": "X", "can_join_groups": True}

    def _fake_httpx_get(url, timeout=None, **_kw):
        return types.SimpleNamespace(
            status_code=200,
            json=lambda: {"ok": True, "result": {"url": "", "pending_update_count": 0}},
        )

    client = client_with_settings(
        telegram_bot_token="valid",
        telegram_webhook_secret="secret",
        tg_client=_OKClient,
        http_get=_fake_httpx_get,
    )
    r = client.get(_BASE, headers=_HEADERS_OK)
    body = r.json()
    assert body["status"] == "webhook_missing"
    assert body["ok"] is False
    assert "getUpdates" in body["hint"] or "setWebhook" in body["hint"] or "registrado" in body["hint"]


def test_status_webhook_mismatched(client_with_settings):
    """Webhook apunta a otra URL distinta."""
    class _OKClient:
        def __init__(self, token, timeout=None): pass
        def get_me(self):
            return {"id": 42, "username": "x_bot", "first_name": "X", "can_join_groups": True}

    def _fake_httpx_get(url, timeout=None, **_kw):
        return types.SimpleNamespace(
            status_code=200,
            json=lambda: {
                "ok": True,
                "result": {"url": "https://old-backend.example.com/tg", "pending_update_count": 0},
            },
        )

    client = client_with_settings(
        telegram_bot_token="valid",
        tg_client=_OKClient,
        http_get=_fake_httpx_get,
    )
    r = client.get(_BASE, headers=_HEADERS_OK)
    body = r.json()
    assert body["status"] == "webhook_mismatched"
    assert body["ok"] is False
    assert "old-backend" in body["hint"]


def test_status_webhook_errors_recent(client_with_settings):
    """Webhook con last_error reciente debe reportarse como webhook_errors."""
    class _OKClient:
        def __init__(self, token, timeout=None): pass
        def get_me(self):
            return {"id": 42, "username": "x", "first_name": "X", "can_join_groups": True}

    def _fake_httpx_get(url, timeout=None, **_kw):
        return types.SimpleNamespace(
            status_code=200,
            json=lambda: {
                "ok": True,
                "result": {
                    "url": "https://web-production-98b02b.up.railway.app/telegram/webhook",
                    "pending_update_count": 3,
                    "last_error_date": int(time.time()),  # hace 0s → reciente
                    "last_error_message": "Connection timeout",
                },
            },
        )

    client = client_with_settings(
        telegram_bot_token="valid",
        tg_client=_OKClient,
        http_get=_fake_httpx_get,
    )
    r = client.get(_BASE, headers=_HEADERS_OK)
    body = r.json()
    assert body["status"] == "webhook_errors"
    assert body["ok"] is False
    assert "Connection timeout" in body["hint"]


def test_status_webhook_errors_antiguos_no_alarman(client_with_settings):
    """Si last_error es de hace >10 min, consideramos el webhook sano."""
    class _OKClient:
        def __init__(self, token, timeout=None): pass
        def get_me(self):
            return {"id": 42, "username": "x", "first_name": "X", "can_join_groups": True}

    def _fake_httpx_get(url, timeout=None, **_kw):
        return types.SimpleNamespace(
            status_code=200,
            json=lambda: {
                "ok": True,
                "result": {
                    "url": "https://web-production-98b02b.up.railway.app/telegram/webhook",
                    "pending_update_count": 0,
                    "last_error_date": int(time.time()) - 3600,  # hace 1h
                    "last_error_message": "Old flake that resolved",
                },
            },
        )

    client = client_with_settings(
        telegram_bot_token="valid",
        tg_client=_OKClient,
        http_get=_fake_httpx_get,
    )
    r = client.get(_BASE, headers=_HEADERS_OK)
    body = r.json()
    assert body["status"] == "healthy"
    assert body["ok"] is True


def test_status_healthy(client_with_settings):
    """Escenario ideal: token OK, webhook URL correcta, sin errores."""
    class _OKClient:
        def __init__(self, token, timeout=None): pass
        def get_me(self):
            return {"id": 42, "username": "sprintagency_reservas_bot",
                    "first_name": "Sprint Agency Reservas", "can_join_groups": True}

    def _fake_httpx_get(url, timeout=None, **_kw):
        return types.SimpleNamespace(
            status_code=200,
            json=lambda: {
                "ok": True,
                "result": {
                    "url": "https://web-production-98b02b.up.railway.app/telegram/webhook",
                    "pending_update_count": 0,
                    "last_error_date": None,
                    "last_error_message": None,
                },
            },
        )

    client = client_with_settings(
        telegram_bot_token="valid",
        telegram_webhook_secret="secret",
        telegram_default_tenant_id="pelu_demo",
        tg_client=_OKClient,
        http_get=_fake_httpx_get,
    )
    r = client.get(_BASE, headers=_HEADERS_OK)
    body = r.json()
    assert body["status"] == "healthy"
    assert body["ok"] is True
    # No debe haber `hint` cuando todo está bien — reduce ruido.
    assert "hint" not in body
    assert body["webhook"]["url"].endswith("/telegram/webhook")
    assert body["webhook"]["secret_token_configured"] is True
    assert body["default_tenant_id"] == "pelu_demo"


def test_status_rechaza_sin_tool_secret(client_with_settings):
    """Sin X-Tool-Secret el endpoint debe devolver 401."""
    client = client_with_settings(telegram_bot_token="x")
    r = client.get(_BASE)  # sin headers
    assert r.status_code == 401
