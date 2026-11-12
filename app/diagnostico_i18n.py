"""Textos del diagnóstico en inglés (/en/diagnosis) y de la página en los dos idiomas.

La lógica (preguntas, puntuación, horas) vive en `diagnostico.py` y es la misma
para los dos idiomas: aquí solo están las traducciones, indexadas por el `id`
de cada pregunta o área para que no se desordenen si cambia el orden.

Lo que lee Mario (resumen del presupuesto, aviso interno, atributos de Brevo)
sigue en español aunque el visitante haga el test en inglés.
"""
from __future__ import annotations

LANGS = ("es", "en")

QUESTIONS_EN: dict[str, dict] = {
    "sector": {"texto": "What does your company do?",
               "opciones": ["Professional services", "Retail or ecommerce", "Manufacturing or distribution",
                            "Hospitality or tourism", "Health or clinics", "Real estate", "Education and training",
                            "Other"]},
    "tamano": {"texto": "How many people work at your company?",
               "opciones": ["1–9", "10–49", "50–249", "250 or more"]},
    "rol": {"texto": "What's your role?",
            "opciones": ["Management or owner", "Sales or marketing", "Operations or admin", "Technology", "Other"]},
    "web": {"texto": "Your website today…",
            "opciones": ["Captures leads and logs them automatically", "Looks fine, but doesn't capture leads",
                         "Has become outdated", "We don't have a website"]},
    "seo": {"texto": "When someone googles what you do, do they find you?",
            "opciones": ["Yes, in the top results", "Only if they search for our name", "Hardly ever", "I don't know"]},
    "resenas": {"texto": "How are you doing on Google reviews and ratings?",
                "opciones": ["Lots of good ones, and we ask every customer", "A few, but good",
                             "A few, and some bad ones unanswered", "No profile, or I don't know"]},
    "captacion": {"texto": "When a potential customer gets in touch (website, WhatsApp, phone), what happens?",
                  "opciones": ["They get a reply automatically, within minutes", "Someone sees it within a few hours",
                               "Sometimes it gets lost", "We don't know"]},
    "seguimiento": {"texto": "Where is your customer information kept?",
                    "opciones": ["In a CRM we use properly", "In a half-used CRM", "In spreadsheets or notes",
                                 "In each salesperson's head"]},
    "facturas": {"texto": "How many invoices or delivery notes are keyed in by hand each month?",
                 "opciones": ["None", "Fewer than 50", "Between 50 and 300", "More than 300"]},
    "datos": {"texto": "How many hours a week does each person spend copying data from one program to another?",
              "opciones": ["Less than 1", "Between 1 and 3", "Between 3 and 6", "More than 6"]},
    "informes": {"texto": "How do you get your monthly numbers?",
                 "opciones": ["From a dashboard that updates itself", "Someone builds a spreadsheet",
                              "It takes us days", "We don't have them"]},
    "atencion": {"texto": "How many times a day are you asked the same thing (opening hours, prices, order status)?",
                 "opciones": ["A few", "Some", "Dozens", "Hundreds"]},
    "emails": {"texto": "How do you communicate with your customers?",
               "opciones": ["Automatic emails based on what each one does", "A newsletter sent by hand",
                            "Every now and then", "We don't write to them"]},
    "herramientas": {"texto": "Which tools do you use?", "ayuda": "Tick all that apply.",
                     "opciones": ["Google Workspace or Microsoft 365", "Holded, Odoo, A3 or Sage",
                                  "HubSpot, Pipedrive or Salesforce", "Shopify or WooCommerce", "WhatsApp Business",
                                  "Excel for almost everything", "Nothing in particular"]},
    "prioridad": {"texto": "What matters most to you right now?",
                  "opciones": ["Selling more", "Saving time", "Making fewer mistakes",
                               "Growing without hiring more people"]},
}

BLOQUES_EN = {"visibilidad": "Visibility", "ventas": "Sales", "operaciones": "Operations", "clientes": "Customers"}

