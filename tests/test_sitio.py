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
    assert "99<small> €/mes</small>" in webs           # los planes de web sí van en su página
    assert "€/mes" not in client.get("/productos/crm-automatizado").text
    assert client.get("/productos/no-existe").status_code == 404
    r = client.get("/productos/asistente-de-voz", follow_redirects=False)
    assert r.status_code == 301 and r.headers["location"] == "/asistente-de-voz"


def test_la_landing_de_voz_se_mueve_sin_perder_nada():
    r = client.get("/asistente-de-voz")
    assert r.status_code == 200
    assert "Habla con Ana" in r.text
    assert '<link rel="canonical" href="https://sprintiasolutions.com/asistente-de-voz">' in r.text
    assert '<a class="logo" href="/"' in r.text
    en = client.get("/en").text
    assert 'hreflang="es" href="https://sprintiasolutions.com/asistente-de-voz"' in en


def test_sitemap_incluye_productos():
    xml = client.get("/sitemap.xml").text
    for path in ["/", "/diagnostico", "/asistente-de-voz", "/productos/crm-automatizado", "/productos/webs",
                 "/productos/automatizacion-de-procesos"]:
        assert f"<loc>https://sprintiasolutions.com{path}</loc>" in xml


def test_autorespuesta_de_la_web_corporativa():
    for source in ("home", "producto-webs"):
        subject, _, body, _, signoff = _autoreply_parts(
            LeadNotification(lead_id=1, name="Laura", phone="+34600000000", source=source))
        assert "voz" not in body and "automatizar" in body and "Mario" in signoff
