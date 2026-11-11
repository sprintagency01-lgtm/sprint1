"""Web corporativa de Sprintia: home y páginas de producto.

Desde 2026-11-12 la home (`/`) presenta a Sprintia como proveedor de
automatización para empresas y el asistente de voz pasa a ser un producto más,
con su página de siempre en `/asistente-de-voz` (la sirve `main.py`).

Los textos de los productos viven aquí; las plantillas están en
`app/templates/sitio/` (Jinja2). Sin precios en la home: solo los planes de
webs en su página (y los de voz en la suya).
"""
from __future__ import annotations

import pathlib
import time

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from jinja2 import Environment, FileSystemLoader, select_autoescape

router = APIRouter(tags=["sitio"])

BASE_URL = "https://sprintiasolutions.com"
_env = Environment(
    loader=FileSystemLoader(pathlib.Path(__file__).parent / "templates" / "sitio"),
    autoescape=select_autoescape(["html"]),
    trim_blocks=True,
    lstrip_blocks=True,
)

_ICON = {
    "voz": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M22 16.9v3a2 2 0 0 1-2.2 2 19.8 19.8 0 0 1-8.6-3.1 19.5 19.5 0 0 1-6-6A19.8 19.8 0 0 1 2.1 4.2 2 2 0 0 1 4.1 2h3a2 2 0 0 1 2 1.7c.1 1 .4 1.9.7 2.8a2 2 0 0 1-.5 2.1L8 9.9a16 16 0 0 0 6 6l1.3-1.3a2 2 0 0 1 2.1-.4c.9.3 1.8.6 2.8.7a2 2 0 0 1 1.7 2z"/></svg>',
    "crm": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="9" cy="8" r="4"/><path d="M2 21c0-4 3-6 7-6s7 2 7 6"/><path d="M16 4.5a4 4 0 0 1 0 7M22 21c0-3-1.7-5-4.5-5.7"/></svg>',
    "web": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="2" y="4" width="20" height="16" rx="2"/><path d="M2 9h20M6 6.5h.01M9 6.5h.01"/><path d="M7 14h6M7 17h4"/></svg>',
    "procesos": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/><path d="M10 6.5h4a3 3 0 0 1 3 3V14M14 17.5h-4a3 3 0 0 1-3-3V10"/></svg>',
}

