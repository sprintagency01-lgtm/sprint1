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
from . import elevenlabs_client
from . import telegram as tg_module

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


@router.post("/services/sync_from_yaml")
def services_sync_from_yaml(
    x_tool_secret: str | None = Header(None),
    tenant_id: str | None = Query(None),
) -> dict[str, Any]:
    """Sincroniza los servicios del tenant desde tenants.yaml a la BD.

    Usa el mismo formato que `/admin/clientes/{id}/servicios`: primero limpia
    los servicios actuales del tenant y luego inserta los del YAML. Idempotente
    — llamar dos veces deja el mismo estado.

    Útil cuando un tenant ha quedado con `services=[]` en BD (caso típico:
    CMS creó el tenant pero la pestaña de servicios no se guardó nunca) y el
    YAML ya tiene los servicios definidos desde la fase de seed.
    """
    _check_secret(x_tool_secret)
    tid = _resolve_tenant_id(tenant_id)

    yaml_map = tn._load_yaml_by_id()
    yaml_tenant = yaml_map.get(tid)
    if not yaml_tenant:
        raise HTTPException(
            status_code=404,
            detail=f"Tenant {tid} no está en tenants.yaml — no hay servicios que copiar",
        )
    services = yaml_tenant.get("services") or []
    if not services:
        raise HTTPException(
            status_code=400,
            detail=f"El YAML no tiene 'services' para {tid}",
        )

    with Session(db_module.engine) as s:
        t = s.get(db_module.Tenant, tid)
        if t is None:
            raise HTTPException(status_code=404, detail=f"Tenant {tid} no existe en BD")
        t.services.clear()
        s.flush()
        added: list[dict[str, Any]] = []
        for i, row in enumerate(services):
            nombre = (row.get("nombre") or "").strip()
            if not nombre:
                continue
            try:
                dur = int(row.get("duracion_min") or 30)
                precio = float(row.get("precio") or 0)
            except (TypeError, ValueError):
                continue
            t.services.append(db_module.Service(
                nombre=nombre, duracion_min=dur, precio=precio, orden=i,
            ))
            added.append({"nombre": nombre, "duracion_min": dur, "precio": precio})
        s.commit()

    log.info("Servicios sincronizados desde YAML para tenant %s: %d items", tid, len(added))
    return {"ok": True, "tenant_id": tid, "count": len(added), "services": added}


class VoicePromptReq(BaseModel):
    voice_prompt: str
    sync_to_elevenlabs: bool = True


@router.post("/tenant/voice/update")
def tenant_voice_update(
    req: VoicePromptReq,
    x_tool_secret: str | None = Header(None),
    tenant_id: str | None = Query(None),
) -> dict[str, Any]:
    """Actualiza `voice_prompt` del tenant y opcionalmente lo envía a ElevenLabs.

    Pensado para automatizar cambios del prompt de voz sin pasar por el CMS.
    Si `sync_to_elevenlabs=true` (default), hace PATCH al agente remoto usando
    `elevenlabs_client.sync_agent` y escribe `voice_last_sync_*` igual que el
    botón del CMS.
    """
    from datetime import datetime as _dt

    _check_secret(x_tool_secret)
    tid = _resolve_tenant_id(tenant_id)
    new_prompt = (req.voice_prompt or "").strip()
    if not new_prompt:
        raise HTTPException(status_code=400, detail="voice_prompt vacío")

    with Session(db_module.engine) as s:
        t = s.get(db_module.Tenant, tid)
        if t is None:
            raise HTTPException(status_code=404, detail=f"Tenant {tid} no existe")
        t.voice_prompt = new_prompt

        synced = False
        sync_error: str | None = None
        if req.sync_to_elevenlabs:
            try:
                elevenlabs_client.sync_agent(
                    t.voice_agent_id,
                    prompt=t.voice_prompt,
                    voice=elevenlabs_client.VoiceParams(
                        voice_id=t.voice_voice_id,
                        stability=t.voice_stability,
                        similarity_boost=t.voice_similarity_boost,
                        speed=t.voice_speed,
                    ),
                )
                t.voice_last_sync_at = _dt.utcnow()
                t.voice_last_sync_status = "ok"
                synced = True
            except elevenlabs_client.ElevenLabsError as e:
                sync_error = str(e)[:380]
                t.voice_last_sync_at = _dt.utcnow()
                t.voice_last_sync_status = sync_error
        s.commit()

    return {
        "ok": synced or not req.sync_to_elevenlabs,
        "tenant_id": tid,
        "prompt_len": len(new_prompt),
        "synced_to_elevenlabs": synced,
        "sync_error": sync_error,
    }


