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
        "planes_nota": "Sin permanencia: te das de baja cuando quieras. ¿Varias sedes, tienda online o algo a medida? Cuéntanoslo y te preparamos un plan.",
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



# ---------------------------------------------------------------- English
# Misma estructura que PRODUCTOS. Las URLs en inglés viven bajo /en; la página
# de voz en inglés es la landing traducida de siempre (`/en/voice-assistant`).
PRODUCTOS_EN: list[dict] = [
    {
        "slug": "voice-assistant", "url": "/en/voice-assistant", "icon": _ICON["voz"],
        "nombre": "Voice assistant", "tag": "AI · 24/7",
        "menu": "Answers calls and books appointments for you",
        "resumen": "An AI receptionist that answers every call, handles questions and books straight into your calendar. On your usual number, at any hour.",
        "puntos": ["Answers 24/7, even when you're busy", "Books and reschedules in your Google Calendar",
                   "Automatic reminders so people show up"],
        "cta": "See and try the assistant",
    },
    {
        "slug": "automated-crm", "url": "/en/products/automated-crm", "icon": _ICON["crm"],
        "nombre": "Automated CRM", "tag": "Sales",
        "menu": "Every lead logged and followed up, no typing",
        "resumen": "Every lead from your website, WhatsApp or phone lands in your CRM on its own. Quotes get followed up automatically and your sales rep is notified at the right moment.",
        "puntos": ["Leads that log themselves, with their source", "Automatic quote follow-up",
                   "Customer emails triggered by what each one does"],
        "cta": "See the automated CRM",
        "seo_title": "Automated CRM for businesses · Sprintia",
        "seo_desc": "We set up and automate your CRM: leads that arrive on their own from your website, WhatsApp and phone, quote follow-up and alerts for your sales team. On HubSpot, Pipedrive, Odoo or Brevo.",
        "kicker": "Automated CRM",
        "h1": "A CRM that <em>fills itself in</em>.",
        "lede": "Every lead from your website, WhatsApp or phone goes into your CRM without anyone typing it. Quotes are followed up automatically and your team gets a nudge exactly when it's time to call.",
        "flujo_titulo": "A quote is sent",
        "flujo": [("The quote goes out", "Logged on the customer's record", "Mon"),
                  ("The customer opens it", "Your sales rep is notified instantly", "Tue"),
                  ("No reply after 3 days", "A follow-up email goes out on its own", "Fri"),
                  ("Still no answer", "A call task, with the full history", "+7 days"),
                  ("They accept", "Becomes a customer and onboarding starts", "Done")],
        "problemas": ["Leads are scattered across email, a spreadsheet and each salesperson's memory.",
                      "Quotes go out and nobody calls back until it's too late.",
                      "You don't know how many leads come in each month or how many end up buying."],
        "incluye_titulo": "Your whole sales process, <em>running on its own</em>.",
        "incluye": [("Automatic lead capture", "From your website, forms, WhatsApp, calls and email, with their source and what they asked for."),
                    ("Quote follow-up", "Reminders and follow-up emails when a customer goes quiet, without anyone having to remember."),
                    ("Team alerts", "When a customer replies, opens a quote or hasn't been heard from in days."),
                    ("Automated emails", "Welcome, reminders, review requests and win-back for dormant customers."),
                    ("Sales dashboard", "Leads, quotes and monthly sales at a glance, no spreadsheet required."),
                    ("Your CRM or a new one", "We work with HubSpot, Pipedrive, Odoo, Brevo and others. If you don't have one, we help you choose and set it up.")],
        "precio_texto": "It depends on how many people use it and what needs connecting. You get a fixed price after the assessment, with what's included and the timeline.",
        "faq": [("Do I have to switch CRM?", "No. If you already use one, we automate it as it is. We only suggest switching if yours can't keep up."),
                ("What if we don't have a CRM?", "We help you pick the one that fits your size and budget, set it up with your data and train your team."),
                ("Will my team actually use it?", "It's designed so they touch as little as possible: data comes in on its own and the CRM tells them what to do next."),
                ("How long until it's up and running?", "Usually two to four weeks, depending on how many lead sources need connecting.")],
        "contacto_titulo": "Tell us how you <em>sell</em> today.",
    },
    {
        "slug": "websites", "url": "/en/products/websites", "icon": _ICON["web"],
        "nombre": "Websites that convert", "tag": "Visibility",
        "menu": "Website, Google and reviews that bring customers",
        "resumen": "Custom websites, fast on mobile and built for Google, with every enquiry connected to your CRM. We also take care of your Google Business Profile and reviews.",
        "puntos": ["Custom design, maintenance included", "SEO foundations and a well-kept Google profile",
                   "Forms and WhatsApp connected to your CRM"],
        "cta": "See our websites",
        "seo_title": "Websites that bring in customers · Sprintia",
        "seo_desc": "Custom websites for businesses: fast on mobile, built for Google and with every enquiry connected to your CRM. Google Business Profile and reviews managed. Monthly fee with design included.",
        "kicker": "Websites that convert",
        "h1": "A website that <em>brings in</em> customers, not just greets them.",
        "lede": "Custom-designed, fast on mobile, built for Google and with every enquiry connected to your CRM. We also look after your Google Business Profile and your reviews.",
        "flujo_titulo": "A customer looking for you",
        "flujo": [("Searches Google for what you do", "Finds you in the results and on Maps", "Step 1"),
                  ("Opens your site on their phone", "It loads in under 2 seconds", "Step 2"),
                  ("Gets in touch", "Through the form or on WhatsApp", "Step 3"),
                  ("Lands in your CRM", "And gets a reply within a minute", "Step 4"),
                  ("After the sale", "We ask for a review automatically", "Step 5")],
        "problemas": ["We have a website, but nobody contacts us through it.",
                      "When people search Google for what we do, others show up.",
                      "We have few reviews, or a bad one nobody answered."],
        "incluye_titulo": "Design, Google and lead capture, <em>in one fee</em>.",
        "incluye": [("Custom design", "Built for your business, not a template. Designed to get visitors to reach out."),
                    ("Fast and mobile-first", "Loads in under 2 seconds, which is where most visits are lost."),
                    ("SEO foundations", "Titles, descriptions, structured data and a sitemap so Google understands what you do."),
                    ("Connected contact", "Forms, WhatsApp and bookings that go straight into your CRM or inbox."),
                    ("Google profile and reviews", "An up-to-date profile and automatic review requests after each sale or service."),
                    ("Maintenance included", "Hosting, domain, SSL certificate and any content changes you need.")],
        "planes_intro": "The website is included in the fee: no design or setup cost.",
        "planes": [
            {"nombre": "Base", "para": "Professional presence", "precio": "59",
             "incluye": ["Custom website, design included", "Hosting, domain and SSL certificate",
                         "Content changes whenever you need them", "WhatsApp support"]},
            {"nombre": "Visible", "para": "Get found", "precio": "99", "destacado": True,
             "incluye": ["Everything in Base", "Managed Google Business Profile",
                         "Reviews, photos, hours and posts kept up to date", "Monthly visibility report"]},
            {"nombre": "Grow", "para": "Bring them back", "precio": "149",
             "incluye": ["Everything in Visible", "Online bookings from your website", "CRM with customer records",
                         "Reminders and follow-ups via WhatsApp and email", "Monthly campaign to your customer base"]},
        ],
        "planes_nota": "No lock-in: cancel whenever you like. Several locations, an online shop or something custom? Tell us and we'll put together a plan.",
        "faq": [("How long does the website take?", "About two to three weeks from when you send us the information, depending on the size of the site."),
                ("Do I own the website?", "The content and the domain are yours. The fee covers design, hosting and maintenance."),
                ("Can I see how my current site is doing first?", "Yes: the free assessment analyses it in seconds and tells you what's missing."),
                ("Do you build online shops?", "Yes, with Shopify or WooCommerce. Tell us about it and we'll prepare a custom plan.")],
        "contacto_titulo": "Tell us what <em>website</em> you need.",
    },
    {
        "slug": "process-automation", "url": "/en/products/process-automation", "icon": _ICON["procesos"],
        "nombre": "Process automation", "tag": "Operations",
        "menu": "Invoices, data, reports and 24/7 support",
        "resumen": "We connect the tools you already use so data moves on its own: invoices that read themselves, reports that stay current and 24/7 answers to the usual questions.",
        "puntos": ["Invoices and delivery notes flow into accounting", "Connected tools, no copy and paste",
                   "Up-to-date reports and 24/7 WhatsApp support"],
        "cta": "See what we automate",
        "seo_title": "Business process automation · Sprintia",
        "seo_desc": "We automate invoices, data between tools, reports and AI customer support. We connect Holded, Odoo, A3, Sage, your shop and your CRM so your team stops copying and pasting.",
        "kicker": "Process automation",
        "h1": "Stop your team being the <em>glue</em>.",
        "lede": "We connect the tools you already use so data moves on its own: invoices that are read and filed into accounting, orders that reach your ERP, reports that update without spreadsheets and 24/7 answers to the usual questions.",
        "flujo_titulo": "A supplier invoice",
        "flujo": [("Arrives by email", "As a PDF, a photo or whatever", "08:15"),
                  ("AI reads it", "Supplier, date, amount and VAT", "08:15"),
                  ("Filed in accounting", "In Holded, A3, Sage or Odoo", "08:16"),
                  ("If something's off", "You're alerted before it's filed", "08:16"),
                  ("Month end", "The report is already done", "Day 1")],
        "problemas": ["Someone spends days every month typing up invoices and delivery notes that already exist as PDFs.",
                      "The same data is copied by hand from one tool to another, errors included.",
                      "You answer the same questions dozens of times a day: hours, prices, order status."],
        "incluye_titulo": "The repetitive stuff, <em>off your desk</em>.",
        "incluye": [("Automatic invoice reading", "AI reads invoices and delivery notes and files them in your accounting software."),
                    ("Connected tools", "Shop, ERP, CRM, email and spreadsheets talking to each other, no copy and paste."),
                    ("Automated reports", "A dashboard with the month's numbers that updates itself, ready for decisions."),
                    ("24/7 support", "WhatsApp or chat answers to the usual questions; everything else goes to a person."),
                    ("Documents that write themselves", "Quotes, contracts and emails generated from the data you already have."),
                    ("Alerts and reminders", "Low stock, unpaid invoices or deadlines coming up, before they become a problem.")],
        "precio_texto": "Every process is different: you get a fixed price per automation, alongside the hours we estimate it saves you. Many pay for themselves within a few months.",
        "faq": [("Which tools do you work with?", "Most of them: Holded, Odoo, A3, Sage, Google Workspace, Microsoft 365, Shopify, WooCommerce, HubSpot and any tool with an API or data export."),
                ("What if the AI gets it wrong?", "Automations include checks: if something doesn't add up or the AI isn't sure, it alerts you instead of filing it."),
                ("Where do we start?", "With the process that eats the most hours. The free assessment gives you a first estimate in two minutes."),
                ("Do we need someone technical?", "No. We build it, maintain it and train whoever is going to use it.")],
        "contacto_titulo": "Which process eats the most <em>hours</em>?",
    },
]

