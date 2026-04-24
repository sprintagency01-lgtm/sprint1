"""Servicio de calendario (Google Calendar).

Abstrae las 4 operaciones que el agente puede ejecutar:
- listar_huecos_libres
- crear_evento
- mover_evento
- cancelar_evento

Diseñado como interfaz clara para poder reemplazar por Outlook/iCal en el futuro.

En este MVP:
- Las credenciales de usuario se guardan en .tokens/{tenant_id}.json.
- El flujo OAuth inicial se lanza con `python -m app.calendar_service authorize`.
- Se asume un calendario por tenant.
"""
from __future__ import annotations

import json
import logging
import os
import pathlib
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, time
from typing import Iterable
from zoneinfo import ZoneInfo

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

from .config import settings

log = logging.getLogger(__name__)

SCOPES = [
    "https://www.googleapis.com/auth/calendar.events",
    "https://www.googleapis.com/auth/calendar.readonly",  # necesario para freeBusy.query
]
TOKENS_DIR = pathlib.Path(os.getenv("TOKENS_DIR", ".tokens"))
TOKENS_DIR.mkdir(parents=True, exist_ok=True)
TZ = ZoneInfo(settings.default_timezone)


def _ensure_local_tz(dt: datetime) -> datetime:
    """Normaliza datetimes al huso del negocio.

    Si llegan naive, se asume que representan hora local del negocio.
    Si llegan con tz, se convierten a la zona configurada para evitar
    desplazamientos al serializar hacia Google Calendar.
    """
    if dt.tzinfo is None:
        return dt.replace(tzinfo=TZ)
    return dt.astimezone(TZ)


@dataclass
class Slot:
    start: datetime
    end: datetime

    def to_human(self, tz_name: str) -> str:
        return f"{self.start.strftime('%a %d/%m %H:%M')}"


def _client_config() -> dict:
    return {
        "web": {
            "client_id": settings.google_client_id,
            "client_secret": settings.google_client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [settings.google_redirect_uri],
        }
    }


def _load_creds(tenant_id: str = "default") -> Credentials | None:
    """Carga credenciales del tenant.

    Si el tenant no tiene un token propio, cae al `default.json`. Este fallback
    es útil en demos y entornos donde un único consent de Google cubre todos
    los calendarios relevantes (típico cuando los calendarios están compartidos
    como editor con la cuenta que hizo el OAuth). En un despliegue multi-tenant
    real conviene que cada tenant tenga su propio token.
    """
    path = TOKENS_DIR / f"{tenant_id}.json"
    if not path.exists():
        fallback = TOKENS_DIR / "default.json"
        if tenant_id != "default" and fallback.exists():
            log.info("Tenant '%s' sin token propio; usando default.json", tenant_id)
            path = fallback
        else:
            return None
    data = json.loads(path.read_text())
    creds = Credentials.from_authorized_user_info(data, SCOPES)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        path.write_text(creds.to_json())
    return creds


def _save_creds(creds: Credentials, tenant_id: str = "default") -> None:
    path = TOKENS_DIR / f"{tenant_id}.json"
    path.write_text(creds.to_json())


# Cache por tenant_id. El objeto Credentials se auto-refresca con su propio
# refresh_token, así que podemos reutilizar el `service` construido. Reconstruirlo
# en cada tool call cuesta ~100-300ms (lectura de disco + discovery). En voz
# cualquier milisegundo importa.
_SERVICE_CACHE: dict[str, object] = {}


def _service(tenant_id: str = "default"):
    cached = _SERVICE_CACHE.get(tenant_id)
    if cached is not None:
        return cached
    creds = _load_creds(tenant_id)
    if not creds:
        raise RuntimeError(
            f"Sin credenciales para tenant '{tenant_id}'. "
            "Ejecuta: python -m app.calendar_service authorize"
        )
    svc = build("calendar", "v3", credentials=creds, cache_discovery=False)
    _SERVICE_CACHE[tenant_id] = svc
    return svc


def _invalidate_service_cache(tenant_id: str | None = None) -> None:
    """Limpia el cache de servicios. Útil si se re-autoriza un tenant."""
    if tenant_id:
        _SERVICE_CACHE.pop(tenant_id, None)
    else:
        _SERVICE_CACHE.clear()