# Orden = orden en la home y en el menú. El primero va destacado (oscuro).
PRODUCTOS: list[dict] = [
    {
        "slug": "asistente-de-voz", "url": "/asistente-de-voz", "icon": _ICON["voz"],
        "nombre": "Asistente de voz", "tag": "IA · 24/7",
        "menu": "Atiende llamadas y reserva citas por ti",
        "resumen": "Una recepcionista con IA que contesta cada llamada, resuelve dudas y reserva en tu agenda. Con tu número de siempre y a cualquier hora.",
        "puntos": ["Atiende 24/7, también cuando estás ocupado", "Reserva y cambia citas en tu Google Calendar",
                   "Recordatorios automáticos para que no falten"],
        "cta": "Ver y probar el asistente",
    },
    {
        "slug": "crm-automatizado", "url": "/productos/crm-automatizado", "icon": _ICON["crm"],
        "nombre": "CRM automatizado", "tag": "Ventas",
        "menu": "Cada contacto registrado y seguido, sin teclear",
        "resumen": "Cada contacto de la web, del WhatsApp o del teléfono entra solo en tu CRM. Los presupuestos se siguen solos y el comercial recibe el aviso cuando toca.",
        "puntos": ["Contactos que entran solos, con su origen", "Seguimiento automático de presupuestos",
                   "Emails a clientes según lo que hace cada uno"],
        "cta": "Ver el CRM automatizado",
        "seo_title": "CRM automatizado para empresas · Sprintia",
        "seo_desc": "Implantamos y automatizamos tu CRM: contactos que entran solos desde la web, WhatsApp y teléfono, seguimiento de presupuestos y avisos al comercial. Sobre HubSpot, Pipedrive, Odoo o Brevo.",
        "kicker": "CRM automatizado",
        "h1": "Un CRM que se <em>rellena solo</em>.",
        "lede": "Cada contacto de la web, del WhatsApp o del teléfono entra en tu CRM sin que nadie lo teclee. Los presupuestos se siguen solos y tu equipo recibe el aviso justo cuando toca llamar.",
        "flujo_titulo": "Un presupuesto enviado",
        "flujo": [("Se envía el presupuesto", "Queda registrado en la ficha del cliente", "Lunes"),
                  ("El cliente lo abre", "El comercial recibe el aviso al momento", "Martes"),
                  ("No contesta en 3 días", "Sale solo un email de seguimiento", "Viernes"),
                  ("Sigue sin respuesta", "Tarea para llamarle, con todo el historial", "+7 días"),
                  ("Acepta", "Pasa a cliente y empieza la bienvenida", "Hecho")],
        "problemas": ["Los contactos están repartidos entre el email, un Excel y la cabeza de cada comercial.",
                      "Se envían presupuestos y nadie vuelve a llamar hasta que ya es tarde.",
                      "No sabes cuántos contactos entran al mes ni cuántos acaban comprando."],
        "incluye_titulo": "Todo tu proceso comercial, <em>en marcha solo</em>.",
        "incluye": [("Entrada automática de contactos", "Desde la web, formularios, WhatsApp, llamadas y email, con su origen y lo que pidieron."),
                    ("Seguimiento de presupuestos", "Recordatorios y emails de seguimiento si el cliente no contesta, sin que nadie se acuerde."),
                    ("Avisos al equipo", "Cuando un cliente responde, abre el presupuesto o lleva días sin noticias."),
                    ("Emails automáticos", "Bienvenida, recordatorios, petición de reseña y reactivación de clientes dormidos."),
                    ("Panel de ventas", "Contactos, presupuestos y ventas del mes de un vistazo, sin montar ningún Excel."),
                    ("Sobre tu CRM o uno nuevo", "Trabajamos con HubSpot, Pipedrive, Odoo, Brevo y otros. Si no tienes, te ayudamos a elegir e implantarlo.")],
        "precio_texto": "Depende de cuántas personas lo usen y de qué haya que conectar. Te damos un precio cerrado después del diagnóstico, con lo que incluye y el plazo.",
        "faq": [("¿Tengo que cambiar de CRM?", "No. Si ya usáis uno, lo automatizamos tal cual. Solo proponemos cambiar si el que tenéis no da más de sí."),
                ("¿Y si no tenemos CRM?", "Te ayudamos a elegir el que encaja con vuestro tamaño y presupuesto, lo implantamos con vuestros datos y formamos al equipo."),
                ("¿Mi equipo lo va a usar de verdad?", "Está pensado para que tengan que tocar lo mínimo: los datos entran solos y el CRM les avisa de lo que tienen que hacer."),
                ("¿Cuánto se tarda en tenerlo funcionando?", "Lo normal son de dos a cuatro semanas, según cuántas fuentes de contactos haya que conectar.")],
        "contacto_titulo": "Cuéntanos cómo <em>vendéis</em> hoy.",
    },
    {
        "slug": "webs", "url": "/productos/webs", "icon": _ICON["web"],
        "nombre": "Webs que captan", "tag": "Visibilidad",
        "menu": "Web, Google y reseñas que traen clientes",
        "resumen": "Webs a medida, rápidas en el móvil y preparadas para Google, con cada contacto conectado a tu CRM. También nos encargamos de tu ficha de Google y tus reseñas.",
        "puntos": ["Diseño a medida y mantenimiento incluido", "SEO de base y ficha de Google trabajada",
                   "Formularios y WhatsApp conectados al CRM"],
        "cta": "Ver las webs",
        "seo_title": "Diseño de webs que captan clientes · Sprintia",
        "seo_desc": "Webs a medida para empresas: rápidas en el móvil, preparadas para Google y con cada contacto conectado a tu CRM. Ficha de Google y reseñas gestionadas. Cuota mensual con diseño incluido.",
        "kicker": "Webs que captan",
        "h1": "Una web que <em>trae</em> clientes, no solo que los recibe.",
        "lede": "Diseñada a medida, rápida en el móvil, preparada para Google y con cada contacto conectado a tu CRM. Nos encargamos también de tu ficha de Google y de tus reseñas.",
        "flujo_titulo": "Un cliente que te busca",
        "flujo": [("Busca lo que haces en Google", "Te encuentra en los resultados y en Maps", "Paso 1"),
                  ("Abre tu web en el móvil", "Carga en menos de 2 segundos", "Paso 2"),
                  ("Te escribe", "Por el formulario o por WhatsApp", "Paso 3"),
                  ("Entra en tu CRM", "Y recibe respuesta en un minuto", "Paso 4"),
                  ("Después de la venta", "Le pedimos la reseña automáticamente", "Paso 5")],
        "problemas": ["Tenemos web, pero no nos escribe nadie desde ella.",
                      "Cuando buscan lo que hacemos en Google, salen otros.",
                      "Tenemos pocas reseñas, o alguna mala sin responder."],
        "incluye_titulo": "Diseño, Google y captación, <em>en una cuota</em>.",
        "incluye": [("Diseño a medida", "Hecha para tu empresa, no una plantilla. Pensada para que el visitante contacte."),
                    ("Rápida y adaptada al móvil", "Que cargue en menos de 2 segundos, que es donde se pierde la mayoría de las visitas."),
                    ("SEO de base", "Títulos, descripciones, datos estructurados y sitemap para que Google entienda qué haces."),
                    ("Contacto conectado", "Formularios, WhatsApp y reservas que entran directos en tu CRM o tu email."),
                    ("Ficha de Google y reseñas", "Ficha al día y petición automática de reseñas después de cada venta o servicio."),
                    ("Mantenimiento incluido", "Hosting, dominio, certificado SSL y los cambios de contenido que necesites.")],
        "planes_intro": "La web va incluida en la cuota: sin coste de diseño ni de alta.",
        "planes": [
            {"nombre": "Base", "para": "Presencia profesional", "precio": "59",
             "incluye": ["Web a medida, diseño incluido", "Hosting, dominio y certificado SSL",
                         "Cambios de contenido cuando los necesites", "Soporte por WhatsApp"]},
            {"nombre": "Visible", "para": "Que te encuentren", "precio": "99", "destacado": True,
             "incluye": ["Todo lo de Base", "Ficha de Google Business gestionada",
                         "Reseñas, fotos, horarios y publicaciones al día", "Informe mensual de visibilidad"]},
            {"nombre": "Crece", "para": "Que vuelvan", "precio": "149",
             "incluye": ["Todo lo de Visible", "Reservas online desde la web", "CRM con fichas de clientes",
                         "Recordatorios y seguimientos por WhatsApp y email", "Campaña mensual a tu base de clientes"]},
        ],
        "planes_nota": "Permanencia mínima de 12 meses. ¿Varias sedes, tienda online o algo a medida? Cuéntanoslo y te preparamos un plan.",
        "faq": [("¿Cuánto se tarda en tener la web?", "Unas dos o tres semanas desde que nos pasas la información, según el tamaño de la web."),
                ("¿La web es mía?", "El contenido y el dominio son tuyos. La cuota cubre el diseño, el alojamiento y el mantenimiento."),
                ("¿Puedo ver antes cómo está mi web actual?", "Sí: el diagnóstico gratuito la analiza en segundos y te dice qué le falta."),
                ("¿Hacéis tiendas online?", "Sí, con Shopify o WooCommerce. Cuéntanoslo y te preparamos un plan a medida.")],
        "contacto_titulo": "Cuéntanos qué <em>web</em> necesitas.",
    },
    {
        "slug": "automatizacion-de-procesos", "url": "/productos/automatizacion-de-procesos", "icon": _ICON["procesos"],
        "nombre": "Automatización de procesos", "tag": "Operaciones",
        "menu": "Facturas, datos, informes y atención 24 h",
        "resumen": "Conectamos los programas que ya usáis para que los datos viajen solos: facturas que se leen solas, informes que se actualizan y atención 24 horas para las preguntas de siempre.",
        "puntos": ["Facturas y albaranes que pasan solos a contabilidad", "Programas conectados, sin copiar y pegar",
                   "Informes al día y atención 24 h por WhatsApp"],
        "cta": "Ver qué automatizamos",
        "seo_title": "Automatización de procesos para empresas · Sprintia",
        "seo_desc": "Automatizamos facturas, datos entre programas, informes y atención al cliente con IA. Conectamos Holded, Odoo, A3, Sage, tu tienda y tu CRM para que tu equipo deje de copiar y pegar.",
        "kicker": "Automatización de procesos",
        "h1": "Que tu equipo deje de hacer de <em>pegamento</em>.",
        "lede": "Conectamos los programas que ya usáis para que los datos viajen solos: facturas que se leen y pasan a contabilidad, pedidos que llegan al ERP, informes que se actualizan sin Excel y respuestas 24 horas a las preguntas de siempre.",
        "flujo_titulo": "Una factura de proveedor",
        "flujo": [("Llega al email", "En PDF, foto o como sea", "08:15"),
                  ("La IA la lee", "Proveedor, fecha, importe e IVA", "08:15"),
                  ("Se registra en contabilidad", "En Holded, A3, Sage u Odoo", "08:16"),
                  ("Si algo no cuadra", "Te avisa antes de registrarla", "08:16"),
                  ("Fin de mes", "El informe ya está hecho", "Día 1")],
        "problemas": ["Alguien pasa días cada mes tecleando facturas y albaranes que ya existen en PDF.",
                      "Los mismos datos se copian a mano de un programa a otro, con sus errores.",
                      "Respondéis decenas de veces al día lo mismo: horarios, precios, estado del pedido."],
        "incluye_titulo": "Lo repetitivo, <em>fuera de la mesa</em>.",
        "incluye": [("Lectura automática de facturas", "La IA lee facturas y albaranes y los registra en tu programa de contabilidad."),
                    ("Programas conectados", "Tienda, ERP, CRM, email y hojas de cálculo hablando entre sí, sin copiar y pegar."),
                    ("Informes automáticos", "Un panel con los números del mes que se actualiza solo, listo para decidir."),
                    ("Atención 24 h", "Respuestas por WhatsApp o chat a las preguntas de siempre; lo demás pasa a una persona."),
                    ("Documentos que se hacen solos", "Presupuestos, contratos y emails generados con los datos que ya tienes."),
                    ("Avisos y alertas", "Stock bajo, cobros pendientes o plazos que vencen, antes de que sean un problema.")],
        "precio_texto": "Cada proceso es distinto: te damos un precio cerrado por automatización, con las horas que estimamos que os ahorra. Muchas se pagan solas en pocos meses.",
        "faq": [("¿Con qué programas trabajáis?", "Con la mayoría: Holded, Odoo, A3, Sage, Google Workspace, Microsoft 365, Shopify, WooCommerce, HubSpot y cualquier programa con API o exportación de datos."),
                ("¿Qué pasa si la IA se equivoca?", "Las automatizaciones llevan revisiones: si un dato no cuadra o la IA no está segura, te avisa en vez de registrarlo."),
                ("¿Por dónde empezamos?", "Por el proceso que más horas os come. El diagnóstico gratuito te da una primera estimación en dos minutos."),
                ("¿Necesitamos a alguien técnico?", "No. Lo montamos, lo mantenemos y formamos a quien lo vaya a usar.")],
        "contacto_titulo": "¿Qué proceso os <em>come</em> más horas?",
    },
]
_BY_SLUG = {p["slug"]: p for p in PRODUCTOS}