FUGAS_EN = {
    "web": ("Website", "it isn't bringing you leads: visitors leave without a trace."),
    "seo": ("SEO", "when people google what you do, you don't show up."),
    "resenas": ("Google reviews", "you have few, or some left unanswered, and that puts off people comparing you."),
    "captacion": ("Replying to new leads", "it takes hours or they get lost, and the customer ends up calling someone else."),
    "seguimiento": ("Sales follow-up", "customer information is scattered and quotes go cold."),
    "facturas": ("Manual invoices", "someone spends hours every month typing in data that already exists."),
    "datos": ("Data copied by hand", "your team acts as the bridge between programs that could talk to each other."),
    "informes": ("Reporting", "getting the monthly numbers takes too long, and they arrive too late to act on."),
    "atencion": ("Customer service", "the same questions eat into your team's time every day."),
    "emails": ("Customer communication", "without automatic emails you miss out on repeat sales and reviews."),
}

AUTOMATIZACIONES_EN = {
    "web": "A website that captures and logs every lead",
    "seo": "Pages and content built to show up on Google",
    "resenas": "Automatic review requests after every sale or service",
    "captacion": "An instant reply to every new lead, logged straight into your CRM",
    "seguimiento": "Automatic quote follow-ups and reminders for your sales team",
    "facturas": "Invoices that read themselves and flow into your accounting software",
    "datos": "Connected software: data moves from one program to the next on its own",
    "informes": "A dashboard with your monthly numbers that updates itself",
    "atencion": "24/7 customer service over WhatsApp or phone",
    "emails": "Automatic emails written and sent to your customers",
}

PERFILES_EN = {
    "piloto": ("On autopilot",
               "You have the basics covered. The next leap isn't getting organised, it's having AI do the work for you."),
    "fugas": ("Solid base, specific leaks",
              "The business runs, but two or three gaps cost you hours and customers every week."),
    "pegamento": ("Your team is the glue",
                  "People join up by hand what your software should be doing on its own."),
    "manos": ("Everything goes through someone's hands",
              "Right now, growing means hiring. With automation, it wouldn't have to."),
}

# Errores que devuelve la API, por si el visitante está en inglés.
API_ERRORS_EN = {
    "Dinos tu nombre.": "Tell us your name.",
    "El email no parece válido.": "That email doesn't look right.",
    "Tienes que aceptar que te enviemos el diagnóstico.": "You need to agree to us sending you the diagnosis.",
    "Has enviado varios diagnósticos seguidos. Prueba dentro de un rato.":
        "You've sent several diagnoses in a row. Try again in a while.",
    "Has analizado muchas webs seguidas. Prueba dentro de un rato.":
        "You've analysed a lot of websites in a row. Try again in a while.",
    "No hemos podido analizar esa web ahora mismo.": "We couldn't analyse that website right now.",
    "faltan respuestas": "some answers are missing",
    "respuestas no válidas": "invalid answers",
}

# ---------------------------------------------------------------------------
# Email
# ---------------------------------------------------------------------------

EMAIL = {
    "es": {
        "subject_hours": "Tu diagnóstico: {min}–{max} horas al mes que se pueden recuperar",
        "subject": "Tu diagnóstico de automatización",
        "eyebrow": "Tu diagnóstico",
        "hours": "{min}–{max} h/mes",
        "hours_sub": "que se pueden recuperar · unos {euros} € al mes",
        "web_h": "Lo que hemos visto en tu web",
        "web_none": "No hemos visto nada grave.",
        "fugas_h": "Lo que te falta",
        "fugas_none": "No hemos visto fugas grandes: tenéis lo básico bien resuelto.",
        "autos_h": "Lo que se podría automatizar",
        "pitch": "Cada empresa lo resuelve de una forma distinta. Si quieres, te preparamos un presupuesto con lo "
                 "que haríamos en tu caso, sin compromiso.",
        "cta": "Pide tu presupuesto sin compromiso",
        "foot": "Las horas son una estimación a partir de tus respuestas y de supuestos medios ({rate} €/h). "
                "Recibes este email porque lo has pedido en sprintiasolutions.com. Puedes responder a este correo "
                "si tienes cualquier duda.",
        "hi": "Hola {first},", "hi_anon": "Hola,",
        "txt_result": "Tu diagnóstico: {nombre}. {frase}",
        "txt_hours": "{min}–{max} h/mes que se pueden recuperar (unos {euros} € al mes).",
        "txt_web": "Lo que hemos visto en tu web ({dominio}, {nota}/100):",
    },
    "en": {
        "subject_hours": "Your diagnosis: {min}–{max} hours a month you could win back",
        "subject": "Your automation diagnosis",
        "eyebrow": "Your diagnosis",
        "hours": "{min}–{max} h/month",
        "hours_sub": "you could win back · about €{euros} a month",
        "web_h": "What we saw on your website",
        "web_none": "We didn't see anything serious.",
        "fugas_h": "What you're missing",
        "fugas_none": "We didn't see any big leaks: you have the basics covered.",
        "autos_h": "What could be automated",
        "pitch": "Every company solves this differently. If you like, we'll put together a no-obligation quote "
                 "with what we'd do in your case.",
        "cta": "Get your no-obligation quote",
        "foot": "The hours are an estimate based on your answers and average assumptions (€{rate}/h). "
                "You're receiving this email because you asked for it at sprintiasolutions.com. "
                "Just reply if you have any questions.",
        "hi": "Hi {first},", "hi_anon": "Hi,",
        "txt_result": "Your diagnosis: {nombre}. {frase}",
        "txt_hours": "{min}–{max} h/month you could win back (about €{euros} a month).",
        "txt_web": "What we saw on your website ({dominio}, {nota}/100):",
    },
}

