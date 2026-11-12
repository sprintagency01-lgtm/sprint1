"""Diagnóstico de automatización: puntuación, endpoints y email."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import diagnostico as d
from app.lead_notifications import LeadNotification, _autoreply_parts
from app.main import app

N = len(d.QUESTIONS)
I = {q["id"]: i for i, q in enumerate(d.QUESTIONS)}


def _answers(pain: int, tamano: int = 1, rol: int = 0, prioridad: int = 1) -> list[int]:
    a = [0] * N
    for q in d.QUESTIONS:
        if q.get("area"):
            a[I[q["id"]]] = pain
    a[I["tamano"]] = tamano
    a[I["rol"]] = rol
    a[I["prioridad"]] = prioridad
    a[I["herramientas"]] = 0b0000011
    return a


@pytest.fixture(autouse=True)
def _clean_hits():
    d._HITS.clear()
    yield
    d._HITS.clear()


def test_parse_valida_longitud_y_rangos():
    ok = _answers(1)
    assert d.parse_answers(ok) == ok
    assert d.parse_answers(d.encode_answers(ok)) == ok
    with pytest.raises(d.InvalidAnswers):
        d.parse_answers(ok[:-1])
    bad = list(ok)
    bad[I["web"]] = 4
    with pytest.raises(d.InvalidAnswers):
        d.parse_answers(bad)
    bad = list(ok)
    bad[I["herramientas"]] = 1 << 7  # solo hay 7 opciones
    with pytest.raises(d.InvalidAnswers):
        d.parse_answers(bad)
    with pytest.raises(d.InvalidAnswers):
        d.parse_answers("1.2.x")


@pytest.mark.parametrize("pain,perfil", [(0, "piloto"), (1, "fugas"), (2, "pegamento"), (3, "manos")])
def test_perfiles_por_nivel_de_fuga(pain, perfil):
    r = d.compute(_answers(pain))
    assert r["perfil"]["id"] == perfil
    assert r["fuga_pct"] == round(100 * pain / 3)
    assert [b["id"] for b in r["bloques"]] == ["visibilidad", "ventas", "operaciones", "clientes"]


def test_sin_fugas_no_hay_lista_de_fugas_ni_automatizaciones():
    r = d.compute(_answers(0))
    assert r["fugas"] == [] and r["automatizaciones"] == []
    assert r["horas"]["max"] > r["horas"]["min"] >= 0


def test_las_horas_crecen_con_el_tamano_y_la_fuga():
    small = d.compute(_answers(3, tamano=0))
    big = d.compute(_answers(3, tamano=3))
    calm = d.compute(_answers(1, tamano=3))
    assert big["horas"]["max"] > small["horas"]["max"]
    assert big["horas"]["max"] > calm["horas"]["max"]
    assert big["euros_mes"] % 50 == 0


def test_la_prioridad_ordena_las_fugas():
    vender = d.compute(_answers(3, prioridad=0))
    ahorrar = d.compute(_answers(3, prioridad=1))
    assert vender["fugas"][0]["area"] == "captacion"
    assert ahorrar["fugas"][0]["area"] == "datos"
    assert len(vender["fugas"]) == 5 and len(vender["automatizaciones"]) == 6


def test_temperatura_del_contacto():
    assert d.compute(_answers(3, tamano=2, rol=0))["temperatura"] == "caliente"
    assert d.compute(_answers(0, tamano=0, rol=4))["temperatura"] == "frío"


def test_resultado_publico_no_lleva_el_detalle():
    pub = d.public_result(d.compute(_answers(2)))
    assert "fugas" not in pub and "automatizaciones" not in pub
    assert pub["n_fugas"] > 0 and "— Diagnóstico —" in pub["resumen"]


def test_email_trae_avance_y_boton_de_presupuesto():
    r = d.compute(_answers(3))
    subject, text, body = d.build_email("Laura Gómez", r)
    assert "horas al mes" in subject
    assert "Lo que te falta" in body and "Lo que se podría automatizar" in body
    assert f"?r={r['codigo']}#presupuesto" in body and "presupuesto sin compromiso" in body
    assert text.startswith("Hola Laura,")


def test_pagina_inyecta_las_preguntas():
    client = TestClient(app)
    res = client.get("/diagnostico")
    assert res.status_code == 200
    assert "¿Cuántas personas sois?" in res.text


def test_endpoint_resultado():
    client = TestClient(app)
    res = client.post("/api/diagnostico/resultado", json={"answers": _answers(2)})
    assert res.status_code == 200
    assert res.json()["perfil"]["id"] == "pegamento"
    assert client.post("/api/diagnostico/resultado", json={"answers": [1, 2]}).status_code == 400


def test_enviar_valida_y_entrega_en_segundo_plano(monkeypatch):
    sent = []
    monkeypatch.setattr(d, "_deliver", lambda *a, **k: sent.append(a))
    client = TestClient(app)
    base = {"answers": _answers(2), "name": "Laura", "email": "laura@example.com", "company": "Acme", "consent": True}

    assert client.post("/api/diagnostico/enviar", json={**base, "consent": False}).status_code == 400
    assert client.post("/api/diagnostico/enviar", json={**base, "email": "no-es-email"}).status_code == 400
    res = client.post("/api/diagnostico/enviar", json=base, headers={"X-Forwarded-For": "203.0.113.5"})
    assert res.status_code == 200 and res.json() == {"ok": True}
    assert len(sent) == 1 and sent[0][1] == "laura@example.com"


def test_enviar_honeypot_y_rate_limit(monkeypatch):
    sent = []
    monkeypatch.setattr(d, "_deliver", lambda *a, **k: sent.append(a))
    client = TestClient(app)
    base = {"answers": _answers(2), "name": "Laura", "email": "laura@example.com", "consent": True}
    h = {"X-Forwarded-For": "203.0.113.9"}

    assert client.post("/api/diagnostico/enviar", json={**base, "website": "x"}, headers=h).json() == {"ok": True}
    assert sent == []
    for _ in range(d._MAX_HITS - 1):
        assert client.post("/api/diagnostico/enviar", json=base, headers=h).status_code == 200
    assert client.post("/api/diagnostico/enviar", json=base, headers=h).status_code == 429


def test_autorespuesta_del_presupuesto_no_habla_de_voz():
    lead = LeadNotification(lead_id=1, name="Laura", phone="+34600000000", source="diagnostico")
    subject, _, body, _, signoff = _autoreply_parts(lead)
    assert "presupuesto" in subject.lower()
    assert "voz" not in body
    assert "Mario" in signoff


def test_diagnostico_en_ingles():
    client = TestClient(app)
    es = client.get("/diagnostico").text
    en = client.get("/en/diagnosis")
    assert en.status_code == 200
    t = en.text
    assert '<html lang="en">' in t and "How many people work at your company?" in t
    assert "Analyse my website" in t and "¿Cuántas personas sois?" not in t
    assert 'hreflang="es" href="https://sprintiasolutions.com/diagnostico"' in t
    assert 'hreflang="en" href="https://sprintiasolutions.com/en/diagnosis"' in es
    assert '"source": "diagnostico-en"' in t and "Pon tu web" in es

    res = client.post("/api/diagnostico/resultado", json={"answers": _answers(2), "lang": "en"}).json()
    assert res["perfil"]["nombre"] == "Your team is the glue"
    assert {b["nombre"] for b in res["bloques"]} == {"Visibility", "Sales", "Operations", "Customers"}
    assert "Perfil: Tu equipo hace de pegamento" in res["resumen"]  # lo que lee Mario sigue en español
    err = client.post("/api/diagnostico/enviar", json={"answers": _answers(2), "name": "L", "lang": "en"})
    assert err.json()["error"] == "Tell us your name."


def test_email_en_ingles():
    r = d.compute(_answers(3))
    subject, text, body = d.build_email("Laura Smith", r, lang="en")
    assert subject.startswith("Your diagnosis:") and text.startswith("Hi Laura,")
    assert "What you're missing" in body and "What could be automated" in body
    assert f"/en/diagnosis?r={r['codigo']}#presupuesto" in body
    assert "Lo que te falta" not in body and '<html lang="en">' in body


def test_autorespuesta_presupuesto_en_ingles():
    lead = LeadNotification(lead_id=1, name="Laura", phone="+34600000000", source="diagnostico-en",
                            landing_language="en")
    subject, _, body, _, signoff = _autoreply_parts(lead)
    assert "quote" in subject and "voice" not in body and "Mario" in signoff
