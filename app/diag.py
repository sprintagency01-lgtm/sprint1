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
from sqlalchemy import desc
from sqlalchemy.orm import Session

from .config import settings
from . import calendar_service as cal
from . import tenants as tn
from . import db as db_module
from . import agent as agent_module

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


class TestAgentReq(BaseModel):
    text: str
    phone: str = "+34600000099"
    tenant_id: str | None = None
    reset_history: bool = False


@router.post("/test_agent")
def test_agent(
    req: TestAgentReq,
    x_tool_secret: str | None = Header(None),
) -> dict[str, Any]:
    """Simula un mensaje entrante SIN pasar por Twilio/WhatsApp.

    Ejecuta el mismo pipeline (agent.reply con modelo y prompt reales, tools
    contra Google Calendar real) y guarda el turno en BD. Devuelve la respuesta
    del agente para que podamos leerla desde fuera.

    Útil para tests integrales sin quemar mensajes de Twilio ni necesitar un
    WhatsApp real. El `phone` por defecto es ficticio para no mezclarse con
    conversaciones de clientes reales.
    """
    _check_secret(x_tool_secret)
    tid = _resolve_tenant_id(req.tenant_id)
    tenant = tn.get_tenant(tid) or {"id": tid}

    if req.reset_history:
        from sqlalchemy import delete
        with Session(db_module.engine) as s:
            s.execute(delete(db_module.Message).where(
                db_module.Message.tenant_id == tid,
                db_module.Message.customer_phone == req.phone,
            ))
            s.commit()

    # Guardar el mensaje del usuario
    db_module.save_message(tid, req.phone, "user", req.text)
    history = db_module.load_history(tid, req.phone)
    # Quitar el último (es el que acabamos de guardar, se pasa como user_message)
    history = history[:-1] if history and history[-1]["role"] == "user" else history

    try:
        reply = agent_module.reply(
            user_message=req.text,
            history=history,
            tenant=tenant,
            caller_phone=req.phone,
        )
    except Exception as e:  # noqa: BLE001
        log.exception("Error en /_diag/test_agent")
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}

    db_module.save_message(tid, req.phone, "assistant", reply)
    return {
        "ok": True,
        "tenant_id": tid,
        "phone": req.phone,
        "reply": reply,
        "model": settings.openai_model,
    }


@router.get("/recent_messages")
def recent_messages(
    x_tool_secret: str | None = Header(None),
    tenant_id: str | None = Query(None),
    limit: int = Query(40, ge=1, le=200),
    phone: str | None = Query(None),
) -> dict[str, Any]:
    """Devuelve los últimos mensajes guardados (user/assistant) para depuración.

    Filtros opcionales por tenant_id y por teléfono del cliente. Protegido con
    X-Tool-Secret. Útil para ver qué ha dicho el bot cuando no hay acceso
    directo a los logs de Railway.
    """
    _check_secret(x_tool_secret)
    with Session(db_module.engine) as s:
        q = s.query(db_module.Message)
        if tenant_id:
            q = q.filter(db_module.Message.tenant_id == tenant_id)
        if phone:
            q = q.filter(db_module.Message.customer_phone == phone)
        rows = q.order_by(desc(db_module.Message.created_at)).limit(limit).all()
    # Orden cronológico ascendente para que sea fácil de leer
    rows = list(reversed(rows))
    return {
        "count": len(rows),
        "messages": [
            {
                "id": m.id,
                "tenant_id": m.tenant_id,
                "phone": m.customer_phone,
                "role": m.role,
                "content": m.content,
                "created_at": m.created_at.isoformat() if m.created_at else None,
            }
            for m in rows
        ],
    }
