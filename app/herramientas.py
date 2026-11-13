"""Herramientas gratis de la web corporativa (captación por SEO y prospección).

- `/analizar-web` · `/en/website-analyzer`: el análisis de la web del
  diagnóstico, sin el test. Usa `/api/diagnostico/analizar`.
- `/herramientas/enlace-resenas-google` · `/en/tools/google-review-link`:
  busca el negocio en Google (Places) y da el enlace para dejar reseña + QR.
- `/herramientas/enlace-whatsapp` · `/en/tools/whatsapp-link`: enlace wa.me
  con mensaje + QR, todo en el navegador.
- `/herramientas` · `/en/tools`: índice.

Las plantillas están en `app/templates/sitio/herramienta-*.html` y se pintan
con el mismo entorno Jinja y `base.html` que el resto de la web (`sitio.py`).
"""
from __future__ import annotations

import logging
import time

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse

from . import sitio
from .config import settings
from .diagnostico import _ip

log = logging.getLogger(__name__)
router = APIRouter(tags=["herramientas"])

_PLACES_URL = "https://places.googleapis.com/v1/places:searchText"
_REVIEW_URL = "https://search.google.com/local/writereview?placeid="

# Rutas por idioma: clave → (es, en)
PATHS = {
    "index": ("/herramientas", "/en/tools"),
    "analizar": ("/analizar-web", "/en/website-analyzer"),
    "resenas": ("/herramientas/enlace-resenas-google", "/en/tools/google-review-link"),
    "whatsapp": ("/herramientas/enlace-whatsapp", "/en/tools/whatsapp-link"),
}


def tool_paths() -> list[str]:
    """Rutas indexables (para el sitemap), en los dos idiomas."""
    return [p for pair in PATHS.values() for p in pair]


# ---------------------------------------------------------------------------
# Textos
# ---------------------------------------------------------------------------

AREAS = {
    "es": {"seo": "Google", "velocidad": "Velocidad", "captacion": "Contacto", "confianza": "Confianza",
           "medicion": "Medición", "resenas": "Reseñas"},
    "en": {"seo": "Google", "velocidad": "Speed", "captacion": "Contact", "confianza": "Trust",
           "medicion": "Tracking", "resenas": "Reviews"},
}