# -------- Freebusy short-TTL cache --------
# Objetivo: cortar latencia cuando Ana llama consultar_disponibilidad varias
# veces en segundos (p.ej. reintento, cambio de duración, prueba con otro
# peluquero). Un TTL muy corto evita problemas de consistencia con reservas
# recientes: _FREEBUSY_TTL = 8s y cualquier crear/mover/cancelar invalida.
import time as _time_mod  # evita chocar con datetime.time importado arriba
_FREEBUSY_TTL = 8.0
_FREEBUSY_CACHE: dict[tuple, tuple[float, dict]] = {}


def _freebusy_query(svc, tenant_id: str, body: dict) -> dict:
    """Llama a svc.freebusy().query con caché de ~8s por (tenant, ventana, cals)."""
    items = body.get("items") or []
    key = (
        tenant_id,
        body.get("timeMin"),
        body.get("timeMax"),
        body.get("timeZone"),
        tuple(sorted(i["id"] for i in items if "id" in i)),
    )
    now = _time_mod.monotonic()
    hit = _FREEBUSY_CACHE.get(key)
    if hit and (now - hit[0]) < _FREEBUSY_TTL:
        return hit[1]
    fb = svc.freebusy().query(body=body).execute()
    _FREEBUSY_CACHE[key] = (now, fb)
    # Limpieza oportunista: si el cache crece mucho, descarta entradas viejas.
    if len(_FREEBUSY_CACHE) > 64:
        for k, (ts, _v) in list(_FREEBUSY_CACHE.items()):
            if (now - ts) >= _FREEBUSY_TTL:
                _FREEBUSY_CACHE.pop(k, None)
    return fb


def _invalidate_freebusy_cache(tenant_id: str | None = None) -> None:
    """Invalida el cache de freebusy. Úsalo al crear/mover/cancelar eventos."""
    if tenant_id is None:
        _FREEBUSY_CACHE.clear()
        return
    for k in list(_FREEBUSY_CACHE.keys()):
        if k[0] == tenant_id:
            _FREEBUSY_CACHE.pop(k, None)


def authorize_interactive(tenant_id: str = "default") -> None:
    """Abre el flujo OAuth en el navegador (solo para desarrollo local).

    IMPORTANTE: google-auth-oauthlib siempre envía redirect_uri como
    `http://localhost:<port>/` (path raíz). Por tanto el redirect URI que
    registréis en Google Cloud DEBE ser exactamente `http://localhost:8765/`
    (o cualquier puerto libre que elijáis aquí). Si tenéis `/oauth/callback`
    en GCP, añadid también `http://localhost:8765/` — Google permite múltiples
    URIs por cliente.
    """
    flow = InstalledAppFlow.from_client_config(_client_config(), SCOPES)
    creds = flow.run_local_server(port=8765)
    _save_creds(creds, tenant_id)
    print(f"OK: guardado en .tokens/{tenant_id}.json")


# ---------- Operaciones de calendario ----------

def _ranges_for_day(
    business_hours: dict | None,
    fallback: tuple[time, time],
    weekday_py: int,
) -> list[tuple[time, time]]:
    """Devuelve las franjas del día `weekday_py` usando el dict business_hours
    si viene, o `fallback` (una sola franja) si no.

    Devuelve [] si el día está cerrado según business_hours. Si business_hours
    es None, siempre devuelve [fallback] (comportamiento legacy).
    """
    if business_hours is None:
        return [fallback]
    # Import local para evitar ciclos (db importa config/settings)
    from . import db as _db
    return _db.ranges_for_weekday(business_hours, weekday_py)


