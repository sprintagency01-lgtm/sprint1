"""Endpoints de diagnóstico/mantenimiento.

Protegidos con el mismo `X-Tool-Secret` que /tools/*. No son parte del producto
— sirven para tareas puntuales (listar calendarios de la cuenta OAuth, crear un
calendario cuando un peluquero se queda sin el suyo, etc.). Aislados aquí para
no ensuciar el módulo de tools de voz.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Query
from pydantic import BaseModel

from .config import settings
from . import calendar_service as cal
from . import tenants as tn

log = logging.getLogger(__name__)

router = APIRouter(prefix="/_diag", tags=["diag"])


def _check_secret(x_tool_secret: str | None) -> None:
    expected = settings.tool_secret
    if not expected:
        raise HTTPException(status_code=500, detail="TOOL_SECRET no configurado")
    if x_tool_secret != expected:
        raise HTTPException(status_code=401, detail="Bad X-Tool-Secret")


def _resolve_tenant_id(tenant_id: str | None) -> str:
    if tenant_id:
        t = tn.get_tenant(tenant_id)
        if not t:
            raise HTTPException(status_code=404, detail=f"Tenant {tenant_id} no existe")
        return t.get("id") or tenant_id
    return (tn.load_tenants()[0]).get("id") or "default"


class CreateCalReq(BaseModel):
    summary: str
    description: str | None = None
    timezone: str | None = None


class TestCalReq(BaseModel):
    calendar_id: str


@router.get("/calendars/list")
def calendars_list(
    x_tool_secret: str | None = Header(None),
    tenant_id: str | None = Query(None),
) -> dict[str, Any]:
    """Lista todos los calendarios accesibles con las credenciales OAuth del tenant.

    Equivalente a `GET /calendar/v3/users/me/calendarList` pero filtrado a los
    campos que nos importan para decidir a cuál apuntar un peluquero.
    """
    _check_secret(x_tool_secret)
    tid = _resolve_tenant_id(tenant_id)
    svc = cal._service(tid)

    items: list[dict[str, Any]] = []
    page_token: str | None = None
    while True:
        resp = svc.calendarList().list(pageToken=page_token, maxResults=250).execute()
        for c in resp.get("items", []):
            items.append({
                "id": c.get("id"),
                "summary": c.get("summary"),
                "description": c.get("description"),
                "primary": bool(c.get("primary", False)),
                "accessRole": c.get("accessRole"),
                "timeZone": c.get("timeZone"),
            })
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    return {"count": len(items), "calendars": items}


@router.post("/calendars/create")
def calendars_create(
    req: CreateCalReq,
    x_tool_secret: str | None = Header(None),
    tenant_id: str | None = Query(None),
) -> dict[str, Any]:
    """Crea un calendario nuevo en la cuenta OAuth del tenant.

    Equivalente a `POST /calendar/v3/calendars`. Devuelve el id del calendario
    recién creado, que es lo que hay que meter en `tenants.yaml` para el
    peluquero correspondiente.
    """
    _check_secret(x_tool_secret)
    tid = _resolve_tenant_id(tenant_id)
    svc = cal._service(tid)

    body: dict[str, Any] = {"summary": req.summary}
    if req.description:
        body["description"] = req.description
    body["timeZone"] = req.timezone or settings.default_timezone

    created = svc.calendars().insert(body=body).execute()
    log.info("Calendario creado para tenant %s: %s (%s)",
             tid, created.get("id"), req.summary)
    return {
        "id": created.get("id"),
        "summary": created.get("summary"),
        "timeZone": created.get("timeZone"),
    }


@router.post("/calendars/test")
def calendars_test(
    req: TestCalReq,
    x_tool_secret: str | None = Header(None),
    tenant_id: str | None = Query(None),
) -> dict[str, Any]:
    """Comprueba si un calendar_id responde (útil para verificar un fix).

    Hace un `calendars().get()`. Devuelve ok:true si existe y el service
    account tiene acceso, ok:false + detalle si no.
    """
    _check_secret(x_tool_secret)
    tid = _resolve_tenant_id(tenant_id)
    svc = cal._service(tid)
    try:
        got = svc.calendars().get(calendarId=req.calendar_id).execute()
        return {"ok": True, "id": got.get("id"), "summary": got.get("summary")}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "detail": str(e)[:300]}