TXT = {
    "es": {
        "crumb": "Herramientas",
        "index": {
            "seo_title": "Herramientas gratis para empresas: web, reseñas y WhatsApp · Sprintia",
            "seo_desc": "Analiza tu web, crea el enlace para que te dejen reseñas en Google y tu enlace de WhatsApp con QR. Gratis y sin registrarte.",
            "h1": "Herramientas gratis para que te <em>encuentren</em> y te escriban.",
            "lede": "Sin registro y sin letra pequeña. Las usamos con nuestros clientes; ahora puedes usarlas tú.",
            "items": [
                ("analizar", "Analiza tu web", "Nota de 0 a 100, lo que ve Google, la velocidad en el móvil y lo que te cuesta clientes.", "Test SEO · velocidad · contacto"),
                ("resenas", "Enlace para reseñas de Google", "Busca tu negocio y llévate el enlace directo para que te valoren, con su código QR y una tarjeta para imprimir.", "Enlace · QR · tarjeta"),
                ("whatsapp", "Enlace de WhatsApp", "Que te escriban con un toque, con el mensaje ya escrito. Con QR y botón para tu web.", "Enlace · QR · botón"),
            ],
            "diag_t": "Diagnóstico de automatización",
            "diag_p": "Tu web y 15 preguntas: cuántas horas de trabajo manual podrías recuperar y por dónde empezar.",
            "diag_tag": "5 minutos",
            "open": "Abrir",
        },
        "analizar": {
            "seo_title": "Analizar web gratis: test SEO y velocidad en segundos · Sprintia",
            "seo_desc": "Analiza tu web gratis: nota de 0 a 100, test SEO, velocidad en el móvil, formas de contacto y reseñas de Google. Resultado al momento, sin registrarte.",
            "h1": "Analiza tu web <em>gratis</em> y mira qué le falta.",
            "lede": "Test SEO, velocidad en el móvil, formas de contacto y reseñas de Google en una sola pasada. Resultado al momento, sin registrarte.",
            "ph": "tuempresa.com", "aria": "Dirección de tu web", "btn": "Analizar",
            "note": "Solo miramos lo que es público: la web, lo que ve Google y tu ficha de empresa.",
            "scan": "Analizando",
            "score_of": "de 100",
            "scale": ["Necesita trabajo", "Floja", "Mejorable", "Bien resuelta"],
            "issues_one": "1 cosa que te cuesta clientes", "issues_many": "{n} cosas que te cuestan clientes",
            "issues_none": "No hemos visto nada grave",
            "good": "Lo que ya está bien", "tools": "Herramientas que hemos detectado",
            "partial": "La web tiene muy poco texto visible o carga con JavaScript: puede que algunos datos no sean exactos.",
            "no_google": "Sin datos de velocidad ni de Google esta vez: la nota se calcula con el resto.",
            "mal": "Importante", "aviso": "A mejorar",
            "cta_h": "¿Cuántas horas pierde tu equipo?",
            "cta_p": "La web es solo una parte. Responde 15 preguntas rápidas y te decimos cuánto trabajo manual podrías automatizar y por dónde empezar.",
            "cta_btn": "Haz el diagnóstico completo",
            "again": "Analizar otra web",
            "checks_t": "Qué revisamos",
            "checks": [
                ("Google", "Título, descripción, encabezados, datos estructurados, sitemap y si Google puede leerla."),
                ("Velocidad", "Lo que tarda en cargar en el móvil, con la medición de Google (PageSpeed)."),
                ("Contacto", "Formularios, teléfono, WhatsApp, reservas: si es fácil escribirte."),
                ("Confianza", "Certificado de seguridad, aviso legal, privacidad y si se ve actual."),
                ("Medición", "Si tienes analítica para saber qué funciona y qué no."),
                ("Reseñas", "Tu nota y número de reseñas en Google frente a lo que esperan tus clientes."),
            ],
            "faq": [
                ("¿Es gratis de verdad?", "Sí. No pedimos email ni registro para ver el resultado. Si quieres el diagnóstico completo de automatización, también es gratis."),
                ("¿Qué diferencia hay con PageSpeed o con un test SEO?", "PageSpeed solo mide velocidad y los test SEO solo miran Google. Aquí ves las dos cosas y además si es fácil contactarte, si das confianza y cómo están tus reseñas: lo que de verdad decide si una visita se convierte en cliente."),
                ("¿Cuánto tarda?", "Entre 5 y 20 segundos, según lo rápida que sea tu web."),
                ("¿Por qué no puedo analizar mi web?", "Algunas webs bloquean las visitas automáticas o no responden. Revisa que la dirección sea correcta; si sigue sin funcionar, escríbenos y la miramos a mano."),
            ],
            "bridge_h": "¿Quieres que la arreglemos?",
            "bridge_p": "Hacemos webs rápidas, preparadas para Google y con cada contacto conectado a tu CRM.",
            "bridge_btn": "Ver webs",
            "bridge_url": "/productos/webs",
        },
        "resenas": {
            "seo_title": "Enlace para reseñas de Google y código QR gratis · Sprintia",
            "seo_desc": "Busca tu negocio y consigue gratis el enlace directo para que te dejen reseñas en Google, con código QR y tarjeta para imprimir.",
            "h1": "El enlace para que te dejen <em>reseñas</em> en Google.",
            "lede": "Busca tu negocio y llévate el enlace directo a la ventana de valorar, con su código QR y una tarjeta lista para imprimir.",
            "ph": "Nombre del negocio y ciudad", "aria": "Nombre de tu negocio y ciudad", "btn": "Buscar",
            "note": "Por ejemplo: Panadería Lola Córdoba.",
            "searching": "Buscando…",
            "pick": "Elige tu negocio",
            "none": "No encontramos ese negocio. Prueba con el nombre exacto de tu ficha y la ciudad.",
            "reviews_one": "1 reseña", "reviews_many": "{n} reseñas", "no_reviews": "Sin reseñas todavía",
            "ready": "Tu enlace para reseñas",
            "copy_link": "Copiar enlace", "copied": "Copiado", "test": "Probar enlace",
            "png": "QR en PNG", "svg": "QR en SVG", "card": "Tarjeta para imprimir",
            "card_h": "¿Te ha gustado?", "card_p": "Déjanos tu reseña en Google", "card_s": "Escanea el código con la cámara del móvil",
            "help_t": "¿No sale tu negocio?",
            "help_p": "Entra en tu Perfil de Empresa de Google, pulsa «Pedir reseñas» y copia el enlace que te da. Funciona igual.",
            "tips_t": "Cómo conseguir más reseñas",
            "tips": [
                ("Pídela en el momento", "Justo después de un buen servicio, cuando el cliente está contento. Mañana ya se le habrá olvidado."),
                ("Ponla a mano", "El QR en el mostrador, en la carta, en la factura o en la firma del email."),
                ("Responde a todas", "También a las malas, con calma. Quien lee las reseñas se fija más en cómo respondes."),
            ],
            "faq": [
                ("¿Es legal pedir reseñas?", "Sí, siempre que no las pagues ni ofrezcas nada a cambio y no pidas solo a los clientes contentos de forma selectiva para ocultar las malas. Google lo prohíbe."),
                ("¿El enlace caduca?", "No. Es el enlace oficial de Google para tu ficha y funciona mientras la ficha exista."),
                ("¿Puedo poner el QR en un cartel?", "Sí. Descárgalo en SVG para imprimirlo grande sin que pierda calidad."),
            ],
            "bridge_h": "¿Y si las reseñas se pidieran solas?",
            "bridge_p": "Conectamos tu web, tu CRM o tu caja para que cada cliente reciba la petición de reseña por WhatsApp o email justo después de comprar.",
            "bridge_btn": "Ver cómo lo hacemos",
            "bridge_url": "/productos/webs",
        },
        "whatsapp": {
            "seo_title": "Generador de enlace de WhatsApp y QR gratis · Sprintia",
            "seo_desc": "Crea gratis tu enlace de WhatsApp (wa.me) con mensaje predefinido, su código QR y un botón para tu web. Sin registrarte.",
            "h1": "Tu enlace de <em>WhatsApp</em>, con mensaje y código QR.",
            "lede": "Para que te escriban con un toque desde Instagram, tu web o un cartel, con el primer mensaje ya escrito.",
            "prefix": "Prefijo", "phone": "Teléfono", "phone_ph": "612 345 678",
            "msg": "Mensaje que aparecerá escrito (opcional)", "msg_ph": "Hola, quería información sobre…",
            "btn": "Crear enlace",
            "err_phone": "Revisa el teléfono: escribe solo el número, sin el prefijo.",
            "ready": "Tu enlace de WhatsApp",
            "copy_link": "Copiar enlace", "copied": "Copiado", "test": "Probar enlace",
            "png": "QR en PNG", "svg": "QR en SVG",
            "snippet_t": "Botón para tu web", "snippet_copy": "Copiar código",
            "snippet_label": "Escríbenos por WhatsApp",
            "uses_t": "Dónde ponerlo",
            "uses": [
                ("Instagram y TikTok", "En el enlace de la biografía, para que te escriban sin buscar tu número."),
                ("Tu web", "Como botón en la cabecera o en cada servicio, con el mensaje adaptado."),
                ("Carteles y tarjetas", "El QR en el escaparate, el mostrador o el coche de empresa."),
            ],
            "faq": [
                ("¿Necesito WhatsApp Business?", "No. El enlace funciona con WhatsApp normal y con WhatsApp Business."),
                ("¿Se guarda mi número?", "No. El enlace se crea en tu navegador y no lo enviamos a ningún sitio."),
                ("¿Puedo cambiar el mensaje luego?", "El mensaje va dentro del enlace: si lo cambias, crea un enlace nuevo y sustituye el anterior."),
            ],
            "bridge_h": "Que ningún WhatsApp se quede sin contestar.",
            "bridge_p": "Conectamos WhatsApp a tu CRM: cada conversación queda en la ficha del cliente, con respuesta automática y aviso al comercial.",
            "bridge_btn": "Ver el CRM automatizado",
            "bridge_url": "/productos/crm-automatizado",
        },
        "js": {
            "err_url_empty": "Escribe la dirección de tu web.",
            "err_url_bad": "Eso no parece una dirección web (por ejemplo, tuempresa.com).",
            "err_analyze": "No hemos podido analizar esa web.",
            "err_search": "No hemos podido buscar ahora mismo. Inténtalo en un momento.",
            "err_q": "Escribe el nombre de tu negocio.",
            "scan_steps": ["Abriendo la web", "Revisando lo que ve Google", "Buscando formas de contacto",
                           "Detectando herramientas", "Buscando tu ficha de Google", "Midiendo la velocidad en el móvil"],
        },
    },
    "en": {
        "crumb": "Tools",
        "index": {
            "seo_title": "Free business tools: website, reviews and WhatsApp · Sprintia",
            "seo_desc": "Analyse your website, get your Google review link and create your WhatsApp link with a QR code. Free, no sign-up.",
            "h1": "Free tools to get you <em>found</em> and contacted.",
            "lede": "No sign-up, no small print. We use them with our clients; now you can too.",
            "items": [
                ("analizar", "Website analyzer", "A score out of 100, what Google sees, mobile speed and what's costing you customers.", "SEO check · speed · contact"),
                ("resenas", "Google review link", "Find your business and get the direct link for customers to review you, with a QR code and a printable card.", "Link · QR · card"),
                ("whatsapp", "WhatsApp link", "Let people message you in one tap, with the first message already written. Includes a QR code and a website button.", "Link · QR · button"),
            ],
            "diag_t": "Automation assessment",
            "diag_p": "Your website plus 15 questions: how many hours of manual work you could win back and where to start.",
            "diag_tag": "5 minutes",
            "open": "Open",
        },
        "analizar": {
            "seo_title": "Free website analyzer: SEO checker and speed test · Sprintia",
            "seo_desc": "Analyse your website for free: a score out of 100, SEO check, mobile speed, contact options and Google reviews. Instant results, no sign-up.",
            "h1": "Analyse your website <em>for free</em> and see what it's missing.",
            "lede": "SEO check, mobile speed, contact options and Google reviews in one pass. Instant results, no sign-up.",
            "ph": "yourcompany.com", "aria": "Your website address", "btn": "Analyse",
            "note": "We only look at what's public: the website, what Google sees and your business listing.",
            "scan": "Analysing",
            "score_of": "out of 100",
            "scale": ["Needs work", "Weak", "Room to improve", "In good shape"],
            "issues_one": "1 thing costing you customers", "issues_many": "{n} things costing you customers",
            "issues_none": "We didn't see anything serious",
            "good": "What's already working", "tools": "Tools we detected",
            "partial": "The site has very little visible text or loads with JavaScript, so some data may not be exact.",
            "no_google": "No speed or Google data this time: the score is based on everything else.",
            "mal": "Important", "aviso": "To improve",
            "cta_h": "How many hours is your team losing?",
            "cta_p": "Your website is only part of it. Answer 15 quick questions and we'll tell you how much manual work you could automate and where to start.",
            "cta_btn": "Take the full assessment",
            "again": "Analyse another website",
            "checks_t": "What we check",
            "checks": [
                ("Google", "Title, description, headings, structured data, sitemap and whether Google can read it."),
                ("Speed", "How long it takes to load on mobile, using Google's own measurement (PageSpeed)."),
                ("Contact", "Forms, phone, WhatsApp, bookings: whether it's easy to get in touch."),
                ("Trust", "Security certificate, legal notice, privacy policy and whether it looks current."),
                ("Tracking", "Whether you have analytics to know what works and what doesn't."),
                ("Reviews", "Your Google rating and number of reviews against what customers expect."),
            ],
            "faq": [
                ("Is it really free?", "Yes. We don't ask for an email or a sign-up to show the result. The full automation assessment is free too."),
                ("How is this different from PageSpeed or an SEO checker?", "PageSpeed only measures speed and SEO checkers only look at Google. Here you get both, plus whether it's easy to contact you, whether you inspire trust and how your reviews look: what actually decides whether a visit becomes a customer."),
                ("How long does it take?", "Between 5 and 20 seconds, depending on how fast your website is."),
                ("Why can't my website be analysed?", "Some websites block automated visits or don't respond. Check the address is right; if it still doesn't work, write to us and we'll look at it by hand."),
            ],
            "bridge_h": "Want us to fix it?",
            "bridge_p": "We build fast websites, ready for Google, with every enquiry connected to your CRM.",
            "bridge_btn": "See websites",
            "bridge_url": "/en/products/websites",
        },
        "resenas": {
            "seo_title": "Free Google review link and QR code generator · Sprintia",
            "seo_desc": "Find your business and get the direct link for customers to leave a Google review, with a QR code and a printable card. Free.",
            "h1": "Your link to get more Google <em>reviews</em>.",
            "lede": "Find your business and get the direct link to the review window, with its QR code and a card ready to print.",
            "ph": "Business name and city", "aria": "Your business name and city", "btn": "Search",
            "note": "For example: Lola's Bakery Manchester.",
            "searching": "Searching…",
            "pick": "Choose your business",
            "none": "We couldn't find that business. Try the exact name on your listing and the city.",
            "reviews_one": "1 review", "reviews_many": "{n} reviews", "no_reviews": "No reviews yet",
            "ready": "Your review link",
            "copy_link": "Copy link", "copied": "Copied", "test": "Test link",
            "png": "QR as PNG", "svg": "QR as SVG", "card": "Printable card",
            "card_h": "Enjoyed it?", "card_p": "Leave us a review on Google", "card_s": "Scan the code with your phone camera",
            "help_t": "Can't find your business?",
            "help_p": "Open your Google Business Profile, tap 'Ask for reviews' and copy the link it gives you. It works the same way.",
            "tips_t": "How to get more reviews",
            "tips": [
                ("Ask at the right moment", "Right after a good service, while the customer is happy. Tomorrow they'll have forgotten."),
                ("Keep it handy", "The QR code on the counter, the menu, the invoice or your email signature."),
                ("Reply to every review", "Bad ones too, calmly. Readers pay more attention to how you reply."),
            ],
            "faq": [
                ("Is it allowed to ask for reviews?", "Yes, as long as you don't pay or offer anything in return, and don't selectively ask only happy customers to hide bad reviews. Google forbids that."),
                ("Does the link expire?", "No. It's Google's official link for your listing and works as long as the listing exists."),
                ("Can I put the QR code on a poster?", "Yes. Download it as SVG to print it large without losing quality."),
            ],
            "bridge_h": "What if reviews asked for themselves?",
            "bridge_p": "We connect your website, CRM or till so every customer gets a review request by WhatsApp or email right after buying.",
            "bridge_btn": "See how we do it",
            "bridge_url": "/en/products/websites",
        },
        "whatsapp": {
            "seo_title": "Free WhatsApp link and QR code generator · Sprintia",
            "seo_desc": "Create your WhatsApp link (wa.me) for free, with a pre-filled message, a QR code and a button for your website. No sign-up.",
            "h1": "Your <em>WhatsApp</em> link, with a message and a QR code.",
            "lede": "So people can message you in one tap from Instagram, your website or a poster, with the first message already written.",
            "prefix": "Code", "phone": "Phone", "phone_ph": "7700 900123",
            "msg": "Pre-filled message (optional)", "msg_ph": "Hi, I'd like some information about…",
            "btn": "Create link",
            "err_phone": "Check the phone number: type just the number, without the country code.",
            "ready": "Your WhatsApp link",
            "copy_link": "Copy link", "copied": "Copied", "test": "Test link",
            "png": "QR as PNG", "svg": "QR as SVG",
            "snippet_t": "Button for your website", "snippet_copy": "Copy code",
            "snippet_label": "Message us on WhatsApp",
            "uses_t": "Where to use it",
            "uses": [
                ("Instagram and TikTok", "In your bio link, so people can message you without looking up your number."),
                ("Your website", "As a button in the header or on each service, with a tailored message."),
                ("Posters and cards", "The QR code in the shop window, on the counter or on your van."),
            ],
            "faq": [
                ("Do I need WhatsApp Business?", "No. The link works with regular WhatsApp and with WhatsApp Business."),
                ("Do you store my number?", "No. The link is created in your browser and we don't send it anywhere."),
                ("Can I change the message later?", "The message is part of the link: if you change it, create a new link and replace the old one."),
            ],
            "bridge_h": "Never leave a WhatsApp unanswered.",
            "bridge_p": "We connect WhatsApp to your CRM: every conversation lands in the customer's record, with an automatic reply and an alert to your sales team.",
            "bridge_btn": "See the automated CRM",
            "bridge_url": "/en/products/automated-crm",
        },
        "js": {
            "err_url_empty": "Type your website address.",
            "err_url_bad": "That doesn't look like a website address (for example, yourcompany.com).",
            "err_analyze": "We couldn't analyse that website.",
            "err_search": "We couldn't search right now. Try again in a moment.",
            "err_q": "Type your business name.",
            "scan_steps": ["Opening the website", "Checking what Google sees", "Looking for ways to get in touch",
                           "Detecting tools", "Looking for your Google Business Profile", "Measuring mobile speed"],
        },
    },
}

