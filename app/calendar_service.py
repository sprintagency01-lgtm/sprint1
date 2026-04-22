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
    path = TOKENS_DIR / f"{tenant_id}.json"
    if not path.exists():
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


def _service(tenant_id: str = "default"):
    creds = _load_creds(tenant_id)
    if not creds:
        raise RuntimeError(
            f"Sin credenciales para tenant '{tenant_id}'. "
            "Ejecuta: python -m app.calendar_service authorize"
        )
    return build("calendar", "v3", credentials=creds, cache_discovery=False)


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

def listar_huecos_libres(
    fecha_desde: datetime,
    fecha_hasta: datetime,
    duracion_minutos: int,
    calendar_id: str | None = None,
    tenant_id: str = "default",
    horario_apertura: tuple[time, time] = (time(9, 0), time(20, 0)),
) -> list[Slot]:
    """Busca huecos libres con la duración pedida en el rango dado.

    Simple heurística: free/busy + rellenar con slots contiguos dentro del horario.
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
    fb = svc.freebusy().query(body=body).execute()
    busy = [
        (datetime.fromisoformat(p["start"].replace("Z", "+00:00")),
         datetime.fromisoformat(p["end"].replace("Z", "+00:00")))
        for p in fb["calendars"][cal]["busy"]
    ]
    busy.sort()

    slots: list[Slot] = []
    delta = timedelta(minutes=duracion_minutos)

    # Recorrido por días dentro del rango
    day = fecha_desde.astimezone(TZ).date()
    end_day = fecha_hasta.astimezone(TZ).date()
    while day <= end_day:
        start_dt = datetime.combine(day, horario_apertura[0], tzinfo=TZ)
        end_dt = datetime.combine(day, horario_apertura[1], tzinfo=TZ)
        cursor = start_dt
        while cursor + delta <= end_dt:
            # ¿colisiona con algún busy?
            slot_end = cursor + delta
            collision = any(
                not (slot_end <= b_start.replace(tzinfo=None)
                     or cursor >= b_end.replace(tzinfo=None))
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
    fb = svc.freebusy().query(body=body).execute()
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

    day = fecha_desde.astimezone(TZ).date()
    end_day = fecha_hasta.astimezone(TZ).date()
    while day <= end_day:
        start_dt = datetime.combine(day, horario_apertura[0], tzinfo=TZ)
        end_dt = datetime.combine(day, horario_apertura[1], tzinfo=TZ)
        cursor = start_dt
        while cursor + delta <= end_dt:
            slot_end = cursor + delta
            for p in peluqueros:
                dias = p.get("dias_trabajo") or list(range(7))
                if day.weekday() not in dias:
                    continue
                busy = busy_por_cal.get(p["calendar_id"], [])
                collision = any(
                    not (slot_end <= b_start.replace(tzinfo=None)
                         or cursor >= b_end.replace(tzinfo=None))
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
    calendar_id: str | None = None,
    tenant_id: str = "default",
) -> dict:
    svc = _service(tenant_id)
    cal = calendar_id or settings.default_calendar_id
    inicio = _ensure_local_tz(inicio)
    fin = _ensure_local_tz(fin)
    event = {
        "summary": titulo,
        "description": f"{descripcion}\n\nTel. cliente: {telefono_cliente}".strip(),
        "start": {"dateTime": inicio.isoformat(), "timeZone": settings.default_timezone},
        "end": {"dateTime": fin.isoformat(), "timeZone": settings.default_timezone},
        "extendedProperties": {
            "private": {"phone": telefono_cliente, "created_by": "bot"}
        },
    }
    return svc.events().insert(calendarId=cal, body=event).execute()


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
    return svc.events().patch(calendarId=cal, eventId=event_id, body=patch).execute()


def cancelar_evento(
    event_id: str,
    calendar_id: str | None = None,
    tenant_id: str = "default",
) -> None:
    svc = _service(tenant_id)
    cal = calendar_id or settings.default_calendar_id
    svc.events().delete(calendarId=cal, eventId=event_id).execute()


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


# ---------- CLI mínima para autorizar ----------
if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "authorize":
        tenant = sys.argv[2] if len(sys.argv) > 2 else "default"
        authorize_interactive(tenant)
    else:
        print("Uso: python -m app.calendar_service authorize [tenant_id]")