FAQ_HOME_EN = [
    ("What kind of company is Sprintia for?",
     "Companies that already work well but lose too much time on repetitive tasks: usually 10 to 250 people, though we also work with smaller businesses."),
    ("Do we have to change the tools we use?",
     "No. We connect the ones you already use. We only suggest changing something if it's genuinely holding you back."),
    ("How much does it cost?",
     "It depends on what needs automating. After the assessment you get a fixed price, with what's included and the timeline. The voice assistant and websites have monthly plans on their pages."),
    ("How long does it take?",
     "A simple automation can be ready in one or two weeks; a full CRM or several connected processes take four to six."),
    ("What happens to our data?",
     "It stays in your tools. We only connect what each automation needs and explain which data goes where."),
]

# Cada producto conoce su página en el otro idioma (para el selector y hreflang).
for _es, _en in zip(PRODUCTOS, PRODUCTOS_EN):
    _es["alt"], _en["alt"] = _en["url"], _es["url"]

_PRODUCTOS = {"es": PRODUCTOS, "en": PRODUCTOS_EN}
_BY_SLUG_EN = {p["slug"]: p for p in PRODUCTOS_EN}

# Textos de interfaz de las plantillas (base y producto).
T: dict[str, dict[str, str]] = {
    "es": {
        "locale": "es_ES", "home": "/", "diag": "/diagnostico", "home_aria": "Sprintia, inicio", "nav_aria": "Principal",
        "nav_products": "Productos", "nav_how": "Cómo trabajamos", "nav_diag": "Diagnóstico gratis", "nav_contact": "Contacto", "nav_menu": "Menú", "nav_menu_aria": "Abrir el menú",
        "nav_cta": "Analiza tu web", "nav_cta_short": "Analizar", "switch_label": "EN", "switch_title": "English",
        "contact_kicker": "Contacto", "contact_title": "Cuéntanos qué te quita <em>tiempo</em>.",
        "contact_lede": "Te respondemos en uno o dos días laborables. Si lo vemos claro, te proponemos qué automatizar primero y cuánto cuesta, sin compromiso.",
        "contact_email": "¿Prefieres el email?", "f_name": "Nombre", "f_company": "Empresa", "f_email": "Email", "f_phone": "Teléfono",
        "f_message": "¿Qué te gustaría automatizar? (opcional)",
        "f_placeholder": "Por ejemplo: pasamos las facturas a mano, los contactos de la web se pierden…",
        "f_consent": "Acepto que Sprintia me contacte para responder a mi solicitud.", "f_send": "Enviar",
        "foot_tagline": "Automatización para empresas: webs, CRM, atención y procesos que funcionan solos.",
        "foot_products": "Productos", "foot_start": "Empieza", "foot_diag": "Analiza tu web gratis",
        "foot_quote": "Pide presupuesto", "foot_lang": "English",
        "js_url": "Escribe la dirección de tu web, por ejemplo tuempresa.com", "js_name": "Dinos tu nombre.",
        "js_email": "Revisa el email.", "js_phone": "Revisa el teléfono.", "js_consent": "Marca la casilla para que podamos contactarte.",
        "js_sending": "Enviando…", "js_fail": "No se pudo enviar.", "js_ok_title": "Recibido",
        "js_ok_text": "Te escribimos en uno o dos días laborables.", "country": "España",
        "live": "EN MARCHA", "crumb_products": "Productos", "example": "Ejemplo",
        "p_quote": "Pide presupuesto", "p_diag": "Analiza tu web gratis", "p_for": "Para quién es",
        "p_if": "Si te pasa <em>esto</em>…", "p_includes": "Qué incluye", "p_plans": "Planes",
        "p_plans_title": "Precio cerrado <em>al mes</em>.", "p_badge": "Más elegido", "p_month": "€/mes", "p_want": "Lo quiero",
        "p_cost": "Cuánto cuesta", "p_cost_title": "Presupuesto cerrado, <em>según tu caso</em>.",
        "p_start_diag": "Empieza con el diagnóstico", "faq_kicker": "Preguntas frecuentes",
        "faq_title": "Lo que nos <em>suelen</em> preguntar.", "p_combines": "Se combina con", "p_others": "Otros <em>productos</em>.",
    },
    "en": {
        "locale": "en_GB", "home": "/en", "diag": "/en/diagnosis", "home_aria": "Sprintia, home", "nav_aria": "Main",
        "nav_products": "Products", "nav_how": "How we work", "nav_diag": "Free assessment", "nav_contact": "Contact", "nav_menu": "Menu", "nav_menu_aria": "Open the menu",
        "nav_cta": "Analyse your website", "nav_cta_short": "Analyse", "switch_label": "ES", "switch_title": "Español",
        "contact_kicker": "Contact", "contact_title": "Tell us what's eating your <em>time</em>.",
        "contact_lede": "We reply within one or two working days. If it's a fit, we'll suggest what to automate first and what it costs, no strings attached.",
        "contact_email": "Prefer email?", "f_name": "Name", "f_company": "Company", "f_email": "Email", "f_phone": "Phone",
        "f_message": "What would you like to automate? (optional)",
        "f_placeholder": "For example: we type invoices by hand, website leads get lost…",
        "f_consent": "I agree that Sprintia may contact me to respond to my request.", "f_send": "Send",
        "foot_tagline": "Automation for businesses: websites, CRM, support and processes that run on their own.",
        "foot_products": "Products", "foot_start": "Get started", "foot_diag": "Analyse your website for free",
        "foot_quote": "Get a quote", "foot_lang": "Español",
        "js_url": "Enter your website address, e.g. yourcompany.com", "js_name": "Tell us your name.",
        "js_email": "Check your email address.", "js_phone": "Check your phone number.", "js_consent": "Tick the box so we can contact you.",
        "js_sending": "Sending…", "js_fail": "Could not send.", "js_ok_title": "Received",
        "js_ok_text": "We'll get back to you within one or two working days.", "country": "",
        "live": "RUNNING", "crumb_products": "Products", "example": "Example",
        "p_quote": "Get a quote", "p_diag": "Analyse your website for free", "p_for": "Who it's for",
        "p_if": "If <em>this</em> sounds familiar…", "p_includes": "What's included", "p_plans": "Plans",
        "p_plans_title": "A fixed price, <em>monthly</em>.", "p_badge": "Most popular", "p_month": "€/month", "p_want": "I want it",
        "p_cost": "Pricing", "p_cost_title": "A fixed quote, <em>for your case</em>.",
        "p_start_diag": "Start with the assessment", "faq_kicker": "FAQ",
        "faq_title": "What people <em>usually</em> ask.", "p_combines": "Works well with", "p_others": "Other <em>products</em>.",
    },
}


