"""Tests de regresión de las optimizaciones de latencia aplicadas en /tools/*.

Cubren:
 - Fast path de mover/cancelar con `calendar_id` en el body: un solo PATCH/DELETE.
 - Fallback legacy de mover/cancelar cuando `calendar_id` viene vacío.
 - Idempotencia de crear_reserva: si ya existe un evento del mismo teléfono
   en ±5min, devuelve duplicate:true sin insertar.
 - Cache de tenant: dos resoluciones consecutivas del mismo tenant no pegan
   dos veces a la BD.
 - Retry con backoff: se reintenta sobre errores transitorios; fallo
   permanente propaga la excepción.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(monkeypatch):
    """TestClient mínimo con solo el router de `eleven_tools` montado.

    Evitamos importar `app.main` porque el startup event intenta warm-up de
    Google y algunos entornos de CI bloquean los PRAGMA iniciales de SQLite.
    Aquí solo necesitamos probar `/tools/*` en aislamiento.
    """
    from fastapi import FastAPI
    from app import eleven_tools

    # Desactivar validación del secret (Settings es frozen=True → no se
    # puede parchear `settings.tool_secret` directamente).
    monkeypatch.setattr("app.eleven_tools._check_secret", lambda _x: None)

    app_min = FastAPI()
    app_min.include_router(eleven_tools.router)
    return TestClient(app_min)


@pytest.fixture
def fake_tenant(monkeypatch):
    """Inyecta un tenant ficticio con 2 peluqueros para ejercitar el fast
    path de mover/cancelar sin tocar la BD."""
    tenant = {
        "id": "t_test",
        "name": "Test Negocio",
        "calendar_id": "main_cal@group.calendar.google.com",
        "timezone": "Europe/Madrid",
        "business_hours": {"mon": ["09:00", "20:00"], "tue": ["09:00", "20:00"]},
        "services": [{"nombre": "Consulta", "duracion_min": 30, "precio": 0}],
        "peluqueros": [
            {"nombre": "Mario", "calendar_id": "mario_cal@group.calendar.google.com", "dias_trabajo": [0,1,2,3,4,5]},
            {"nombre": "Ana",   "calendar_id": "ana_cal@group.calendar.google.com",   "dias_trabajo": [0,1,2,3,4]},
        ],
    }
    # `_resolve_tenant` en eleven_tools llama a `tn.get_tenant(tid, include_system_prompt=False)`.
    def _fake_get_tenant(tid, *, include_system_prompt=True):
        if tid == "t_test":
            return tenant
        return None
    monkeypatch.setattr("app.tenants.get_tenant", _fake_get_tenant)
    return tenant


# ---------------------------------------------------------------------------
#  mover_reserva — fast path con calendar_id
# ---------------------------------------------------------------------------

def test_mover_fast_path_usa_calendar_id_del_body(client, fake_tenant):
    """Si el agente envía calendar_id, no se itera peluqueros: 1 solo mover_evento."""
    calls: list[dict] = []

    def _fake_mover(event_id, nuevo_inicio, nuevo_fin, calendar_id, tenant_id):
        calls.append({"event_id": event_id, "calendar_id": calendar_id})
        return {"id": event_id, "updated": True}

    with patch("app.eleven_tools.cal.mover_evento", side_effect=_fake_mover):
        r = client.post(
            "/tools/mover_reserva?tenant_id=t_test",
            headers={"X-Tool-Secret": "x"},
            json={
                "event_id": "evt_abc",
                "nuevo_inicio_iso": "2026-04-28T10:00:00",
                "nuevo_fin_iso":    "2026-04-28T10:30:00",
                "calendar_id":      "mario_cal@group.calendar.google.com",
            },
        )
    assert r.status_code == 200
    body = r.json()
    assert body == {"ok": True, "calendar_id": "mario_cal@group.calendar.google.com"}
    # Fast path: una sola llamada a Google Calendar, sin iteración.
    assert len(calls) == 1
    assert calls[0]["calendar_id"] == "mario_cal@group.calendar.google.com"


def test_mover_fallback_legacy_sin_calendar_id(client, fake_tenant):
    """Sin calendar_id en el body, itera peluqueros (comportamiento antiguo)."""
    calls: list[dict] = []

    def _fake_mover(event_id, nuevo_inicio, nuevo_fin, calendar_id, tenant_id):
        calls.append({"calendar_id": calendar_id})
        # Falla en Mario, acierta en Ana → iteración hasta encontrar.
        if calendar_id == "mario_cal@group.calendar.google.com":
            raise RuntimeError("404 not found")
        return {"id": event_id}

    with patch("app.eleven_tools.cal.mover_evento", side_effect=_fake_mover):
        r = client.post(
            "/tools/mover_reserva?tenant_id=t_test",
            headers={"X-Tool-Secret": "x"},
            json={
                "event_id": "evt_abc",
                "nuevo_inicio_iso": "2026-04-28T10:00:00",
                "nuevo_fin_iso":    "2026-04-28T10:30:00",
            },
        )
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["calendar_id"] == "ana_cal@group.calendar.google.com"
    # Dos intentos: mario (falla) → ana (ok).
    assert [c["calendar_id"] for c in calls] == [
        "mario_cal@group.calendar.google.com",
        "ana_cal@group.calendar.google.com",
    ]


# ---------------------------------------------------------------------------
#  cancelar_reserva — fast path
# ---------------------------------------------------------------------------

def test_cancelar_fast_path_usa_calendar_id_del_body(client, fake_tenant):
    calls: list[dict] = []

    def _fake_cancel(event_id, calendar_id, tenant_id):
        calls.append({"event_id": event_id, "calendar_id": calendar_id})
        return None

    with patch("app.eleven_tools.cal.cancelar_evento", side_effect=_fake_cancel):
        r = client.post(
            "/tools/cancelar_reserva?tenant_id=t_test",
            headers={"X-Tool-Secret": "x"},
            json={"event_id": "evt_xyz", "calendar_id": "ana_cal@group.calendar.google.com"},
        )
    assert r.status_code == 200
    assert r.json()["calendar_id"] == "ana_cal@group.calendar.google.com"
    assert len(calls) == 1


# ---------------------------------------------------------------------------
#  crear_reserva — idempotencia
# ---------------------------------------------------------------------------

def test_crear_reserva_idempotente_si_ya_existe(client, fake_tenant):
    """Si hay un evento del mismo teléfono en ±5min, devolver duplicate:true
    y NO llamar a crear_evento. Walk-in ('sin preferencia') asigna peluquero
    automáticamente; mockeamos `peluqueros_disponibles_en_slot` para evitar
    el freebusy real."""
    buscar_calls = []
    crear_calls = []

    def _fake_buscar(tel, desde, hasta, calendar_id, tenant_id):
        buscar_calls.append({"tel": tel, "calendar_id": calendar_id})
        return {"id": "evt_existente", "start": {"dateTime": "2026-04-28T10:00:00+02:00"}}

    def _fake_crear(**kwargs):
        crear_calls.append(kwargs)
        return {"id": "evt_nuevo"}

    def _fake_disponibles(inicio, fin, peluqueros, tenant_id):
        # Mario libre, Marcos no — el helper devuelve solo Mario.
        return [{**peluqueros[0], "busy_count_dia": 0}]

    with patch("app.eleven_tools.cal.buscar_evento_por_telefono", side_effect=_fake_buscar), \
         patch("app.eleven_tools.cal.crear_evento", side_effect=_fake_crear), \
         patch("app.eleven_tools.cal.peluqueros_disponibles_en_slot", side_effect=_fake_disponibles):
        r = client.post(
            "/tools/crear_reserva?tenant_id=t_test",
            headers={"X-Tool-Secret": "x"},
            json={
                "titulo": "Juan — Consulta (sin preferencia)",
                "inicio_iso": "2026-04-28T10:00:00",
                "fin_iso":    "2026-04-28T10:30:00",
                "telefono_cliente": "+34600000001",
                "peluquero": "sin preferencia",
            },
        )
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["event_id"] == "evt_existente"
    assert body.get("duplicate") is True
    # Devuelve el peluquero asignado por walk-in, no "sin preferencia".
    assert body["peluquero"] == "Mario"
    # No debería haberse llamado a crear_evento.
    assert crear_calls == []
    # Sí debería haberse hecho la búsqueda idempotente y EN EL CALENDARIO
    # del peluquero asignado, no en el primary.
    assert buscar_calls
    assert buscar_calls[0]["calendar_id"] == "mario_cal@group.calendar.google.com"


def test_crear_reserva_inserta_si_no_hay_duplicado(client, fake_tenant):
    """Sin duplicado, inserta normalmente. Walk-in asigna peluquero auto."""
    crear_calls = []

    def _fake_buscar(tel, desde, hasta, calendar_id, tenant_id):
        return None  # no hay evento previo

    def _fake_crear(**kwargs):
        crear_calls.append(kwargs)
        return {"id": "evt_new_123"}

    def _fake_disponibles(inicio, fin, peluqueros, tenant_id):
        return [{**peluqueros[0], "busy_count_dia": 0}]

    with patch("app.eleven_tools.cal.buscar_evento_por_telefono", side_effect=_fake_buscar), \
         patch("app.eleven_tools.cal.crear_evento", side_effect=_fake_crear), \
         patch("app.eleven_tools.cal.peluqueros_disponibles_en_slot", side_effect=_fake_disponibles):
        r = client.post(
            "/tools/crear_reserva?tenant_id=t_test",
            headers={"X-Tool-Secret": "x"},
            json={
                "titulo": "Juan — Consulta (sin preferencia)",
                "inicio_iso": "2026-04-28T10:00:00",
                "fin_iso":    "2026-04-28T10:30:00",
                "telefono_cliente": "+34600000001",
                "peluquero": "sin preferencia",
            },
        )
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["event_id"] == "evt_new_123"
    assert body.get("duplicate") is not True
    assert body["peluquero"] == "Mario"
    assert len(crear_calls) == 1
    # El evento se crea en el calendario del peluquero asignado, no en primary.
    assert crear_calls[0]["calendar_id"] == "mario_cal@group.calendar.google.com"


def test_crear_reserva_walkin_elige_menos_cargado(client, fake_tenant):
    """Round-robin: cuando hay 2 peluqueros libres, gana el de menor busy_count_dia."""
    crear_calls = []

    def _fake_disponibles(inicio, fin, peluqueros, tenant_id):
        # Mario tiene 5 eventos hoy, Marcos solo 1 → debe ganar Marcos.
        return [
            {**peluqueros[0], "busy_count_dia": 5},
            {**peluqueros[1], "busy_count_dia": 1},
        ]

    def _fake_crear(**kwargs):
        crear_calls.append(kwargs)
        return {"id": "evt_walkin"}

    with patch("app.eleven_tools.cal.buscar_evento_por_telefono", return_value=None), \
         patch("app.eleven_tools.cal.crear_evento", side_effect=_fake_crear), \
         patch("app.eleven_tools.cal.peluqueros_disponibles_en_slot", side_effect=_fake_disponibles):
        r = client.post(
            "/tools/crear_reserva?tenant_id=t_test",
            headers={"X-Tool-Secret": "x"},
            json={
                "titulo": "Juan — Consulta",
                "inicio_iso": "2026-04-28T10:00:00",
                "fin_iso":    "2026-04-28T10:30:00",
                "telefono_cliente": "+34600000001",
                "peluquero": "",  # vacío también cae en walk-in
            },
        )
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    # Ana (1 evento) gana sobre Mario (5 eventos).
    assert body["peluquero"] == "Ana"
    assert crear_calls[0]["calendar_id"] == "ana_cal@group.calendar.google.com"


def test_crear_reserva_walkin_falla_si_nadie_libre(client, fake_tenant):
    """Si no hay peluquero libre a esa hora, ok:false con mensaje legible
    para que Ana ofrezca otra hora — no se llama a crear_evento."""
    crear_calls = []

    def _fake_crear(**kwargs):
        crear_calls.append(kwargs)
        return {"id": "no_deberia_llegar_aqui"}

    with patch("app.eleven_tools.cal.crear_evento", side_effect=_fake_crear), \
         patch("app.eleven_tools.cal.peluqueros_disponibles_en_slot", return_value=[]):
        r = client.post(
            "/tools/crear_reserva?tenant_id=t_test",
            headers={"X-Tool-Secret": "x"},
            json={
                "titulo": "Juan — Consulta",
                "inicio_iso": "2026-04-28T10:00:00",
                "fin_iso":    "2026-04-28T10:30:00",
                "telefono_cliente": "+34600000001",
                "peluquero": "sin preferencia",
            },
        )
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False
    assert "libre" in body["error"].lower()
    assert crear_calls == []


def test_crear_reserva_fallback_a_primary_cuando_pelu_404(client, fake_tenant):
    """Bug observado en pelu_demo (2026-05-03): los calendars de los peluqueros
    son legibles vía freebusy pero la OAuth no tiene WRITE → Google devuelve
    404 al crear evento, y la cita se pierde. Ahora hay fallback automático al
    calendar principal del tenant si el del peluquero da 404 / notFound.
    """
    crear_calls = []

    def _fake_crear(**kwargs):
        crear_calls.append(kwargs["calendar_id"])
        # Mario (calendar del peluquero asignado por walkin) no acepta write.
        if kwargs["calendar_id"] == "mario_cal@group.calendar.google.com":
            raise RuntimeError(
                "<HttpError 404 when requesting .../mario_cal/events?alt=json returned \"Not Found\". Details: \"Not Found\">"
            )
        # El primary del tenant sí acepta.
        return {"id": "evt_fallback_ok"}

    def _fake_disponibles(inicio, fin, peluqueros, tenant_id):
        # Solo Mario libre — walkin lo elige, su calendar es el que falla.
        return [{**peluqueros[0], "busy_count_dia": 0}]

    with patch("app.eleven_tools.cal.buscar_evento_por_telefono", return_value=None), \
         patch("app.eleven_tools.cal.crear_evento", side_effect=_fake_crear), \
         patch("app.eleven_tools.cal.peluqueros_disponibles_en_slot", side_effect=_fake_disponibles):
        r = client.post(
            "/tools/crear_reserva?tenant_id=t_test",
            headers={"X-Tool-Secret": "x"},
            json={
                "titulo": "Juan — Consulta (sin preferencia)",
                "inicio_iso": "2026-04-28T10:00:00",
                "fin_iso":    "2026-04-28T10:30:00",
                "telefono_cliente": "+34600000001",
                "peluquero": "sin preferencia",
            },
        )
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True, f"esperaba ok=True tras fallback, body={body}"
    assert body["event_id"] == "evt_fallback_ok"
    # La response sigue informando del peluquero asignado por walkin (Mario),
    # aunque la cita finalmente se haya guardado en el calendar principal.
    assert body["peluquero"] == "Mario"
    # Dos intentos: peluquero (404) → primary (ok).
    assert crear_calls == [
        "mario_cal@group.calendar.google.com",
        "main_cal@group.calendar.google.com",
    ]


def test_crear_reserva_no_fallback_si_no_es_404(client, fake_tenant):
    """Si el error NO es 404, no entrar en el fallback — los 5xx, rate limits y
    timeouts ya tienen su propio retry en `_retry_google`. Si fallan ahí, se
    propaga el ok:false original sin doblar la latencia con un segundo intento.
    """
    crear_calls = []

    def _fake_crear(**kwargs):
        crear_calls.append(kwargs["calendar_id"])
        raise RuntimeError("permission denied: insufficient scope")

    def _fake_disponibles(inicio, fin, peluqueros, tenant_id):
        return [{**peluqueros[0], "busy_count_dia": 0}]

    with patch("app.eleven_tools.cal.buscar_evento_por_telefono", return_value=None), \
         patch("app.eleven_tools.cal.crear_evento", side_effect=_fake_crear), \
         patch("app.eleven_tools.cal.peluqueros_disponibles_en_slot", side_effect=_fake_disponibles):
        r = client.post(
            "/tools/crear_reserva?tenant_id=t_test",
            headers={"X-Tool-Secret": "x"},
            json={
                "titulo": "Juan — Consulta",
                "inicio_iso": "2026-04-28T10:00:00",
                "fin_iso":    "2026-04-28T10:30:00",
                "telefono_cliente": "+34600000001",
                "peluquero": "sin preferencia",
            },
        )
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False
    # Solo se intentó el calendar del peluquero (1 vez), sin fallback.
    assert crear_calls == ["mario_cal@group.calendar.google.com"]


# ---------------------------------------------------------------------------
#  Caché de tenant in-memory
# ---------------------------------------------------------------------------

def test_tenant_cache_evita_segunda_query(monkeypatch):
    """Dos get_tenant consecutivos con el mismo id no deberían volver a
    consultar la fuente (BD/YAML). Verificamos monkeypatcheando
    `_load_yaml_by_id` y la sesión de SQLAlchemy con un stub, para no
    depender de la BD real en el sandbox.
    """
    from app import tenants as tn

    tn.invalidate_tenant_cache()

    yaml_calls = {"n": 0}
    db_calls = {"n": 0}

    def _counted_yaml():
        yaml_calls["n"] += 1
        return {}

    monkeypatch.setattr(tn, "_load_yaml_by_id", _counted_yaml)

    # Stub de Session context manager que devuelve un tenant fake.
    class _FakeTenant:
        id = "t_cache_test"
        def to_dict(self, *, include_system_prompt=True):
            return {"id": self.id, "name": "Cache Test"}

    class _FakeSession:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def get(self, model, pk):
            db_calls["n"] += 1
            return _FakeTenant() if pk == "t_cache_test" else None

    monkeypatch.setattr(tn, "Session", lambda _engine: _FakeSession())

    t1 = tn.get_tenant("t_cache_test", include_system_prompt=False)
    n_yaml_after_1 = yaml_calls["n"]
    n_db_after_1 = db_calls["n"]

    t2 = tn.get_tenant("t_cache_test", include_system_prompt=False)
    n_yaml_after_2 = yaml_calls["n"]
    n_db_after_2 = db_calls["n"]

    assert t1 is not None and t2 is not None
    assert t1["id"] == "t_cache_test"
    # La segunda llamada NO debe haber tocado ni YAML ni BD: viene de caché.
    assert n_yaml_after_2 == n_yaml_after_1, \
        "Segundo get_tenant no debería re-leer YAML"
    assert n_db_after_2 == n_db_after_1, \
        "Segundo get_tenant no debería re-leer BD"

    tn.invalidate_tenant_cache()


def test_invalidate_tenant_cache_borra_entrada(monkeypatch):
    from app import tenants as tn

    tn.invalidate_tenant_cache()

    # Forzar una entrada manual en el caché para el test (evita montar BD).
    tn._TENANT_CACHE["t_foo::sp=0"] = (0.0, {"id": "t_foo"})  # expirada
    tn._TENANT_CACHE["__all__"] = (0.0, [])

    tn.invalidate_tenant_cache("t_foo")
    assert "t_foo::sp=0" not in tn._TENANT_CACHE
    assert "__all__" not in tn._TENANT_CACHE


# ---------------------------------------------------------------------------
#  _retry_google — backoff con jitter
# ---------------------------------------------------------------------------

def test_retry_google_reintenta_transient_y_tiene_exito(monkeypatch):
    from app import eleven_tools as et
    monkeypatch.setattr(et.time, "sleep", lambda *_: None)  # no esperar en tests

    attempts = {"n": 0}

    def _flaky():
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise RuntimeError("HTTP 503 Service Unavailable")
        return "ok"

    result = et._retry_google(_flaky, "fake_op", attempts=2)
    assert result == "ok"
    assert attempts["n"] == 2


def test_retry_google_no_reintenta_permanent(monkeypatch):
    from app import eleven_tools as et
    monkeypatch.setattr(et.time, "sleep", lambda *_: None)

    attempts = {"n": 0}

    def _hard_fail():
        attempts["n"] += 1
        raise RuntimeError("HTTP 400 invalid argument")  # no transitorio

    with pytest.raises(RuntimeError):
        et._retry_google(_hard_fail, "fake_op", attempts=3)
    # Debería haber intentado una sola vez y abortado (no reintenta 400).
    assert attempts["n"] == 1
