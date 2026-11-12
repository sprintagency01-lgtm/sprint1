"""Web corporativa: home, páginas de producto, página de voz y sitemap."""
from __future__ import annotations

from fastapi.testclient import TestClient

from app import sitio
from app.lead_notifications import LeadNotification, _autoreply_parts
from app.main import app

client = TestClient(app)


def test_home_presenta_sprintia_y_sus_productos():
    r = client.get("/")
    assert r.status_code == 200
    assert "Automatización para empresas" in r.text
    for p in sitio.PRODUCTOS:
        assert f'href="{p["url"]}"' in r.text and p["nombre"] in r.text
    assert 'data-url="hero"' in r.text            # la acción principal es analizar la web
    assert "€/mes" not in r.text and "59€" not in r.text  # sin precios en la home
    assert '<link rel="canonical" href="https://sprintiasolutions.com/">' in r.text
    assert "value=\"home\"" not in r.text and "fd.set('source', 'home')" in r.text


def test_paginas_de_producto():
    for slug in ("crm-automatizado", "webs", "automatizacion-de-procesos"):
        r = client.get(f"/productos/{slug}")
        assert r.status_code == 200, slug
        assert f'href="https://sprintiasolutions.com/productos/{slug}"' in r.text
        assert f"fd.set('source', 'producto-{slug}')" in r.text
        assert '"@type": "Service"' in r.text
    webs = client.get("/productos/webs").text
    assert "€/mes" not in webs and "59" not in webs     # sin precios en la web corporativa
    assert "sin permanencia" in webs
    assert "€/month" not in client.get("/en/products/websites").text
    assert client.get("/productos/no-existe").status_code == 404
    r = client.get("/productos/asistente-de-voz", follow_redirects=False)
    assert r.status_code == 301 and r.headers["location"] == "/asistente-de-voz"


def test_la_landing_de_voz_se_mueve_sin_perder_nada():
    r = client.get("/asistente-de-voz")
    assert r.status_code == 200
    assert "Habla con Ana" in r.text
    assert '<link rel="canonical" href="https://sprintiasolutions.com/asistente-de-voz">' in r.text
    assert '<a class="logo" href="/"' in r.text
    en = client.get("/en/voice-assistant").text
    assert 'hreflang="es" href="https://sprintiasolutions.com/asistente-de-voz"' in en
    assert '<link rel="canonical" href="https://sprintiasolutions.com/en/voice-assistant">' in en
    assert '<a class="logo" href="/en"' in en and 'href="/asistente-de-voz" class="btn btn-sm btn-ghost" hreflang="es"' in en
    es = client.get("/asistente-de-voz").text
    assert 'href="/en/voice-assistant" class="btn btn-sm btn-ghost" hreflang="en"' in es
    assert "Permanencia mínima" not in es and "12-month" not in en  # sin permanencia


def test_sitemap_incluye_productos():
    xml = client.get("/sitemap.xml").text
    for path in ["/", "/diagnostico", "/asistente-de-voz", "/productos/crm-automatizado", "/productos/webs",
                 "/productos/automatizacion-de-procesos", "/en", "/en/voice-assistant", "/en/products/automated-crm",
                 "/en/products/websites", "/en/products/process-automation"]:
        assert f"<loc>https://sprintiasolutions.com{path}</loc>" in xml


def test_autorespuesta_de_la_web_corporativa():
    for source in ("home", "producto-webs"):
        subject, _, body, _, signoff = _autoreply_parts(
            LeadNotification(lead_id=1, name="Laura", phone="+34600000000", source=source))
        assert "voz" not in body and "automatizar" in body and "Mario" in signoff


def test_home_y_productos_en_ingles():
    r = client.get("/en")
    assert r.status_code == 200 and '<html lang="en">' in r.text
    assert "Automation for businesses" in r.text and "Productos" not in r.text
    for p in sitio.PRODUCTOS_EN:
        assert f'href="{p["url"]}"' in r.text and p["nombre"] in r.text
    assert "€/month" not in r.text
    assert '<link rel="canonical" href="https://sprintiasolutions.com/en">' in r.text
    assert 'hreflang="es" href="https://sprintiasolutions.com/"' in r.text
    assert "fd.set('source', 'home-en')" in r.text and "fd.set('landing_language', 'en')" in r.text
    home_es = client.get("/").text
    assert 'hreflang="en" href="https://sprintiasolutions.com/en"' in home_es and 'class="lang" href="/en"' in home_es

    for p in sitio.PRODUCTOS_EN[1:]:
        r = client.get(p["url"])
        assert r.status_code == 200, p["url"]
        assert '<html lang="en">' in r.text and "Pide presupuesto" not in r.text
        assert f'class="lang" href="{p["alt"]}"' in r.text
        assert f"fd.set('source', 'producto-{p['slug']}-en')" in r.text
    webs = client.get("/en/products/websites").text
    assert "€/month" not in webs and "no lock-in" in webs
    assert client.get("/en/products/nope").status_code == 404
    r = client.get("/en/products/voice-assistant", follow_redirects=False)
    assert r.status_code == 301 and r.headers["location"] == "/en/voice-assistant"


def test_autorespuesta_en_ingles():
    subject, _, body, _, signoff = _autoreply_parts(LeadNotification(
        lead_id=1, name="Laura", phone="+34600000000", source="home-en", landing_language="en"))
    assert subject.startswith("We've received") and "automate" in body and "voice" not in body


def test_privacidad_y_aviso_legal():
    for path, alt, txt in (("/privacidad", "/en/privacy", "Quién es el responsable"),
                           ("/en/privacy", "/privacidad", "Who we are")):
        r = client.get(path)
        assert r.status_code == 200, path
        assert txt in r.text and sitio.OWNER["nif"] in r.text and "aepd.es" in r.text
        assert f'href="https://sprintiasolutions.com{alt}"' in r.text   # hreflang
        assert 'id="contactForm"' not in r.text
    home = client.get("/").text
    assert 'href="/privacidad" target="_blank">política de privacidad</a>' in home
    assert '<a href="/privacidad">Privacidad y aviso legal</a>' in home
    assert 'href="/en/privacy"' in client.get("/en").text
    assert 'href="/privacidad"' in client.get("/diagnostico").text
    assert 'href="/en/privacy"' in client.get("/en/diagnosis").text
    assert 'href="/en/privacy"' in client.get("/en/voice-assistant").text
    sm = client.get("/sitemap.xml").text
    assert "/privacidad</loc>" in sm and "/en/privacy</loc>" in sm
