"""Análisis de webs del diagnóstico: lectura del HTML, nota, seguridad y endpoints."""
from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from app import diagnostico as d
from app import web_audit as w
from app.main import app

GOOD = """<!doctype html><html lang="es"><head>
<title>Clínica Dental Sonrisa · Dentistas en Córdoba</title>
<meta name="description" content="Clínica dental en Córdoba con implantes, ortodoncia invisible y urgencias. Pide cita online hoy mismo.">
<meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="canonical" href="https://sonrisa.es/">
<script async src="https://www.googletagmanager.com/gtag/js?id=G-XXXX"></script>
<script src="//js.hs-scripts.com/123.js"></script>
<script type="application/ld+json">{"@context":"https://schema.org","@type":"Dentist","name":"Sonrisa"}</script>
</head><body>
<h1>Tu dentista en Córdoba</h1>
<img src="a.jpg" alt="Sala"><img src="b.jpg" alt="Equipo"><img src="c.jpg" alt="">
<form action="/contacto"><input type="email" name="email"><textarea name="m"></textarea></form>
<a href="tel:+34957000000">Llámanos</a>
<a href="https://wa.me/34600000000">WhatsApp</a>
<a href="https://www.doctoralia.es/sonrisa">Pide cita</a>
<footer>© 2018 - {year} Sonrisa</footer>
</body></html>"""

BAD = """<html><head><title>Inicio</title><meta name="robots" content="noindex"></head>
<body><p>Somos expertos en HubSpot y Mailchimp desde hace años.</p>
<a href="https://wa.me/?text=Mira%20esto">Compartir</a>
<img src="1.jpg"><img src="2.jpg"><img src="3.jpg">
<footer>© 2019 Empresa</footer></body></html>"""

YEAR = 2026


def _facts(html, url="https://sonrisa.es/", **extra):
    f = w.extract_facts(html.replace("{year}", str(YEAR)), url)
    f.update({"robots_block": False, "sitemap": True, **extra})
    return f


def test_normaliza_y_rechaza_direcciones_raras():
    assert w.normalize_url("sonrisa.es") == "https://sonrisa.es/"
    assert w.normalize_url(" HTTP://Sonrisa.ES/citas?x=1 ") == "http://sonrisa.es/citas?x=1"
    assert w.normalize_url("panadería.es").startswith("https://xn--panadera-")
    for bad in ["", "localhost", "javascript:alert(1)", "http://user:pw@sonrisa.es", "sonrisa.es:8080", "a" * 400]:
        with pytest.raises(w.AuditError):
            w.normalize_url(bad)


@pytest.mark.parametrize("ip,ok", [("8.8.8.8", True), ("127.0.0.1", False), ("10.1.2.3", False),
                                   ("169.254.169.254", False), ("192.168.1.1", False), ("::1", False),
                                   ("::ffff:127.0.0.1", False), ("100.64.0.1", False), ("2606:4700::1111", True)])
def test_solo_ips_publicas(ip, ok):
    assert w._ip_is_public(ip) is ok


def test_no_se_conecta_a_hosts_internos(monkeypatch):
    async def fake_getaddrinfo(host, *a, **k):
        return [(2, 1, 6, "", ("10.0.0.5", 0))]
    loop_cls = type(asyncio.new_event_loop())
    monkeypatch.setattr(loop_cls, "getaddrinfo", lambda self, host, *a, **k: fake_getaddrinfo(host))
    with pytest.raises(w.AuditError, match="pública"):
        asyncio.run(w._check_host("rebind.example.com"))


def test_lee_la_web_buena():
    f = _facts(GOOD)
    assert f["title"].startswith("Clínica Dental") and f["viewport"] and f["https"]
    assert f["form"] and f["tel"] and f["whatsapp"]
    assert f["analitica"] == ["Google Analytics"] and f["marketing"] == ["HubSpot"]
    assert f["reservas"] == ["Doctoralia"] and "Dentist" in f["schema"]
    assert f["copyright"] == YEAR and f["imgs"] == 3 and f["imgs_no_alt"] == 0


def test_nombrar_herramientas_o_compartir_por_whatsapp_no_cuenta():
    f = _facts(BAD, url="http://empresa.es/")
    assert f["marketing"] == [] and f["whatsapp"] is False and f["form"] is False
    assert f["noindex"] and not f["viewport"] and not f["https"]


def test_nota_y_hallazgos():
    good = w.evaluate(_facts(GOOD), {"perf": 92, "seo": 100, "lcp_s": 1.8},
                      {"found": True, "rating": 4.8, "count": 120}, year=YEAR)
    bad = w.evaluate(_facts(BAD, url="http://empresa.es/", sitemap=False),
                     {"perf": 31, "seo": 60, "lcp_s": 6.2}, {"found": False}, year=YEAR)
    assert good["nota"] >= 90 and good["nivel"] == "Bien resuelta"
    assert bad["nota"] < 30 and bad["nivel"] == "Necesita trabajo"
    titulos = [f["titulo"] for f in bad["hallazgos"]]
    assert "Tarda 6,2 s en cargar en el móvil" in titulos
    assert "La web le pide a Google que no la muestre" in titulos
    assert "No hay ninguna forma clara de contactar" in titulos
    assert "No encontramos vuestra ficha de Google" in titulos
    assert "El pie de la web dice © 2019" in titulos
    assert bad["hallazgos"][0]["nivel"] == "mal"  # primero lo grave
    assert "4,8 ★ con 120 reseñas en Google" in good["bien"]