API_ERR = {
    "es": {"short": "Escribe al menos 3 letras del nombre de tu negocio.",
           "limit": "Has hecho muchas búsquedas seguidas. Prueba dentro de un rato.",
           "off": "La búsqueda de negocios no está disponible ahora mismo. Copia el enlace desde tu Perfil de Empresa de Google («Pedir reseñas»).",
           "fail": "Google no ha respondido. Inténtalo en un momento."},
    "en": {"short": "Type at least 3 letters of your business name.",
           "limit": "You've searched a lot in a row. Try again in a while.",
           "off": "Business search isn't available right now. Copy the link from your Google Business Profile ('Ask for reviews').",
           "fail": "Google didn't respond. Try again in a moment."},
}


# ---------------------------------------------------------------------------
# Páginas
# ---------------------------------------------------------------------------

def _page(key: str, lang: str) -> HTMLResponse:
    es, en = PATHS[key]
    path, alt = (es, en) if lang == "es" else (en, es)
    tx = TXT[lang]
    sec = tx[key]
    urls = {k: v[0] if lang == "es" else v[1] for k, v in PATHS.items()}
    return sitio._cached(f"tool:{lang}:{key}", lambda: sitio._render(
        f"herramienta-{key}.html", lang, path=path, alt_path=alt, lead_source=f"herramienta-{key}" + ("-en" if lang == "en" else ""),
        seo_title=sec["seo_title"], seo_desc=sec["seo_desc"], tx=tx, s=sec, urls=urls,
        areas=AREAS[lang], review_base=_REVIEW_URL,
    ))


