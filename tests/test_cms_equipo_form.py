"""Test de regresión del POST /admin/clientes/{id}/equipo.

Cubre el bug del 2026-04-29: los checkboxes de "Días laborables" se
nombraban `dias_trabajo_{índice_día}` en vez de `dias_trabajo_{índice_miembro}`,
así que los días marcados se mezclaban entre miembros. El template ya
está arreglado y el JS renumera los `name`s antes de submit. Este test
simula el form que el navegador enviaría tras esa renumeración para
asegurar que el handler asocia cada lista de días al miembro correcto,
incluso después de que el usuario haya añadido/quitado miembros.

Notas técnicas:
- Pasamos el body como string urlencoded (con keys duplicadas para los
  checkboxes multi-valor) en `content=`. El kwarg `data=` de httpx
  colapsa los duplicados al usar dict-like, lo que falsea el escenario
  real del navegador.
"""
from __future__ import annotations

import urllib.parse

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.main import app
from app import db as db_module
from app.cms import auth as cms_auth


_HDR_FORM = {"Content-Type": "application/x-www-form-urlencoded"}


@pytest.fixture
def auth_client():
    """TestClient con la dependencia de auth burlada (uid=1 fijo)."""
    app.dependency_overrides[cms_auth.current_user_id] = lambda: 1
    yield TestClient(app)
    app.dependency_overrides.pop(cms_auth.current_user_id, None)


def _seed_tenant_with_equipo(tenant_id: str, miembros: list[tuple[str, list[int]]]) -> None:
    """Crea (o reemplaza) un tenant con N miembros con los días dados."""
    with Session(db_module.engine) as s:
        existing = s.get(db_module.Tenant, tenant_id)
        if existing is not None:
            s.delete(existing)
            s.commit()
        t = db_module.Tenant(
            id=tenant_id,
            name="Test Equipo",
            calendar_id="primary",
            timezone="Europe/Madrid",
        )
        s.add(t)
        s.flush()
        for orden, (nombre, dias) in enumerate(miembros):
            m = db_module.MiembroEquipo(
                tenant_id=tenant_id,
                nombre=nombre,
                calendar_id=f"cal_{nombre.lower().replace(' ', '_')}@example.com",
                orden=orden,
            )
            m.dias_trabajo = dias
            t.equipo.append(m)
        s.commit()


def _read_equipo(tenant_id: str) -> list[dict]:
    with Session(db_module.engine) as s:
        t = s.get(db_module.Tenant, tenant_id)
        return [
            {"nombre": m.nombre, "calendar_id": m.calendar_id, "dias": list(m.dias_trabajo or [])}
            for m in sorted(t.equipo, key=lambda x: x.orden or 0)
        ]


def _post_equipo(client, tenant_id: str, pairs: list[tuple[str, str]]):
    """POST con duplicados de keys preservados (lo que hace el navegador real)."""
    body = urllib.parse.urlencode(pairs)
    return client.post(
        f"/admin/clientes/{tenant_id}/equipo",
        content=body,
        headers=_HDR_FORM,
        follow_redirects=False,
    )


def test_equipo_save_dias_distintos_por_miembro(auth_client):
    """Cada miembro guarda exactamente los días marcados, sin mezclas."""
    tid = "test_equipo_basico"
    _seed_tenant_with_equipo(tid, [("Mario", [0]), ("Marcos", [0])])

    pairs = [
        ("nombre", "Mario"),
        ("calendar_id", "cal_mario@example.com"),
        ("dias_trabajo_0", "0"),
        ("dias_trabajo_0", "1"),
        ("dias_trabajo_0", "2"),
        ("dias_trabajo_0", "3"),
        ("dias_trabajo_0", "4"),
        ("nombre", "Marcos"),
        ("calendar_id", "cal_marcos@example.com"),
        ("dias_trabajo_1", "2"),
    ]
    r = _post_equipo(auth_client, tid, pairs)
    assert r.status_code == 303, r.text

    equipo = _read_equipo(tid)
    assert len(equipo) == 2
    assert equipo[0]["nombre"] == "Mario"
    assert equipo[0]["dias"] == [0, 1, 2, 3, 4]
    assert equipo[1]["nombre"] == "Marcos"
    assert equipo[1]["dias"] == [2]


def test_equipo_save_tras_quitar_miembro_central(auth_client):
    """Si el JS renumera correctamente, quitar un miembro central no
    desincroniza los días de los que quedan."""
    tid = "test_equipo_borrar_medio"
    _seed_tenant_with_equipo(
        tid,
        [
            ("A", [0, 1, 2, 3, 4, 5]),
            ("B", [0, 1, 2]),
            ("C", [2]),
        ],
    )

    pairs = [
        ("nombre", "A"),
        ("calendar_id", "cal_a@example.com"),
        ("dias_trabajo_0", "0"),
        ("dias_trabajo_0", "1"),
        ("dias_trabajo_0", "2"),
        ("dias_trabajo_0", "3"),
        ("dias_trabajo_0", "4"),
        ("nombre", "C"),
        ("calendar_id", "cal_c@example.com"),
        ("dias_trabajo_1", "2"),
    ]
    r = _post_equipo(auth_client, tid, pairs)
    assert r.status_code == 303, r.text

    equipo = _read_equipo(tid)
    assert [m["nombre"] for m in equipo] == ["A", "C"]
    assert equipo[0]["dias"] == [0, 1, 2, 3, 4]
    assert equipo[1]["dias"] == [2]


def test_equipo_save_nombre_vacio_descarta_miembro(auth_client):
    """Si el usuario vacía el nombre de un miembro, esa fila se descarta."""
    tid = "test_equipo_nombre_vacio"
    _seed_tenant_with_equipo(
        tid,
        [
            ("Mario", [0, 1, 2, 3, 4]),
            ("Test calendar", [0, 1, 2, 3, 4, 5, 6]),
        ],
    )

    pairs = [
        ("nombre", "Mario"),
        ("calendar_id", "cal_mario@example.com"),
        ("dias_trabajo_0", "0"),
        ("dias_trabajo_0", "1"),
        ("dias_trabajo_0", "2"),
        ("dias_trabajo_0", "3"),
        ("dias_trabajo_0", "4"),
        ("nombre", ""),
        ("calendar_id", "cal_test@example.com"),
        ("dias_trabajo_1", "0"),
    ]
    r = _post_equipo(auth_client, tid, pairs)
    assert r.status_code == 303, r.text

    equipo = _read_equipo(tid)
    assert len(equipo) == 1
    assert equipo[0]["nombre"] == "Mario"
    assert equipo[0]["dias"] == [0, 1, 2, 3, 4]