def _render(name: str, lang: str = "es", **ctx) -> str:
    ctx.setdefault("productos", _PRODUCTOS[lang])
    ctx.setdefault("base_url", BASE_URL)
    ctx.setdefault("year", time.strftime("%Y"))
    return _env.get_template(name).render(lang=lang, t=T[lang], **ctx)


_CACHE: dict[str, str] = {}


def _cached(key: str, fn) -> HTMLResponse:
    if key not in _CACHE:
        _CACHE[key] = fn()
    return HTMLResponse(_CACHE[key])


@router.get("/", response_class=HTMLResponse)
async def home() -> HTMLResponse:
    return _cached("home", lambda: _render(
        "home.html", path="/", alt_path="/en", lead_source="home",
        seo_title="Automatización para empresas: webs, CRM y procesos · Sprintia",
        og_title="Sprintia · Lo que tu equipo hace a mano, que se haga solo",
        seo_desc="Diseñamos y montamos automatizaciones para empresas: webs que captan clientes, CRM automatizado, asistente de voz con IA y automatización de procesos. Analiza tu web gratis.",
        herramientas=HERRAMIENTAS, faq=FAQ_HOME,
    ))


@router.get("/en", response_class=HTMLResponse)
async def home_en() -> HTMLResponse:
    return _cached("home:en", lambda: _render(
        "home_en.html", "en", path="/en", alt_path="/", lead_source="home-en",
        seo_title="Business automation: websites, CRM and processes · Sprintia",
        og_title="Sprintia · What your team does by hand, done automatically",
        seo_desc="We design and build automation for businesses: websites that bring in customers, an automated CRM, an AI voice assistant and process automation. Analyse your website for free.",
        herramientas=HERRAMIENTAS, faq=FAQ_HOME_EN,
    ))


