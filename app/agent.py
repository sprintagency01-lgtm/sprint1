"""Agente LLM con function calling (OpenAI).

El agente recibe el mensaje del cliente + el historial + el prompt del tenant
y decide:
- Contestar texto directamente.
- Llamar a una de las funciones (consultar_disponibilidad, crear_reserva, ...).
- Ofrecer OPCIONES CLICABLES al cliente (ofrecer_huecos, ofrecer_equipo,
  pedir_confirmacion). Estas tools terminan el turno inmediatamente y devuelven
  un `AgentReply` con `interactive` poblado — el webhook las convierte a un
  mensaje de lista o botones en WhatsApp.

Las funciones se ejecutan en el backend contra Google Calendar (seguro:
el LLM NO escribe nunca al calendario directamente, todo pasa por funciones
que validan entrada).

Modelo por defecto: gpt-4o-mini (rápido y barato, suficiente para function
calling de un chatbot de reservas). Podéis subir a gpt-4o o gpt-4.1 si
veis que falla con casos complejos.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from openai import OpenAI

from .config import settings
from . import calendar_service as cal
from . import db as db_module
from . import interactive as interactive_ids

log = logging.getLogger(__name__)

client = OpenAI(api_key=settings.openai_api_key)


# ---------- Tipo de retorno del agente (text + opcional interactive) ----------

@dataclass
class AgentReply:
    """Respuesta completa del agente tras un turno.

    - `text`: el cuerpo del mensaje a enviar/guardar. Siempre no vacío.
    - `interactive`: spec del mensaje interactivo si el agente quiere
      ofrecer opciones clicables. `None` para respuestas de texto puro.
      Formato:
          {
            "type": "list" | "buttons",
            "body": str,                       # mismo que text en la práctica
            "button": str (opc., solo list),
            "section_title": str (opc.),
            "options": [{"id": str, "title": str, "description"?: str}, ...]
          }
      El `id` de cada opción sigue el formato del módulo `interactive` —
      el backend lo genera, el LLM no lo inventa.
    """

    text: str
    interactive: dict[str, Any] | None = None

    @property
    def has_interactive(self) -> bool:
        return bool(self.interactive and self.interactive.get("options"))


class _EarlyReply(Exception):
    """Señal interna: un tool quiere TERMINAR el turno con una AgentReply concreta.

    La lanzan las tools "de oferta" (ofrecer_huecos, ofrecer_equipo,
    pedir_confirmacion): el loop del agente la captura y devuelve la reply
    sin pedir más completions al LLM.
    """

    def __init__(self, reply: AgentReply) -> None:
        self.reply = reply

# ---------- Definición de herramientas (tools) para OpenAI ----------
# Formato OpenAI: {"type": "function", "function": {name, description, parameters}}
# `parameters` sigue JSON Schema.

TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "consultar_disponibilidad",
            "description": (
                "Devuelve huecos libres en el calendario del negocio para la "
                "duración pedida, dentro de un rango de fechas. Usa SIEMPRE "
                "esta función antes de proponer una hora al cliente. NUNCA la "
                "llames sin haber preguntado antes la preferencia de "
                "peluquero/a al cliente."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "fecha_desde_iso": {
                        "type": "string",
                        "description": "Fecha/hora inicio en ISO 8601, zona Europe/Madrid. Ej: 2026-04-25T09:00:00",
                    },
                    "fecha_hasta_iso": {
                        "type": "string",
                        "description": "Fecha/hora fin en ISO 8601.",
                    },
                    "duracion_minutos": {
                        "type": "integer",
                        "description": (
                            "Duración del servicio en minutos. Debe coincidir "
                            "con el servicio que ha pedido el cliente — NO la "
                            "inventes. Duraciones típicas de esta peluquería: "
                            "Corte hombre=30, Corte mujer=45, Color=90, Mechas=120."
                        ),
                    },
                    "peluquero_preferido": {
                        "type": "string",
                        "description": (
                            "Nombre del peluquero/a que el cliente prefiere, o "
                            "la cadena literal 'sin preferencia' si le da igual. "
                            "OBLIGATORIO: pregunta al cliente '¿tienes "
                            "preferencia de peluquero o te da igual?' ANTES de "
                            "llamar a esta función. No inventes un nombre — si "
                            "el cliente no ha contestado, haz la pregunta y "
                            "espera su respuesta."
                        ),
                    },
                    "max_resultados": {
                        "type": "integer",
                        "description": "Máximo número de huecos a devolver. Por defecto 5.",
                    },
                },
                "required": [
                    "fecha_desde_iso",
                    "fecha_hasta_iso",
                    "duracion_minutos",
                    "peluquero_preferido",
                ],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "crear_reserva",
            "description": (
                "Crea una cita en el calendario. LLÁMALA EN CUANTO el cliente "
                "confirme (un 'sí', 'confirma', 'ok', 'dale', 'perfecto', "
                "'adelante'...) después de que tú hayas preguntado '¿lo confirmo?'. "
                "NO reconsultes disponibilidad ni reofrerezcas huecos en ese "
                "momento — los datos (servicio, hora, nombre, peluquero) ya "
                "estaban en el turno de confirmación. Solo si falta "
                "nombre_cliente o peluquero_preferido, pregúntalo ANTES, pero "
                "una vez todo está acordado y el cliente dice sí, ejecuta esta "
                "función al primer intento."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "titulo": {
                        "type": "string",
                        "description": (
                            "Título de la cita. FORMATO EXACTO: "
                            "'Nombre — Servicio (con Peluquero)'. "
                            "El NOMBRE DEL CLIENTE va PRIMERO, nunca el "
                            "servicio. Ejemplos correctos: "
                            "'Marcos — Corte hombre (con Laura)', "
                            "'Javier Test — Corte hombre (sin preferencia)'. "
                            "Ejemplo INCORRECTO (NO hagas esto): "
                            "'Corte hombre — Marcos'."
                        ),
                    },
                    "nombre_cliente": {
                        "type": "string",
                        "description": (
                            "Nombre del cliente tal y como lo ha dicho. Obligatorio. "
                            "Si no lo sabes, pregúntalo antes de llamar a esta función."
                        ),
                    },
                    "peluquero_preferido": {
                        "type": "string",
                        "description": (
                            "Mismo valor que se usó en consultar_disponibilidad: "
                            "nombre del peluquero/a o 'sin preferencia'. Obligatorio."
                        ),
                    },
                    "inicio_iso": {"type": "string"},
                    "fin_iso": {"type": "string"},
                    "telefono_cliente": {
                        "type": "string",
                        "description": (
                            "Teléfono del cliente. Úsalo del CONTEXTO del "
                            "sistema (campo 'Teléfono del cliente = ...'). "
                            "NUNCA preguntes al cliente su teléfono: ya lo "
                            "tenemos porque nos escribe desde su WhatsApp."
                        ),
                    },
                    "notas": {"type": "string"},
                },
                "required": [
                    "titulo",
                    "nombre_cliente",
                    "peluquero_preferido",
                    "inicio_iso",
                    "fin_iso",
                    "telefono_cliente",
                ],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "buscar_reserva_cliente",
            "description": "Busca la próxima reserva del cliente por su teléfono.",
            "parameters": {
                "type": "object",
                "properties": {
                    "telefono_cliente": {"type": "string"},
                    "dias_adelante": {"type": "integer"},
                },
                "required": ["telefono_cliente"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "mover_reserva",
            "description": "Mueve una reserva existente a nueva fecha/hora.",
            "parameters": {
                "type": "object",
                "properties": {
                    "event_id": {"type": "string"},
                    "nuevo_inicio_iso": {"type": "string"},
                    "nuevo_fin_iso": {"type": "string"},
                },
                "required": ["event_id", "nuevo_inicio_iso", "nuevo_fin_iso"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "cancelar_reserva",
            "description": "Cancela una reserva. SOLO tras confirmación explícita del cliente.",
            "parameters": {
                "type": "object",
                "properties": {"event_id": {"type": "string"}},
                "required": ["event_id"],
            },
        },
    },
    # -----------------------------------------------------------------
    # TOOLS DE OFERTA (UI interactivo WhatsApp)
    #
    # Estas tools NO devuelven datos para que el LLM siga razonando:
    # TERMINAN el turno y generan un mensaje con opciones clicables en
    # WhatsApp. El LLM debe llamarlas al final de una fase y NO añadir
    # texto extra después.
    # -----------------------------------------------------------------
    {
        "type": "function",
        "function": {
            "name": "ofrecer_servicio",
            "description": (
                "Ofrece al cliente los servicios del negocio como LISTA "
                "CLICABLE en WhatsApp. Úsalo en el PASO 1 del flujo (cuando "
                "el cliente pide 'cita' o 'reservar' sin decir qué servicio). "
                "No hace falta pasar la lista de servicios — el backend los "
                "lee de la configuración del negocio y añade una opción "
                "'Otro' al final. TERMINA el turno: NO añadas texto después."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "body": {
                        "type": "string",
                        "description": (
                            "Frase introductoria corta, máx 1 frase. Ej: "
                            "'¿Qué te hacemos?'. Evita listar los servicios "
                            "aquí — los renderiza la propia función."
                        ),
                    },
                },
                "required": ["body"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "ofrecer_huecos",
            "description": (
                "Ofrece al cliente huecos horarios como LISTA CLICABLE en "
                "WhatsApp. Llama a esta función INMEDIATAMENTE después de "
                "consultar_disponibilidad cuando ya tengas los huecos. "
                "TERMINA el turno — no añadas texto en la respuesta, el "
                "`body` de esta función es el texto que verá el cliente. "
                "Los IDs de cada opción los genera el backend — pasa solo "
                "las horas ISO y el backend añade una opción 'Otra hora' "
                "automáticamente."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "body": {
                        "type": "string",
                        "description": (
                            "Frase introductoria corta (máx 2 frases). Ej: "
                            "'Estos son los huecos libres el viernes 24. "
                            "¿Cuál te encaja?'."
                        ),
                    },
                    "huecos": {
                        "type": "array",
                        "description": (
                            "Lista de huecos a ofrecer, en el MISMO ORDEN "
                            "que quieres que aparezcan. Máximo 9 (el 10º "
                            "queda para 'Otra hora')."
                        ),
                        "items": {
                            "type": "object",
                            "properties": {
                                "inicio_iso": {
                                    "type": "string",
                                    "description": "Inicio del hueco, ISO 8601.",
                                },
                                "fin_iso": {
                                    "type": "string",
                                    "description": "Fin del hueco, ISO 8601.",
                                },
                            },
                            "required": ["inicio_iso", "fin_iso"],
                        },
                    },
                },
                "required": ["body", "huecos"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "equipo_disponible_en",
            "description": (
                "Devuelve qué miembros del equipo están libres en un hueco "
                "concreto (misma duración del servicio). Úsalo JUSTO después "
                "de que el cliente elija una hora, para saber si hay que "
                "ofrecerle elegir miembro o si sólo queda uno disponible."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "inicio_iso": {"type": "string"},
                    "fin_iso": {"type": "string"},
                },
                "required": ["inicio_iso", "fin_iso"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "ofrecer_equipo",
            "description": (
                "Ofrece al cliente elegir miembro del equipo como LISTA "
                "CLICABLE. Úsalo en DOS escenarios: "
                "(a) al principio, cuando preguntas por preferencia de "
                "peluquero antes de mirar huecos — en ese caso pasa "
                "modo_preferencia=true y lista TODOS los miembros del "
                "equipo; el backend añadirá un botón 'Me da igual' al "
                "final. "
                "(b) tras equipo_disponible_en con >1 miembros libres en un "
                "hueco concreto — en ese caso modo_preferencia=false (o "
                "omitido) y el backend añade 'Otro miembro' para volver "
                "al paso anterior. "
                "TERMINA el turno — no añadas texto en la respuesta."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "body": {
                        "type": "string",
                        "description": (
                            "Frase corta. Ej: '¿Tienes preferencia?' o "
                            "'¿Con quién prefieres?'."
                        ),
                    },
                    "miembros": {
                        "type": "array",
                        "description": (
                            "Lista de miembros a ofrecer. En modo_preferencia "
                            "incluye TODO el equipo. En modo normal incluye "
                            "solo los libres a esa hora (IDs de "
                            "equipo_disponible_en)."
                        ),
                        "items": {
                            "type": "object",
                            "properties": {
                                "id": {
                                    "type": "string",
                                    "description": "member_id (entero como string) del miembro del equipo.",
                                },
                                "nombre": {"type": "string"},
                            },
                            "required": ["id", "nombre"],
                        },
                    },
                    "modo_preferencia": {
                        "type": "boolean",
                        "description": (
                            "True si es la pregunta inicial de "
                            "preferencia (el botón extra será 'Me da "
                            "igual'). False/omitido si es tras "
                            "equipo_disponible_en en un hueco (el botón "
                            "extra será 'Otro miembro')."
                        ),
                    },
                },
                "required": ["body", "miembros"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "pedir_confirmacion",
            "description": (
                "Muestra al cliente el RESUMEN de la reserva y le pide "
                "confirmación con dos botones: Sí / No. TERMINA el turno — "
                "no llames a crear_reserva directamente tras esta función; "
                "espera a que el cliente pulse 'Sí' en el siguiente turno."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "resumen": {
                        "type": "string",
                        "description": (
                            "Resumen en UNA frase de todo lo acordado. Ej: "
                            "'Corte de hombre, viernes 24 a las 10:00 con "
                            "Mario. ¿Te lo confirmo?'"
                        ),
                    },
                },
                "required": ["resumen"],
            },
        },
    },
]


# ---------- Ejecutor de herramientas (lado backend) ----------

def _execute_tool(name: str, args: dict, tenant: dict, caller_phone: str) -> str:
    """Ejecuta la herramienta y devuelve un string JSON con el resultado.
    OpenAI recibe este string como contenido de un mensaje role=tool."""
    calendar_id = tenant.get("calendar_id") or settings.default_calendar_id
    tenant_id = tenant.get("id", "default")

    try:
        if name == "consultar_disponibilidad":
            peluquero = (args.get("peluquero_preferido") or "").strip()
            if not peluquero:
                # Red de seguridad: schema ya lo marca required, pero si el LLM
                # se salta forzamos la pregunta antes de devolver huecos.
                return json.dumps({
                    "error": (
                        "Falta peluquero_preferido. Pregunta al cliente "
                        "'¿tienes preferencia de peluquero o te da igual?' "
                        "ANTES de consultar disponibilidad."
                    ),
                })
            desde = datetime.fromisoformat(args["fecha_desde_iso"])
            hasta = datetime.fromisoformat(args["fecha_hasta_iso"])
            dur = int(args["duracion_minutos"])
            slots = cal.listar_huecos_libres(
                desde, hasta, dur, calendar_id=calendar_id, tenant_id=tenant_id
            )
            limit = int(args.get("max_resultados", 5))
            out = [
                {"inicio": s.start.isoformat(), "fin": s.end.isoformat()}
                for s in slots[:limit]
            ]
            return json.dumps({"huecos": out, "peluquero_preferido": peluquero})

        if name == "crear_reserva":
            nombre_cliente = (args.get("nombre_cliente") or "").strip()
            if not nombre_cliente:
                # Red de seguridad: el schema ya lo marca required, pero si el LLM
                # se salta, abortamos con un error claro en vez de crear evento sin nombre.
                return json.dumps({
                    "error": "Falta nombre_cliente. Pregunta al cliente por su nombre antes de llamar a crear_reserva.",
                })
            peluquero = (args.get("peluquero_preferido") or "").strip()
            if not peluquero:
                return json.dumps({
                    "error": (
                        "Falta peluquero_preferido. Pregunta la preferencia "
                        "de peluquero antes de crear la reserva."
                    ),
                })
            ev = cal.crear_evento(
                titulo=args["titulo"],
                inicio=datetime.fromisoformat(args["inicio_iso"]),
                fin=datetime.fromisoformat(args["fin_iso"]),
                descripcion=args.get("notas", ""),
                telefono_cliente=args.get("telefono_cliente", caller_phone),
                nombre_cliente=nombre_cliente,
                calendar_id=calendar_id,
                tenant_id=tenant_id,
            )
            # Además del link interno al evento del calendario del negocio,
            # construimos un "add to calendar" que el cliente puede pulsar
            # para añadir la cita a SU propio Google Calendar. Esto mejora
            # mucho el UX: recordatorio nativo en el móvil del cliente sin
            # que tengamos que mandarle nada más.
            add_url = _build_google_add_to_calendar_url(
                titulo=args["titulo"],
                inicio=datetime.fromisoformat(args["inicio_iso"]),
                fin=datetime.fromisoformat(args["fin_iso"]),
                descripcion=args.get("notas", "") or "",
                ubicacion=(tenant.get("name") or "").strip(),
                tz=(tenant.get("timezone") or settings.default_timezone),
            )
            return json.dumps({
                "ok": True,
                "event_id": ev.get("id"),
                "link": ev.get("htmlLink"),
                "add_to_calendar_url": add_url,
            })

        if name == "buscar_reserva_cliente":
            desde = datetime.utcnow()
            hasta = desde + timedelta(days=int(args.get("dias_adelante", 30)))
            ev = cal.buscar_evento_por_telefono(
                args["telefono_cliente"], desde, hasta,
                calendar_id=calendar_id, tenant_id=tenant_id,
            )
            if not ev:
                return json.dumps({"encontrada": False})
            return json.dumps({
                "encontrada": True,
                "event_id": ev["id"],
                "titulo": ev.get("summary"),
                "inicio": ev["start"].get("dateTime"),
                "fin": ev["end"].get("dateTime"),
            })

        if name == "mover_reserva":
            ev = cal.mover_evento(
                event_id=args["event_id"],
                nuevo_inicio=datetime.fromisoformat(args["nuevo_inicio_iso"]),
                nuevo_fin=datetime.fromisoformat(args["nuevo_fin_iso"]),
                calendar_id=calendar_id, tenant_id=tenant_id,
            )
            return json.dumps({"ok": True, "event_id": ev.get("id")})

        if name == "cancelar_reserva":
            cal.cancelar_evento(args["event_id"], calendar_id=calendar_id, tenant_id=tenant_id)
            return json.dumps({"ok": True})

        # --- Tools de oferta (terminan el turno con interactive) ---------

        if name == "equipo_disponible_en":
            disponibles = _miembros_disponibles_en(
                tenant=tenant,
                inicio_iso=args["inicio_iso"],
                fin_iso=args["fin_iso"],
            )
            return json.dumps({"miembros": disponibles})

        if name == "ofrecer_servicio":
            raise _EarlyReply(_build_reply_ofrecer_servicio(args, tenant))

        if name == "ofrecer_huecos":
            raise _EarlyReply(_build_reply_ofrecer_huecos(args, tenant))

        if name == "ofrecer_equipo":
            raise _EarlyReply(_build_reply_ofrecer_equipo(args, tenant))

        if name == "pedir_confirmacion":
            raise _EarlyReply(_build_reply_confirmacion(args))

        return json.dumps({"error": f"herramienta desconocida: {name}"})

    except _EarlyReply:
        # Propagar — el loop del agente decide qué hacer con esto.
        raise
    except Exception as e:
        log.exception("Error ejecutando tool %s", name)
        return json.dumps({"error": str(e)})


# ---------- Builder del "Add to Calendar" URL de Google ----------

def _build_google_add_to_calendar_url(
    *,
    titulo: str,
    inicio: datetime,
    fin: datetime,
    descripcion: str = "",
    ubicacion: str = "",
    tz: str = "Europe/Madrid",
) -> str:
    """Construye la URL de "añadir a mi Google Calendar" para el cliente.

    Este URL abre el formulario de creación de evento de Google Calendar
    precargado con los datos. Pensado para enviarse tras `crear_reserva`
    para que el cliente lo añada a su calendario personal (recordatorios,
    visibilidad en su agenda, sin que tengamos que integrarnos con él).

    Formato documentado por Google (no forma parte de la API oficial pero
    es público y estable desde hace años):

        https://calendar.google.com/calendar/render?action=TEMPLATE
            &text=<título URL-encoded>
            &dates=<YYYYMMDDTHHMMSS>/<YYYYMMDDTHHMMSS>   (hora local)
            &ctz=<IANA timezone>                         (p.ej. Europe/Madrid)
            &details=<descripción URL-encoded>
            &location=<ubicación URL-encoded>

    Nota: pasamos el intervalo en la zona horaria del negocio + `ctz=` para
    que quien abra el enlace vea la hora correcta en su Google Calendar,
    independientemente de dónde esté. Si las fechas de entrada traen
    tzinfo, las convertimos a la TZ del tenant; si no, las tratamos como
    hora local de esa TZ.
    """
    from urllib.parse import urlencode
    from zoneinfo import ZoneInfo

    try:
        zone = ZoneInfo(tz)
    except Exception:  # pragma: no cover - TZ inválida no debería darse
        zone = ZoneInfo("Europe/Madrid")

    def _local(dt: datetime) -> datetime:
        if dt.tzinfo is not None:
            return dt.astimezone(zone)
        return dt  # naive ya se asume local; no lo "movemos" artificialmente

    ini_local = _local(inicio)
    fin_local = _local(fin)
    fmt = "%Y%m%dT%H%M%S"
    params = {
        "action": "TEMPLATE",
        "text": titulo,
        "dates": f"{ini_local.strftime(fmt)}/{fin_local.strftime(fmt)}",
        "ctz": tz,
    }
    if descripcion:
        params["details"] = descripcion
    if ubicacion:
        params["location"] = ubicacion
    return "https://calendar.google.com/calendar/render?" + urlencode(params)


# ---------- Builders de AgentReply para las tools de oferta ----------

_DIAS_CORTOS_ES = ["lun", "mar", "mié", "jue", "vie", "sáb", "dom"]


def _format_slot_title(inicio_iso: str, fin_iso: str) -> str:
    """Genera un título corto para la fila de lista.

    Max 24 chars (límite WhatsApp). Formato: "vie 24 abr, 10:00".
    """
    try:
        dt = datetime.fromisoformat(inicio_iso)
    except ValueError:
        return (inicio_iso or "")[:24]
    dia = _DIAS_CORTOS_ES[dt.weekday()]
    mes = _MONTHS_ES[dt.month - 1][:3] if dt.month <= 12 else ""
    title = f"{dia} {dt.day} {mes}, {dt.strftime('%H:%M')}"
    return title[:24]


def _slugify_service(nombre: str) -> str:
    """Normaliza un nombre de servicio a slug estable.

    - minúsculas
    - acentos fuera (á→a, é→e, etc.)
    - espacios y "/" pasan a "-"
    - colapsa guiones duplicados
    - recorta a 60 chars

    Es reversible por búsqueda: el slug se mapea de vuelta al servicio
    comparando contra cada service['nombre'] slugificado.
    """
    import unicodedata
    s = (nombre or "").strip().lower()
    # Quitar acentos/diacríticos
    s = "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")
    # Espacios, "/" y barras → guion
    s = re.sub(r"[\s/]+", "-", s)
    # Todo lo que no sea alfanumérico o "-" fuera
    s = re.sub(r"[^a-z0-9\-]", "", s)
    # Colapsar guiones y recortar
    s = re.sub(r"-+", "-", s).strip("-")
    return s[:60]


def _build_reply_ofrecer_servicio(args: dict, tenant: dict) -> AgentReply:
    """Construye AgentReply para ofrecer_servicio: lista con los servicios
    del tenant + fila 'Otro'.

    El LLM solo aporta el `body`; la lista se genera leyendo
    `tenant["services"]` (formato YAML: nombre, duracion_min, precio).
    """
    body = (args.get("body") or "").strip() or "¿Qué te hacemos?"
    services = tenant.get("services") or []
    options: list[dict[str, str]] = []
    # WhatsApp lista → 10 filas; dejamos 1 para "Otro".
    for s in services[:9]:
        nombre = (s.get("nombre") or "").strip()
        if not nombre:
            continue
        slug = _slugify_service(nombre)
        if not slug:
            continue
        precio = s.get("precio")
        dur = s.get("duracion_min")
        # Descripción compacta ≤72 chars: "30 min · 15€"
        desc_parts: list[str] = []
        if isinstance(dur, (int, float)):
            desc_parts.append(f"{int(dur)} min")
        if isinstance(precio, (int, float)):
            desc_parts.append(f"{int(precio)}€")
        desc = " · ".join(desc_parts) if desc_parts else ""
        row: dict[str, str] = {
            "id": interactive_ids.make_service_id(slug),
            "title": nombre[:24],
        }
        if desc:
            row["description"] = desc[:72]
        options.append(row)
    options.append({
        "id": interactive_ids.make_other_id("svc"),
        "title": "Otro",
    })
    spec = {
        "type": "list",
        "body": body,
        "button": "Ver servicios",
        "section_title": "Servicios",
        "options": options,
    }
    return AgentReply(text=body, interactive=spec)


def _build_reply_ofrecer_huecos(args: dict, tenant: dict) -> AgentReply:
    """Construye AgentReply para ofrecer_huecos: lista + fila 'Otra hora'."""
    body = (args.get("body") or "").strip() or "¿Cuál de estas horas te encaja?"
    huecos = args.get("huecos") or []
    options: list[dict[str, str]] = []
    # WhatsApp permite 10 filas; dejamos 1 para "Otra hora" → huecos ≤ 9.
    for h in huecos[:9]:
        inicio = (h.get("inicio_iso") or "").strip()
        fin = (h.get("fin_iso") or "").strip()
        if not inicio or not fin:
            continue
        options.append({
            "id": interactive_ids.make_slot_id(inicio, fin),
            "title": _format_slot_title(inicio, fin),
        })
    options.append({
        "id": interactive_ids.make_other_id("slot"),
        "title": "Otra hora",
    })
    spec = {
        "type": "list",
        "body": body,
        "button": "Ver huecos",
        "section_title": "Horas libres",
        "options": options,
    }
    return AgentReply(text=body, interactive=spec)


def _build_reply_ofrecer_equipo(args: dict, tenant: dict) -> AgentReply:
    """Construye AgentReply para ofrecer_equipo.

    Dos modos según `modo_preferencia` (default False):

    - modo_preferencia=False (uso clásico, tras equipo_disponible_en):
        el botón extra es "Otro miembro" (id `other:team`), y main.py
        lo interpreta como "vuelve al paso anterior".

    - modo_preferencia=True (pregunta inicial de preferencia):
        el botón extra es "Me da igual" (id `team:none`), que el
        backend resuelve como "sin preferencia" y continúa el flujo
        con todo el equipo.
    """
    body = (args.get("body") or "").strip() or "¿Con quién prefieres?"
    miembros = args.get("miembros") or []
    modo_pref = bool(args.get("modo_preferencia") or False)
    options: list[dict[str, str]] = []
    # WhatsApp lista permite 10; dejamos 1 para el botón extra.
    for m in miembros[:9]:
        mid = str(m.get("id") or "").strip()
        nombre = (m.get("nombre") or "").strip()
        if not mid or not nombre:
            continue
        options.append({
            "id": interactive_ids.make_team_id(mid),
            "title": nombre[:24],
        })
    if modo_pref:
        # "Me da igual" en la pregunta inicial; equivalente a "sin preferencia".
        options.append({
            "id": interactive_ids.make_team_id(None),  # team:none
            "title": "Me da igual",
        })
        section_title = "Equipo"
        button_label = "Elegir"
    else:
        options.append({
            "id": interactive_ids.make_other_id("team"),
            "title": "Otro miembro",
        })
        section_title = "Equipo disponible"
        button_label = "Elegir"
    spec = {
        "type": "list",
        "body": body,
        "button": button_label,
        "section_title": section_title,
        "options": options,
    }
    return AgentReply(text=body, interactive=spec)


def _build_reply_confirmacion(args: dict) -> AgentReply:
    """Construye AgentReply para pedir_confirmacion: botones Sí/No."""
    resumen = (args.get("resumen") or "").strip() or "¿Confirmo la reserva?"
    options = [
        {"id": interactive_ids.make_confirm_id(True), "title": "Sí, confirmar"},
        {"id": interactive_ids.make_confirm_id(False), "title": "No, cambiar"},
    ]
    spec = {
        "type": "buttons",
        "body": resumen,
        "options": options,
    }
    return AgentReply(text=resumen, interactive=spec)


def _miembros_disponibles_en(
    tenant: dict,
    inicio_iso: str,
    fin_iso: str,
) -> list[dict[str, Any]]:
    """Devuelve la lista de miembros del equipo libres en el hueco dado.

    Consulta freebusy individual de cada calendario del miembro. Si un miembro
    no tiene calendar_id propio (antiguo setup single-calendar), se asume
    disponible (mismo criterio que listar_huecos_por_peluqueros).
    """
    equipo = tenant.get("equipo") or tenant.get("peluqueros") or []
    if not equipo:
        return []

    try:
        inicio = datetime.fromisoformat(inicio_iso)
        fin = datetime.fromisoformat(fin_iso)
    except ValueError:
        return []

    tenant_id = tenant.get("id", "default")
    duracion = max(1, int((fin - inicio).total_seconds() // 60))

    disponibles: list[dict[str, Any]] = []
    for m in equipo:
        dias = m.get("dias_trabajo") or list(range(7))
        if inicio.weekday() not in dias:
            continue
        cal_id = m.get("calendar_id") or ""
        if not cal_id:
            # Sin calendar propio: se asume disponible (legacy).
            disponibles.append({
                "id": str(m.get("id") or ""),
                "nombre": m.get("nombre") or "",
            })
            continue
        # Consulta de huecos sobre ese calendario para la ventana exacta.
        try:
            slots = cal.listar_huecos_libres(
                inicio, fin, duracion,
                calendar_id=cal_id, tenant_id=tenant_id,
                business_hours=tenant.get("business_hours"),
            )
        except Exception:
            log.exception("freebusy miembro %s falló — se omite", m.get("nombre"))
            continue
        # Si hay al menos 1 slot que cubra exactamente [inicio, fin), está libre.
        for s in slots:
            if s.start <= inicio and s.end >= fin:
                disponibles.append({
                    "id": str(m.get("id") or ""),
                    "nombre": m.get("nombre") or "",
                })
                break
    return disponibles


# ---------- Sanitizer de salida (WhatsApp-friendly) ----------

# Marcadores numéricos/viñeta típicos al inicio de cada línea.
#   - `1.`, `12)` → decimales seguidos de punto o paréntesis
#   - `1️⃣ 2️⃣ 3️⃣ …` → dígitos con enclosing keycap (U+20E3) precedidos por el dígito
#   - `-`, `*`, `•` al inicio de línea
_RE_LIST_PREFIX = re.compile(
    r"^\s*(?:"
    r"(?:\d+[\.\)])"           # "1." o "1)"
    r"|(?:[0-9]\uFE0F?\u20E3)" # "1️⃣"
    r"|(?:🥇|🥈|🥉)"            # medallas
    r"|[-*•·]"                 # guiones/bullets
    r")\s+",
    flags=re.UNICODE,
)

# Markdown: negritas ** **, __ __, cursivas *x* o _x_ (conservadoras: sólo si
# envuelven contenido sin saltos de línea). Dejamos los asteriscos simples *x*
# porque WhatsApp sí los renderiza como negrita y a veces son intencionados;
# pero **x** SIEMPRE queda como ruido en WhatsApp.
_RE_DOUBLE_STAR = re.compile(r"\*\*(.+?)\*\*", flags=re.DOTALL)
_RE_DOUBLE_UNDERSCORE = re.compile(r"__(.+?)__", flags=re.DOTALL)

# Línea que empieza con un pictograma/emoji "decorativo" típico de fichas:
# 📅 🗓️ ⏰ 👤 📱 📋 ✂️ 💇 ✅ 📍 💈 🗒️ etc. — es decir, cualquier símbolo en los
# rangos estándar de emoji/pictograph, seguido (opc.) de variation selector y
# espacio, y luego contenido.
_RE_EMOJI_PREFIX = re.compile(
    r"^\s*"
    r"[\U0001F300-\U0001FAFF\u2600-\u27BF\u2700-\u27BF]"
    r"[\uFE0F\u200D\U0001F3FB-\U0001F3FF]*"
    r"(?:[\U0001F300-\U0001FAFF\u2600-\u27BF][\uFE0F\u200D\U0001F3FB-\U0001F3FF]*)*"
    r"\s+(?P<rest>.+)$",
    flags=re.UNICODE,
)

# Emoji atómico + modificadores ZWJ/skin-tone/variation selector. Sirve para
# localizar emojis individuales en el texto y recortar el exceso (regla 3:
# máximo 1 por mensaje).
_RE_EMOJI_ATOM = re.compile(
    r"[\U0001F300-\U0001FAFF\u2600-\u27BF]"
    r"(?:\u200D[\U0001F300-\U0001FAFF\u2600-\u27BF])*"
    r"[\uFE0F\U0001F3FB-\U0001F3FF]*",
    flags=re.UNICODE,
)


def _cap_emoji_count(text: str, max_count: int = 1) -> str:
    """Deja como mucho `max_count` emojis en el texto.

    Estrategia: encuentra todas las apariciones; si hay más de `max_count`,
    mantiene las primeras y elimina el resto (también los espacios contiguos
    huérfanos). No toca espacios si no hay que recortar.
    """
    matches = list(_RE_EMOJI_ATOM.finditer(text))
    if len(matches) <= max_count:
        return text
    # Recorrer de derecha a izquierda para no romper los offsets al borrar.
    for m in reversed(matches[max_count:]):
        start, end = m.start(), m.end()
        # Consumir el espacio previo si queda al final de una palabra, para
        # evitar "hola  cita" con doble espacio.
        while start > 0 and text[start - 1] == " " and (end == len(text) or text[end] in (" ", "\n", ".", ",", "!", "?")):
            start -= 1
        text = text[:start] + text[end:]
    return text


def _sanitize_whatsapp(text: str) -> str:
    """Limpia la salida del LLM para WhatsApp.

    - Elimina markdown de negrita doble (** **, __ __).
    - Si detecta líneas que empiezan con marcadores de lista (1., 1️⃣, -, *),
      quita el marcador y une las líneas contiguas con ", " para que el
      resultado sea una frase continua.
    - Colapsa saltos de línea múltiples.

    Conservador: si no ve marcadores de lista deja el texto tal cual. El
    objetivo es respetar el estilo del LLM cuando ya responde bien, y sólo
    intervenir cuando lo estropea.
    """
    if not text:
        return text

    # 1) quitar negritas markdown dobles
    text = _RE_DOUBLE_STAR.sub(r"\1", text)
    text = _RE_DOUBLE_UNDERSCORE.sub(r"\1", text)

    # 2) aplanar listas (marcador numérico, guion, asterisco, emoji-keycap)
    #    y "fichas" (2+ líneas seguidas con prefijo emoji decorativo).
    lines = text.split("\n")
    out_lines: list[str] = []
    list_buffer: list[str] = []    # ítems de lista → unidos con ", " y " o "
    emoji_buffer: list[str] = []   # líneas tipo ficha → unidas con ", "

    def _flush_list() -> None:
        nonlocal list_buffer
        if not list_buffer:
            return
        if len(list_buffer) == 1:
            out_lines.append(list_buffer[0])
        else:
            joined = ", ".join(list_buffer[:-1]) + " o " + list_buffer[-1]
            out_lines.append(joined)
        list_buffer = []

    def _flush_emoji() -> None:
        nonlocal emoji_buffer
        if not emoji_buffer:
            return
        # Si hay 2+ líneas, es una ficha — las unimos como prosa con coma.
        # Si sólo hay una, la dejamos tal cual (puede ser un saludo "👋 Hola").
        if len(emoji_buffer) == 1:
            out_lines.append(emoji_buffer[0])
        else:
            out_lines.append(", ".join(emoji_buffer))
        emoji_buffer = []

    def _flush_all() -> None:
        _flush_list()
        _flush_emoji()

    for raw in lines:
        m_list = _RE_LIST_PREFIX.match(raw)
        if m_list:
            _flush_emoji()  # cerramos un bloque distinto
            item = raw[m_list.end():].strip()
            if item:
                list_buffer.append(item)
            continue
        m_emoji = _RE_EMOJI_PREFIX.match(raw)
        if m_emoji:
            _flush_list()
            rest = m_emoji.group("rest").strip()
            if rest:
                emoji_buffer.append(rest)
            continue
        _flush_all()
        out_lines.append(raw)
    _flush_all()

    # 3) colapsar líneas en blanco múltiples
    result = "\n".join(out_lines)
    result = re.sub(r"\n{3,}", "\n\n", result)

    # 4) máximo 1 emoji por mensaje (regla 3 del FORMATO)
    result = _cap_emoji_count(result, max_count=1)

    return result.strip()


# ---------- Contexto temporal inyectado al prompt ----------

_WEEKDAYS_ES = [
    "lunes", "martes", "miércoles", "jueves",
    "viernes", "sábado", "domingo",
]
_MONTHS_ES = [
    "enero", "febrero", "marzo", "abril", "mayo", "junio",
    "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre",
]


def _format_date_es(d: datetime) -> str:
    return f"{_WEEKDAYS_ES[d.weekday()]} {d.day} de {_MONTHS_ES[d.month - 1]} de {d.year}"


def _build_time_context(now: datetime) -> str:
    """Genera una tabla de días nombrados → fecha para los próximos 8 días.

    Los LLM son malos calculando "lunes" a partir de "hoy es jueves 23". Les
    damos la tabla ya resuelta para que sólo tengan que mirar.
    """
    today = _format_date_es(now)
    lines = [
        f"Hoy es {today}, {now.strftime('%H:%M')} (zona {settings.default_timezone}).",
        f"Mañana es {_format_date_es(now + timedelta(days=1))}.",
    ]
    for i in range(2, 8):
        d = now + timedelta(days=i)
        lines.append(f"En {i} días: {_format_date_es(d)}.")
    return "\n".join(lines)


def _build_context_footer(tenant: dict, time_ctx: str, caller_phone: str) -> str:
    """Footer con datos dinámicos que se anexa al system_prompt.

    Inyecta:
    - Nombre real del negocio (cuando hables del negocio usa ESTE nombre).
    - Tabla de fechas ya resuelta (vía _build_time_context).
    - Teléfono del cliente y regla explícita de no preguntarlo.
    """
    business_name = tenant.get("name") or "la peluquería"
    return (
        f"\n\n════════ DATOS DINÁMICOS DE ESTA CONVERSACIÓN ════════\n"
        f"\nNEGOCIO: {business_name}.\n"
        f"Cuando menciones el negocio al cliente, usa SIEMPRE este nombre "
        f"exacto. No lo traduzcas, no lo abrevies, no inventes otro.\n"
        f"\nCONTEXTO TEMPORAL (consulta esta tabla SIEMPRE que el cliente "
        f"diga 'hoy', 'mañana', 'el lunes', etc. — NO calcules fechas tú):\n"
        f"{time_ctx}\n"
        f"\nTELÉFONO DEL CLIENTE: {caller_phone}\n"
        f"El cliente nos escribe desde ese número de WhatsApp. Úsalo "
        f"directamente al crear la reserva (campo telefono_cliente). "
        f"NUNCA le preguntes 'cuál es tu teléfono' — ya lo tenemos.\n"
    )


# ---------- Loop principal del agente ----------

def _history_to_openai(history: list[dict]) -> list[dict]:
    """Convierte el historial guardado (role=user|assistant, content=str) al
    formato que acepta OpenAI (mismos roles). Se ignoran roles desconocidos.

    OpenAI permite mensajes consecutivos del mismo rol, pero los fusionamos
    igualmente para dejar el historial limpio.
    """
    out: list[dict] = []
    last_role = None
    for m in history:
        role = m["role"]
        if role not in ("user", "assistant", "system"):
            continue
        if role == last_role and role != "system":
            out[-1]["content"] += "\n" + m["content"]
        else:
            out.append({"role": role, "content": m["content"]})
            last_role = role
    return out


def reply(user_message: str, history: list[dict], tenant: dict, caller_phone: str) -> AgentReply:
    """Dispatcher: delega al provider configurado (OpenAI o Anthropic).

    `LLM_PROVIDER=anthropic` cambia al adaptador de Anthropic (Claude). Cualquier
    otro valor (o vacío) cae en OpenAI, que es el comportamiento original.

    Devuelve siempre un `AgentReply`. Si el provider antiguo devuelve str (por
    compatibilidad), lo envolvemos aquí.
    """
    if settings.llm_provider == "anthropic":
        from . import agent_anthropic  # import tardío: no forzar dep si no se usa
        result = agent_anthropic.reply(
            user_message=user_message,
            history=history,
            tenant=tenant,
            caller_phone=caller_phone,
        )
        if isinstance(result, AgentReply):
            return result
        # Compatibilidad con versiones antiguas del provider que devolvían str.
        return AgentReply(text=str(result or ""))
    return _reply_openai(
        user_message=user_message,
        history=history,
        tenant=tenant,
        caller_phone=caller_phone,
    )


def _reply_openai(user_message: str, history: list[dict], tenant: dict, caller_phone: str) -> AgentReply:
    """Devuelve la respuesta de texto del agente tras resolver tool calls (provider OpenAI)."""
    # Usar TZ del negocio para que el "hoy" del prompt coincida con lo que
    # percibe el cliente; Railway corre en UTC.
    time_ctx = _build_time_context(datetime.now(ZoneInfo(settings.default_timezone)))
    system_prompt = (
        tenant["system_prompt"]
        + _build_context_footer(tenant=tenant, time_ctx=time_ctx, caller_phone=caller_phone)
    )

    # OpenAI mete el system prompt como un mensaje más al inicio.
    messages: list[dict] = [{"role": "system", "content": system_prompt}]
    messages.extend(_history_to_openai(history))
    messages.append({"role": "user", "content": user_message})

    tenant_id = tenant.get("id", "default")

    # La familia GPT-5 (y los modelos o1/o3) usa `max_completion_tokens` en vez
    # de `max_tokens`. Detectamos por prefijo; si añadimos más modelos con la
    # API nueva, incluirlos aquí.
    model_name = settings.openai_model
    uses_new_token_param = (
        model_name.startswith("gpt-5")
        or model_name.startswith("o1")
        or model_name.startswith("o3")
        or model_name.startswith("o4")
    )
    token_kwargs: dict[str, int] = (
        {"max_completion_tokens": 1024} if uses_new_token_param else {"max_tokens": 1024}
    )

    for _ in range(6):  # máx 6 rondas para evitar loops infinitos
        resp = client.chat.completions.create(
            model=model_name,
            messages=messages,
            tools=TOOLS,
            tool_choice="auto",
            **token_kwargs,
        )

        # --- Tracking de tokens (no-op si falla) ------------------------
        try:
            usage = getattr(resp, "usage", None)
            if usage is not None:
                db_module.save_token_usage(
                    tenant_id=tenant_id,
                    model=settings.openai_model,
                    input_tokens=getattr(usage, "prompt_tokens", 0) or 0,
                    output_tokens=getattr(usage, "completion_tokens", 0) or 0,
                    customer_phone=caller_phone,
                )
        except Exception:
            log.exception("Error guardando token_usage")
        # ----------------------------------------------------------------

        choice = resp.choices[0]
        msg = choice.message

        # Caso 1: el modelo quiere ejecutar herramientas
        if msg.tool_calls:
            # Añadir la respuesta del assistant con los tool_calls al historial
            messages.append({
                "role": "assistant",
                "content": msg.content or "",
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments,
                        },
                    }
                    for tc in msg.tool_calls
                ],
            })

            # Ejecutar cada tool call y añadir un mensaje role=tool por cada uno
            for tc in msg.tool_calls:
                try:
                    args = json.loads(tc.function.arguments or "{}")
                except json.JSONDecodeError:
                    args = {}
                try:
                    result = _execute_tool(tc.function.name, args, tenant, caller_phone)
                except _EarlyReply as er:
                    # Una tool de oferta (ofrecer_huecos / ofrecer_equipo /
                    # pedir_confirmacion) ha TERMINADO el turno. Devolvemos
                    # la AgentReply con interactive sin pedir más al LLM.
                    return er.reply
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": result,
                })
            continue

        # Caso 2: el modelo acaba sin pedir herramientas
        text = (msg.content or "").strip()
        if choice.finish_reason not in ("stop", "length", None):
            log.warning("finish_reason inesperado: %s", choice.finish_reason)
        clean = _sanitize_whatsapp(text)
        return AgentReply(text=clean or "¿En qué puedo ayudarte?")

    return AgentReply(
        text="Lo siento, no he podido completar la petición. ¿Puedes intentarlo de otra forma?"
    )
