"""Diagnóstico de automatización (sprintiasolutions.com/diagnostico).

Test de 15 preguntas para empresas que quieren automatizar. Sirve para captar
contactos sin vender nada en la primera interacción:

1. En pantalla: perfil, horas recuperables al mes y cuatro barras por bloque.
2. Por email (pide nombre, email y empresa): un avance de lo que le falta y
   de lo que se podría automatizar, sin el cómo, con un botón para pedir
   presupuesto sin compromiso.
3. El presupuesto entra por el formulario público de siempre (`POST
   /api/leads`, `source=diagnostico`), así que llega al CMS, a Brevo y al
   email interno igual que cualquier solicitud de la landing.

Si el visitante pone su web, `app/web_audit.py` la analiza primero (nota,
hallazgos) y rellena solo las preguntas que se ven desde fuera; el test baja
de 15 a 9-10 preguntas y el email incluye lo que hemos visto en su web.

Las preguntas, la puntuación y los textos viven aquí; la página solo pinta.
Las respuestas viajan como una lista de índices (`1.0.2.3…`) que no contiene
datos personales, por eso pueden ir en la URL del botón del email.
"""
from __future__ import annotations

import html
import logging
import pathlib
import re
import secrets
import time

import httpx
from fastapi import APIRouter, BackgroundTasks, Request
from fastapi.responses import HTMLResponse, JSONResponse

from jinja2 import Environment, FileSystemLoader, select_autoescape

from . import diagnostico_i18n as i18n
from . import web_audit
from .brevo import send_transactional_email
from .config import settings

log = logging.getLogger(__name__)

router = APIRouter(tags=["diagnostico"])

PUBLIC_URL = "https://sprintiasolutions.com/diagnostico"
PUBLIC_URLS = {"es": PUBLIC_URL, "en": "https://sprintiasolutions.com/en/diagnosis"}
_TEMPLATES = Environment(loader=FileSystemLoader(pathlib.Path(__file__).parent / "templates"),
                         autoescape=select_autoescape(["html"]))
_TEMPLATES.policies["json.dumps_kwargs"] = {"ensure_ascii": False}
_PAGE_CACHE: dict[str, str] = {}

# Coste por hora de trabajo administrativo que se usa para pasar horas a euros.
COSTE_HORA_EUR = 25

# ---------------------------------------------------------------------------
# Preguntas
# ---------------------------------------------------------------------------
# Cada pregunta de "fuga" tiene un área y cuatro opciones que suman 0..3.
# Las de perfil (sector, tamaño, rol), herramientas y prioridad no puntúan.

BLOQUES = [
    {"id": "visibilidad", "nombre": "Visibilidad"},
    {"id": "ventas", "nombre": "Ventas"},
    {"id": "operaciones", "nombre": "Operaciones"},
    {"id": "clientes", "nombre": "Clientes"},
]