HERRAMIENTAS = ["Google Workspace", "Microsoft 365", "HubSpot", "Pipedrive", "Odoo", "Holded", "A3", "Sage",
                "Brevo", "Mailchimp", "WhatsApp Business", "Shopify", "WooCommerce", "WordPress", "Stripe",
                "Google Calendar", "n8n", "Make", "Zapier"]

FAQ_HOME = [
    ("¿Para qué tipo de empresa es Sprintia?",
     "Para empresas que ya funcionan y donde se va demasiado tiempo en tareas repetitivas: de 10 a 250 personas, aunque también trabajamos con negocios más pequeños."),
    ("¿Tengo que cambiar los programas que usamos?",
     "No. Conectamos los que ya usáis. Solo proponemos cambiar alguno si de verdad os está frenando."),
    ("¿Cuánto cuesta?",
     "Depende de qué haya que automatizar. Después del diagnóstico te damos un precio cerrado, con lo que incluye y el plazo. El asistente de voz y las webs tienen planes mensuales en su página."),
    ("¿Cuánto se tarda?",
     "Una automatización sencilla puede estar en una o dos semanas; un CRM completo o varios procesos conectados, de cuatro a seis."),
    ("¿Qué pasa con nuestros datos?",
     "Siguen en vuestras herramientas. Solo conectamos lo necesario para cada automatización y os explicamos qué datos pasan por dónde."),
]