def listar_huecos_libres(
    fecha_desde: datetime,
    fecha_hasta: datetime,
    duracion_minutos: int,
    calendar_id: str | None = None,
    tenant_id: str = "default",
    horario_apertura: tuple[time, time] = (time(9, 0), time(20, 0)),
    business_hours: dict | None = None,
) -> list[Slot]:
    """Busca huecos libres con la duración pedida en el rango dado.

    Si se pasa `business_hours`, respeta las franjas por día (soporta turnos
    partidos tipo 09-12 + 14-20). Si no, se usa `horario_apertura` como franja
    única para todos los días (legacy).
    """
    svc = _service(tenant_id)
    cal = calendar_id or settings.default_calendar_id
    fecha_desde = _ensure_local_tz(fecha_desde)
    fecha_hasta = _ensure_local_tz(fecha_hasta)
    body = {
        "timeMin": fecha_desde.isoformat(),
        "timeMax": fecha_hasta.isoformat(),
        "timeZone": settings.default_timezone,
        "items": [{"id": cal}],
    }
    fb = _freebusy_query(svc, tenant_id, body)
    busy = [
        (datetime.fromisoformat(p["start"].replace("Z", "+00:00")),
         datetime.fromisoformat(p["end"].replace("Z", "+00:00")))
        for p in fb["calendars"][cal]["busy"]
    ]
    busy.sort()

    slots: list[Slot] = []
    delta = timedelta(minutes=duracion_minutos)

    # Recorrido por días dentro del rango, iterando cada franja por día.
    day = fecha_desde.astimezone(TZ).date()
    end_day = fecha_hasta.astimezone(TZ).date()
    while day <= end_day:
        day_ranges = _ranges_for_day(business_hours, horario_apertura, day.weekday())
        for open_t, close_t in day_ranges:
            start_dt = datetime.combine(day, open_t, tzinfo=TZ)
            end_dt = datetime.combine(day, close_t, tzinfo=TZ)
            cursor = start_dt
            while cursor + delta <= end_dt:
                slot_end = cursor + delta
                collision = any(
                    not (slot_end <= b_start or cursor >= b_end)
                    for b_start, b_end in busy
                )
                if not collision:
                    slots.append(Slot(cursor, slot_end))
                cursor += delta
        day += timedelta(days=1)

    return slots


def listar_huecos_por_peluqueros(
    fecha_desde: datetime,
    fecha_hasta: datetime,
    duracion_minutos: int,
    peluqueros: list[dict],
    tenant_id: str = "default",
    horario_apertura: tuple[time, time] = (time(9, 0), time(20, 0)),
    business_hours: dict | None = None,
) -> list[dict]:
    """Huecos libres teniendo en cuenta varios peluqueros a la vez.

    Cada peluquero es un dict con keys: nombre, calendar_id, dias_trabajo
    (lista de weekday de Python: 0=lunes..6=domingo).

    Un slot está disponible para un peluquero si:
      - El día de la semana está en su dias_trabajo.
      - No hay eventos que solapen en su calendario propio.
    """
    if not peluqueros:
        return []

    svc = _service(tenant_id)
    fecha_desde = _ensure_local_tz(fecha_desde)
    fecha_hasta = _ensure_local_tz(fecha_hasta)
    body = {
        "timeMin": fecha_desde.isoformat(),
        "timeMax": fecha_hasta.isoformat(),
        "timeZone": settings.default_timezone,
        "items": [{"id": p["calendar_id"]} for p in peluqueros],
    }
    fb = _freebusy_query(svc, tenant_id, body)
    busy_por_cal: dict[str, list[tuple[datetime, datetime]]] = {}
    for cal_id, info in fb["calendars"].items():
        periodos = [
            (datetime.fromisoformat(p["start"].replace("Z", "+00:00")),
             datetime.fromisoformat(p["end"].replace("Z", "+00:00")))
            for p in info.get("busy", [])
        ]
        periodos.sort()
        busy_por_cal[cal_id] = periodos

    delta = timedelta(minutes=duracion_minutos)
    resultados: list[dict] = []

    # La ventana intra-día que realmente exploramos es la intersección entre
    # (horario del negocio) y (ventana solicitada fecha_desde–fecha_hasta).
    # Así, si el cliente pide "por la tarde" (15:00–20:30), no ofrecemos
    # huecos de la mañana.
    fecha_desde_local = fecha_desde.astimezone(TZ)
    fecha_hasta_local = fecha_hasta.astimezone(TZ)

    day = fecha_desde_local.date()
    end_day = fecha_hasta_local.date()
    while day <= end_day:
        # Respeta todas las franjas del día si business_hours viene con varias
        # (turnos partidos). En caso contrario, una única franja con
        # horario_apertura como antes.
        day_ranges = _ranges_for_day(business_hours, horario_apertura, day.weekday())
        for open_t, close_t in day_ranges:
            open_dt = datetime.combine(day, open_t, tzinfo=TZ)
            close_dt = datetime.combine(day, close_t, tzinfo=TZ)
            # Recortamos por la ventana pedida en el primer y último día.
            start_dt = max(open_dt, fecha_desde_local) if day == fecha_desde_local.date() else open_dt
            end_dt = min(close_dt, fecha_hasta_local) if day == fecha_hasta_local.date() else close_dt
            cursor = start_dt
            while cursor + delta <= end_dt:
                slot_end = cursor + delta
                for p in peluqueros:
                    dias = p.get("dias_trabajo") or list(range(7))
                    if day.weekday() not in dias:
                        continue
                    busy = busy_por_cal.get(p["calendar_id"], [])
                    collision = any(
                        not (slot_end <= b_start or cursor >= b_end)
                        for b_start, b_end in busy
                    )
                    if not collision:
                        resultados.append({
                            "inicio": cursor,
                            "fin": slot_end,
                            "peluquero": p["nombre"],
                            "calendar_id": p["calendar_id"],
                        })
                cursor += delta
        day += timedelta(days=1)

    return resultados