QUESTIONS: list[dict] = [
    {
        "id": "sector", "bloque": "empresa",
        "texto": "¿A qué se dedica tu empresa?",
        "opciones": ["Servicios profesionales", "Comercio o ecommerce", "Industria o distribución",
                     "Hostelería o turismo", "Salud o clínicas", "Inmobiliaria", "Formación", "Otro"],
    },
    {
        "id": "tamano", "bloque": "empresa",
        "texto": "¿Cuántas personas sois?",
        "opciones": ["1–9", "10–49", "50–249", "250 o más"],
    },
    {
        "id": "rol", "bloque": "empresa",
        "texto": "¿Cuál es tu papel?",
        "opciones": ["Dirección o propiedad", "Ventas o marketing", "Operaciones o administración",
                     "Tecnología", "Otro"],
    },
    {
        "id": "web", "bloque": "visibilidad", "area": "web",
        "texto": "Tu web hoy…",
        "opciones": ["Capta contactos y los registra sola", "Está bien, pero no capta",
                     "Se ha quedado antigua", "No tenemos web"],
    },
    {
        "id": "seo", "bloque": "visibilidad", "area": "seo",
        "texto": "Cuando alguien busca en Google lo que hacéis, ¿os encuentra?",
        "opciones": ["Sí, en los primeros resultados", "Solo si busca nuestro nombre",
                     "Casi nunca", "No lo sé"],
    },
    {
        "id": "resenas", "bloque": "visibilidad", "area": "resenas",
        "texto": "¿Cómo estáis en Google en reseñas y valoraciones?",
        "opciones": ["Muchas y buenas, y las pedimos a cada cliente", "Pocas, pero buenas",
                     "Pocas y alguna mala sin responder", "No tenemos ficha o no lo sé"],
    },
    {
        "id": "captacion", "bloque": "ventas", "area": "captacion",
        "texto": "Cuando entra un posible cliente (web, WhatsApp, teléfono), ¿qué pasa?",
        "opciones": ["Se le responde solo, en minutos", "Alguien lo ve en unas horas",
                     "A veces se pierde", "No lo sabemos"],
    },
    {
        "id": "seguimiento", "bloque": "ventas", "area": "seguimiento",
        "texto": "¿Dónde está la información de tus clientes?",
        "opciones": ["En un CRM que usamos bien", "En un CRM a medias",
                     "En Excel o en notas", "En la cabeza de cada comercial"],
    },
    {
        "id": "facturas", "bloque": "operaciones", "area": "facturas",
        "texto": "¿Cuántas facturas o albaranes se pasan a mano al mes?",
        "opciones": ["Ninguna", "Menos de 50", "Entre 50 y 300", "Más de 300"],
    },
    {
        "id": "datos", "bloque": "operaciones", "area": "datos",
        "texto": "¿Cuántas horas a la semana pasa cada persona copiando datos de un programa a otro?",
        "opciones": ["Menos de 1", "Entre 1 y 3", "Entre 3 y 6", "Más de 6"],
    },
    {
        "id": "informes", "bloque": "operaciones", "area": "informes",
        "texto": "¿Cómo sacáis los números del mes?",
        "opciones": ["Con un panel que se actualiza solo", "Alguien monta un Excel",
                     "Tardamos días", "No los tenemos"],
    },
    {
        "id": "atencion", "bloque": "clientes", "area": "atencion",
        "texto": "¿Cuántas veces al día os preguntan lo mismo (horarios, precios, estado del pedido)?",
        "opciones": ["Pocas", "Algunas", "Decenas", "Cientos"],
    },
    {
        "id": "emails", "bloque": "clientes", "area": "emails",
        "texto": "¿Cómo os comunicáis con vuestros clientes?",
        "opciones": ["Con emails automáticos según lo que hace cada uno", "Newsletter a mano",
                     "De vez en cuando", "No les escribimos"],
    },
    {
        "id": "herramientas", "bloque": "empresa", "multi": True,
        "texto": "¿Qué herramientas usáis?",
        "ayuda": "Marca todas las que apliquen.",
        "opciones": ["Google Workspace o Microsoft 365", "Holded, Odoo, A3 o Sage",
                     "HubSpot, Pipedrive o Salesforce", "Shopify o WooCommerce",
                     "WhatsApp Business", "Excel para casi todo", "Nada en especial"],
    },
    {
        "id": "prioridad", "bloque": "empresa",
        "texto": "¿Qué te importa más ahora mismo?",
        "opciones": ["Vender más", "Ahorrar tiempo", "Cometer menos errores",
                     "Crecer sin contratar más gente"],
    },
]

_Q = {q["id"]: i for i, q in enumerate(QUESTIONS)}
AREAS = [q["area"] for q in QUESTIONS if q.get("area")]
_AREA_BLOQUE = {q["area"]: q["bloque"] for q in QUESTIONS if q.get("area")}

# Qué le falta (email). Solo se nombra un área si suma 2 o más puntos.
FUGAS = {
    "web": ("Web", "no te está trayendo contactos: quien llega, se va sin dejar rastro."),
    "seo": ("SEO", "cuando buscan lo que haces en Google, no apareces."),
    "resenas": ("Reseñas en Google", "tienes pocas o sin responder, y eso frena a quien te compara."),
    "captacion": ("Respuesta a contactos nuevos", "tardáis horas o se pierden, y el cliente acaba llamando a otro."),
    "seguimiento": ("Seguimiento comercial", "la información de clientes está repartida y los presupuestos se enfrían."),
    "facturas": ("Facturas a mano", "alguien pasa horas cada mes tecleando datos que ya existen."),
    "datos": ("Datos copiados a mano", "tu equipo hace de puente entre programas que podrían hablar entre sí."),
    "informes": ("Informes", "sacar los números del mes cuesta demasiado y llegan tarde para decidir."),
    "atencion": ("Atención al cliente", "las preguntas repetidas se comen tiempo del equipo cada día."),
    "emails": ("Comunicación con clientes", "sin emails automáticos se escapan ventas repetidas y reseñas."),
}

