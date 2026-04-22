"""Endpoints HTTP que ElevenLabs Conversational AI llama como "server tools".

El agente de voz, cuando habla con un cliente, decide qué herramienta ejecutar
y hace una llamada HTTP POST a estos endpoints con los argumentos como JSON.
Nosotros ejecutamos la operación real contra Google Calendar y devolvemos el
resultado (que ElevenLabs devuelve al LLM para que siga la conversación).

Protegidos con un secreto compartido (X-Tool-Secret) para que nadie externo
pueda provocar reservas falsas.

Para cambiar de tenant, el agente pasa `tenant_id` como query param al
registrar la URL. En el MVP (un solo negocio) basta con que caiga en el primero.
"""
from __future__ import annotations

import json
import logging
import time
import traceback
from datetime import datetime, timedelta
from typing import Any, Callable

from fastapi import APIRouter, Header, HTTPException, Query
from pydantic import BaseModel, Field

from .config import settings
from . import calendar_service as cal
from . import tenants as tn

log = logging.getLogger(__name__)

router = APIRouter(prefix="/tools", tags=["voice-agent-tools"])


# ---------- Retry helper ----------

def _retry_google(fn: Callable[[], Any], op_name: str, attempts: int = 2, sleep_s: float = 0.8):
    """Ejecuta `fn` reintentando ante errores transitorios de Google Calendar.

    Google a veces tira 500/503/429 puntuales (rate-limits, hiccups). En un
    flujo de voz hay que reintentar AL MENOS una vez antes de tirar a mano: si
    no, el cliente oye "llame al 910" cuando el siguiente segundo todo funciona.
    """
    last_exc = None
    for i in range(attempts):
        try:
            return fn()
        except Exception as e:  # pragma: no cover - incluye HttpError, ConnectionError, etc.
            msg = str(e)
            last_exc = e
            transient = any(tok in msg for tok in ("500", "502", "503", "504",
                                                     "rateLimit", "quotaExceeded",
                                                     "Internal Server Error",
                                                     "backendError", "timeout"))
            log.warning("[%s] intento %d/%d falló (transient=%s): %s",
                         op_name, i + 1, attempts, transient, msg)
            if not transient or i == attempts - 1:
                raise
            time.sleep(sleep_s * (i + 1))
    # no debería llegar aquí
    raise last_exc  # type: ignore[misc]


# ---------- Auth helper ----------

def _check_secret(x_tool_secret: str | None) -> None:
    """Exige el header X-Tool-Secret y comprueba que coincida con TOOL_SECRET del .env."""
    expected = settings.tool_secret
    if not expected:
        raise HTTPException(
            status_code=500,
            detail="TOOL_SECRET no configurado en .env. Sin secreto, no abrimos endpoints.",
        )
    if x_tool_secret != expected:
        raise HTTPException(status_code=401, detail="Bad X-Tool-Secret")


def _resolve_tenant(tenant_id: str | None) -> dict:
    """Devuelve el dict del tenant. Si no se especifica, usa el primero del YAML."""
    all_tenants = tn.load_tenants()
    if tenant_id:
        for t in all_tenants:
            if t.get("id") == tenant_id:
                return t
        raise HTTPException(status_code=404, detail=f"Tenant '{tenant_id}' no encontrado")
    return all_tenants[0]


def _calendar_id_for_booking(tenant: dict, peluquero: str | None = None) -> str:
    """Calendario destino para nuevas reservas en este entorno.

    En fase de pruebas queremos que la reserva final aparezca en el calendario
    principal del cliente (Sprintagency), salvo que en el futuro se decida otro
    comportamiento.
    """
    return tenant.get("calendar_id") or settings.default_calendar_id


# ---------- Pydantic models ----------

class ConsultaReq(BaseModel):
    fecha_desde_iso: str = Field(..., description="ISO 8601 (Europe/Madrid naive o con offset)")
    fecha_hasta_iso: str
    duracion_minutos: int
    peluquero_preferido: str | None = None
    max_resultados: int = 5