def crear_evento(
    titulo: str,
    inicio: datetime,
    fin: datetime,
    descripcion: str = "",
    telefono_cliente: str = "",
    nombre_cliente: str = "",
    calendar_id: str | None = None,
    tenant_id: str = "default",
    # Metadatos opcionales que el portal usa para reconstruir las reservas.
    # Quedan en extendedProperties.private y no afectan a la vista del
    # usuario en Google Calendar.
    service_id: str | int | None = None,
    member_id: str | int | None = None,
    channel: str = "bot",
) -> dict:
    svc = _service(tenant_id)
    cal = calendar_id or settings.default_calendar_id
    inicio = _ensure_local_tz(inicio)
    fin = _ensure_local_tz(fin)

    nombre = (nombre_cliente or "").strip()
    summary = titulo or ""
    if nombre and nombre.lower() not in summary.lower():
        summary = f"{summary} — {nombre}" if summary else nombre

    desc_lines = []
    if nombre:
        desc_lines.append(f"Cliente: {nombre}")
    if descripcion:
        desc_lines.append(descripcion.strip())
    if telefono_cliente:
        desc_lines.append(f"Tel. cliente: {telefono_cliente}")
    description = "\n\n".join(desc_lines).strip()

    private: dict[str, str] = {
        "phone": telefono_cliente or "",
        "client_name": nombre,
        "created_by": channel or "bot",
        "channel": channel or "bot",
    }
    if service_id is not None:
        private["service_id"] = str(service_id)
    if member_id is not None:
        private["member_id"] = str(member_id)

    event = {
        "summary": summary,
        "description": description,
        "start": {"dateTime": inicio.isoformat(), "timeZone": settings.default_timezone},
        "end": {"dateTime": fin.isoformat(), "timeZone": settings.default_timezone},
        "extendedProperties": {"private": private},
    }
    res = svc.events().insert(calendarId=cal, body=event).execute()
    _invalidate_freebusy_cache(tenant_id)
    return res


def mover_evento(
    event_id: str,
    nuevo_inicio: datetime,
    nuevo_fin: datetime,
    calendar_id: str | None = None,
    tenant_id: str = "default",
) -> dict:
    svc = _service(tenant_id)
    cal = calendar_id or settings.default_calendar_id
    nuevo_inicio = _ensure_local_tz(nuevo_inicio)
    nuevo_fin = _ensure_local_tz(nuevo_fin)
    patch = {
        "start": {"dateTime": nuevo_inicio.isoformat(), "timeZone": settings.default_timezone},
        "end": {"dateTime": nuevo_fin.isoformat(), "timeZone": settings.default_timezone},
    }
    res = svc.events().patch(calendarId=cal, eventId=event_id, body=patch).execute()
    _invalidate_freebusy_cache(tenant_id)
    return res


def cancelar_evento(
    event_id: str,
    calendar_id: str | None = None,
    tenant_id: str = "default",
) -> None:
    svc = _service(tenant_id)
    cal = calendar_id or settings.default_calendar_id
    svc.events().delete(calendarId=cal, eventId=event_id).execute()
    _invalidate_freebusy_cache(tenant_id)


