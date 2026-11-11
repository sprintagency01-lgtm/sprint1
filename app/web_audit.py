"""Análisis automático de una web para el diagnóstico (/diagnostico).

El visitante pone la dirección de su web y en unos segundos ve una nota de
0 a 100 con hallazgos concretos ("tarda 5,8 s en cargar en el móvil", "no hay
forma de contactar sin llamar"). Lo que se ve desde fuera ya no se pregunta:
el análisis rellena solo las preguntas de web, SEO, reseñas, captación,
emails y herramientas del test.

Fuentes:
- El HTML de la portada, robots.txt y sitemap (siempre, sin claves).
- PageSpeed Insights (velocidad en móvil y SEO renderizado) y Places API
  (nota y número de reseñas de Google). Las dos con `DIAG_GOOGLE_API_KEY`;
  sin la clave se omiten y la pregunta de reseñas se sigue haciendo.

Seguridad: la URL la escribe un desconocido y la pide nuestro servidor, así
que solo se aceptan http/https a los puertos 80/443 de hosts que resuelven a
IPs públicas, se comprueba otra vez en cada redirección y se corta la
descarga a 2,5 MB.
"""
from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
import re
import socket
import time
import unicodedata
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

import httpx

from .config import settings

log = logging.getLogger(__name__)

# Muchos cortafuegos bloquean agentes que no parecen un navegador.
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/130.0 Safari/537.36 SprintiaDiagnostico/1.0")
MAX_BYTES = 2_500_000
_PSI_URL = "https://www.googleapis.com/pagespeedonline/v5/runPagespeed"
_PLACES_URL = "https://places.googleapis.com/v1/places:searchText"


class AuditError(ValueError):
    """Error con mensaje apto para enseñárselo al visitante."""


# ---------------------------------------------------------------------------
# URL y descarga segura
# ---------------------------------------------------------------------------

def normalize_url(raw) -> str:
    s = str(raw or "").strip()
    if not s:
        raise AuditError("Escribe la dirección de tu web.")
    if len(s) > 300:
        raise AuditError("Esa dirección es demasiado larga.")
    if not re.match(r"^https?://", s, re.I):
        s = "https://" + s.lstrip("/")
    try:
        parts = urlsplit(s)
        port = parts.port
    except ValueError as e:
        raise AuditError("Esa dirección no parece una web.") from e
    host = (parts.hostname or "").strip().rstrip(".").lower()
    try:
        host = host.encode("idna").decode("ascii")
    except UnicodeError as e:
        raise AuditError("Esa dirección no parece una web.") from e
    if not host or "." not in host or not re.fullmatch(r"[a-z0-9.-]+", host):
        raise AuditError("Esa dirección no parece una web.")
    if parts.username or parts.password or port not in (None, 80, 443):
        raise AuditError("Esa dirección no parece una web.")
    path = parts.path or "/"
    return f"{parts.scheme.lower()}://{host}{path}" + (f"?{parts.query}" if parts.query else "")


def _ip_is_public(raw: str) -> bool:
    try:
        ip = ipaddress.ip_address(raw.split("%")[0])
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return ip.is_global and not ip.is_multicast


async def _check_host(host: str) -> None:
    if not host:
        raise AuditError("Esa dirección no parece una web.")
    loop = asyncio.get_running_loop()
    try:
        infos = await asyncio.wait_for(loop.getaddrinfo(host, None, type=socket.SOCK_STREAM), 6)
    except Exception as e:
        raise AuditError("No encontramos esa web. Revisa que la dirección esté bien escrita.") from e
    if not infos or not all(_ip_is_public(info[4][0]) for info in infos):
        raise AuditError("Esa dirección no es una web pública.")


async def safe_get(client: httpx.AsyncClient, url: str, max_bytes: int = MAX_BYTES, hops: int = 5):
    """GET siguiendo redirecciones a mano y validando cada salto."""
    for _ in range(hops + 1):
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https") or parts.port not in (None, 80, 443):
            raise AuditError("La web redirige a una dirección que no podemos analizar.")
        await _check_host((parts.hostname or "").lower())
        async with client.stream("GET", url) as r:
            loc = r.headers.get("location")
            if r.status_code in (301, 302, 303, 307, 308) and loc:
                url = urljoin(url, loc)
                continue
            buf = bytearray()
            async for chunk in r.aiter_bytes():
                buf += chunk
                if len(buf) >= max_bytes:
                    break
            return str(r.url), r.status_code, r.headers, bytes(buf)
    raise AuditError("La web redirige demasiadas veces.")


