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


def test_aviso_de_cookies():
    for path, txt in (("/", "Rechazar"), ("/en", "Reject"), ("/diagnostico", "Rechazar"),
                      ("/en/diagnosis", "Reject"), ("/productos/webs", "Rechazar"), ("/privacidad", "Rechazar")):
        html = client.get(path).text
        assert 'id="ck"' in html and txt in html, path
        assert "data-cookies" in html, path                       # se puede volver a abrir
        # PostHog arranca sin guardar nada hasta que se acepta
        assert "persistence:window.phPersistence?phPersistence():'memory'" in html, path
        assert html.index("window.phPersistence = function") < html.index("posthog.init("), path


def test_la_landing_de_voz_no_muestra_precios():
    import json, re
    for path in ("/asistente-de-voz", "/en/voice-assistant"):
        html = client.get(path).text
        for s in ("79€", "149€", "299€", "59€", "€59", "€149", "0,25€", "/mes</span>", '"price"', "planes-web"):
            assert s not in html, (path, s)
        assert 'id="precios"' in html and 'data-lead="Precio"' in html
        for block in re.findall(r'<script type="application/ld\+json">(.*?)</script>', html, re.S):
            json.loads(block)                                   # JSON-LD sigue siendo válido
    en = client.get("/en/voice-assistant").text
    assert "A fixed price, <em>for your business</em>." in en and 'href="/en/products/websites"' in en


# ---------- Herramientas gratis ----------
from types import SimpleNamespace
from app import herramientas


def test_paginas_de_herramientas_en_dos_idiomas():
    for key, (es, en) in herramientas.PATHS.items():
        for path, alt, lang in ((es, en, "es"), (en, es, "en")):
            r = client.get(path)
            assert r.status_code == 200, path
            assert f'<html lang="{lang}">' in r.text, path
            assert f'<link rel="canonical" href="https://sprintiasolutions.com{path}">' in r.text, path
            assert f'href="https://sprintiasolutions.com{alt}"' in r.text, path       # hreflang
            assert "€/mes" not in r.text and "€/month" not in r.text, path
    az = client.get("/analizar-web").text
    assert "/api/diagnostico/analizar" in az and "?web=" in az and "analyzer_run" in az
    assert "Analizar web gratis" in az
    rv = client.get("/en/tools/google-review-link").text
    assert "search.google.com/local/writereview?placeid=" in rv and "qrcode.min.js" in rv
    wa = client.get("/herramientas/enlace-whatsapp").text
    assert "https://wa.me/" in wa and 'value="+34"' in wa
    assert 'value="+44"' in client.get("/en/tools/whatsapp-link").text


def test_herramientas_en_menu_pie_y_sitemap():
    home = client.get("/").text
    assert 'href="/herramientas"' in home and 'href="/herramientas/enlace-whatsapp"' in home
    assert 'href="/en/tools/google-review-link"' in client.get("/en").text
    sm = client.get("/sitemap.xml").text
    for p in herramientas.tool_paths():
        assert f"https://sprintiasolutions.com{p}</loc>" in sm, p


def test_busqueda_de_negocios(monkeypatch):
    herramientas._HITS.clear(); herramientas._CACHE.clear()
    url = "/api/herramientas/negocios"
    assert client.post(url, json={"q": "ab"}).status_code == 400
    # sin clave: aviso claro, en el idioma pedido
    monkeypatch.setattr(herramientas, "settings", SimpleNamespace(diag_google_api_key=""))
    r = client.post(url, json={"q": "Panadería Lola", "lang": "en"})
    assert r.status_code == 503 and "Google Business Profile" in r.json()["error"]

    monkeypatch.setattr(herramientas, "settings", SimpleNamespace(diag_google_api_key="test-key"))
    calls = []

    class FakeResp:
        status_code = 200
        text = ""
        def json(self):
            return {"places": [{"id": "ChIJ123", "displayName": {"text": "Panadería Lola"},
                                "formattedAddress": "Calle Mayor 1, Córdoba", "rating": 4.7, "userRatingCount": 88},
                               {"displayName": {"text": "sin id"}}]}

    class FakeClient:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def post(self, u, json=None, headers=None):
            calls.append((u, json, headers))
            return FakeResp()

    monkeypatch.setattr(herramientas.httpx, "AsyncClient", FakeClient)
    r = client.post(url, json={"q": "Panadería Lola Córdoba"})
    assert r.status_code == 200
    assert r.json()["results"] == [{"place_id": "ChIJ123", "name": "Panadería Lola",
                                    "address": "Calle Mayor 1, Córdoba", "rating": 4.7, "count": 88}]
    assert calls[0][1]["regionCode"] == "ES" and "places.id" in calls[0][2]["X-Goog-FieldMask"]
    client.post(url, json={"q": "Panadería Lola Córdoba"})
    assert len(calls) == 1                                          # segunda vez, de la caché

    herramientas._HITS.clear()
    for _ in range(herramientas._MAX_SEARCHES):
        client.post(url, json={"q": "Panadería Lola Córdoba"})
    r = client.post(url, json={"q": "Panadería Lola Córdoba"})
    assert r.status_code == 429
    herramientas._HITS.clear()


def test_herramientas_sin_textos_rotos():
    for p in herramientas.tool_paths():
        html = client.get(p).text
        assert "built-in method" not in html and "{{" not in html, p