class CrearReq(BaseModel):
    titulo: str
    inicio_iso: str
    fin_iso: str
    # El teléfono es opcional: algunos clientes no lo quieren dar y preferimos
    # que la reserva se cree igualmente a que salte un 422 y Ana se corte.
    telefono_cliente: str | None = None
    peluquero: str = "sin preferencia"
    notas: str = ""


class BuscarReq(BaseModel):
    # Opcional: con caller_id inyectado vía query param, el LLM no manda teléfono.
    # Si lo manda explícito (cliente da otro número), se usa ese. Si no, se cae
    # al caller_id como fallback dentro del handler.
    telefono_cliente: str | None = None
    dias_adelante: int = 30


class MoverReq(BaseModel):
    event_id: str
    nuevo_inicio_iso: str
    nuevo_fin_iso: str
    peluquero: str | None = None  # si se mueve entre peluqueros


class CancelarReq(BaseModel):
    event_id: str


# ---------- Helpers internos ----------

def _peluqueros_filtrados(tenant: dict, preferido: str | None) -> list[dict]:
    pelus = tenant.get("peluqueros") or []
    if preferido:
        pref = preferido.strip().lower()
        match = [p for p in pelus if p["nombre"].strip().lower() == pref]
        if match:
            return match
    return pelus


def _horario(tenant: dict):
    """Devuelve (apertura, cierre) típicas del negocio.

    Soporta dos esquemas de `business_hours`:
      - Plano (YAML legacy): {"open": "09:30", "close": "20:30"}
      - Por día (BD CMS):    {"mon": ["09:30","20:30"], "sat": ["closed"]}
    Con schema por-día se toma el primer día no cerrado para delimitar el
    rango intra-día de búsqueda; la validación por día laborable la imponen
    los `dias_trabajo` de cada peluquero en calendar_service.
    """
    from datetime import time as _time
    bh = tenant.get("business_hours") or {}

    def _p(s, d):
        try:
            h, m = s.split(":")
            return _time(int(h), int(m))
        except Exception:
            return d

    # Schema plano
    if "open" in bh or "close" in bh:
        return (_p(bh.get("open", "09:00"), _time(9, 0)),
                _p(bh.get("close", "20:00"), _time(20, 0)))

    # Schema por día — primer día no cerrado
    for day_key in ("mon", "tue", "wed", "thu", "fri", "sat", "sun"):
        h = bh.get(day_key)
        if h and h != ["closed"] and h[0] != "closed" and len(h) >= 2:
            return (_p(h[0], _time(9, 0)), _p(h[1], _time(20, 0)))

    return (_time(9, 0), _time(20, 0))


# ---------- Endpoints ----------