# Lo que se podría automatizar (email). Titulares, sin el cómo.
AUTOMATIZACIONES = {
    "web": "Una web que capta y registra cada contacto",
    "seo": "Páginas y contenidos pensados para aparecer en Google",
    "resenas": "Petición automática de reseñas después de cada venta o servicio",
    "captacion": "Respuesta inmediata a cada contacto nuevo, que entra solo en tu CRM",
    "seguimiento": "Seguimiento automático de presupuestos y avisos al comercial",
    "facturas": "Facturas que se leen solas y pasan a tu programa de contabilidad",
    "datos": "Programas conectados: los datos viajan solos de uno a otro",
    "informes": "Un panel con los números del mes que se actualiza solo",
    "atencion": "Atención al cliente 24 h por WhatsApp o teléfono",
    "emails": "Creación y envío automático de emails a tus clientes",
}

# Orden de prioridad de las áreas según lo que quiere quitarse de encima.
_PREFERENCIA = {
    0: ["captacion", "seguimiento", "seo", "web", "resenas", "emails", "atencion", "datos", "facturas", "informes"],
    1: ["datos", "facturas", "informes", "atencion", "captacion", "seguimiento", "emails", "resenas", "seo", "web"],
    2: ["datos", "facturas", "informes", "seguimiento", "captacion", "atencion", "emails", "resenas", "web", "seo"],
    3: ["atencion", "datos", "facturas", "captacion", "seguimiento", "informes", "emails", "resenas", "seo", "web"],
}

PERFILES = [
    (25, "piloto", "Piloto automático",
     "Tenéis lo básico resuelto. El siguiente salto ya no es ordenar, es que la IA trabaje por vosotros."),
    (50, "fugas", "Buena base, fugas concretas",
     "La empresa funciona, pero dos o tres agujeros os cuestan horas y clientes cada semana."),
    (75, "pegamento", "Tu equipo hace de pegamento",
     "Las personas unen a mano lo que los programas deberían hacer solos."),
    (101, "manos", "Todo pasa por las manos de alguien",
     "Crecer ahora mismo significa contratar. Automatizando, no haría falta."),
]

# Supuestos para estimar horas al mes.
_PLANTILLA = [5, 25, 120, 300]           # personas (punto medio por tramo)
_HORAS_COPIA_SEMANA = [0.5, 2, 4.5, 8]   # por persona afectada
_FACTURAS_MES = [0, 30, 150, 500]        # x 4 minutos
_PREGUNTAS_DIA = [3, 15, 50, 200]        # x 2 minutos x 21 días
_HORAS_INFORMES = [0, 6, 16, 24]
_HORAS_SEGUIMIENTO = [0, 4, 10, 16]


class InvalidAnswers(ValueError):
    pass


def parse_answers(raw) -> list[int]:
    """Acepta lista de ints o el código `1.0.2…` y valida rangos."""
    if isinstance(raw, str):
        parts = [p for p in raw.strip().split(".")]
        try:
            raw = [int(p) for p in parts]
        except ValueError as e:
            raise InvalidAnswers("respuestas no válidas") from e
    if not isinstance(raw, list) or len(raw) != len(QUESTIONS):
        raise InvalidAnswers("faltan respuestas")
    out: list[int] = []
    for q, v in zip(QUESTIONS, raw):
        if isinstance(v, bool) or not isinstance(v, int):
            raise InvalidAnswers("respuestas no válidas")
        n = len(q["opciones"])
        limit = (1 << n) if q.get("multi") else n
        if not 0 <= v < limit:
            raise InvalidAnswers("respuestas no válidas")
        out.append(v)
    return out


def encode_answers(answers: list[int]) -> str:
    return ".".join(str(a) for a in answers)


def _round_to(x: float, step: int) -> int:
    return int(round(x / step) * step)