def _decode(body: bytes, headers) -> str:
    m = re.search(r"charset=([\w-]+)", headers.get("content-type", ""), re.I)
    if not m:
        m = re.search(rb"<meta[^>]+charset=[\"']?([\w-]+)", body[:4000], re.I)
    enc = (m.group(1).decode() if m and isinstance(m.group(1), bytes) else m.group(1) if m else "utf-8")
    try:
        return body.decode(enc, errors="replace")
    except LookupError:
        return body.decode("utf-8", errors="replace")


# ---------------------------------------------------------------------------
# Lectura del HTML
# ---------------------------------------------------------------------------

class _Page(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title = ""
        self.metas: dict[str, str] = {}
        self.lang = ""
        self.canonical = False
        self.h1 = 0
        self.imgs = 0
        self.imgs_no_alt = 0
        self.hrefs: list[str] = []
        self.forms = 0
        self.email_inputs = 0
        self.ld: list = []
        self.text_len = 0
        self.code: list[str] = []   # src/href/scripts: donde se detectan herramientas
        self._in_title = False
        self._in_ld = False
        self._ld_buf: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        a = {k.lower(): (v or "") for k, v in attrs}
        for k in ("src", "href", "action", "data-src", "class", "id"):
            if a.get(k) and (k not in ("class", "id") or tag in ("div", "form", "iframe", "section")):
                self.code.append(a[k])
        if tag == "html":
            self.lang = a.get("lang", "")
        elif tag == "title":
            self._in_title = True
        elif tag == "meta":
            key = (a.get("name") or a.get("property") or "").lower()
            if key and key not in self.metas:
                self.metas[key] = a.get("content", "")
        elif tag == "link" and "canonical" in a.get("rel", "").lower():
            self.canonical = True
        elif tag == "h1":
            self.h1 += 1
        elif tag == "img":
            self.imgs += 1
            if "alt" not in a:
                self.imgs_no_alt += 1
        elif tag == "a":
            self.hrefs.append(a.get("href", ""))
        elif tag == "form":
            self.forms += 1
        elif tag == "input":
            t = a.get("type", "").lower()
            n = (a.get("name", "") + a.get("id", "")).lower()
            if t in ("email", "tel") or "mail" in n or "phone" in n or "telefono" in n:
                self.email_inputs += 1
        elif tag == "script":
            self._skip += 1
            if "ld+json" in a.get("type", "").lower():
                self._in_ld = True
                self._ld_buf = []
        elif tag in ("style", "noscript", "svg"):
            self._skip += 1

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False
        elif tag == "script":
            self._skip = max(0, self._skip - 1)
            if self._in_ld:
                self._in_ld = False
                try:
                    self.ld.append(json.loads("".join(self._ld_buf)))
                except ValueError:
                    pass
        elif tag in ("style", "noscript", "svg"):
            self._skip = max(0, self._skip - 1)

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        elif self._in_ld:
            self._ld_buf.append(data)
        elif self._skip:
            self.code.append(data[:20000])
        else:
            self.text_len += len(data.strip())


def _ld_types(items) -> set[str]:
    out: set[str] = set()

    def walk(x):
        if isinstance(x, dict):
            t = x.get("@type")
            for v in (t if isinstance(t, list) else [t]):
                if isinstance(v, str):
                    out.add(v)
            for v in x.values():
                if isinstance(v, (dict, list)):
                    walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)

    walk(items)
    return out


