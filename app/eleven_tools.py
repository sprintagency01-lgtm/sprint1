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
import random
import time
import traceback
from datetime import datetime, timedelta
from typing import Any, Callable
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Header, HTTPException, Query, Request
from pydantic import BaseModel, Field

from .config import settings
from . import calendar_service as cal
from . import tenants as tn

log = logging.getLogger(__name__)

router = APIRouter(prefix="/tools", tags=["voice-agent-tools"])


_SIN_PREFERENCIA_VALUES = {
    "",
    "sin preferencia",
    "sinpreferencia",
    "me da igual",
    "medaigual",
    "me daigual",
    "cualquiera",
    "cualquier",
    "no importa",
    "da igual",
}


# ---------- Retry helper ----------

# Tokens típicos de errores transitorios en respuestas de googleapiclient:
# códigos HTTP 5xx, límites de rate, errores internos, timeouts de red.
_TRANSIENT_ERROR_TOKENS = (
    "500", "502", "503", "504",
    "rateLimit", "quotaExceeded",
    "Internal Server Error",
    "backendError", "timeout",
)


def _retry_google(
    fn: Callable[[], Any],
    op_name: str,
    attempts: int = 2,
    base_delay_s: float = 0.4,
    max_delay_s: float = 1.5,
):
    """Ejecuta `fn` reintentando ante errores transitorios de Google Calendar.

    Backoff exponencial con jitter (AWS full jitter simplificado):
        delay_n = random(0, min(max, base * 2^n))

    Ventajas sobre la versión lineal anterior:
    - Menos colisiones cuando varios workers chocan con el mismo rate-limit.
    - En caso nominal con 2 intentos, el primer retry espera ~0-0,4s en lugar
      de un fijo 0,8s → mediana de latencia más baja en el peor caso.
    - El cap a 1,5s evita que un tercer reintento (si lo activamos) bloquee
      el hilo demasiado tiempo.
    """
    last_exc = None
    for i in range(attempts):
        try:
            return fn()
        except Exception as e:  # pragma: no cover - HttpError, ConnectionError, etc.
            msg = str(e)
            last_exc = e
            transient = any(tok in msg for tok in _TRANSIENT_ERROR_TOKENS)
            if not transient or i == attempts - 1:
                log.warning("[%s] intento %d/%d falló (transient=%s, final): %s",
                             op_name, i + 1, attempts, transient, msg[:200])
                raise
            capped = min(max_delay_s, base_delay_s * (2 ** i))
            delay = random.uniform(0, capped)
            log.warning("[%s] intento %d/%d falló (transient) — retry en %.2fs: %s",
                         op_name, i + 1, attempts, delay, msg[:200])
            time.sleep(delay)
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
    """Devuelve el dict del tenant. Si no se especifica, usa el primero.

    Hot path de voz: NO necesita `system_prompt` (usa `voice.prompt` en
    ElevenLabs), así que pide la versión ligera para ahorrar el render del
    prompt en cada tool call. Además se apoya en el caché in-memory de
    `tenants.py` (TTL 30s): típicamente <0,1ms por llamada en caché
    caliente vs ~10-30ms con lectura BD + YAML + render.
    """
    if tenant_id:
        t = tn.get_tenant(tenant_id, include_system_prompt=False)
        if t is None:
            raise HTTPException(status_code=404, detail=f"Tenant '{tenant_id}' no encontrado")
        return t
    all_tenants = tn.load_tenants()
    if not all_tenants:
        raise HTTPException(status_code=500, detail="No hay tenants configurados")
    return all_tenants[0]


def _calendar_id_for_booking(tenant: dict, peluquero: str | None = None) -> str:
    """Calendario destino para nuevas reservas en este entorno.

    En fase de pruebas queremos que la reserva final aparezca en el calendario
    principal del cliente (Sprintagency), salvo que en el futuro se decida otro
    comportamiento.
    """
    return tenant.get("calendar_id") or settings.default_calendar_id


def _is_sin_preferencia(value: str | None) -> bool:
    """Normaliza las variantes típicas de "sin preferencia".

    El contrato histórico del proyecto usa a veces cadena vacía y a veces la
    literal "sin preferencia". Si no toleramos ambas, el agente remoto y el
    backend quedan desalineados y Ana responde que ese peluquero no existe.
    """
    norm = (value or "").strip().lower()
    return norm in _SIN_PREFERENCIA_VALUES