def _render(name: str, **ctx) -> str:
    ctx.setdefault("productos", PRODUCTOS)
    ctx.setdefault("base_url", BASE_URL)
    ctx.setdefault("year", time.strftime("%Y"))
    return _env.get_template(name).render(**ctx)


_CACHE: dict[str, str] = {}


def _cached(key: str, fn) -> HTMLResponse:
    if key not in _CACHE:
        _CACHE[key] = fn()
    return HTMLResponse(_CACHE[key])


@router.get("/", response_class=HTMLResponse)
async def home() -> HTMLResponse:
    return _cached("home", lambda: _render(
        "home.html", path="/", lead_source="home",
        seo_title="Sprintia · Automatización para empresas: webs, CRM, atención y procesos",
        og_title="Sprintia · Lo que tu equipo hace a mano, que se haga solo",
        seo_desc="Diseñamos y montamos automatizaciones para empresas: webs que captan clientes, CRM automatizado, asistente de voz con IA y automatización de procesos. Analiza tu web gratis.",
        herramientas=HERRAMIENTAS, faq=FAQ_HOME,
    ))


@router.get("/productos/{slug}", response_class=HTMLResponse)
async def producto(slug: str):
    if slug == "asistente-de-voz":
        return RedirectResponse("/asistente-de-voz", status_code=301)
    p = _BY_SLUG.get(slug)
    if not p:
        raise HTTPException(status_code=404)
    return _cached(f"p:{slug}", lambda: _render(
        "producto.html", p=p, path=p["url"], lead_source=f"producto-{slug}",
        seo_title=p["seo_title"], seo_desc=p["seo_desc"],
    ))


def product_paths() -> list[str]:
    """Rutas indexables de producto (para el sitemap)."""
    return [p["url"] for p in PRODUCTOS]