# Firmas para detectar herramientas en el HTML (en minúsculas).
_SIG: dict[str, dict[str, tuple[str, ...]]] = {
    "analitica": {
        "Google Analytics": ("googletagmanager.com/gtag/js", "google-analytics.com", "gtag('config'", 'gtag("config"'),
        "Google Tag Manager": ("googletagmanager.com/gtm.js", "googletagmanager.com/ns.html"),
        "Píxel de Meta": ("connect.facebook.net", "fbq('init'", 'fbq("init"'),
        "Microsoft Clarity": ("clarity.ms",),
        "Hotjar": ("static.hotjar.com",),
        "PostHog": ("posthog",),
        "Matomo": ("matomo", "piwik"),
        "Plausible": ("plausible.io",),
    },
    "marketing": {
        "HubSpot": ("js.hs-scripts.com", "js.hsforms.net", "hs-analytics", "hubspot"),
        "Mailchimp": ("list-manage.com", "chimpstatic.com", "mailchimp"),
        "Brevo": ("sendinblue", "brevo.com", "sibforms"),
        "Klaviyo": ("klaviyo",),
        "ActiveCampaign": ("activecampaign", "activehosted.com"),
        "MailerLite": ("mailerlite",),
        "Acumbamail": ("acumbamail",),
        "Pipedrive": ("pipedrive", "leadbooster"),
        "Salesforce": ("salesforce", "pardot"),
        "Zoho": ("zoho.com", "zohopublic", "salesiq"),
        "Odoo": ("odoo",),
    },
    "chat": {
        "Intercom": ("intercom",),
        "Tidio": ("tidio",),
        "Crisp": ("crisp.chat",),
        "Tawk.to": ("tawk.to",),
        "Zendesk": ("zdassets.com", "zopim"),
        "Drift": ("js.driftt.com", "drift.com"),
        "LiveChat": ("livechatinc.com",),
        "HubSpot Chat": ("js.usemessages.com",),
        "Landbot": ("landbot",),
        "ManyChat": ("manychat",),
        "Chatbot": ("chatbase", "botpress", "voiceflow"),
    },
    "reservas": {
        "Calendly": ("calendly.com",),
        "Cal.com": ("cal.com/",),
        "Booksy": ("booksy",),
        "Treatwell": ("treatwell",),
        "TheFork": ("thefork", "eltenedor"),
        "CoverManager": ("covermanager",),
        "SimplyBook": ("simplybook",),
        "OpenTable": ("opentable",),
        "Doctoralia": ("doctoralia",),
        "Acuity": ("acuityscheduling",),
        "Google Calendar": ("calendar.google.com/calendar/appointments",),
    },
    "plataforma": {
        "Shopify": ("cdn.shopify.com", "myshopify.com"),
        "WooCommerce": ("woocommerce",),
        "WordPress": ("wp-content", "wp-includes"),
        "Wix": ("wixstatic.com", "wix.com"),
        "Squarespace": ("squarespace",),
        "Webflow": ("webflow",),
        "PrestaShop": ("prestashop",),
        "Framer": ("framerusercontent.com", "framer.com"),
        "Jimdo": ("jimdo",),
    },
}
_FORM_EMBEDS = ("js.hsforms.net", "typeform.com", "jotform", "docs.google.com/forms", "wpcf7", "wpforms",
                "gform_wrapper", "elementor-form", "tally.so", "formspree", "ninja-forms", "sibforms")


def _detect(low: str) -> dict[str, list[str]]:
    found = {k: [name for name, pats in group.items() if any(p in low for p in pats)] for k, group in _SIG.items()}
    if "WooCommerce" in found["plataforma"]:
        found["plataforma"] = [p for p in found["plataforma"] if p != "WordPress"] or ["WooCommerce"]
    return found