# ---------------------------------------------------------------------------
# Página
# ---------------------------------------------------------------------------

PAGE = {
    "es": {
        "path": "/diagnostico", "alt_path": "/en/diagnosis", "home": "/",
        "title": "Analiza tu web y tu empresa gratis · Sprintia",
        "description": "Pon tu web y en segundos ves su nota: velocidad, Google, reseñas y formas de contacto. "
                       "Con 9 preguntas más, cuántas horas de trabajo manual podrías automatizar. Gratis.",
        "og_title": "Pon tu web y te decimos qué se te escapa",
        "og_desc": "Análisis de tu web y diagnóstico de automatización gratis en 2 minutos.",
        "og_locale": "es_ES",
        "back": "Volver a la web", "switch": "EN", "switch_title": "Read in English",
        "intro_eyebrow": "Diagnóstico gratuito · 2 minutos",
        "intro_h1": "Pon tu web y te decimos <em>qué se te escapa</em>",
        "intro_lead": "La analizamos en unos segundos (velocidad, Google, reseñas, formas de contacto) y con unas "
                      "preguntas rápidas calculamos cuántas horas de trabajo manual podrías automatizar.",
        "url_ph": "tuempresa.com", "url_aria": "Dirección de tu web", "url_btn": "Analizar mi web",
        "biz_ph": "Nombre del negocio en Google (opcional, para tus reseñas)", "biz_aria": "Nombre del negocio",
        "skip": "No tengo web: hacer el test sin ella",
        "trust": ["Resultado al momento", "Sin registro para verlo", "Gratis"],
        "areas_aria": "Qué analiza",
        "areas": [("Visibilidad", "Web, Google y reseñas"), ("Ventas", "Contactos y seguimiento"),
                  ("Operaciones", "Facturas, datos e informes"), ("Clientes", "Atención y emails")],
        "scan": "Analizando",
        "web_eyebrow": "Tu web", "good": "Lo que está bien", "detected": "Hemos detectado",
        "partial": "Tu web se genera con JavaScript y desde fuera vemos menos de lo normal: puede que algún punto "
                   "esté mejor de lo que sale aquí.",
        "next_eyebrow": "Siguiente paso", "next_h2": "Ahora, lo que no se ve desde fuera", "next_btn": "Seguir",
        "q_back": "Pregunta anterior", "q_next": "Continuar",
        "calc": "Calculando tu diagnóstico",
        "r_eyebrow": "Tu resultado", "r_hours": "Horas recuperables", "r_hm": "h/mes",
        "r_equiv": "equivale a unos", "r_month": "al mes",
        "gate_h2": "Recibe tu diagnóstico",
        "gate_p": "Te enviamos por email lo que le falta a tu empresa y lo que se podría automatizar en tu caso.",
        "f_name": "Nombre", "f_company": "Empresa", "f_email": "Email", "f_phone": "Teléfono", "f_web": "Web",
        "consent_diag": "Acepto que Sprintia me envíe el diagnóstico por email.",
        "consent_mkt": "Quiero recibir de vez en cuando ideas y casos de automatización.",
        "gate_btn": "Recibir mi diagnóstico", "restart": "Repetir el test",
        "quote_eyebrow": "Sin compromiso", "quote_h2": "¿Lo vemos para tu caso?",
        "quote_p": "Te preparamos un presupuesto con lo que haríamos en tu empresa: web, Google, ventas o "
                   "automatizaciones. Te respondemos en uno o dos días.",
        "f_ask": "¿Qué te gustaría resolver primero? (opcional)",
        "consent_quote": "Acepto que Sprintia me contacte para preparar el presupuesto.",
        "quote_btn": "Pedir presupuesto",
        "foot": "Las horas son una estimación a partir de tus respuestas y de supuestos medios (25 €/h de trabajo "
                "administrativo).",
    },
    "en": {
        "path": "/en/diagnosis", "alt_path": "/diagnostico", "home": "/en",
        "title": "Analyse your website and business for free · Sprintia",
        "description": "Enter your website and see its score in seconds: speed, Google, reviews and ways to get in "
                       "touch. With 9 more questions, how many hours of manual work you could automate. Free.",
        "og_title": "Enter your website and we'll show you what's slipping away",
        "og_desc": "Free website analysis and automation diagnosis in 2 minutes.",
        "og_locale": "en_GB",
        "back": "Back to the website", "switch": "ES", "switch_title": "Leer en español",
        "intro_eyebrow": "Free diagnosis · 2 minutes",
        "intro_h1": "Enter your website and we'll show you <em>what's slipping away</em>",
        "intro_lead": "We analyse it in a few seconds (speed, Google, reviews, ways to get in touch) and, with a few "
                      "quick questions, work out how many hours of manual work you could automate.",
        "url_ph": "yourcompany.com", "url_aria": "Your website address", "url_btn": "Analyse my website",
        "biz_ph": "Business name on Google (optional, for your reviews)", "biz_aria": "Business name",
        "skip": "No website? Take the test without it",
        "trust": ["Instant result", "No sign-up to see it", "Free"],
        "areas_aria": "What it analyses",
        "areas": [("Visibility", "Website, Google and reviews"), ("Sales", "Leads and follow-up"),
                  ("Operations", "Invoices, data and reports"), ("Customers", "Service and emails")],
        "scan": "Analysing",
        "web_eyebrow": "Your website", "good": "What's working", "detected": "We detected",
        "partial": "Your site is built with JavaScript, so from outside we see less than usual: some points may be "
                   "better than they look here.",
        "next_eyebrow": "Next step", "next_h2": "Now, what can't be seen from outside", "next_btn": "Continue",
        "q_back": "Previous question", "q_next": "Continue",
        "calc": "Calculating your diagnosis",
        "r_eyebrow": "Your result", "r_hours": "Hours you could win back", "r_hm": "h/month",
        "r_equiv": "worth about", "r_month": "a month",
        "gate_h2": "Get your diagnosis",
        "gate_p": "We'll email you what your company is missing and what could be automated in your case.",
        "f_name": "Name", "f_company": "Company", "f_email": "Email", "f_phone": "Phone", "f_web": "Website",
        "consent_diag": "I agree to Sprintia emailing me the diagnosis.",
        "consent_mkt": "I'd like to receive automation ideas and case studies now and then.",
        "gate_btn": "Get my diagnosis", "restart": "Retake the test",
        "quote_eyebrow": "No obligation", "quote_h2": "Shall we look at your case?",
        "quote_p": "We'll put together a quote with what we'd do in your company: website, Google, sales or "
                   "automations. We reply within one or two days.",
        "f_ask": "What would you like to fix first? (optional)",
        "consent_quote": "I agree to Sprintia contacting me to prepare the quote.",
        "quote_btn": "Request a quote",
        "foot": "The hours are an estimate based on your answers and average assumptions (€25/h of admin work).",
    },
}