def compute(answers: list[int]) -> dict:
    a = {q["id"]: answers[i] for i, q in enumerate(QUESTIONS)}
    puntos = {area: a[area] for area in AREAS}  # el índice de opción ES la puntuación 0..3
    maximo = 3 * len(AREAS)
    fuga_pct = round(100 * sum(puntos.values()) / maximo)

    perfil = next(p for p in PERFILES if fuga_pct < p[0])

    bloques = []
    for b in BLOQUES:
        areas = [ar for ar in AREAS if _AREA_BLOQUE[ar] == b["id"]]
        pct = round(100 * sum(puntos[ar] for ar in areas) / (3 * len(areas)))
        bloques.append({"id": b["id"], "nombre": b["nombre"], "pct": pct})

    plantilla = _PLANTILLA[a["tamano"]]
    oficina = plantilla * 0.4
    horas = (
        _HORAS_COPIA_SEMANA[a["datos"]] * oficina * 0.5 * 4.3 * 0.5
        + _FACTURAS_MES[a["facturas"]] * 4 / 60
        + _PREGUNTAS_DIA[a["atencion"]] * 2 / 60 * 21 * 0.6
        + _HORAS_INFORMES[a["informes"]] * 0.8
        + _HORAS_SEGUIMIENTO[a["seguimiento"]]
    )
    h_min = max(0, _round_to(horas * 0.8, 5))
    h_max = max(h_min + 5, _round_to(horas * 1.2, 5))
    euros = _round_to((h_min + h_max) / 2 * COSTE_HORA_EUR, 50)

    pref = _PREFERENCIA[a["prioridad"]]
    orden = sorted(AREAS, key=lambda ar: (-puntos[ar], pref.index(ar)))
    fugas = [{"area": ar, "titulo": FUGAS[ar][0], "texto": FUGAS[ar][1]}
             for ar in orden if puntos[ar] >= 2][:5]
    areas_auto = [ar for ar in orden if puntos[ar] >= 1][:6]
    automatizaciones = [AUTOMATIZACIONES[ar] for ar in areas_auto]

    herramientas = [opt for j, opt in enumerate(QUESTIONS[_Q["herramientas"]]["opciones"])
                    if a["herramientas"] & (1 << j)]

    # Nota del contacto para priorizar llamadas.
    nota = [0, 2, 3, 3][a["tamano"]] + (2 if a["rol"] == 0 else 0)
    nota += 3 if fuga_pct >= 75 else 2 if fuga_pct >= 50 else 1 if fuga_pct >= 25 else 0
    temperatura = "caliente" if nota >= 6 else "templado" if nota >= 3 else "frío"

    def label(qid: str) -> str:
        return QUESTIONS[_Q[qid]]["opciones"][a[qid]]

    return {
        "perfil": {"id": perfil[1], "nombre": perfil[2], "frase": perfil[3]},
        "fuga_pct": fuga_pct,
        "horas": {"min": h_min, "max": h_max},
        "euros_mes": euros,
        "bloques": bloques,
        "fugas": fugas,
        "automatizaciones": automatizaciones,
        "areas_auto": areas_auto,
        "contexto": {
            "sector": label("sector"), "tamano": label("tamano"), "rol": label("rol"),
            "prioridad": label("prioridad"), "herramientas": herramientas,
        },
        "temperatura": temperatura,
        "codigo": encode_answers(answers),
    }


def _lang(raw) -> str:
    return raw if raw in i18n.LANGS else "es"


def localize(r: dict, lang: str) -> dict:
    """El resultado con los textos que ve el visitante en su idioma (el cálculo es el mismo)."""
    if lang != "en":
        return r
    nombre, frase = i18n.PERFILES_EN[r["perfil"]["id"]]
    return {
        **r,
        "perfil": {"id": r["perfil"]["id"], "nombre": nombre, "frase": frase},
        "bloques": [{**b, "nombre": i18n.BLOQUES_EN[b["id"]]} for b in r["bloques"]],
        "fugas": [{"area": f["area"], "titulo": i18n.FUGAS_EN[f["area"]][0], "texto": i18n.FUGAS_EN[f["area"]][1]}
                  for f in r["fugas"]],
        "automatizaciones": [i18n.AUTOMATIZACIONES_EN[ar] for ar in r["areas_auto"]],
    }


def public_result(r: dict, lang: str = "es") -> dict:
    """Lo que ve la pantalla: sin el detalle, que va por email. El resumen va a Mario, en español."""
    v = localize(r, lang)
    return {
        "perfil": v["perfil"],
        "fuga_pct": r["fuga_pct"],
        "horas": r["horas"],
        "euros_mes": r["euros_mes"],
        "bloques": v["bloques"],
        "n_fugas": len(r["fugas"]),
        "n_automatizaciones": len(r["automatizaciones"]),
        "codigo": r["codigo"],
        "resumen": summary_text(r),
    }