def extract_facts(html_text: str, final_url: str) -> dict:
    """Todo lo que se puede saber del HTML de la portada."""
    p = _Page()
    try:
        p.feed(html_text)
        p.close()
    except Exception:  # HTML roto: nos quedamos con lo leído
        log.debug("HTML mal formado en %s", final_url)
    low = html_text.lower()
    code = "\n".join(p.code).lower()   # sin el texto visible: nombrar HubSpot no es usarlo
    hrefs = [h.strip().lower() for h in p.hrefs]
    det = _detect(code)
    # wa.me/?text=… es un botón de compartir, no un canal de contacto: hace falta un número.
    whatsapp = bool(re.search(r"wa\.me/\+?\d{6,}|whatsapp\.com/send/?\?phone=\+?\d{6,}", code)) or any(
        x in code for x in ("joinchat", "whatsapp-button", "wa-chat", "click-to-chat", "wa-widget"))
    years = [int(y) for y in re.findall(
        r"(?:©|&copy;|&#169;|copyright)\s*(?:[^<\d]{0,40})?(?:(?:19|20)\d{2}\s*[-–—/]\s*)?((?:19|20)\d{2})", low)]
    robots_meta = (p.metas.get("robots", "") + " " + p.metas.get("googlebot", "")).lower()
    types = _ld_types(p.ld)
    return {
        "final_url": final_url,
        "https": final_url.startswith("https://"),
        "title": re.sub(r"\s+", " ", p.title).strip(),
        "description": p.metas.get("description", "").strip(),
        "viewport": "width=device-width" in p.metas.get("viewport", "").replace(" ", "").lower(),
        "noindex": "noindex" in robots_meta,
        "h1": p.h1,
        "imgs": p.imgs,
        "imgs_no_alt": p.imgs_no_alt,
        "lang": p.lang,
        "canonical": p.canonical,
        "schema": sorted(types),
        "form": p.forms > 0 and (p.email_inputs > 0 or "<textarea" in low) or any(e in code for e in _FORM_EMBEDS),
        "tel": any(h.startswith("tel:") for h in hrefs),
        "mailto": any(h.startswith("mailto:") for h in hrefs),
        "whatsapp": whatsapp,
        "chat": det["chat"],
        "reservas": det["reservas"],
        "analitica": det["analitica"],
        "marketing": det["marketing"],
        "plataforma": det["plataforma"][:1],
        "copyright": max(years) if years else None,
        "text_len": p.text_len,
    }


def _robots_blocks_all(txt: str) -> bool:
    agent_all, blocks = False, False
    for line in txt.splitlines():
        line = line.split("#")[0].strip().lower()
        if line.startswith("user-agent:"):
            agent_all = line.split(":", 1)[1].strip() == "*"
        elif agent_all and line.startswith("disallow:") and line.split(":", 1)[1].strip() == "/":
            blocks = True
    return blocks


# ---------------------------------------------------------------------------
# Google: PageSpeed y reseñas
# ---------------------------------------------------------------------------

async def pagespeed(url: str) -> dict | None:
    key = settings.diag_google_api_key.strip()
    if not key:
        return None
    params = [("url", url), ("strategy", "mobile"), ("category", "performance"), ("category", "seo"),
              ("locale", "es"), ("key", key)]
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(50, connect=6)) as c:
            r = await c.get(_PSI_URL, params=params)
        if r.status_code != 200:
            log.warning("PageSpeed status=%s body=%s", r.status_code, r.text[:200])
            return None
        lh = r.json().get("lighthouseResult") or {}
    except Exception:
        log.warning("PageSpeed no respondió para %s", url, exc_info=True)
        return None
    cats, audits = lh.get("categories") or {}, lh.get("audits") or {}

    def score(cat):
        s = (cats.get(cat) or {}).get("score")
        return round(s * 100) if isinstance(s, (int, float)) else None

    lcp = (audits.get("largest-contentful-paint") or {}).get("numericValue")
    return {"perf": score("performance"), "seo": score("seo"),
            "lcp_s": round(lcp / 1000, 1) if isinstance(lcp, (int, float)) else None}


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9 ]+", " ", s).strip()


def _site(host_or_url: str) -> str:
    h = urlsplit(host_or_url if "//" in host_or_url else "//" + host_or_url).hostname or ""
    return h.lower().removeprefix("www.")


def _query_from_title(title: str, host: str) -> str:
    first = re.split(r"\s[|\-–—·:]\s|\s\|\s|\|", title or "")[0].strip()
    if len(first) >= 3 and _norm(first) not in {"inicio", "home", "bienvenido", "bienvenidos", "pagina de inicio"}:
        return first[:80]
    return host.split(".")[0].replace("-", " ")