def test_respuestas_automaticas():
    good = w.evaluate(_facts(GOOD), {"perf": 92, "seo": 100, "lcp_s": 1.8},
                      {"found": True, "rating": 4.8, "count": 120}, year=YEAR)["auto"]
    assert good == {"web": 0, "seo": 0, "captacion": 1, "emails": 1, "herramientas": (1 << 2) | (1 << 4),
                    "resenas": 0}
    bad = w.evaluate(_facts(BAD, url="http://empresa.es/"), None, None, year=YEAR)["auto"]
    assert bad["web"] == 2 and bad["captacion"] == 3 and bad["seo"] >= 2
    assert "resenas" not in bad  # sin Google se sigue preguntando
    # Todas son respuestas válidas del test
    answers = [0] * len(d.QUESTIONS)
    for k, v in {**good, **bad}.items():
        answers[d._Q[k]] = v
    d.parse_answers(answers)


def test_robots_que_bloquea_todo():
    assert w._robots_blocks_all("User-agent: *\nDisallow: /\n")
    assert not w._robots_blocks_all("User-agent: *\nDisallow: /admin\nUser-agent: Bad\nDisallow: /")


@pytest.fixture
def fake_audit(monkeypatch):
    audit = w.evaluate(_facts(BAD, url="http://empresa.es/", sitemap=False), None, None, year=YEAR)
    audit.update(url="http://empresa.es/", dominio="empresa.es")
    calls = []

    async def fake_analyze(url, negocio=""):
        calls.append((url, negocio))
        if "caida" in url:
            raise w.AuditError("No hemos podido abrir esa web. Revisa la dirección.")
        return audit

    monkeypatch.setattr(w, "analyze", fake_analyze)
    d._AUDIT_HITS.clear()
    d._HITS.clear()
    yield audit, calls
    d._AUDIT_HITS.clear()
    d._HITS.clear()


def test_endpoint_analizar(fake_audit):
    _, calls = fake_audit
    client = TestClient(app)
    res = client.post("/api/diagnostico/analizar", json={"url": "empresa.es", "negocio": "Empresa SL"})
    assert res.status_code == 200
    body = res.json()
    assert body["nota"] < 40 and body["id"] and body["auto"]["web"] == 2
    assert "web: http://empresa.es/" in body["resumen_web"].lower()
    assert calls == [("https://empresa.es/", "Empresa SL")]
    assert client.post("/api/diagnostico/analizar", json={"url": "localhost"}).status_code == 400
    assert client.post("/api/diagnostico/analizar", json={"url": "caida.es"}).status_code == 422


def test_analizar_tiene_limite_por_ip(fake_audit):
    client = TestClient(app)
    h = {"X-Forwarded-For": "203.0.113.77"}
    for _ in range(d._MAX_AUDITS):
        assert client.post("/api/diagnostico/analizar", json={"url": "empresa.es"}, headers=h).status_code == 200
    assert client.post("/api/diagnostico/analizar", json={"url": "empresa.es"}, headers=h).status_code == 429


def test_el_email_usa_la_auditoria_del_servidor(fake_audit, monkeypatch):
    sent = []
    monkeypatch.setattr(d, "_deliver", lambda *a, **k: sent.append(a))
    client = TestClient(app)
    audit_id = client.post("/api/diagnostico/analizar", json={"url": "empresa.es"}).json()["id"]
    answers = [0] * len(d.QUESTIONS)
    base = {"answers": answers, "name": "Laura", "email": "laura@example.com", "consent": True}
    assert client.post("/api/diagnostico/enviar", json={**base, "audit_id": audit_id}).status_code == 200
    assert client.post("/api/diagnostico/enviar", json={**base, "audit_id": "inventado"}).status_code == 200
    assert sent[0][-1]["dominio"] == "empresa.es"
    assert sent[1][-1] is None

    r = d.compute(answers)
    subject, text, body = d.build_email("Laura", r, sent[0][-1])
    assert "Lo que hemos visto en tu web" in body and "empresa.es" in body
    assert "No hay ninguna forma clara de contactar" in text
    assert "Web: http://empresa.es/" in d.summary_text(r, sent[0][-1])


def test_hallazgos_en_ingles():
    bad = w.evaluate(_facts(BAD, url="http://empresa.es/", sitemap=False),
                     {"perf": 31, "seo": 60, "lcp_s": 6.2}, {"found": False}, year=YEAR)
    v = d.audit_view(bad, "en")
    titulos = [f["titulo"] for f in v["hallazgos"]]
    assert v["nivel"] == "Needs work"
    assert "Takes 6.2 s to load on mobile" in titulos
    assert "The site asks Google not to show it" in titulos
    assert "The footer still says © 2019" in titulos
    assert all(f["titulo"] != f.get("titulo_en") for f in bad["hallazgos"])  # todos traducidos
    good = w.evaluate(_facts(GOOD), {"perf": 92, "seo": 100, "lcp_s": 1.8},
                      {"found": True, "rating": 4.8, "count": 120}, year=YEAR)
    assert "4.8 ★ from 120 reviews on Google" in d.audit_view(good, "en")["bien"]
    assert w.error_en("La web responde con un error (404). Revisa la dirección.").startswith("The site responds")