# Textos que pinta el JavaScript.
JS = {
    "es": {
        "locale": "es-ES", "lang": "es", "country": "España", "source": "diagnostico",
        "blocks": {"empresa": "Tu empresa", "visibilidad": "Visibilidad", "ventas": "Ventas",
                   "operaciones": "Operaciones", "clientes": "Clientes"},
        "scan_steps": ["Abriendo la web", "Revisando lo que ve Google", "Buscando formas de contacto",
                       "Detectando herramientas", "Buscando vuestra ficha de Google", "Midiendo la velocidad en el móvil"],
        "err_url_empty": "Escribe la dirección de tu web.",
        "err_url_bad": "Eso no parece una dirección web (por ejemplo, tuempresa.com).",
        "err_analyze": "No hemos podido analizar esa web.",
        "err_analyze_tail": " Si quieres, puedes hacer el test sin ella.",
        "finds_one": "Lo que te está costando clientes", "finds_many": "{n} cosas que te están costando clientes",
        "finds_none": "No hemos visto nada grave",
        "next_p": "{n} preguntas rápidas sobre facturas, datos e informes para calcular cuántas horas de trabajo "
                  "manual se podrían recuperar.",
        "calc_err": "No hemos podido calcular el resultado. Inténtalo de nuevo.",
        "blocks_note": "Cuanto más larga la barra, más tiempo o clientes se escapan en esa área.",
        "fuga_1": "punto de mejora", "fuga_n": "puntos de mejora",
        "auto_1": "automatización posible", "auto_n": "automatizaciones posibles",
        "web_chip": "Tu web: ",
        "err_name": "Dinos tu nombre.", "err_email": "Revisa el email.", "err_phone": "Revisa el teléfono.",
        "err_consent": "Marca la casilla para que podamos enviártelo.",
        "err_consent_quote": "Marca la casilla para que podamos contactarte.",
        "sending": "Enviando…", "err_send": "No se pudo enviar.",
        "gate_btn": "Recibir mi diagnóstico", "quote_btn": "Pedir presupuesto",
        "gate_done_h": "Te lo hemos enviado",
        "gate_done_p": "Llega a <b></b> en un minuto. Si no lo ves, mira en promociones o spam.",
        "quote_done_h": "Recibido",
        "quote_done_p": "Revisamos tu diagnóstico y te escribimos en uno o dos días laborables con una propuesta.",
    },
    "en": {
        "locale": "en-GB", "lang": "en", "country": "", "source": "diagnostico-en",
        "blocks": {"empresa": "Your company", **BLOQUES_EN},
        "scan_steps": ["Opening the website", "Checking what Google sees", "Looking for ways to get in touch",
                       "Detecting tools", "Looking for your Google Business Profile", "Measuring mobile speed"],
        "err_url_empty": "Type your website address.",
        "err_url_bad": "That doesn't look like a website address (for example, yourcompany.com).",
        "err_analyze": "We couldn't analyse that website.",
        "err_analyze_tail": " If you like, you can take the test without it.",
        "finds_one": "What's costing you customers", "finds_many": "{n} things costing you customers",
        "finds_none": "We didn't see anything serious",
        "next_p": "{n} quick questions about invoices, data and reports to work out how many hours of manual work "
                  "you could win back.",
        "calc_err": "We couldn't calculate the result. Please try again.",
        "blocks_note": "The longer the bar, the more time or customers you're losing in that area.",
        "fuga_1": "area to improve", "fuga_n": "areas to improve",
        "auto_1": "possible automation", "auto_n": "possible automations",
        "web_chip": "Your website: ",
        "err_name": "Tell us your name.", "err_email": "Check your email.", "err_phone": "Check your phone number.",
        "err_consent": "Tick the box so we can send it to you.",
        "err_consent_quote": "Tick the box so we can contact you.",
        "sending": "Sending…", "err_send": "Couldn't send it.",
        "gate_btn": "Get my diagnosis", "quote_btn": "Request a quote",
        "gate_done_h": "We've sent it",
        "gate_done_p": "It'll reach <b></b> in a minute. If you can't see it, check your promotions or spam folder.",
        "quote_done_h": "Received",
        "quote_done_p": "We'll review your diagnosis and write to you within one or two working days with a proposal.",
    },
}