def _product_page(p: dict, lang: str) -> HTMLResponse:
    suffix = "-en" if lang == "en" else ""
    return _cached(f"p:{lang}:{p['slug']}", lambda: _render(
        "producto.html", lang, p=p, path=p["url"], alt_path=p["alt"], lead_source=f"producto-{p['slug']}{suffix}",
        seo_title=p["seo_title"], seo_desc=p["seo_desc"],
    ))


@router.get("/productos/{slug}", response_class=HTMLResponse)
async def producto(slug: str):
    if slug == "asistente-de-voz":
        return RedirectResponse("/asistente-de-voz", status_code=301)
    p = _BY_SLUG.get(slug)
    if not p:
        raise HTTPException(status_code=404)
    return _product_page(p, "es")


@router.get("/en/products/{slug}", response_class=HTMLResponse)
async def product_en(slug: str):
    if slug == "voice-assistant":
        return RedirectResponse("/en/voice-assistant", status_code=301)
    p = _BY_SLUG_EN.get(slug)
    if not p:
        raise HTTPException(status_code=404)
    return _product_page(p, "en")


def product_paths() -> list[str]:
    """Rutas indexables de producto (para el sitemap), en los dos idiomas."""
    return [p["url"] for p in PRODUCTOS + PRODUCTOS_EN]