@router.get("/tenant/voice")
def tenant_voice_config(
    x_tool_secret: str | None = Header(None),
    tenant_id: str | None = Query(None),
) -> dict[str, Any]:
    """Devuelve la config de voz del tenant (agent_id ElevenLabs, prompt, voice_id).

    Útil para comprobar si el `voice_prompt` replica bugs del `system_prompt` —
    en particular si menciona "PELUQUERO/A OBLIGATORIO" en un tenant sin equipo.
    """
    _check_secret(x_tool_secret)
    tid = _resolve_tenant_id(tenant_id)
    with Session(db_module.engine) as s:
        t = s.get(db_module.Tenant, tid)
        if t is None:
            raise HTTPException(status_code=404, detail=f"Tenant {tid} no existe en BD")
        return {
            "tenant_id": t.id,
            "tenant_name": t.name,
            "voice_agent_id": t.voice_agent_id or "",
            "voice_voice_id": t.voice_voice_id or "",
            "voice_prompt": t.voice_prompt or "",
            "voice_last_sync_at": t.voice_last_sync_at.isoformat() if t.voice_last_sync_at else None,
            "voice_last_sync_status": t.voice_last_sync_status or "",
        }


@router.get("/tenants/list")
def tenants_list(
    x_tool_secret: str | None = Header(None),
) -> dict[str, Any]:
    """Enumera todos los tenants de la BD (id + name + sector).

    Útil cuando el CMS muestra un tenant que no está en tenants.yaml y no
    sabemos qué `id` tiene en la BD (caso típico: el CMS genera un slug al
    crear el tenant).
    """
    _check_secret(x_tool_secret)
    with Session(db_module.engine) as s:
        rows = s.query(db_module.Tenant).all()
        out = []
        for t in rows:
            out.append({
                "id": t.id,
                "name": getattr(t, "name", None),
                "sector": getattr(t, "sector", None),
                "calendar_id": getattr(t, "calendar_id", None),
                "phone_number_id": getattr(t, "phone_number_id", None),
                "is_active": getattr(t, "is_active", None),
            })
    return {"count": len(out), "tenants": out}


