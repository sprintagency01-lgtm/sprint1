"""Tests unitarios de `app.telegram`.

Cubren las piezas que no requieren red:
- Conversión AgentReply → payload para send_message (con/sin interactivos).
- Truncado de callback_data largos al límite de 64 bytes.
- Parseo defensivo de `handle_update` frente a updates raras (stickers,
  sin texto, sin chat_id, tipos ignorados).
- Camino feliz de `handle_update` con el agente monkeypatcheado — sin red,
  sin OpenAI, sin BD real.

Nota sobre los parches: `handle_update` hace `from . import agent as agent_mod`
dentro de la función para evitar ciclos al importar. Como `app.agent` ya está
registrado como submódulo del paquete `app` cuando se carga el test suite
completo, parchear `sys.modules` NO bastaba (ver conversación con Marcos).
La forma robusta es parchear los atributos concretos con
`monkeypatch.setattr("app.agent.reply", ...)`.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from app import telegram as tg


# ---------------------------------------------------------------------------
#  Fixtures ligeros
# ---------------------------------------------------------------------------

@dataclass
class _FakeReply:
    """Stand-in de `app.agent.AgentReply` para el test."""
    text: str
    interactive: dict | None = None


# ---------------------------------------------------------------------------
#  agent_reply_to_payload
# ---------------------------------------------------------------------------

def test_payload_texto_simple():
    reply = _FakeReply(text="hola, ¿en qué te ayudo?")
    p = tg.agent_reply_to_payload(reply, chat_id=123)
    assert p["chat_id"] == 123
    assert p["text"] == "hola, ¿en qué te ayudo?"
    assert "reply_markup" not in p


def test_payload_sin_interactive_options():
    # Interactive presente pero sin options → se trata como texto puro.
    reply = _FakeReply(text="x", interactive={"type": "list", "options": []})
    p = tg.agent_reply_to_payload(reply, chat_id=1)
    assert "reply_markup" not in p


def test_payload_lista_una_por_fila():
    options = [
        {"id": "slot:2026-04-24T10:00:2026-04-24T10:30", "title": "Hoy 10:00"},
        {"id": "slot:2026-04-24T11:00:2026-04-24T11:30", "title": "Hoy 11:00"},
        {"id": "slot:2026-04-24T12:00:2026-04-24T12:30", "title": "Hoy 12:00"},
    ]
    reply = _FakeReply(
        text="tengo estos huecos",
        interactive={"type": "list", "options": options},
    )
    p = tg.agent_reply_to_payload(reply, chat_id=1)
    kb = p["reply_markup"]["inline_keyboard"]
    # Una fila por opción → listas son legibles en móvil.
    assert len(kb) == 3
    assert all(len(row) == 1 for row in kb)
    assert kb[0][0]["text"] == "Hoy 10:00"
    assert kb[0][0]["callback_data"] == "slot:2026-04-24T10:00:2026-04-24T10:30"


def test_payload_botones_corto_una_fila():
    options = [
        {"id": "confirm:yes", "title": "Sí"},
        {"id": "confirm:no", "title": "No"},
    ]
    reply = _FakeReply(
        text="¿confirmas?",
        interactive={"type": "buttons", "options": options},
    )
    p = tg.agent_reply_to_payload(reply, chat_id=1)
    kb = p["reply_markup"]["inline_keyboard"]
    # Confirmar sí/no cabe en una fila.
    assert len(kb) == 1
    assert len(kb[0]) == 2
    assert kb[0][0]["callback_data"] == "confirm:yes"


def test_payload_botones_pocos_cabe_horizontal():
    # 3 botones de equipo en una sola fila (Mario, Marcos, Cualquiera).
    options = [
        {"id": "team:1", "title": "Mario"},
        {"id": "team:2", "title": "Marcos"},
        {"id": "team:none", "title": "Cualquiera"},
    ]
    reply = _FakeReply(text="¿con quién?", interactive={"type": "buttons", "options": options})
    p = tg.agent_reply_to_payload(reply, chat_id=1)
    kb = p["reply_markup"]["inline_keyboard"]
    assert len(kb) == 1
    assert len(kb[0]) == 3


def test_payload_muchos_botones_fallback_lista():
    # Con más de 3, aunque type="buttons", nos replegamos a 1-por-fila
    # (legibilidad manda).
    options = [{"id": f"team:{i}", "title": f"Pelu{i}"} for i in range(5)]
    reply = _FakeReply(text="x", interactive={"type": "buttons", "options": options})
    p = tg.agent_reply_to_payload(reply, chat_id=1)
    kb = p["reply_markup"]["inline_keyboard"]
    assert len(kb) == 5
    assert all(len(r) == 1 for r in kb)


# ---------------------------------------------------------------------------
#  _truncate_callback_data
# ---------------------------------------------------------------------------

def test_truncate_callback_data_short_no_op():
    assert tg._truncate_callback_data("confirm:yes") == "confirm:yes"


def test_truncate_callback_data_boundary():
    exact = "a" * 64
    assert tg._truncate_callback_data(exact) == exact


def test_truncate_callback_data_supera_recorta():
    big = "x" * 100
    out = tg._truncate_callback_data(big)
    assert len(out.encode("utf-8")) <= 64


def test_truncate_callback_data_respeta_utf8():
    # 30 caracteres cada uno 2 bytes = 60 bytes → cabe.
    # 33 caracteres de 2 bytes = 66 bytes → hay que recortar sin romper
    # un carácter multibyte a la mitad.
    s = "ñ" * 33
    out = tg._truncate_callback_data(s)
    # Debe seguir siendo decodificable como UTF-8 sin errores.
    out.encode("utf-8").decode("utf-8")
    assert len(out.encode("utf-8")) <= 64


# ---------------------------------------------------------------------------
#  _extract_turn
# ---------------------------------------------------------------------------

def test_extract_turn_mensaje_normal():
    update = {"message": {"chat": {"id": 42}, "text": "hola"}}
    chat_id, text, cb = tg._extract_turn(update)
    assert chat_id == 42
    assert text == "hola"
    assert cb is None


def test_extract_turn_edited_message():
    update = {"edited_message": {"chat": {"id": 7}, "text": "corregido"}}
    chat_id, text, cb = tg._extract_turn(update)
    assert chat_id == 7
    assert text == "corregido"


def test_extract_turn_callback_query():
    update = {
        "callback_query": {
            "id": "cq1",
            "data": "slot:2026-04-24T10:00:2026-04-24T10:30",
            "message": {"chat": {"id": 99}},
        }
    }
    chat_id, text, cb = tg._extract_turn(update)
    assert chat_id == 99
    assert text == "slot:2026-04-24T10:00:2026-04-24T10:30"
    assert cb == "cq1"


def test_extract_turn_update_sin_nada():
    chat_id, text, cb = tg._extract_turn({"poll": {"question": "x"}})
    assert chat_id is None
    assert text == ""


# ---------------------------------------------------------------------------
#  handle_update: casos que NO llaman al agente
# ---------------------------------------------------------------------------

def test_handle_update_tipo_no_soportado():
    out = tg.handle_update({"poll_answer": {}}, bot_token="XXX")
    assert out == {"ok": True, "ignored": "tipo de update no soportado"}


def test_handle_update_sin_texto_sin_callback():
    # Sticker sin texto: no llamamos al agente, pero es "ok" para Telegram.
    update = {"message": {"chat": {"id": 1}, "sticker": {"emoji": "👋"}}}
    out = tg.handle_update(update, bot_token="XXX")
    assert out == {"ok": True, "ignored": "sin texto procesable"}


def test_handle_update_payload_no_dict():
    # Telegram nunca debería enviar esto, pero defensivo.
    out = tg.handle_update([], bot_token="XXX")  # type: ignore[arg-type]
    assert out["ok"] is False


# ---------------------------------------------------------------------------
#  handle_update: camino feliz con parches directos sobre módulos reales
# ---------------------------------------------------------------------------

class _FakeTgClient:
    """Substituto para TelegramClient — registra llamadas, no hace HTTP.

    send_message acepta tanto positional (chat_id, text) como kwargs
    (chat_id=..., text=...) porque handle_update usa **payload (kwargs).
    """
    def __init__(self, bot_token: str, *, timeout=None) -> None:
        self.bot_token = bot_token
        self.sent: list[dict] = []
        self.actions: list[str] = []
        self.callbacks_acked: list[str] = []

    def send_message(self, chat_id=None, text=None, **kwargs):
        record = {"chat_id": chat_id, "text": text}
        record.update(kwargs)
        self.sent.append(record)
        return {"message_id": 1}

    def send_chat_action(self, chat_id, action="typing"):
        self.actions.append(action)
        return {}

    def answer_callback_query(self, callback_query_id, **_kw):
        self.callbacks_acked.append(callback_query_id)
        return {}


@pytest.fixture
def patched_world(monkeypatch):
    """Patch agent.reply / db.* / tenants.* / TelegramClient sobre los módulos reales."""
    # 1) Agent reply fake: capturamos inputs y devolvemos respuesta fija.
    captured: dict[str, Any] = {}

    def _fake_reply(user_message, history, tenant, caller_phone):
        captured["user_message"] = user_message
        captured["history"] = list(history)
        captured["tenant_id"] = tenant.get("id")
        captured["caller_phone"] = caller_phone
        return _FakeReply(text="vale, a las 10 te va bien?")

    monkeypatch.setattr("app.agent.reply", _fake_reply)

    # 2) DB: no escribimos BD real.
    saved: list[dict] = []

    def _save_message(**kwargs):
        saved.append(kwargs)

    def _load_history(**_kwargs):
        return []

    monkeypatch.setattr("app.db.save_message", _save_message)
    monkeypatch.setattr("app.db.load_history", _load_history)

    # 3) Tenants: responde con uno fijo.
    tenant_dict = {
        "id": "default", "name": "Test",
        "kind": "contracted", "status": "active",
        "system_prompt": "test prompt",
    }
    monkeypatch.setattr("app.tenants.get_tenant", lambda tid: tenant_dict if tid == "default" else None)
    monkeypatch.setattr("app.tenants.load_tenants", lambda: [tenant_dict])

    # 4) TelegramClient: no HTTP real.
    clients: list[_FakeTgClient] = []

    def _factory(token, timeout=None):
        c = _FakeTgClient(token, timeout=timeout)
        clients.append(c)
        return c

    monkeypatch.setattr(tg, "TelegramClient", _factory)

    return {"captured": captured, "saved": saved, "clients": clients, "tenant": tenant_dict}


def test_handle_update_mensaje_feliz(patched_world):
    update = {"message": {"chat": {"id": 12345}, "text": "quiero una cita"}}
    out = tg.handle_update(update, bot_token="FAKE", preferred_tenant_id="default")

    assert out == {"ok": True, "chat_id": 12345, "tenant_id": "default"}

    captured = patched_world["captured"]
    assert captured["user_message"] == "quiero una cita"
    assert captured["caller_phone"] == "tg:12345"
    assert captured["tenant_id"] == "default"

    # Dos mensajes guardados en BD: user + assistant.
    saved = patched_world["saved"]
    assert len(saved) == 2
    assert saved[0]["role"] == "user"
    assert saved[0]["content"] == "quiero una cita"
    assert saved[1]["role"] == "assistant"
    assert saved[1]["content"] == "vale, a las 10 te va bien?"
    assert saved[0]["customer_phone"] == "tg:12345"

    # Se mandó send_chat_action + send_message.
    client = patched_world["clients"][0]
    assert "typing" in client.actions
    assert len(client.sent) == 1
    assert client.sent[0]["chat_id"] == 12345
    assert client.sent[0]["text"] == "vale, a las 10 te va bien?"


def test_handle_update_callback_query_acknowledged(patched_world):
    update = {
        "callback_query": {
            "id": "cb123",
            "data": "confirm:yes",
            "message": {"chat": {"id": 999}},
        }
    }
    out = tg.handle_update(update, bot_token="FAKE", preferred_tenant_id="default")
    assert out["ok"] is True

    client = patched_world["clients"][0]
    # Debe haber hecho ack de la callback antes de nada para cerrar el spinner.
    assert "cb123" in client.callbacks_acked
    # Y el agente recibió "confirm:yes" como texto sintético.
    assert patched_world["captured"]["user_message"] == "confirm:yes"


def test_handle_update_sin_tenant_disponible(monkeypatch):
    """Si no hay ningún tenant en la BD avisamos al cliente sin explotar."""
    monkeypatch.setattr("app.tenants.get_tenant", lambda tid: None)
    monkeypatch.setattr("app.tenants.load_tenants", lambda: [])

    clients: list[_FakeTgClient] = []

    def _factory(token, timeout=None):
        c = _FakeTgClient(token, timeout=timeout)
        clients.append(c)
        return c

    monkeypatch.setattr(tg, "TelegramClient", _factory)

    out = tg.handle_update({"message": {"chat": {"id": 1}, "text": "hola"}}, bot_token="FAKE")
    assert out["ok"] is False
    assert "tenants" in out["error"]

    # Al cliente sí le mandamos un aviso legible.
    assert len(clients) == 1
    assert len(clients[0].sent) == 1
    aviso = clients[0].sent[0]["text"] or ""
    assert "negocio" in aviso.lower() or "conectado" in aviso.lower()


# ---------------------------------------------------------------------------
#  _resolve_tenant_id
# ---------------------------------------------------------------------------

def test_resolve_tenant_id_prefiere_explicito(monkeypatch):
    t1 = {"id": "explicit", "kind": "contracted", "status": "active"}
    t2 = {"id": "another", "kind": "contracted", "status": "active"}
    monkeypatch.setattr("app.tenants.get_tenant", lambda tid: t1 if tid == "explicit" else None)
    monkeypatch.setattr("app.tenants.load_tenants", lambda: [t1, t2])
    assert tg._resolve_tenant_id("explicit") == "explicit"


def test_resolve_tenant_id_cae_a_contracted_active(monkeypatch):
    t1 = {"id": "a", "kind": "lead", "status": "active"}
    t2 = {"id": "b", "kind": "contracted", "status": "paused"}
    t3 = {"id": "c", "kind": "contracted", "status": "active"}
    monkeypatch.setattr("app.tenants.get_tenant", lambda tid: None)
    monkeypatch.setattr("app.tenants.load_tenants", lambda: [t1, t2, t3])
    # Preferencia por contracted+active incluso si no está el primero.
    assert tg._resolve_tenant_id("") == "c"


def test_resolve_tenant_id_fallback_primero(monkeypatch):
    t1 = {"id": "a", "kind": "lead"}
    t2 = {"id": "b", "kind": "lead"}
    monkeypatch.setattr("app.tenants.get_tenant", lambda tid: None)
    monkeypatch.setattr("app.tenants.load_tenants", lambda: [t1, t2])
    assert tg._resolve_tenant_id("") == "a"


def test_resolve_tenant_id_sin_tenants(monkeypatch):
    monkeypatch.setattr("app.tenants.get_tenant", lambda tid: None)
    monkeypatch.setattr("app.tenants.load_tenants", lambda: [])
    assert tg._resolve_tenant_id("") is None


# ---------------------------------------------------------------------------
#  Endpoint /telegram/webhook (integración FastAPI)
# ---------------------------------------------------------------------------

import types


def _fake_settings(**overrides):
    """Construye un objeto tipo `Settings` con los atributos que usa el endpoint.

    Como `app.config.Settings` es frozen no podemos mutarlo. En vez de eso
    reemplazamos `app.main.settings` por un SimpleNamespace con los atributos
    relevantes — solo `telegram_bot_token`, `telegram_webhook_secret` y
    `telegram_default_tenant_id` son los que mira el endpoint.
    """
    defaults = dict(telegram_bot_token="", telegram_webhook_secret="",
                    telegram_default_tenant_id="")
    defaults.update(overrides)
    return types.SimpleNamespace(**defaults)


def test_webhook_sin_config_devuelve_501(monkeypatch):
    """Si TELEGRAM_BOT_TOKEN no está, el endpoint debe decirlo claramente."""
    from fastapi.testclient import TestClient
    from app.main import app
    import app.main as main_mod

    monkeypatch.setattr(main_mod, "settings", _fake_settings())
    client = TestClient(app)
    r = client.post("/telegram/webhook", json={})
    assert r.status_code == 501


def test_webhook_rechaza_secret_incorrecto(monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import app
    import app.main as main_mod

    monkeypatch.setattr(main_mod, "settings", _fake_settings(
        telegram_bot_token="T",
        telegram_webhook_secret="SUPER",
    ))
    client = TestClient(app)
    r = client.post(
        "/telegram/webhook",
        json={"message": {"chat": {"id": 1}, "text": "hola"}},
        headers={"X-Telegram-Bot-Api-Secret-Token": "WRONG"},
    )
    assert r.status_code == 401


def test_webhook_acepta_secret_correcto(monkeypatch, patched_world):
    from fastapi.testclient import TestClient
    from app.main import app
    import app.main as main_mod

    monkeypatch.setattr(main_mod, "settings", _fake_settings(
        telegram_bot_token="T",
        telegram_webhook_secret="OK",
        telegram_default_tenant_id="default",
    ))
    client = TestClient(app)
    r = client.post(
        "/telegram/webhook",
        json={"message": {"chat": {"id": 1234}, "text": "hola"}},
        headers={"X-Telegram-Bot-Api-Secret-Token": "OK"},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is True
    assert data["chat_id"] == 1234