def _route(key: str, lang: str):
    async def view() -> HTMLResponse:
        return _page(key, lang)
    view.__name__ = f"tool_{key}_{lang}"
    return view


for _k, (_es, _en) in PATHS.items():
    router.add_api_route(_es, _route(_k, "es"), methods=["GET"], response_class=HTMLResponse)
    router.add_api_route(_en, _route(_k, "en"), methods=["GET"], response_class=HTMLResponse)


# ---------------------------------------------------------------------------
# Búsqueda de negocios (Google Places) para el enlace de reseñas
# ---------------------------------------------------------------------------

_HITS: dict[str, list[float]] = {}
_MAX_SEARCHES, _WINDOW_S = 20, 600
_CACHE: dict[str, tuple[float, list[dict]]] = {}
_CACHE_TTL = 24 * 3600


def _limited(ip: str) -> bool:
    if not ip:
        return False
    now = time.monotonic()
    if len(_HITS) > 10_000:
        _HITS.clear()
    hits = [t for t in _HITS.get(ip, []) if t > now - _WINDOW_S]
    _HITS[ip] = hits
    if len(hits) >= _MAX_SEARCHES:
        return True
    hits.append(now)
    return False


async def search_places(q: str, lang: str) -> list[dict]:
    """Lanza RuntimeError("off") sin clave y RuntimeError("fail") si Google falla."""
    key = settings.diag_google_api_key.strip()
    if not key:
        raise RuntimeError("off")
    ck = f"{lang}|{q.lower()}"
    hit = _CACHE.get(ck)
    if hit and time.time() - hit[0] < _CACHE_TTL:
        return hit[1]
    headers = {"X-Goog-Api-Key": key, "Content-Type": "application/json",
               "X-Goog-FieldMask": "places.id,places.displayName,places.formattedAddress,"
                                   "places.rating,places.userRatingCount"}
    body = {"textQuery": q, "languageCode": lang, "pageSize": 6}
    if lang == "es":
        body["regionCode"] = "ES"
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(10, connect=5)) as c:
            r = await c.post(_PLACES_URL, json=body, headers=headers)
    except Exception as e:
        log.warning("Places (reseñas) no respondió q=%s", q, exc_info=True)
        raise RuntimeError("fail") from e
    if r.status_code != 200:
        log.warning("Places (reseñas) status=%s body=%s", r.status_code, r.text[:200])
        raise RuntimeError("fail")
    out = []
    for pl in (r.json().get("places") or [])[:6]:
        pid = pl.get("id")
        if not pid:
            continue
        out.append({"place_id": pid, "name": (pl.get("displayName") or {}).get("text", ""),
                    "address": pl.get("formattedAddress", ""), "rating": pl.get("rating"),
                    "count": int(pl.get("userRatingCount") or 0)})
    if len(_CACHE) > 2000:
        _CACHE.clear()
    _CACHE[ck] = (time.time(), out)
    return out


@router.post("/api/herramientas/negocios")
async def buscar_negocios(request: Request):
    try:
        body = await request.json()
    except Exception:
        body = {}
    body = body if isinstance(body, dict) else {}
    lang = "en" if body.get("lang") == "en" else "es"
    q = " ".join(str(body.get("q") or "").split())[:120]
    if len(q) < 3:
        return JSONResponse({"error": API_ERR[lang]["short"]}, status_code=400)
    if _limited(_ip(request)):
        return JSONResponse({"error": API_ERR[lang]["limit"]}, status_code=429)
    try:
        results = await search_places(q, lang)
    except RuntimeError as e:
        code = str(e) if str(e) in ("off", "fail") else "fail"
        return JSONResponse({"error": API_ERR[lang][code]}, status_code=503 if code == "off" else 502)
    return {"results": results}
