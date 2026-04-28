from __future__ import annotations

import types

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app import db as db_module
from app import eleven_tools as et
from app.main import app


def _headers(secret: str = "test-secret-conftest") -> dict[str, str]:
    return {"X-Tool-Secret": secret}


def test_consultar_disponibilidad_acepta_sin_preferencia_con_equipo(monkeypatch):
    tenant = {
        "id": "pelu_demo",
        "business_hours": {},
        "peluqueros": [
            {"nombre": "Mario", "calendar_id": "cal-mario", "dias_trabajo": [0, 1, 2, 3, 4, 5]},
            {"nombre": "Marcos", "calendar_id": "cal-marcos", "dias_trabajo": [0, 1, 2, 3, 4, 5]},
        ],
    }

    monkeypatch.setattr(et, "_resolve_tenant", lambda _tid: tenant)
    monkeypatch.setattr(
        et.cal,
        "listar_huecos_por_peluqueros",
        lambda *a, **kw: [
            {
                "inicio": a[0],
                "fin": a[0],
                "peluquero": "Mario",
            }
        ],
    )

    req = et.ConsultaReq(
        fecha_desde_iso="2099-04-29T15:00:00",
        fecha_hasta_iso="2099-04-29T20:30:00",
        duracion_minutos=30,
        peluquero_preferido="sin preferencia",
        max_resultados=3,
    )
    out = et.consultar_disponibilidad(req, x_tool_secret="test-secret-conftest", tenant_id="pelu_demo")

    assert out["huecos"]
    assert "aviso" not in out
    assert out["huecos"][0]["peluquero"] == "Mario"


def test_elevenlabs_healthcheck_usa_voice_config_real_y_detecta_drift(monkeypatch):
    import app.diag as diag_mod

    with Session(db_module.engine) as s:
        tenant = db_module.Tenant(
            id="voice_health",
            name="Negocio Voz",
            calendar_id="primary",
            timezone="Europe/Madrid",
            voice_agent_id="agent_tenant_123",
            voice_voice_id="voice_abc",
            voice_prompt="PROMPT VIEJO QUE YA NO COINCIDE",
        )
        tenant.services.append(db_module.Service(nombre="Consulta", duracion_min=30, precio=20, orden=0))
        s.add(tenant)
        s.commit()

    monkeypatch.setattr(
        diag_mod,
        "settings",
        types.SimpleNamespace(
            tool_secret="test-secret-conftest",
            elevenlabs_api_key="test-eleven-key",
            elevenlabs_agent_id="agent_global_unused",
        ),
    )
    monkeypatch.setattr(
        diag_mod.elevenlabs_client,
        "get_agent",
        lambda agent_id: {
            "name": "Agente demo",
            "conversation_config": {
                "agent": {
                    "prompt": {
                        "tools": [
                            {"name": "consultar_disponibilidad"},
                            {"name": "crear_reserva"},
                            {"name": "buscar_reserva_cliente"},
                            {"name": "mover_reserva"},
                            {"name": "cancelar_reserva"},
                        ]
                    }
                }
            },
        },
    )

    client = TestClient(app)
    r = client.get("/_diag/elevenlabs/healthcheck?tenant_id=voice_health", headers=_headers())
    assert r.status_code == 200
    body = r.json()

    assert body["tenant_voice_config"]["ok"] is True
    assert body["tenant_voice_config"]["prompt_source"] == "stored"
    assert body["tenant_voice_config"]["voice_id_set"] is True
    assert body["tenant_voice_config"]["prompt_drift"] is True
    assert body["tenant_voice_config"]["agent_id"] == "agent_tenant_123"
    assert body["tenant_voice_config"]["agent_id_source"] == "tenant"
    assert body["agent"]["ok"] is True
    assert body["agent_tools"]["ok"] is True


def test_tenant_voice_refresh_regenera_prompt_y_lo_sincroniza(monkeypatch):
    import app.diag as diag_mod

    with Session(db_module.engine) as s:
        tenant = db_module.Tenant(
            id="voice_refresh",
            name="Abogado Demo",
            calendar_id="primary",
            timezone="Europe/Madrid",
            voice_agent_id="agent_refresh_1",
            voice_voice_id="voice_123",
            voice_prompt="prompt obsoleto",
        )
        tenant.services.append(db_module.Service(nombre="Primera consulta", duracion_min=30, precio=50, orden=0))
        s.add(tenant)
        s.commit()

    monkeypatch.setattr(
        diag_mod,
        "settings",
        types.SimpleNamespace(
            tool_secret="test-secret-conftest",
            elevenlabs_api_key="test-eleven-key",
            elevenlabs_agent_id="",
        ),
    )

    sync_calls: list[dict] = []

    def _fake_sync(agent_id, *, prompt, voice):
        sync_calls.append({"agent_id": agent_id, "prompt": prompt, "voice_id": voice.voice_id})

    monkeypatch.setattr(diag_mod.elevenlabs_client, "sync_agent", _fake_sync)

    client = TestClient(app)
    r = client.post(
        "/_diag/tenant/voice/refresh?tenant_id=voice_refresh",
        headers={**_headers(), "Content-Type": "application/json"},
        json={"sync_to_elevenlabs": True},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert body["synced_to_elevenlabs"] is True
    assert body["prompt_len"] > 0
    assert sync_calls and sync_calls[0]["agent_id"] == "agent_refresh_1"
    assert "Primera consulta 30min 50€" in sync_calls[0]["prompt"]

    with Session(db_module.engine) as s:
        refreshed = s.get(db_module.Tenant, "voice_refresh")
        assert refreshed is not None
        assert "Primera consulta 30min 50€" in refreshed.voice_prompt
        assert refreshed.voice_last_sync_status == "ok"