# ---------- Pydantic models ----------

# Cota dura para duraciones que llegan del LLM. Sin esto, Ana puede pedir
# huecos de 8 horas o crear una reserva que ocupe el día entero al cliente
# si malinterpreta una pausa o un "un día". 5 minutos es el mínimo razonable
# (sirve para tests y citas tipo "flequillo"); 240 minutos cubre tratamientos
# largos (mechas, color + corte) sin abrir la puerta a errores groseros.
_MIN_DURACION_MIN = 5
_MAX_DURACION_MIN = 240


class ConsultaReq(BaseModel):
    fecha_desde_iso: str = Field(..., description="ISO 8601 (Europe/Madrid naive o con offset)")
    fecha_hasta_iso: str
    duracion_minutos: int = Field(..., ge=_MIN_DURACION_MIN, le=_MAX_DURACION_MIN)
    peluquero_preferido: str | None = None
    max_resultados: int = Field(default=5, ge=1, le=15)


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
    # Opcional: búsqueda alternativa por nombre del cliente ("está a nombre de
    # Mario"). Si viene, el backend intenta encontrar el evento buscando en el
    # summary y en extendedProperties.private.client_name. Útil cuando el
    # cliente llama desde otro número o no se acuerda de con qué teléfono lo
    # reservó.
    nombre_cliente: str | None = None
    dias_adelante: int = 30


class MoverReq(BaseModel):
    event_id: str
    nuevo_inicio_iso: str
    nuevo_fin_iso: str
    peluquero: str | None = None  # si se mueve entre peluqueros
    # Opcional: calendar_id devuelto por `buscar_reserva_cliente`. Si viene,
    # se hace un PATCH directo sin iterar peluqueros (ahorra 200-1500 ms en
    # el peor caso). Si falta, se mantiene el comportamiento legacy de
    # probar calendario a calendario.
    calendar_id: str | None = None


class CancelarReq(BaseModel):
    event_id: str
    # Mismo razonamiento que MoverReq.calendar_id: si ElevenLabs nos pasa el
    # calendar_id que ya obtuvo de buscar_reserva_cliente, borramos directo.
    calendar_id: str | None = None


# ---------- Helpers internos ----------

def _peluqueros_filtrados(tenant: dict, preferido: str | None) -> list[dict]:
    pelus = tenant.get("peluqueros") or []
    if preferido:
        pref = preferido.strip().lower()
        match = [p for p in pelus if p["nombre"].strip().lower() == pref]
        if match:
            return match
    return pelus


def _asignar_peluquero_walkin(
    tenant: dict, inicio: datetime, fin: datetime,
) -> dict | None:
    """Elige un peluquero cuando el cliente NO indicó preferencia.

    Reglas:
      1. Trabaja ese día (weekday in dias_trabajo).
      2. Está libre en [inicio, fin] (sin evento solapando).
      3. De los que cumplen 1 y 2, gana el menos cargado del día. Empates →
         tie-break aleatorio para repartir walk-ins entre peluqueros que estén
         igual de tranquilos.

    Devuelve el dict del peluquero (con keys nombre, calendar_id, ...) o `None`
    si no hay nadie disponible. El llamante decide qué responder al cliente
    cuando no hay peluquero (típicamente: "ya no me queda nadie a esa hora").
    """
    peluqueros = tenant.get("peluqueros") or []
    if not peluqueros:
        return None

    libres = cal.peluqueros_disponibles_en_slot(
        inicio=inicio,
        fin=fin,
        peluqueros=peluqueros,
        tenant_id=tenant.get("id", "default"),
    )
    if not libres:
        return None

    min_carga = min(p["busy_count_dia"] for p in libres)
    candidatos = [p for p in libres if p["busy_count_dia"] == min_carga]
    return random.choice(candidatos)


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

    # Schema por día — primer día no cerrado. Si ese día tiene múltiples
    # franjas (turnos partidos: 09-12,14-20) devolvemos (primera apertura,
    # última cierre). En la práctica este cálculo solo se usa como fallback
    # si al caller no le pasamos también `business_hours`; el loop real en
    # calendar_service ya respeta las franjas por día cuando hay dict.
    for day_key in ("mon", "tue", "wed", "thu", "fri", "sat", "sun"):
        h = bh.get(day_key)
        if h and h != ["closed"] and h[0] != "closed" and len(h) >= 2:
            return (_p(h[0], _time(9, 0)), _p(h[-1], _time(20, 0)))

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
        if preferido_norm and not _is_sin_preferencia(preferido_norm):
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
                    business_hours=tenant.get("business_hours"),
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

        # Filtrar huecos que ya pasaron (con margen de 10 min). Pasó en
        # producción el 2026-04-24: Anabel preguntó a las 12h y el bot le
        # ofreció hueco a las 9h del mismo día.
        huecos = _descartar_huecos_pasados(huecos)

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
                business_hours=tenant.get("business_hours"),
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
    # Mismo filtro antipasado que en la rama con peluqueros.
    slots = _descartar_slots_pasados(slots)
    return {"huecos": [{"inicio": s.start.isoformat(), "fin": s.end.isoformat()} for s in slots[:limit]]}