def summary_text(r: dict, audit: dict | None = None) -> str:
    """Resumen en texto plano para el mensaje del presupuesto (lo lee Mario)."""
    c = r["contexto"]
    lines = [
        "— Diagnóstico —",
        *(web_audit.summary_lines(audit) if audit else []),
        f"Perfil: {r['perfil']['nombre']} (fuga {r['fuga_pct']} %)",
        f"Horas recuperables: {r['horas']['min']}–{r['horas']['max']} h/mes (≈ {r['euros_mes']} €/mes)",
        f"Sector: {c['sector']} · Tamaño: {c['tamano']} · Rol: {c['rol']}",
        f"Prioridad: {c['prioridad']}",
    ]
    if c["herramientas"]:
        lines.append("Herramientas: " + ", ".join(c["herramientas"]))
    if r["fugas"]:
        lines.append("Fugas: " + "; ".join(f["titulo"] for f in r["fugas"]))
    lines.append("Bloques: " + ", ".join(f"{b['nombre']} {b['pct']} %" for b in r["bloques"]))
    return "\n".join(lines)


def audit_view(audit: dict, lang: str = "es") -> dict:
    """Nivel, hallazgos y puntos fuertes en el idioma del visitante."""
    if lang != "en":
        return {"nivel": audit["nivel"], "bien": audit["bien"],
                "hallazgos": [{k: f[k] for k in ("nivel", "area", "titulo", "detalle")} for f in audit["hallazgos"]]}
    return {
        "nivel": audit.get("nivel_en") or audit["nivel"],
        "bien": audit.get("bien_en") or audit["bien"],
        "hallazgos": [{"nivel": f["nivel"], "area": f["area"], "titulo": f.get("titulo_en") or f["titulo"],
                       "detalle": f.get("detalle_en", "")} for f in audit["hallazgos"]],
    }


def public_audit(audit: dict, audit_id: str, lang: str = "es") -> dict:
    """Lo que ve la pantalla del análisis de la web."""
    return {
        "id": audit_id, "url": audit["url"], "dominio": audit["dominio"],
        "nota": audit["nota"], **audit_view(audit, lang),
        "detectado": audit["detectado"], "auto": audit["auto"], "parcial": audit["parcial"],
        "con_google": audit.get("velocidad") is not None, "resumen_web": "\n".join(web_audit.summary_lines(audit)),
    }


# ---------------------------------------------------------------------------
# Email del diagnóstico
# ---------------------------------------------------------------------------

def build_email(name: str, r: dict, audit: dict | None = None, lang: str = "es") -> tuple[str, str, str]:
    lang = _lang(lang)
    L = i18n.EMAIL[lang]
    first = (name or "").split()[0] if name else ""
    h = r["horas"]
    r = localize(r, lang)
    subject = L["subject_hours"].format(**h) if h["max"] >= 15 else L["subject"]
    cta = f"{PUBLIC_URLS[lang]}?r={r['codigo']}#presupuesto"
    e = html.escape
    euros_txt = f"{r['euros_mes']:,}"
    if lang == "es":
        euros_txt = euros_txt.replace(",", ".")

    fugas_html = "".join(
        f"<li style='margin:0 0 10px'><b>{e(f['titulo'])}:</b> {e(f['texto'])}</li>" for f in r["fugas"]
    ) or f"<li>{L['fugas_none']}</li>"
    autos_html = "".join(f"<li style='margin:0 0 8px'>{e(t)}</li>" for t in r["automatizaciones"])
    web_html, web_txt = "", []
    if audit:
        av = audit_view(audit, lang)
        malos = av["hallazgos"][:5]
        items = "".join(f"<li style='margin:0 0 8px'>{e(f['titulo'])}</li>" for f in malos) \
            or f"<li>{L['web_none']}</li>"
        web_html = (
            f"<h2 style=\"font-size:16px;margin:0 0 4px\">{L['web_h']}</h2>"
            f"<p style=\"margin:0 0 10px;font-size:14px;color:#7d7c78\">{e(audit['dominio'])} · "
            f"<b style=\"color:#15161a\">{audit['nota']}/100</b> ({e(av['nivel'].lower())})</p>"
            f"<ul style=\"margin:0 0 20px;padding-left:20px;font-size:15px;line-height:1.5;color:#3a3b40\">{items}</ul>"
        )
        web_txt = [L["txt_web"].format(dominio=audit["dominio"], nota=audit["nota"]),
                   *[f"- {f['titulo']}" for f in malos], ""]

    html_body = f"""<!doctype html><html lang="{lang}"><body style="margin:0;background:#f4efe6;font-family:-apple-system,'Segoe UI',Helvetica,Arial,sans-serif;color:#15161a">
<div style="max-width:560px;margin:0 auto;padding:28px 18px">
  <div style="font-weight:700;font-size:18px;letter-spacing:-.01em;margin-bottom:18px">Sprintia</div>
  <div style="background:#fafaf7;border:1px solid rgba(21,22,26,.10);border-radius:20px;padding:28px">
    <p style="margin:0 0 6px;font-size:12px;letter-spacing:.14em;text-transform:uppercase;color:#7d7c78">{L['eyebrow']}</p>
    <h1 style="margin:0 0 6px;font-family:Georgia,serif;font-weight:400;font-size:30px;line-height:1.1">{e(r['perfil']['nombre'])}</h1>
    <p style="margin:0 0 18px;color:#3a3b40;font-size:15px;line-height:1.5">{e(r['perfil']['frase'])}</p>
    <div style="background:#dee3ff;border-radius:14px;padding:16px 18px;margin:0 0 22px">
      <div style="font-size:26px;font-weight:700;color:#1f37c4">{L['hours'].format(**h)}</div>
      <div style="font-size:14px;color:#3a3b40">{L['hours_sub'].format(euros=euros_txt)}</div>
    </div>
    {web_html}<h2 style="font-size:16px;margin:0 0 10px">{L['fugas_h']}</h2>
    <ul style="margin:0 0 20px;padding-left:20px;font-size:15px;line-height:1.5;color:#3a3b40">{fugas_html}</ul>
    <h2 style="font-size:16px;margin:0 0 10px">{L['autos_h']}</h2>
    <ul style="margin:0 0 24px;padding-left:20px;font-size:15px;line-height:1.5;color:#3a3b40">{autos_html}</ul>
    <p style="margin:0 0 16px;font-size:15px;line-height:1.5;color:#3a3b40">{L['pitch']}</p>
    <a href="{e(cta)}" style="display:inline-block;background:#2d4cff;color:#fff;text-decoration:none;font-weight:600;padding:14px 22px;border-radius:999px;font-size:15px">{L['cta']} →</a>
  </div>
  <p style="font-size:12px;color:#7d7c78;line-height:1.5;margin:18px 4px 0">{L['foot'].format(rate=COSTE_HORA_EUR)}</p>
</div></body></html>"""

    text_lines = [
        L["hi"].format(first=first) if first else L["hi_anon"],
        "",
        L["txt_result"].format(nombre=r["perfil"]["nombre"], frase=r["perfil"]["frase"]),
        L["txt_hours"].format(euros=euros_txt, **h),
        "",
        *web_txt,
        L["fugas_h"] + ":",
        *[f"- {f['titulo']}: {f['texto']}" for f in r["fugas"]],
        "",
        L["autos_h"] + ":",
        *[f"- {t}" for t in r["automatizaciones"]],
        "",
        f"{L['cta']}: {cta}",
        "",
        "Sprintia",
    ]
    return subject, "\n".join(text_lines), html_body