def buscar_evento_por_telefono(
    telefono: str,
    desde: datetime,
    hasta: datetime,
    calendar_id: str | None = None,
    tenant_id: str = "default",
) -> dict | None:
    """Busca el próximo evento de un cliente por su teléfono."""
    svc = _service(tenant_id)
    cal = calendar_id or settings.default_calendar_id
    desde = _ensure_local_tz(desde)
    hasta = _ensure_local_tz(hasta)
    events = svc.events().list(
        calendarId=cal,
        timeMin=desde.isoformat(),
        timeMax=hasta.isoformat(),
        singleEvents=True,
        orderBy="startTime",
        privateExtendedProperty=f"phone={telefono}",
    ).execute().get("items", [])
    return events[0] if events else None


def buscar_evento_por_nombre(
    nombre: str,
    desde: datetime,
    hasta: datetime,
    calendar_id: str | None = None,
    tenant_id: str = "default",
) -> dict | None:
    """Busca el próximo evento por nombre del cliente.

    Usa el parámetro `q` de Google Calendar `events.list`, que hace
    búsqueda full-text sobre summary, description, location, attendees,
    etc. Filtra los resultados para evitar matchear al peluquero/profesional:

    - **Match estricto A**: `extendedProperties.private.client_name` coincide
      (exacto o como substring) con el nombre buscado. Este es el más fiable
      porque `crear_evento` escribe el nombre del cliente en este campo.

    - **Match estricto B**: el `summary` empieza por el nombre buscado. Título
      canónico es `Nombre — Servicio (con Peluquero)`, así que si el summary
      empieza por "Mario —" es cita DE Mario; si aparece "con Mario" al final
      es el peluquero, NO matchea.

    - Si ninguno matchea, devolvemos `None` (mejor vacío que falso positivo).
    """
    nombre = (nombre or "").strip()
    if not nombre:
        return None
    svc = _service(tenant_id)
    cal = calendar_id or settings.default_calendar_id
    desde = _ensure_local_tz(desde)
    hasta = _ensure_local_tz(hasta)
    events = svc.events().list(
        calendarId=cal,
        timeMin=desde.isoformat(),
        timeMax=hasta.isoformat(),
        singleEvents=True,
        orderBy="startTime",
        q=nombre,
        maxResults=20,
    ).execute().get("items", [])
    nombre_low = nombre.lower()
    for ev in events:
        priv = ((ev.get("extendedProperties") or {}).get("private") or {})
        client_name_low = (priv.get("client_name") or "").lower().strip()
        # Match A: client_name — lo más fiable.
        if client_name_low and (
            client_name_low == nombre_low
            or nombre_low in client_name_low
            or client_name_low in nombre_low
        ):
            return ev
        # Match B: summary empieza por el nombre (convención "Nombre — Servicio").
        summary = (ev.get("summary") or "").strip().lower()
        if summary.startswith(nombre_low + " ") or summary.startswith(nombre_low + " —") \
                or summary.startswith(nombre_low + "—") or summary == nombre_low:
            return ev
    # No match estricto → no devolvemos nada. Evita confundir peluqueros con
    # clientes ("Mario" aparece en "Eva — Corte (con Mario)" pero NO es cita de Mario).
    return None


def listar_eventos(
    desde: datetime,
    hasta: datetime,
    calendar_id: str | None = None,
    tenant_id: str = "default",
) -> list[dict]:
    """Lista eventos del calendario en un rango.

    Devuelve la lista cruda de eventos de Google (ya expandidos: singleEvents=True,
    ordenados por startTime). Quien llama los mapea al formato del portal.
    """
    svc = _service(tenant_id)
    cal = calendar_id or settings.default_calendar_id
    desde = _ensure_local_tz(desde)
    hasta = _ensure_local_tz(hasta)
    items: list[dict] = []
    page_token: str | None = None
    while True:
        params = dict(
            calendarId=cal,
            timeMin=desde.isoformat(),
            timeMax=hasta.isoformat(),
            singleEvents=True,
            orderBy="startTime",
            maxResults=250,
        )
        if page_token:
            params["pageToken"] = page_token
        resp = svc.events().list(**params).execute()
        items.extend(resp.get("items", []))
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    return items


# ---------- CLI mínima para autorizar ----------
if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "authorize":
        tenant = sys.argv[2] if len(sys.argv) > 2 else "default"
        authorize_interactive(tenant)
    else:
        print("Uso: python -m app.calendar_service authorize [tenant_id]")