# ---------- Filtros de huecos futuros ----------

def _tz_now_local() -> datetime:
    from zoneinfo import ZoneInfo
    return datetime.now(ZoneInfo(settings.default_timezone))


def _to_aware(dt: datetime) -> datetime:
    """Devuelve dt como timezone-aware en la zona configurada."""
    from zoneinfo import ZoneInfo
    if dt.tzinfo is not None:
        return dt
    return dt.replace(tzinfo=ZoneInfo(settings.default_timezone))


# Margen mínimo entre "ahora" y el inicio del hueco. 10 min es suficiente
# para que el cliente llegue desde donde esté y para no ofrecer algo
# inminente que, aunque técnicamente libre, es poco práctico.
_MIN_BUFFER_MINUTES = 10


def _descartar_huecos_pasados(huecos: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Filtra huecos (dicts con clave "inicio" datetime) a los futuros.

    Tolera "inicio" naive o aware y usa la TZ del negocio como referencia.
    """
    cutoff = _tz_now_local() + timedelta(minutes=_MIN_BUFFER_MINUTES)
    out: list[dict[str, Any]] = []
    for h in huecos:
        ini = h.get("inicio")
        if ini is None:
            continue
        if _to_aware(ini) >= cutoff:
            out.append(h)
    return out


def _descartar_slots_pasados(slots):
    """Igual que `_descartar_huecos_pasados` pero sobre objetos tipo `Slot`
    (namedtuple-like con `start`/`end`) que devuelve `listar_huecos_libres`.
    """
    cutoff = _tz_now_local() + timedelta(minutes=_MIN_BUFFER_MINUTES)
    return [s for s in slots if _to_aware(s.start) >= cutoff]


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

    # Cap de duración. Si el LLM se confunde y manda una cita de 6h, mejor
    # que falle aquí con mensaje claro a que se cree el evento y bloquee la
    # agenda. fin <= inicio también lo descartamos.
    duracion_min = (fin_dt - inicio_dt).total_seconds() / 60.0
    if duracion_min < _MIN_DURACION_MIN or duracion_min > _MAX_DURACION_MIN:
        log.warning(
            "crear_reserva: duración fuera de rango %.1f min (inicio=%s fin=%s)",
            duracion_min, req.inicio_iso, req.fin_iso,
        )
        return {
            "ok": False,
            "error": (
                f"La duración de la cita ({duracion_min:.0f} min) está fuera de "
                f"rango ({_MIN_DURACION_MIN}-{_MAX_DURACION_MIN} min). "
                "Vuelve a confirmar el servicio con el cliente."
            ),
            "retryable": False,
        }

    # ----- Resolución de peluquero y calendario destino -----
    # Tres casos según `req.peluquero`:
    #   (a) tenant sin peluqueros configurados → primary calendar, sin nombre.
    #   (b) cliente NO eligió peluquero ("sin preferencia"/vacío) → walk-in:
    #       elegimos el menos cargado entre los libres ese día y la cita va
    #       a SU calendario. La response devuelve `peluquero` con su nombre
    #       real, no "sin preferencia", para que Ana pueda decírselo.
    #   (c) cliente eligió peluquero concreto → validamos que existe y la
    #       cita va a su calendario.
    peluquero_in = (req.peluquero or "").strip()
    peluquero_asignado = ""
    if not peluqueros:
        # (a) Despachos sin equipo (ej. abogado solo): cae al primary.
        destino_cal = _calendar_id_for_booking(tenant, None)
    elif _is_sin_preferencia(peluquero_in):
        # (b) Walk-in: backend asigna por menos-cargado / random tie-break.
        elegido = _asignar_peluquero_walkin(tenant, inicio_dt, fin_dt)
        if elegido is None:
            log.info(
                "crear_reserva walkin: no hay peluquero libre para %s-%s",
                req.inicio_iso, req.fin_iso,
            )
            return {
                "ok": False,
                "error": (
                    "A esa hora ya no me queda ningún peluquero libre. "
                    "Ofrécele otra hora al cliente."
                ),
                "retryable": False,
            }
        peluquero_asignado = elegido["nombre"]
        destino_cal = elegido["calendar_id"]
        log.info(
            "crear_reserva walkin → %s (busy_count_dia=%d)",
            peluquero_asignado, elegido.get("busy_count_dia", -1),
        )
    else:
        # (c) Peluquero explícito.
        match = [p for p in peluqueros if p["nombre"].strip().lower() == peluquero_in.lower()]
        if not match:
            raise HTTPException(
                status_code=400,
                detail=f"Peluquero '{peluquero_in}' no existe. "
                       f"Opciones: " + ", ".join(p["nombre"] for p in peluqueros),
            )
        peluquero_asignado = match[0]["nombre"]
        destino_cal = match[0]["calendar_id"]

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
    # ---------- Idempotencia ---------------------------------------------
    # ElevenLabs reintenta una tool call tras timeout de red (20s). Si el
    # insert contra Google Calendar llegó, pero la respuesta HTTP se perdió
    # en vuelo, el reintento creaba una SEGUNDA reserva: cita duplicada para
    # el mismo cliente a la misma hora. Antes de insertar, buscamos evento
    # existente del mismo teléfono en una ventana ±5min alrededor del inicio;
    # si lo encontramos, devolvemos ok:true + duplicate:true con el event_id
    # existente, y Ana cierra con naturalidad sin duplicar la agenda.
    #
    # Best-effort: si la búsqueda falla (404, 5xx), caemos a insert normal
    # para no bloquear la reserva por un fallo del "check" secundario.
    if tel:
        try:
            ventana_desde = inicio_dt - timedelta(minutes=5)
            ventana_hasta = inicio_dt + timedelta(minutes=5)
            ev_existente = cal.buscar_evento_por_telefono(
                tel, ventana_desde, ventana_hasta,
                calendar_id=destino_cal,
                tenant_id=tenant.get("id", "default"),
            )
            if ev_existente:
                log.info(
                    "crear_reserva idempotente: ya existe evento=%s para tel=%s en ventana, no duplico",
                    ev_existente.get("id"), tel,
                )
                return {
                    "ok": True,
                    "event_id": ev_existente.get("id"),
                    "peluquero": peluquero_asignado or "sin preferencia",
                    "duplicate": True,
                }
        except Exception:
            # Log y sigue: mejor crear (y tolerar duplicado en el peor caso)
            # que bloquear la reserva por un fallo del check secundario.
            log.exception("idempotency check falló — continúo con insert")

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
    peluquero_resp = peluquero_asignado or "sin preferencia"
    log.info("Reserva creada por voz: %s (%s)", ev.get("id"), peluquero_resp)
    return {
        "ok": True,
        "event_id": ev.get("id"),
        "peluquero": peluquero_resp,
    }


@router.post("/buscar_reserva_cliente")
def buscar_reserva_cliente(
    req: BuscarReq,
    x_tool_secret: str | None = Header(None),
    tenant_id: str | None = Query(None),
    caller_id: str | None = Query(None),
) -> dict[str, Any]:
    """Busca la próxima reserva del cliente por teléfono O por nombre.

    Estrategia:
      1. Si hay teléfono (body o caller_id), busca por teléfono en todos
         los calendarios (peluqueros + principal). Cada calendario = 1
         intento independiente — tolera que alguno 404.
      2. Si NO se encontró por teléfono y viene `nombre_cliente` en el
         body, busca por nombre (Google `events.list?q=<nombre>`). Útil
         cuando el cliente llama desde otro número o no lo recuerda.
      3. Devuelve la primera coincidencia con `calendar_id`, `titulo`,
         `inicio`, `fin` y `via_busqueda` ("telefono" | "nombre").
    """
    _check_secret(x_tool_secret)
    tenant = _resolve_tenant(tenant_id)
    peluqueros = tenant.get("peluqueros") or []
    desde = datetime.utcnow()
    hasta = desde + timedelta(days=req.dias_adelante)
    tid = tenant.get("id", "default")

    # Normaliza teléfono y hace fallback al caller_id.
    tel = (req.telefono_cliente or "").strip()
    if tel.lower() in ("none", "null", "n/a", "na", "-", "unknown", "anonymous", ""):
        tel = ""
    if not tel and caller_id:
        cid = caller_id.strip()
        if cid.lower() not in ("none", "null", "n/a", "na", "-", "unknown", "anonymous", ""):
            tel = cid

    # Normaliza nombre.
    nombre = (req.nombre_cliente or "").strip()
    if nombre.lower() in ("none", "null", "n/a", "na", "-", ""):
        nombre = ""

    # Si no hay NI teléfono NI nombre, no podemos buscar nada.
    if not tel and not nombre:
        return {"encontrada": False, "motivo": "sin_telefono_ni_nombre"}

    # Calendarios a recorrer: peluqueros + principal.
    calendars_to_check = [p["calendar_id"] for p in peluqueros]
    main_cal = tenant.get("calendar_id") or settings.default_calendar_id
    if main_cal not in calendars_to_check:
        calendars_to_check.append(main_cal)

    errors: list[str] = []

    # --- Búsqueda 1: por teléfono --------------------------------------
    if tel:
        for cal_id in calendars_to_check:
            try:
                ev = _retry_google(
                    lambda cal_id=cal_id: cal.buscar_evento_por_telefono(
                        tel, desde, hasta,
                        calendar_id=cal_id,
                        tenant_id=tid,
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
                        "via_busqueda": "telefono",
                    }
            except Exception as e:  # noqa: BLE001
                errors.append(f"{cal_id[:20]}…: {str(e)[:120]}")
                log.warning("buscar_reserva_cliente[tel]: fallo en cal %s: %s", cal_id, e)
                continue

    # --- Búsqueda 2: por nombre -----------------------------------------
    if nombre:
        for cal_id in calendars_to_check:
            try:
                ev = _retry_google(
                    lambda cal_id=cal_id: cal.buscar_evento_por_nombre(
                        nombre, desde, hasta,
                        calendar_id=cal_id,
                        tenant_id=tid,
                    ),
                    "buscar_evento_por_nombre",
                )
                if ev:
                    return {
                        "encontrada": True,
                        "event_id": ev["id"],
                        "titulo": ev.get("summary"),
                        "inicio": ev["start"].get("dateTime"),
                        "fin": ev["end"].get("dateTime"),
                        "calendar_id": cal_id,
                        "via_busqueda": "nombre",
                    }
            except Exception as e:  # noqa: BLE001
                errors.append(f"{cal_id[:20]}…(nombre): {str(e)[:120]}")
                log.warning("buscar_reserva_cliente[nombre]: fallo en cal %s: %s", cal_id, e)
                continue

    # Si TODOS los calendarios fallaron en las dos vías, error graceful.
    n_intentos_esperados = len(calendars_to_check) * (1 if tel else 0) + len(calendars_to_check) * (1 if nombre else 0)
    if errors and len(errors) >= n_intentos_esperados:
        log.error("buscar_reserva_cliente: todos los intentos fallaron: %s", errors)
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
    nuevo_inicio = datetime.fromisoformat(req.nuevo_inicio_iso)
    nuevo_fin = datetime.fromisoformat(req.nuevo_fin_iso)
    # Cap de duración: aplica también al mover (un Ana confundida puede
    # mover "a las 10" a 10:00 dejando fin = 18:00 por error).
    duracion_min = (nuevo_fin - nuevo_inicio).total_seconds() / 60.0
    if duracion_min < _MIN_DURACION_MIN or duracion_min > _MAX_DURACION_MIN:
        log.warning(
            "mover_reserva: duración fuera de rango %.1f min",
            duracion_min,
        )
        return {
            "ok": False,
            "error": (
                f"La duración resultante ({duracion_min:.0f} min) está fuera "
                f"de rango ({_MIN_DURACION_MIN}-{_MAX_DURACION_MIN} min)."
            ),
            "retryable": False,
        }
    tid = tenant.get("id", "default")

    # Fast path: si `buscar_reserva_cliente` ya devolvió `calendar_id` y el
    # agente lo re-envía, hacemos un PATCH directo sin iterar peluqueros.
    # Ahorra hasta (N-1) × ~200-500ms en tenants con varios calendarios.
    if req.calendar_id:
        try:
            cal.mover_evento(
                event_id=req.event_id,
                nuevo_inicio=nuevo_inicio,
                nuevo_fin=nuevo_fin,
                calendar_id=req.calendar_id,
                tenant_id=tid,
            )
            return {"ok": True, "calendar_id": req.calendar_id}
        except Exception as e:
            # Si el calendar_id del body falla, caemos al fallback legacy.
            log.warning(
                "mover_reserva fast-path calendar=%s falló (%s) — fallback a iteración",
                req.calendar_id, str(e)[:200],
            )

    # Fallback legacy: el agente no nos pasó calendar_id (o el fast path falló).
    # Probamos peluqueros uno a uno, luego el calendario principal.
    cal_id = tenant.get("calendar_id") or settings.default_calendar_id
    pelus = tenant.get("peluqueros") or []
    for p in pelus:
        try:
            cal.mover_evento(
                event_id=req.event_id,
                nuevo_inicio=nuevo_inicio,
                nuevo_fin=nuevo_fin,
                calendar_id=p["calendar_id"],
                tenant_id=tid,
            )
            return {"ok": True, "calendar_id": p["calendar_id"]}
        except Exception:
            continue
    cal.mover_evento(
        event_id=req.event_id,
        nuevo_inicio=nuevo_inicio,
        nuevo_fin=nuevo_fin,
        calendar_id=cal_id,
        tenant_id=tid,
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
    tid = tenant.get("id", "default")

    # Fast path: calendar_id del body (devuelto por buscar_reserva_cliente).
    if req.calendar_id:
        try:
            cal.cancelar_evento(
                req.event_id, calendar_id=req.calendar_id, tenant_id=tid,
            )
            return {"ok": True, "calendar_id": req.calendar_id}
        except Exception as e:
            log.warning(
                "cancelar_reserva fast-path calendar=%s falló (%s) — fallback a iteración",
                req.calendar_id, str(e)[:200],
            )

    # Fallback legacy: iterar calendarios del tenant.
    pelus = tenant.get("peluqueros") or []
    for p in pelus:
        try:
            cal.cancelar_evento(
                req.event_id, calendar_id=p["calendar_id"], tenant_id=tid,
            )
            return {"ok": True, "calendar_id": p["calendar_id"]}
        except Exception:
            continue
    cal_id = tenant.get("calendar_id") or settings.default_calendar_id
    cal.cancelar_evento(req.event_id, calendar_id=cal_id, tenant_id=tid)
    return {"ok": True, "calendar_id": cal_id}


# ---------- Personalization webhook (ElevenLabs conversation_initiation) ----------
#
# Este endpoint lo llama ElevenLabs UNA vez al inicio de cada llamada, antes
# de que empiece la conversación real. Responde con `dynamic_variables` que
# el agente puede interpolar en su prompt y en sus tools. Evita que Gemini
# tenga que calcular weekday desde system__time_utc cada turno (ahorra
# tokens de prefill y reduce alucinaciones de fecha).
#
# Formato que espera ElevenLabs (ver docs Convai):
#   POST /tools/eleven/personalization
#   Body: { "caller_id": "+34...", "agent_id": "...", "called_number": "...", "tenant_id": "..." }
#   Respuesta: {
#     "type": "conversation_initiation_client_data",
#     "dynamic_variables": { "hoy_dia_semana": "viernes", ... }
#   }
#
# Protegido por X-Tool-Secret. Sin tool_secret → 500.


_DIA_ES = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
_MES_ES = ["enero", "febrero", "marzo", "abril", "mayo", "junio",
            "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"]


def _fecha_natural(d) -> str:
    """'viernes 25 de abril' — formato hablable."""
    return f"{_DIA_ES[d.weekday()]} {d.day} de {_MES_ES[d.month - 1]}"


@router.post("/eleven/personalization")
async def eleven_personalization(
    request: Request,
    x_tool_secret: str | None = Header(None),
) -> dict[str, Any]:
    """Devuelve dynamic_variables precomputadas para el agente.

    Variables:
      - hoy_fecha_iso, mañana_fecha_iso, pasado_fecha_iso: "YYYY-MM-DD" en TZ del tenant.
      - hoy_dia_semana, mañana_dia_semana: "lunes".."domingo".
      - hoy_natural, mañana_natural: "viernes 25 de abril".
      - hora_local: "17:23" — útil para que Ana decida si "mañana" vs "esta tarde".
      - tenant_name, tenant_id.
      - caller_id_legible: el caller_id separado con espacios para que el TTS
        hable dígito a dígito si es necesario (p.ej. "+34600 000 001").

    El prompt puede usar {{hoy_dia_semana}}, {{mañana_natural}}, etc.
    """
    _check_secret(x_tool_secret)

    try:
        body = await request.json()
    except Exception:
        body = {}

    # `tenant_id` puede venir en query param (configurable al registrar el
    # webhook en ElevenLabs) o en el body. Prioridad: query > body > primer.
    tenant_id = (
        request.query_params.get("tenant_id")
        or body.get("tenant_id")
        or None
    )
    tenant = _resolve_tenant(tenant_id)
    tz_name = tenant.get("timezone") or settings.default_timezone
    tz = ZoneInfo(tz_name)

    now = datetime.now(tz)
    hoy = now.date()
    manana = hoy + timedelta(days=1)
    pasado = hoy + timedelta(days=2)

    caller_id_raw = (body.get("caller_id") or "").strip()
    caller_id_legible = " ".join(list(caller_id_raw.replace(" ", ""))) if caller_id_raw else ""

    dynamic_variables = {
        "tenant_id": tenant.get("id") or "",
        "tenant_name": tenant.get("name") or "",
        "hoy_fecha_iso": hoy.isoformat(),
        "manana_fecha_iso": manana.isoformat(),
        "pasado_fecha_iso": pasado.isoformat(),
        "hoy_dia_semana": _DIA_ES[hoy.weekday()],
        "manana_dia_semana": _DIA_ES[manana.weekday()],
        "hoy_natural": _fecha_natural(hoy),
        "manana_natural": _fecha_natural(manana),
        "hora_local": now.strftime("%H:%M"),
        "caller_id_legible": caller_id_legible,
    }
    log.info(
        "personalization tenant=%s hoy=%s manana=%s caller=%s",
        dynamic_variables["tenant_id"], dynamic_variables["hoy_dia_semana"],
        dynamic_variables["manana_dia_semana"],
        "yes" if caller_id_raw else "no",
    )

    # ---------- Prefetch especulativo de freebusy ----------
    # Precalentamos el cache freebusy para los próximos 2 días sobre los
    # calendarios del equipo. La mayoría de llamadas pide "mañana por la
    # tarde" o "hoy tarde"; si tenemos el freebusy caliente, cuando Ana
    # llame a `consultar_disponibilidad` 2-5s después, la tool devuelve en
    # <50ms en vez de 500-900ms. Ganancia directa ~400-800ms en el turno
    # donde el cliente pide hora.
    #
    # Se hace best-effort en background (threadpool); si falla, el
    # personalization sigue devolviendo las dynamic_variables a tiempo.
    import asyncio
    async def _prefetch():
        try:
            peluqueros = tenant.get("peluqueros") or []
            if not peluqueros:
                return
            desde = datetime.combine(hoy, datetime.min.time(), tzinfo=tz)
            hasta = datetime.combine(pasado, datetime.min.time(), tzinfo=tz) + timedelta(days=1)
            tid_val = tenant.get("id", "default")
            horario = _horario(tenant)
            # Duraciones típicas a precalentar: 30min (corte hombre) y 45min (mujer).
            loop = asyncio.get_running_loop()
            for duracion in (30, 45):
                await loop.run_in_executor(
                    None,
                    lambda d=duracion: cal.listar_huecos_por_peluqueros(
                        desde, hasta, d,
                        peluqueros=peluqueros,
                        tenant_id=tid_val,
                        horario_apertura=horario,
                        business_hours=tenant.get("business_hours"),
                    ),
                )
            log.info("prefetch freebusy OK tenant=%s durs=30,45", tid_val)
        except Exception as e:
            log.warning("prefetch freebusy falló tenant=%s: %s",
                        dynamic_variables["tenant_id"], str(e)[:150])

    # fire-and-forget: no bloqueamos la respuesta al webhook
    asyncio.create_task(_prefetch())

    return {
        "type": "conversation_initiation_client_data",
        "dynamic_variables": dynamic_variables,
    }