# ---------------------------------------------------------------------------
# Brevo: contacto con los datos del diagnóstico
# ---------------------------------------------------------------------------

_BREVO = "https://api.brevo.com/v3"
_ATTRS = ("DIAG_PERFIL", "DIAG_HORAS", "DIAG_FUGAS", "DIAG_TAMANO", "DIAG_TEMPERATURA", "DIAG_FECHA",
          "DIAG_WEB", "DIAG_WEB_NOTA")
_attrs_ready = False


def _brevo_headers() -> dict:
    return {"accept": "application/json", "content-type": "application/json",
            "api-key": settings.brevo_api_key.strip()}


def _ensure_attrs() -> None:
    global _attrs_ready
    if _attrs_ready:
        return
    for name in _ATTRS:
        try:
            httpx.post(f"{_BREVO}/contacts/attributes/normal/{name}", json={"type": "text"},
                       headers=_brevo_headers(), timeout=8)
        except Exception:
            pass
    _attrs_ready = True


def _sync_contact(name: str, email: str, company: str, marketing: bool, r: dict, audit: dict | None = None) -> None:
    if not settings.brevo_api_key.strip():
        return
    _ensure_attrs()
    first, _, last = (name or "").strip().partition(" ")
    attributes: dict = {
        "FIRSTNAME": first, "OPT_IN": bool(marketing),
        "DIAG_PERFIL": r["perfil"]["nombre"],
        "DIAG_HORAS": f"{r['horas']['min']}-{r['horas']['max']}",
        "DIAG_FUGAS": ", ".join(f["area"] for f in r["fugas"]),
        "DIAG_TAMANO": r["contexto"]["tamano"],
        "DIAG_TEMPERATURA": r["temperatura"],
        "DIAG_FECHA": time.strftime("%Y-%m-%d"),
    }
    if audit:
        attributes["DIAG_WEB"] = audit["dominio"]
        attributes["DIAG_WEB_NOTA"] = str(audit["nota"])
    if last:
        attributes["LASTNAME"] = last
    if company and settings.brevo_company_attribute:
        attributes[settings.brevo_company_attribute] = company[:200]
    if settings.brevo_sector_attribute:
        attributes[settings.brevo_sector_attribute] = r["contexto"]["sector"]
    payload: dict = {"email": email, "attributes": attributes, "updateEnabled": True}
    list_ids = [int(x) for x in re.findall(r"\d+", settings.brevo_diag_list_ids or settings.brevo_list_ids)]
    if list_ids:
        payload["listIds"] = list_ids
    try:
        resp = httpx.post(f"{_BREVO}/contacts", json=payload, headers=_brevo_headers(), timeout=10)
        if resp.status_code >= 400:
            log.warning("Brevo contacto diagnóstico status=%s body=%s", resp.status_code, resp.text[:300])
    except Exception:
        log.exception("No se pudo sincronizar el contacto del diagnóstico con Brevo")