async def google_reviews(query: str, host: str, negocio: str = "") -> dict | None:
    """None si no se pudo consultar; {"found": False} si no hay ficha clara."""
    key = settings.diag_google_api_key.strip()
    if not key or not query:
        return None
    headers = {"X-Goog-Api-Key": key, "Content-Type": "application/json",
               "X-Goog-FieldMask": "places.displayName,places.rating,places.userRatingCount,"
                                   "places.websiteUri,places.googleMapsUri"}
    body = {"textQuery": query, "languageCode": "es", "regionCode": "ES", "pageSize": 5}
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(10, connect=5)) as c:
            r = await c.post(_PLACES_URL, json=body, headers=headers)
        if r.status_code != 200:
            log.warning("Places status=%s body=%s", r.status_code, r.text[:200])
            return None
        places = r.json().get("places") or []
    except Exception:
        log.warning("Places no respondió para %s", query, exc_info=True)
        return None
    site = _site(host)
    match = next((pl for pl in places if pl.get("websiteUri") and _site(pl["websiteUri"]) == site), None)
    if match is None and negocio and places:
        name = _norm((places[0].get("displayName") or {}).get("text", ""))
        want = _norm(negocio)
        if want and (want in name or name in want):
            match = places[0]
    if match is None:
        return {"found": False}
    return {"found": True, "name": (match.get("displayName") or {}).get("text", ""),
            "rating": match.get("rating"), "count": int(match.get("userRatingCount") or 0),
            "maps_url": match.get("googleMapsUri", "")}


# ---------------------------------------------------------------------------
# Nota y hallazgos
# ---------------------------------------------------------------------------

def _f(nivel: str, area: str, titulo: str, detalle: str = "") -> dict:
    return {"nivel": nivel, "area": area, "titulo": titulo, "detalle": detalle}


def _num(x: float) -> str:
    return f"{x:.1f}".replace(".", ",")


def _join(names: list[str]) -> str:
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " y " + names[-1]