@router.get("/tenant")
def tenant_inspect(
    x_tool_secret: str | None = Header(None),
    tenant_id: str | None = Query(None),
) -> dict[str, Any]:
    """Devuelve los campos clave del tenant tal y como los ve el agente.

    Pensado para verificar qué `name` se inyecta en el footer del prompt
    (p.ej. si la BD trae 'Peluquería Demo' o 'Peluquería Ejemplo'), sin
    tener que entrar al CMS.
    """
    _check_secret(x_tool_secret)
    tid = _resolve_tenant_id(tenant_id)
    t = tn.get_tenant(tid) or {}
    return {
        "id": t.get("id"),
        "name": t.get("name"),
        "calendar_id": t.get("calendar_id"),
        "phone_number_id": t.get("phone_number_id"),
        "n_services": len(t.get("services") or []),
        "n_peluqueros": len(t.get("peluqueros") or []),
        "system_prompt": (t.get("system_prompt") or ""),
    }


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
    # Devolver el modelo efectivo del provider activo para distinguir
    # respuestas cuando conmutamos entre OpenAI y Anthropic en caliente.
    active_model = (
        settings.anthropic_model
        if settings.llm_provider == "anthropic"
        else settings.openai_model
    )
    return {
        "ok": True,
        "tenant_id": tid,
        "phone": req.phone,
        "reply": reply,
        "provider": settings.llm_provider,
        "model": active_model,
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


# ---------------------------------------------------------------------------
#  Healthchecks de canales externos
# ---------------------------------------------------------------------------

@router.get("/elevenlabs/healthcheck")
def elevenlabs_healthcheck(
    x_tool_secret: str | None = Header(None),
    tenant_id: str | None = Query(None),
) -> dict[str, Any]:
    """Valida el estado del stack de voz para un tenant (o el primero si no se pasa).

    Comprueba y devuelve por separado (cada bloque con `ok` bool):

    - `api_key`: hay ELEVENLABS_API_KEY configurada.
    - `tool_secret`: hay TOOL_SECRET configurada (lo que permite que ElevenLabs
      llame a /tools/* con autenticación).
    - `agent`: el `voice_agent_id` del tenant (o el ELEVENLABS_AGENT_ID global
      si el tenant no tiene) existe en ElevenLabs y responde al GET.
    - `agent_tools`: el agente remoto tiene las 5 tools esperadas registradas
      (consultar_disponibilidad, crear_reserva, buscar_reserva_cliente,
      mover_reserva, cancelar_reserva).
    - `tenant_voice_config`: el tenant tiene prompt + voice_id rellenos.

    No gasta dinero: solo lee. No lanza: cualquier fallo se reporta en el
    bloque correspondiente. El frontend del CMS puede pintar luz verde/roja
    por bloque.
    """
    _check_secret(x_tool_secret)
    resolved_id = _resolve_tenant_id(tenant_id)
    tenant = tn.get_tenant(resolved_id) or {}

    report: dict[str, Any] = {
        "tenant_id": resolved_id,
        "api_key": {"ok": bool(settings.elevenlabs_api_key.strip()),
                     "hint": "Configura ELEVENLABS_API_KEY en Railway." if not settings.elevenlabs_api_key.strip() else ""},
        "tool_secret": {"ok": bool(settings.tool_secret.strip()),
                         "hint": "Configura TOOL_SECRET en Railway (lo usan las tools /tools/*)." if not settings.tool_secret.strip() else ""},
    }

    tenant_agent_id = (tenant.get("voice_agent_id") or "").strip()
    effective_agent_id = tenant_agent_id or (settings.elevenlabs_agent_id or "").strip()

    report["tenant_voice_config"] = {
        "ok": bool((tenant.get("voice_prompt") or "").strip() and (tenant.get("voice_voice_id") or "").strip()),
        "prompt_len": len((tenant.get("voice_prompt") or "")),
        "voice_id_set": bool((tenant.get("voice_voice_id") or "").strip()),
        "agent_id": effective_agent_id or "(none)",
        "agent_id_source": "tenant" if tenant_agent_id else ("global_env" if effective_agent_id else "missing"),
    }

    if not effective_agent_id:
        report["agent"] = {"ok": False, "error": "Sin voice_agent_id y sin ELEVENLABS_AGENT_ID global."}
        report["agent_tools"] = {"ok": False, "skipped": "Sin agente al que consultar."}
        return report

    if not settings.elevenlabs_api_key.strip():
        report["agent"] = {"ok": False, "error": "No consulto ElevenLabs sin API key."}
        report["agent_tools"] = {"ok": False, "skipped": "Sin API key."}
        return report

    # Consulta remota protegida contra fallos de red/auth.
    try:
        remote = elevenlabs_client.get_agent(effective_agent_id)
    except Exception as e:  # noqa: BLE001
        report["agent"] = {"ok": False, "error": str(e)[:280]}
        report["agent_tools"] = {"ok": False, "skipped": "get_agent falló."}
        return report

    report["agent"] = {
        "ok": True,
        "agent_id": effective_agent_id,
        "name": remote.get("name"),
    }

    # Introspección de las tools registradas en el agente remoto. La API de
    # Eleven las pone bajo conversation_config.agent.prompt.tools.
    expected_tools = {
        "consultar_disponibilidad",
        "crear_reserva",
        "buscar_reserva_cliente",
        "mover_reserva",
        "cancelar_reserva",
    }
    remote_tools: list[dict[str, Any]] = (
        ((remote.get("conversation_config") or {}).get("agent") or {})
        .get("prompt", {})
        .get("tools") or []
    )
    remote_tool_names = {
        (t.get("name") or "").strip()
        for t in remote_tools if isinstance(t, dict)
    }
    missing = sorted(expected_tools - remote_tool_names)
    extra = sorted(remote_tool_names - expected_tools - {""})
    report["agent_tools"] = {
        "ok": not missing,
        "registered": sorted(remote_tool_names - {""}),
        "missing": missing,
        "extra": extra,
    }
    return report


@router.get("/telegram/status")
def telegram_status(
    x_tool_secret: str | None = Header(None),
) -> dict[str, Any]:
    """Verifica que el bot de Telegram está conectado y el webhook configurado.

    Llama a `getMe` y `getWebhookInfo`. No gasta nada (Telegram es gratuito).
    Si TELEGRAM_BOT_TOKEN no está configurado, devuelve `configured: false`
    explícitamente en vez de fallar, para que el CMS pueda pintar "canal
    desactivado" limpiamente.
    """
    _check_secret(x_tool_secret)

    token = settings.telegram_bot_token.strip()
    if not token:
        return {
            "configured": False,
            "hint": "Añade TELEGRAM_BOT_TOKEN en Railway para activar el canal.",
        }

    try:
        client = tg_module.TelegramClient(token)
        me = client.get_me()
    except tg_module.TelegramError as e:
        return {
            "configured": True,
            "ok": False,
            "error": str(e)[:280],
        }
    except Exception as e:  # noqa: BLE001
        return {
            "configured": True,
            "ok": False,
            "error": f"getMe falló: {str(e)[:260]}",
        }

    # Info del webhook es útil para ver si está registrado y sin errores de entrega.
    webhook_info: dict[str, Any] = {}
    try:
        # Reutilizamos el canal interno del cliente para no duplicar URL.
        # Telegram getWebhookInfo acepta GET sin payload.
        import httpx
        r = httpx.get(f"https://api.telegram.org/bot{token}/getWebhookInfo", timeout=10.0)
        if r.status_code < 400:
            webhook_info = (r.json() or {}).get("result") or {}
    except Exception as e:  # noqa: BLE001
        webhook_info = {"error": str(e)[:200]}

    return {
        "configured": True,
        "ok": True,
        "bot": {
            "id": me.get("id"),
            "username": me.get("username"),
            "first_name": me.get("first_name"),
            "can_join_groups": me.get("can_join_groups"),
        },
        "webhook": {
            "url": webhook_info.get("url"),
            "has_custom_certificate": webhook_info.get("has_custom_certificate"),
            "pending_update_count": webhook_info.get("pending_update_count"),
            "last_error_date": webhook_info.get("last_error_date"),
            "last_error_message": webhook_info.get("last_error_message"),
            "secret_token_configured": bool(settings.telegram_webhook_secret.strip()),
        },
        "default_tenant_id": settings.telegram_default_tenant_id or "(fallback al primer contracted)",
    }