def _deliver(name: str, email: str, company: str, marketing: bool, r: dict, audit: dict | None = None,
             lang: str = "es") -> None:
    subject, text, html_body = build_email(name, r, audit, lang)
    if not send_transactional_email(to_email=email, to_name=name, subject=subject, text=text,
                                    html_body=html_body, tag="diagnostico"):
        log.warning("Email de diagnóstico no enviado a %s (¿falta BREVO_SENDER_EMAIL?)", email)
    _sync_contact(name, email, company, marketing, r, audit)
    to = settings.lead_notify_email_to.strip()
    if to:
        internal = (
            f"Nuevo diagnóstico ({r['temperatura']})\n"
            f"Nombre: {name}\nEmail: {email}\nEmpresa: {company or '—'}\n"
            f"Consentimiento marketing: {'sí' if marketing else 'no'}\n"
            f"Idioma: {'inglés (/en/diagnosis)' if lang == 'en' else 'español'}\n\n"
            f"{summary_text(r, audit)}"
        )
        for rcpt in [p.strip() for p in to.replace(";", ",").split(",") if p.strip()]:
            send_transactional_email(
                to_email=rcpt,
                subject=f"Nuevo diagnóstico Sprintia: {company or name} ({r['temperatura']})",
                text=internal,
                html_body="<p>" + html.escape(internal).replace("\n", "<br>") + "</p>",
                tag="diagnostico-interno",
            )


# ---------------------------------------------------------------------------
# Rutas
# ---------------------------------------------------------------------------

_HITS: dict[str, list[float]] = {}
_MAX_HITS, _WINDOW_S = 5, 3600
# Análisis de webs: cuesta llamadas a Google, así que va con su propio límite.
_AUDIT_HITS: dict[str, list[float]] = {}
_MAX_AUDITS = 20
# Auditorías hechas, para que el email use los hallazgos del servidor y no
# lo que mande el navegador (si no, cualquiera podría meter texto en el email).
_AUDITS: dict[str, tuple[float, dict]] = {}
_AUDIT_TTL = 48 * 3600
_EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


def _ip(request: Request) -> str:
    xff = request.headers.get("x-forwarded-for", "").split(",")[0].strip()
    return (xff or (request.client.host if request.client else ""))[:64]


def _limited(ip: str, store: dict | None = None, limit: int | None = None) -> bool:
    store = _HITS if store is None else store
    limit = _MAX_HITS if limit is None else limit
    if not ip:
        return False
    cutoff = time.monotonic() - _WINDOW_S
    hits = [t for t in store.get(ip, []) if t > cutoff]
    if len(store) > 10_000:
        store.clear()
    store[ip] = hits
    return len(hits) >= limit


def _hit(ip: str, store: dict | None = None) -> None:
    if ip:
        (_HITS if store is None else store).setdefault(ip, []).append(time.monotonic())


def _save_audit(audit: dict) -> str:
    now = time.time()
    if len(_AUDITS) > 5000:
        for k in [k for k, (ts, _) in _AUDITS.items() if now - ts > _AUDIT_TTL]:
            _AUDITS.pop(k, None)
        if len(_AUDITS) > 5000:
            _AUDITS.clear()
    audit_id = secrets.token_urlsafe(9)
    _AUDITS[audit_id] = (now, audit)
    return audit_id


def get_audit(audit_id) -> dict | None:
    hit = _AUDITS.get(str(audit_id or ""))
    if hit and time.time() - hit[0] < _AUDIT_TTL:
        return hit[1]
    return None