def evaluate(facts: dict, psi: dict | None = None, reviews: dict | None = None, year: int | None = None) -> dict:
    """Pasa los datos a nota 0..100, hallazgos y respuestas automáticas del test."""
    year = year or int(time.strftime("%Y"))
    points: dict[str, tuple[float, float]] = {}  # bloque -> (conseguido, máximo)
    out: list[dict] = []

    # Velocidad (solo con PageSpeed)
    perf = (psi or {}).get("perf")
    if perf is not None:
        lcp = psi.get("lcp_s")
        points["velocidad"] = (25 * perf / 100, 25)
        cuanto = f" ({_num(lcp)} s)" if lcp else ""
        if (lcp and lcp > 4) or perf < 50:
            out.append(_f("mal", "velocidad",
                          f"Tarda {_num(lcp)} s en cargar en el móvil" if lcp else f"Va lenta en el móvil: {perf}/100 en Google",
                          "Google recomienda menos de 2,5 s. Con cada segundo de más se va parte de las visitas antes de ver nada."))
        elif (lcp and lcp > 2.5) or perf < 80:
            out.append(_f("aviso", "velocidad", f"Carga algo lenta en el móvil{cuanto}",
                          "Google recomienda menos de 2,5 s y nota de 90 o más."))
        else:
            out.append(_f("ok", "velocidad", f"Carga rápido en el móvil{cuanto}"))

    # SEO básico
    t, d = facts.get("title", ""), facts.get("description", "")
    seo_checks = []
    if not t:
        seo_checks.append((False, 4, _f("mal", "seo", "La página no tiene título para Google",
                                        "Es lo primero que lee Google y lo que sale en azul en los resultados.")))
    elif not 10 <= len(t) <= 70:
        seo_checks.append((False, 4, _f("aviso", "seo", "El título que ve Google es demasiado " + ("corto" if len(t) < 10 else "largo"),
                                        "Lo ideal son 30-60 caracteres con lo que hacéis y dónde.")))
    else:
        seo_checks.append((True, 4, None))
    if not d:
        seo_checks.append((False, 4, _f("mal", "seo", "No tiene descripción para Google",
                                        "Google se inventa el texto que sale bajo tu enlace, y suele ser peor que uno escrito a mano.")))
    else:
        seo_checks.append((True, 4, None))
    seo_checks.append((facts.get("h1", 0) > 0, 2, _f("aviso", "seo", "No tiene un titular principal (H1)",
                                                      "Le dice a Google de qué va la página.")))
    indexable = not facts.get("noindex") and not facts.get("robots_block")
    seo_checks.append((indexable, 6, _f("mal", "seo", "La web le pide a Google que no la muestre",
                                        "Tiene una etiqueta o regla que impide aparecer en los resultados. Suele quedarse puesta por error.")))
    if facts.get("sitemap") is not None:
        seo_checks.append((bool(facts["sitemap"]), 3, _f("aviso", "seo", "No tiene mapa del sitio para Google",
                                                         "Sin sitemap, Google tarda más en encontrar las páginas nuevas.")))
    seo_checks.append((bool(facts.get("schema")), 3, _f("aviso", "seo", "Google no sabe qué tipo de negocio sois",
                                                        "Faltan los datos estructurados (tipo de negocio, dirección, horario) que usa para los resultados enriquecidos.")))
    imgs, no_alt = facts.get("imgs", 0), facts.get("imgs_no_alt", 0)
    if imgs >= 3:
        seo_checks.append((no_alt / imgs <= 0.3, 2, _f("aviso", "seo", f"{no_alt} de {imgs} imágenes no tienen texto alternativo",
                                                       "Google no puede leer las imágenes sin él, y tampoco quien usa lector de pantalla.")))
    got = sum(w for ok, w, _ in seo_checks if ok)
    top = sum(w for _, w, _ in seo_checks)
    seo_ratio = got / top if top else 0
    if (psi or {}).get("seo") is not None:
        seo_ratio = 0.5 * seo_ratio + 0.5 * psi["seo"] / 100
    points["seo"] = (25 * seo_ratio, 25)
    out += [f for ok, _, f in seo_checks if not ok and f]
    if all(ok for ok, _, _ in seo_checks):
        out.append(_f("ok", "seo", "SEO básico en orden: título, descripción y Google puede indexarla"))

    # Captación
    form, wa, chat, tel = facts.get("form"), facts.get("whatsapp"), facts.get("chat") or [], facts.get("tel")
    instant = wa or bool(chat)
    cap = (10 if form else 0) + (8 if instant else 0) + (4 if tel else 0) + (3 if facts.get("reservas") else 0)
    points["captacion"] = (min(cap, 22), 22)
    if not form and not instant:
        out.append(_f("mal", "captacion",
                      "No hay forma de contactar sin llamar" if tel else "No hay ninguna forma clara de contactar",
                      "Ni formulario, ni WhatsApp, ni chat: quien no quiere llamar se va sin dejar sus datos."))
    elif not form:
        out.append(_f("aviso", "captacion", "No tiene formulario de contacto",
                      "Quien prefiere escribir y esperar respuesta no tiene dónde dejar sus datos."))
    if not instant:
        out.append(_f("aviso", "captacion", "Fuera de horario no responde nadie: no hay chat ni WhatsApp",
                      "Cada vez más clientes escriben en vez de llamar, y muchos lo hacen por la noche."))
    if not tel:
        out.append(_f("aviso", "captacion", "El teléfono no se puede pulsar para llamar desde el móvil"))
    if form and instant:
        canales = ["formulario"] + (["WhatsApp"] if wa else []) + chat[:1]
        out.append(_f("ok", "captacion", "Se puede contactar por " + _join(canales)))
    if facts.get("reservas"):
        out.append(_f("ok", "captacion", f"Tiene reservas o citas online ({facts['reservas'][0]})"))

    # Medición y herramientas
    an, mk = facts.get("analitica") or [], facts.get("marketing") or []
    points["medicion"] = ((9 if an else 0) + (6 if mk else 0), 15)
    if not an:
        out.append(_f("mal", "medicion", "No mide sus visitas",
                      "No hay Google Analytics ni nada parecido: no se sabe cuánta gente entra ni de dónde viene."))
    else:
        out.append(_f("ok", "medicion", "Mide sus visitas con " + _join(an[:2])))
    if not mk:
        out.append(_f("aviso", "medicion", "No vemos dónde acaban los contactos",
                      "Ni CRM ni herramienta de email conectada a la web: los contactos se quedan en un buzón."))
    else:
        out.append(_f("ok", "medicion", "Los contactos van a " + _join(mk[:2])))

    # Confianza y mantenimiento
    old = facts.get("copyright") is not None and facts["copyright"] <= year - 2
    points["confianza"] = ((6 if facts.get("https") else 0) + (5 if facts.get("viewport") else 0) + (0 if old else 4), 15)
    if not facts.get("https"):
        out.append(_f("mal", "confianza", "La web no es segura (sin HTTPS)",
                      "Chrome la marca como «No segura» delante de cada visita y Google la penaliza."))
    if not facts.get("viewport"):
        out.append(_f("mal", "confianza", "No está adaptada al móvil",
                      "En el móvil se ve en miniatura, y desde ahí llegan la mayoría de las visitas."))
    if old:
        out.append(_f("aviso", "confianza", f"El pie de la web dice © {facts['copyright']}",
                      "Da sensación de web abandonada aunque el negocio esté a tope."))

    # Reseñas en Google
    if reviews is not None:
        if not reviews.get("found"):
            points["resenas"] = (0, 15)
            out.append(_f("mal", "resenas", "No encontramos vuestra ficha de Google",
                          "Sin ficha (o sin la web enlazada en ella) no salís en Google Maps ni en las búsquedas de la zona."))
        else:
            n, r = reviews.get("count") or 0, reviews.get("rating") or 0
            points["resenas"] = (7.5 * min(1, n / 50) + 7.5 * max(0, min(1, (r - 3.5) / 1.3)), 15)
            if n < 10:
                out.append(_f("aviso", "resenas", f"Solo {n} reseña{'s' if n != 1 else ''} en Google",
                              "Con menos de 10, quien te compara elige al de al lado. Pedirlas después de cada venta se puede automatizar."))
            elif r < 4.2:
                out.append(_f("aviso", "resenas", f"{_num(r)} ★ de media en Google ({n} reseñas)",
                              "Por debajo de 4,2 muchos clientes ni hacen clic. Responder y pedir reseñas a los contentos lo sube."))
            else:
                out.append(_f("ok", "resenas", f"{_num(r)} ★ con {n} reseñas en Google"))

    got = sum(p[0] for p in points.values())
    top = sum(p[1] for p in points.values())
    nota = round(100 * got / top) if top else 0
    nivel = ("Bien resuelta" if nota >= 80 else "Mejorable" if nota >= 60 else "Floja" if nota >= 40
             else "Necesita trabajo")

    orden = {"mal": 0, "aviso": 1, "ok": 2}
    out.sort(key=lambda f: orden[f["nivel"]])

    return {
        "nota": nota,
        "nivel": nivel,
        "hallazgos": [f for f in out if f["nivel"] != "ok"],
        "bien": [f["titulo"] for f in out if f["nivel"] == "ok"],
        "detectado": {k: facts.get(k) or [] for k in ("plataforma", "analitica", "marketing", "chat", "reservas")}
                     | {"whatsapp": bool(wa)},
        "auto": auto_answers(facts, psi, reviews, seo_ratio, year),
        "parcial": facts.get("text_len", 0) < 300 and perf is None,
        "velocidad": psi,
        "resenas": reviews,
    }