@router.post("/consultar_disponibilidad")
def consultar_disponibilidad(
    req: ConsultaReq,
    x_tool_secret: str | None = Header(None),
    tenant_id: str | None = Query(None),
) -> dict[str, Any]:
    _check_secret(x_tool_secret)
    tenant = _resolve_tenant(tenant_id)

    # Parseo defensivo: si el LLM nos manda una fecha con formato raro, no
    # queremos explotar con 500 — preferimos que Ana reciba un mensaje legible.
    try:
        desde = datetime.fromisoformat(req.fecha_desde_iso)
        hasta = datetime.fromisoformat(req.fecha_hasta_iso)
    except ValueError as e:
        log.warning("consultar_disponibilidad: fechas inválidas %s / %s: %s",
                    req.fecha_desde_iso, req.fecha_hasta_iso, e)
        return {
            "huecos": [],
            "error": f"No entiendo la fecha. Prueba de nuevo con un formato claro.",
            "retryable": False,
            "detail": str(e)[:200],
        }
    horario = _horario(tenant)
    limit = max(1, min(req.max_resultados, 15))
    peluqueros = tenant.get("peluqueros") or []

    if peluqueros:
        # Caso 1: el cliente pidió un peluquero concreto pero NO existe.
        # Antes _peluqueros_filtrados caía a "todos los peluqueros" cuando no
        # encontraba match, así que Ana ofrecía huecos de Mario al pedir "Pepa".
        # Ahora devolvemos aviso explícito con la lista real.
        preferido_norm = (req.peluquero_preferido or "").strip().lower()
        if preferido_norm:
            match = [p for p in peluqueros if p["nombre"].strip().lower() == preferido_norm]
            if not match:
                return {
                    "huecos": [],
                    "aviso": (
                        f"No tengo peluquero con nombre '{req.peluquero_preferido}'. "
                        "Peluqueros: " + ", ".join(p["nombre"] for p in peluqueros)
                    ),
                }
            pelus = match
        else:
            pelus = peluqueros

        try:
            huecos = _retry_google(
                lambda: cal.listar_huecos_por_peluqueros(
                    desde, hasta, req.duracion_minutos,
                    peluqueros=pelus,
                    tenant_id=tenant.get("id", "default"),
                    horario_apertura=horario,
                ),
                "listar_huecos_por_peluqueros",
            )
        except Exception as e:  # noqa: BLE001
            log.error("consultar_disponibilidad falló: %s\n%s", e, traceback.format_exc())
            return {
                "huecos": [],
                "error": "No he podido consultar la agenda ahora mismo. Vuélvelo a intentar en un momento.",
                "retryable": True,
                "detail": str(e)[:200],
            }
        huecos.sort(key=lambda h: h["inicio"])

        # Caso 2: el cliente pidió peluquero concreto y no hay huecos, típicamente
        # porque ese día no trabaja (Marcos solo miércoles, por ejemplo). Sin
        # aviso Ana diría "no hay huecos" a secas y el cliente se queda perdido.
        aviso = None
        if preferido_norm and not huecos:
            p = pelus[0]
            dias = p.get("dias_trabajo") or list(range(7))
            dias_es = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
            dias_nombre = [dias_es[d] for d in dias if 0 <= d <= 6]
            aviso = (
                f"{p['nombre']} no tiene huecos en ese rango. "
                f"Días que trabaja: {', '.join(dias_nombre)}."
            )

        resp: dict[str, Any] = {
            "huecos": [
                {
                    "inicio": h["inicio"].isoformat(),
                    "fin": h["fin"].isoformat(),
                    "peluquero": h["peluquero"],
                }
                for h in huecos[:limit]
            ]
        }
        if aviso:
            resp["aviso"] = aviso
        return resp

    # Fallback: modo calendario único
    try:
        slots = _retry_google(
            lambda: cal.listar_huecos_libres(
                desde, hasta, req.duracion_minutos,
                calendar_id=tenant.get("calendar_id") or settings.default_calendar_id,
                tenant_id=tenant.get("id", "default"),
                horario_apertura=horario,
            ),
            "listar_huecos_libres",
        )
    except Exception as e:  # noqa: BLE001
        log.error("consultar_disponibilidad (single cal) falló: %s\n%s", e, traceback.format_exc())
        return {
            "huecos": [],
            "error": "No he podido consultar la agenda ahora mismo. Vuélvelo a intentar en un momento.",
            "retryable": True,
            "detail": str(e)[:200],
        }
    return {"huecos": [{"inicio": s.start.isoformat(), "fin": s.end.isoformat()} for s in slots[:limit]]}