def _questions(lang: str) -> list[dict]:
    out = []
    for q in QUESTIONS:
        item = {k: q[k] for k in ("id", "bloque", "texto", "opciones", "multi", "ayuda") if k in q}
        if lang == "en":
            item.update(i18n.QUESTIONS_EN[q["id"]])
        out.append(item)
    return out


def render_page(lang: str = "es") -> str:
    lang = _lang(lang)
    if lang not in _PAGE_CACHE:
        data = {"questions": _questions(lang), "sector_es": QUESTIONS[_Q["sector"]]["opciones"],
                "t": i18n.JS[lang]}
        _PAGE_CACHE[lang] = _TEMPLATES.get_template("diagnostico.html").render(
            lang=lang, u=i18n.PAGE[lang], data=data)
    return _PAGE_CACHE[lang]


@router.get("/diagnostico", response_class=HTMLResponse)
async def diagnostico_page() -> HTMLResponse:
    return HTMLResponse(render_page("es"))


@router.get("/en/diagnosis", response_class=HTMLResponse)
async def diagnosis_page_en() -> HTMLResponse:
    return HTMLResponse(render_page("en"))


def _err(msg: str, status: int, lang: str) -> JSONResponse:
    if lang == "en":
        msg = i18n.API_ERRORS_EN.get(msg) or web_audit.error_en(msg)
    return JSONResponse({"error": msg}, status_code=status)


async def _json(request: Request) -> dict:
    try:
        body = await request.json()
    except Exception:
        return {}
    return body if isinstance(body, dict) else {}


@router.post("/api/diagnostico/resultado")
async def diagnostico_resultado(request: Request):
    body = await _json(request)
    lang = _lang(body.get("lang"))
    try:
        r = compute(parse_answers(body.get("answers")))
    except InvalidAnswers as e:
        return _err(str(e), 400, lang)
    return public_result(r, lang)


@router.post("/api/diagnostico/analizar")
async def diagnostico_analizar(request: Request):
    body = await _json(request)
    lang = _lang(body.get("lang"))
    ip = _ip(request)
    if _limited(ip, _AUDIT_HITS, _MAX_AUDITS):
        return _err("Has analizado muchas webs seguidas. Prueba dentro de un rato.", 429, lang)
    negocio = str(body.get("negocio") or "").strip()[:120]
    try:
        url = web_audit.normalize_url(body.get("url"))
    except web_audit.AuditError as e:
        return _err(str(e), 400, lang)
    _hit(ip, _AUDIT_HITS)
    try:
        audit = await web_audit.analyze(url, negocio)
    except web_audit.AuditError as e:
        log.info("análisis web fallido url=%s motivo=%s", url, e)
        return _err(str(e), 422, lang)
    except Exception:
        log.exception("análisis web roto url=%s", url)
        return _err("No hemos podido analizar esa web ahora mismo.", 502, lang)
    log.info("análisis web url=%s nota=%s lang=%s", audit["url"], audit["nota"], lang)
    return public_audit(audit, _save_audit(audit), lang)


@router.post("/api/diagnostico/enviar")
async def diagnostico_enviar(request: Request, background_tasks: BackgroundTasks):
    body = await _json(request)
    lang = _lang(body.get("lang"))
    ip = _ip(request)
    if str(body.get("website") or "").strip():  # honeypot
        log.warning("diagnóstico descartado por honeypot ip=%s", ip)
        _hit(ip)
        return {"ok": True}
    if _limited(ip):
        return _err("Has enviado varios diagnósticos seguidos. Prueba dentro de un rato.", 429, lang)
    name = str(body.get("name") or "").strip()[:120]
    email = str(body.get("email") or "").strip().lower()[:200]
    company = str(body.get("company") or "").strip()[:200]
    marketing = bool(body.get("marketing_consent"))
    if len(name) < 2:
        return _err("Dinos tu nombre.", 400, lang)
    if not _EMAIL_RE.match(email):
        return _err("El email no parece válido.", 400, lang)
    if not body.get("consent"):
        return _err("Tienes que aceptar que te enviemos el diagnóstico.", 400, lang)
    try:
        r = compute(parse_answers(body.get("answers")))
    except InvalidAnswers as e:
        return _err(str(e), 400, lang)
    audit = get_audit(body.get("audit_id"))
    _hit(ip)
    log.info("diagnóstico enviado email=%s perfil=%s temp=%s web=%s lang=%s", email, r["perfil"]["id"],
             r["temperatura"], audit["dominio"] if audit else "-", lang)
    background_tasks.add_task(_deliver, name, email, company, marketing, r, audit, lang=lang)
    return {"ok": True}