def auto_answers(facts: dict, psi: dict | None, reviews: dict | None, seo_ratio: float, year: int) -> dict[str, int]:
    """Respuestas del test que se deducen de la web (índice de opción = nivel de fuga)."""
    perf = (psi or {}).get("perf")
    old = facts.get("copyright") is not None and facts["copyright"] <= year - 3
    moderna = facts.get("https") and facts.get("viewport") and (perf is None or perf >= 40) and not old
    instant = facts.get("whatsapp") or bool(facts.get("chat"))
    capta = facts.get("form") or instant
    mk = facts.get("marketing") or []
    a = {
        "web": 2 if not moderna else 0 if capta and mk else 1,
        "seo": 0 if seo_ratio >= 0.85 else 1 if seo_ratio >= 0.6 else 2 if seo_ratio >= 0.35 else 3,
        "captacion": 1 if instant else 2 if facts.get("form") else 3,
        "emails": 1 if mk else 2,
    }
    # Herramientas (multi): Shopify/WooCommerce=bit 3, CRM=bit 2, WhatsApp=bit 4.
    bits = 0
    if set(facts.get("plataforma") or []) & {"Shopify", "WooCommerce"}:
        bits |= 1 << 3
    if set(mk) & {"HubSpot", "Pipedrive", "Salesforce", "Zoho"}:
        bits |= 1 << 2
    if facts.get("whatsapp"):
        bits |= 1 << 4
    a["herramientas"] = bits
    if reviews is not None:
        if not reviews.get("found"):
            a["resenas"] = 3
        else:
            n, r = reviews.get("count") or 0, reviews.get("rating") or 0
            a["resenas"] = 0 if n >= 50 and r >= 4.5 else 1 if n >= 10 and r >= 4.0 else 2
    return a