@router.post("/crear_reserva")
def crear_reserva(
    req: CrearReq,
    x_tool_secret: str | None = Header(None),
    tenant_id: str | None = Query(None),
    caller_id: str | None = Query(None),
) -> dict[str, Any]:
    _check_secret(x_tool_secret)
    tenant = _resolve_tenant(tenant_id)
    peluqueros = tenant.get("peluqueros") or []

    # Parseo defensivo de fechas (evita reintentos inútiles contra Google)
    try:
        inicio_dt = datetime.fromisoformat(req.inicio_iso)
        fin_dt = datetime.fromisoformat(req.fin_iso)
    except ValueError as e:
        log.warning("crear_reserva: fechas inválidas %s / %s: %s",
                    req.inicio_iso, req.fin_iso, e)
        return {
            "ok": False,
            "error": "No entiendo la fecha/hora de la cita. Vuelve a confirmar con el cliente.",
            "retryable": False,
            "detail": str(e)[:200],
        }

    destino_cal = _calendar_id_for_booking(tenant, req.peluquero)
    peluquero = (req.peluquero or "").strip()
    if peluqueros and peluquero and peluquero.lower() != "sin preferencia":
        match = [p for p in peluqueros if p["nombre"].strip().lower() == peluquero.lower()]
        if not match:
            raise HTTPException(
                status_code=400,
                detail=f"Peluquero '{peluquero}' no existe. "
                       f"Opciones: " + ", ".join(p["nombre"] for p in peluqueros),
            )

    # Algunos LLMs serializan Python None como la string literal "None" cuando
    # no deberían enviar nada. Normalizamos para que no acabemos poniendo
    # "None" / "null" en la descripción del evento de calendario.
    tel = (req.telefono_cliente or "").strip()
    if tel.lower() in ("none", "null", "n/a", "na", "sin telefono", "sin teléfono", "-"):
        tel = ""
    # Si el LLM no mandó teléfono pero ElevenLabs sí nos dio el caller_id de
    # la llamada entrante, lo usamos automáticamente. Así Ana no tiene que
    # pedir el número en llamadas reales.
    if not tel and caller_id:
        cid = caller_id.strip()
        if cid.lower() not in ("none", "null", "n/a", "na", "-", "unknown", "anonymous", ""):
            tel = cid
    # Reintenta ante errores transitorios de Google. Si aun así falla,
    # devolvemos 200 con ok:false + mensaje legible para que Ana pueda decidir
    # si reintenta o deriva al número de tienda, en vez de oír un 500 mudo.
    try:
        ev = _retry_google(
            lambda: cal.crear_evento(
                titulo=req.titulo,
                inicio=inicio_dt,
                fin=fin_dt,
                descripcion=req.notas,
                telefono_cliente=tel,
                calendar_id=destino_cal,
                tenant_id=tenant.get("id", "default"),
            ),
            "crear_evento",
        )
    except Exception as e:  # noqa: BLE001 - queremos cualquier excepción
        log.error("crear_reserva falló tras reintentos: %s\n%s",
                  e, traceback.format_exc())
        return {
            "ok": False,
            "error": (
                "No se ha podido guardar la cita en el calendario ahora mismo. "
                "Puede ser un pequeño corte del servicio de Google. "
                "Intenta de nuevo en unos segundos."
            ),
            "retryable": True,
            "detail": str(e)[:200],
        }
    log.info("Reserva creada por voz: %s (%s)", ev.get("id"), peluquero or "sin preferencia")
    return {
        "ok": True,
        "event_id": ev.get("id"),
        "peluquero": peluquero or "sin preferencia",
    }