def summary_lines(audit: dict) -> list[str]:
    """Texto plano para el aviso interno y el mensaje del presupuesto."""
    lines = [f"Web: {audit['url']} · nota {audit['nota']}/100 ({audit['nivel']})"]
    lines += [f"  ✗ {f['titulo']}" for f in audit["hallazgos"][:6]]
    det = audit["detectado"]
    tools = det["plataforma"] + det["analitica"] + det["marketing"] + det["chat"] + det["reservas"]
    if tools:
        lines.append("  Detectado: " + ", ".join(tools))
    return lines


# ---------------------------------------------------------------------------
# Orquestación con caché
# ---------------------------------------------------------------------------

_CACHE: dict[str, tuple[float, dict]] = {}   # host -> (ts, auditoría)
_CACHE_TTL = 6 * 3600


async def analyze(raw_url: str, negocio: str = "") -> dict:
    url = normalize_url(raw_url)
    host = urlsplit(url).hostname or ""
    key = f"{host}|{_norm(negocio)}"
    hit = _CACHE.get(key)
    if hit and time.time() - hit[0] < _CACHE_TTL:
        return hit[1]

    timeout = httpx.Timeout(12, connect=6)
    headers = {"User-Agent": UA, "Accept": "text/html,application/xhtml+xml",
               "Accept-Language": "es-ES,es;q=0.9"}
    psi_task = asyncio.create_task(pagespeed(url))
    async with httpx.AsyncClient(timeout=timeout, headers=headers, follow_redirects=False) as client:
        try:
            final_url, status, resp_headers, body = await safe_get(client, url)
        except AuditError:
            psi_task.cancel()
            raise
        except httpx.HTTPError:
            if url.startswith("https://") and not str(raw_url).strip().lower().startswith("https://"):
                try:
                    final_url, status, resp_headers, body = await safe_get(client, "http://" + url[8:])
                except (httpx.HTTPError, AuditError) as e:
                    psi_task.cancel()
                    raise AuditError("No hemos podido abrir esa web. Revisa la dirección.") from e
            else:
                psi_task.cancel()
                raise AuditError("No hemos podido abrir esa web. Revisa la dirección.")
        if status in (401, 403, 429, 503):
            psi_task.cancel()
            raise AuditError("Esa web no deja que la analicemos: bloquea las visitas automáticas.")
        if status >= 400:
            psi_task.cancel()
            raise AuditError(f"La web responde con un error ({status}). Revisa la dirección.")
        facts = extract_facts(_decode(body, resp_headers), final_url)

        origin = "{0.scheme}://{0.netloc}".format(urlsplit(final_url))

        async def small(path: str):
            try:
                u, st, h, b = await safe_get(client, origin + path, max_bytes=200_000, hops=3)
                return (st, b.decode("utf-8", errors="replace")) if st == 200 else (st, "")
            except Exception:
                return None

        robots, sm, sm_idx = await asyncio.gather(small("/robots.txt"), small("/sitemap.xml"),
                                                  small("/sitemap_index.xml"))
    robots_txt = robots[1] if robots else ""
    facts["robots_block"] = _robots_blocks_all(robots_txt)
    if robots is None and sm is None:
        facts["sitemap"] = None  # no pudimos comprobarlo
    else:
        facts["sitemap"] = ("sitemap:" in robots_txt.lower()
                            or any(x and ("<urlset" in x[1] or "<sitemapindex" in x[1]) for x in (sm, sm_idx)))

    query = negocio.strip() or _query_from_title(facts["title"], host)
    reviews_task = asyncio.create_task(google_reviews(query, host, negocio.strip()))
    psi, reviews = await asyncio.gather(psi_task, reviews_task)

    audit = evaluate(facts, psi, reviews)
    audit["url"] = facts["final_url"]
    audit["dominio"] = _site(host)
    _CACHE[key] = (time.time(), audit)
    if len(_CACHE) > 3000:
        _CACHE.clear()
    return audit