@router.post("/buscar_reserva_cliente")
def buscar_reserva_cliente(
    req: BuscarReq,
    x_tool_secret: str | None = Header(None),
    tenant_id: str | None = Query(None),
    caller_id: str | None = Query(None),
) -> dict[str, Any]:
    _check_secret(x_tool_secret)
    tenant = _resolve_tenant(tenant_id)
    peluqueros = tenant.get("peluqueros") or []
    desde = datetime.utcnow()
    hasta = desde + timedelta(days=req.dias_adelante)

    # Normalizamos teléfono y hacemos fallback al caller_id si el LLM no lo pasó
    # (o pasó basura tipo "None").
    tel = (req.telefono_cliente or "").strip()
    if tel.lower() in ("none", "null", "n/a", "na", "-", "unknown", "anonymous", ""):
        tel = ""
    if not tel and caller_id:
        cid = caller_id.strip()
        if cid.lower() not in ("none", "null", "n/a", "na", "-", "unknown", "anonymous", ""):
            tel = cid
    if not tel:
        return {"encontrada": False}

    # Buscar en todos los calendarios de peluqueros + el principal
    calendars_to_check = [p["calendar_id"] for p in peluqueros]
    main_cal = tenant.get("calendar_id") or settings.default_calendar_id
    if main_cal not in calendars_to_check:
        calendars_to_check.append(main_cal)

    # Recorremos calendario a calendario. Cada uno tiene su propio try/except:
    # si uno 404 (calendario mal compartido) o tiene un hipido transitorio,
    # no queremos abortar toda la búsqueda — seguimos con los otros. Sólo si
    # TODOS fallan devolvemos error graceful (retryable) a Ana para que decida.
    errors: list[str] = []
    for cal_id in calendars_to_check:
        try:
            ev = _retry_google(
                lambda cal_id=cal_id: cal.buscar_evento_por_telefono(
                    tel, desde, hasta,
                    calendar_id=cal_id,
                    tenant_id=tenant.get("id", "default"),
                ),
                "buscar_evento_por_telefono",
            )
            if ev:
                return {
                    "encontrada": True,
                    "event_id": ev["id"],
                    "titulo": ev.get("summary"),
                    "inicio": ev["start"].get("dateTime"),
                    "fin": ev["end"].get("dateTime"),
                    "calendar_id": cal_id,
                }
        except Exception as e:  # noqa: BLE001
            errors.append(f"{cal_id[:20]}…: {str(e)[:120]}")
            log.warning("buscar_reserva_cliente: fallo en cal %s: %s", cal_id, e)
            continue

    # Si todos los calendarios fallaron (no sólo "no había cita"), devolvemos
    # graceful con retryable. Si sólo algunos 404aron pero otros respondieron
    # OK sin encontrar nada, es un "no encontrada" legítimo, no un error.
    if errors and len(errors) == len(calendars_to_check):
        log.error("buscar_reserva_cliente: todos los calendarios fallaron: %s", errors)
        return {
            "encontrada": False,
            "error": (
                "No he podido consultar el calendario ahora mismo. "
                "Puede ser un pequeño corte del servicio de Google. "
                "Intenta de nuevo en unos segundos."
            ),
            "retryable": True,
            "detail": "; ".join(errors)[:200],
        }
    return {"encontrada": False}


@router.post("/mover_reserva")
def mover_reserva(
    req: MoverReq,
    x_tool_secret: str | None = Header(None),
    tenant_id: str | None = Query(None),
) -> dict[str, Any]:
    _check_secret(x_tool_secret)
    tenant = _resolve_tenant(tenant_id)
    # En MVP el evento vive en el calendario de su peluquero original; si el
    # agente quiere cambiar de peluquero, eso requiere borrar y crear de nuevo.
    cal_id = tenant.get("calendar_id") or settings.default_calendar_id
    # Intenta detectar en qué calendario está (peluqueros primero, luego main)
    pelus = tenant.get("peluqueros") or []
    for p in pelus:
        try:
            cal.mover_evento(
                event_id=req.event_id,
                nuevo_inicio=datetime.fromisoformat(req.nuevo_inicio_iso),
                nuevo_fin=datetime.fromisoformat(req.nuevo_fin_iso),
                calendar_id=p["calendar_id"],
                tenant_id=tenant.get("id", "default"),
            )
            return {"ok": True, "calendar_id": p["calendar_id"]}
        except Exception:
            continue
    cal.mover_evento(
        event_id=req.event_id,
        nuevo_inicio=datetime.fromisoformat(req.nuevo_inicio_iso),
        nuevo_fin=datetime.fromisoformat(req.nuevo_fin_iso),
        calendar_id=cal_id,
        tenant_id=tenant.get("id", "default"),
    )
    return {"ok": True, "calendar_id": cal_id}


@router.post("/cancelar_reserva")
def cancelar_reserva(
    req: CancelarReq,
    x_tool_secret: str | None = Header(None),
    tenant_id: str | None = Query(None),
) -> dict[str, Any]:
    _check_secret(x_tool_secret)
    tenant = _resolve_tenant(tenant_id)
    pelus = tenant.get("peluqueros") or []
    # Intentar borrar en cada calendario hasta que funcione
    for p in pelus:
        try:
            cal.cancelar_evento(
                req.event_id,
                calendar_id=p["calendar_id"],
                tenant_id=tenant.get("id", "default"),
            )
            return {"ok": True, "calendar_id": p["calendar_id"]}
        except Exception:
            continue
    cal_id = tenant.get("calendar_id") or settings.default_calendar_id
    cal.cancelar_evento(
        req.event_id,
        calendar_id=cal_id,
        tenant_id=tenant.get("id", "default"),
    )
    return {"ok": True, "calendar_id": cal_id}
