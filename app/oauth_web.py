"""Flujo OAuth de Google basado en web (para producción en Railway).

Expone dos rutas:
  GET /oauth/start?tenant_id=...  → redirige a Google. Requiere admin logueado.
  GET /oauth/callback              → recibe el code, guarda tokens en
                                     TOKENS_DIR/{tenant_id}.json.

La diferencia con calendar_service.authorize_interactive:
  - Aquí usamos `Flow` (server-side redirect), no `InstalledAppFlow`.
  - El redirect URI debe coincidir exactamente con el dado de alta en GCP
    y con la env var GOOGLE_REDIRECT_URI.
  - El tenant_id viaja firmado dentro del parámetro `state` de OAuth,
    así no hace falta guardarlo en ninguna cookie/sesión entre llamadas.
"""
from __future__ import annotations

import logging
import os
import pathlib

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from google_auth_oauthlib.flow import Flow
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from .config import settings
from .calendar_service import SCOPES, TOKENS_DIR, _client_config
from .cms.auth import (
    read_session as _cms_read_session,
    COOKIE_NAME as _CMS_COOKIE,
    _SECRET as _SESSION_SECRET,
)  # type: ignore
from .portal.auth import (
    read_session as _portal_read_session,
    COOKIE_NAME as _PORTAL_COOKIE,
)

log = logging.getLogger(__name__)

router = APIRouter(prefix="/oauth", tags=["oauth"])

# Serializador para firmar el parámetro `state` (tenant_id firmado).
# Usa la misma SESSION_SECRET que el CMS pero con salt distinto.
_state_serializer = URLSafeTimedSerializer(_SESSION_SECRET, salt="reservabot-oauth-state")
_STATE_TTL_SECONDS = 600  # 10 min para completar el flujo

# Permite el callback sobre HTTP sólo en dev.
if os.getenv("OAUTHLIB_INSECURE_TRANSPORT"):
    os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"

# Google a veces devuelve un *superscope* del que pedimos: si la cuenta del
# usuario ya tenía aprobado un permiso más amplio (típico:
# `https://www.googleapis.com/auth/calendar` cuando nosotros pedimos solo
# `calendar.events` + `calendar.readonly`), oauthlib detecta el mismatch y
# lanza `Warning: Scope has changed from ... to ...` como excepción que
# revienta el callback. La librería expone una env var para relajar esa
# validación — es seguro porque (a) Google solo añade scopes, no quita; y
# (b) el state firmado ya garantiza el origen del flow.
os.environ.setdefault("OAUTHLIB_RELAX_TOKEN_SCOPE", "1")


def _build_flow(state: str | None = None) -> Flow:
    flow = Flow.from_client_config(
        _client_config(),
        scopes=SCOPES,
        state=state,
    )
    flow.redirect_uri = settings.google_redirect_uri
    return flow


def _resolve_caller(
    request: Request, tenant_id: str, back: str
) -> tuple[str, int]:
    """Valida que la petición a /oauth/start viene de un usuario autorizado.

    Retorna el `back` finalmente usado y el id del usuario solo a efectos de
    log. Casos:

    - `back=admin` → exigir cookie del CMS (`reservabot_admin`); cualquier
      admin válido puede conectar Google para cualquier tenant.
    - `back=portal` → exigir cookie del portal (`reservabot_portal`) y que
      el `tid` de la sesión coincida con el `tenant_id` del query (un usuario
      del portal solo puede gestionar SU tenant, no el de otro).

    Si no se especifica `back`, intentamos primero la cookie del CMS y caemos
    a la del portal como conveniencia (compat con bookmarks viejos del CMS).
    """
    cms_token = request.cookies.get(_CMS_COOKIE)
    portal_token = request.cookies.get(_PORTAL_COOKIE)

    if back == "portal":
        if not portal_token:
            raise HTTPException(401, "Sesión de portal requerida")
        sess = _portal_read_session(portal_token)
        if sess is None:
            raise HTTPException(401, "Sesión de portal inválida")
        uid, tid = sess
        if tid != tenant_id:
            raise HTTPException(403, "No puedes conectar Google de otro tenant")
        return ("portal", uid)

    if back == "admin":
        if not cms_token:
            raise HTTPException(401, "Sesión de admin requerida")
        sess = _cms_read_session(cms_token)
        if sess is None:
            raise HTTPException(401, "Sesión de admin inválida")
        return ("admin", sess)

    # back no especificado: best-effort.
    if cms_token:
        sess = _cms_read_session(cms_token)
        if sess is not None:
            return ("admin", sess)
    if portal_token:
        sess = _portal_read_session(portal_token)
        if sess is not None and sess[1] == tenant_id:
            return ("portal", sess[0])
    raise HTTPException(401, "Necesitas iniciar sesión en el panel para conectar Google")


@router.get("/start", response_class=RedirectResponse)
async def oauth_start(
    request: Request,
    tenant_id: str = "default",
    member_id: int | None = None,
    back: str = "",
):
    """Arranca el flujo de OAuth.

    Dos modos según `member_id`:
      - sin `member_id`: el token se guarda para el tenant entero, en
        `TOKENS_DIR/{tenant_id}.json`. Es el que usa el bot (agent.py y
        eleven_tools) para leer/escribir en el calendario principal.
      - con `member_id`: el token se guarda para un miembro específico del
        equipo, en `TOKENS_DIR/{tenant_id}_member_{member_id}.json`. Lo usan
        el CMS y el portal del cliente para listar/crear calendarios bajo la
        cuenta Google propia de ese miembro.

    Y dos modos según `back`:
      - `admin` (default histórico): el callback redirige a /admin/...
      - `portal`: el callback redirige a /app#equipo. La sesión del portal
        debe pertenecer al `tenant_id` solicitado.

    Firma todo (tenant_id, member_id, back) dentro de `state` para que el
    callback sepa a dónde devolver.
    """
    back_resolved, _uid = _resolve_caller(request, tenant_id, back)
    payload: dict = {"tenant_id": tenant_id, "back": back_resolved}
    if member_id is not None:
        payload["member_id"] = int(member_id)
    state = _state_serializer.dumps(payload)
    flow = _build_flow(state=state)
    auth_url, _ = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",  # fuerza refresh_token la primera vez
    )
    log.info(
        "oauth start tenant=%s member=%s back=%s redirect=%s",
        tenant_id, member_id, back_resolved, settings.google_redirect_uri,
    )
    return RedirectResponse(auth_url, status_code=302)


def _error_page(title: str, detail: str, status: int = 500) -> HTMLResponse:
    """Render mínimo para que el usuario vea algo legible cuando el callback
    falla, en vez del 'Internal Server Error' pelado del default."""
    body = f"""<!doctype html>
<html lang=\"es\"><head><meta charset=\"utf-8\">
<title>OAuth Google — {title}</title>
<style>
  body {{ font-family: system-ui, sans-serif; max-width: 560px; margin: 4rem auto;
          padding: 2rem; background: #0f172a; color: #e2e8f0; border-radius: 8px; }}
  h1 {{ font-size: 22px; margin-top: 0; }}
  pre {{ background: #1e293b; padding: 12px; border-radius: 6px; white-space: pre-wrap;
         word-break: break-word; font-size: 13px; }}
  a {{ color: #38bdf8; }}
</style></head>
<body>
  <h1>{title}</h1>
  <pre>{detail}</pre>
  <p><a href=\"/app#equipo\">Volver al panel</a></p>
</body></html>
"""
    return HTMLResponse(body, status_code=status)


@router.get("/callback")
async def oauth_callback(request: Request):
    """Recibe `code` de Google, lo canjea y guarda los tokens.

    Capturamos cualquier excepción para mostrar una página legible y, sobre
    todo, dejar log con `exc_info=True` — sin esto, Railway nos devuelve un
    'Internal Server Error' pelado cuando algo del intercambio con Google
    falla y no hay forma de saber por qué.
    """
    qp = request.query_params
    err = qp.get("error")
    if err:
        return _error_page(
            "Google devolvió un error",
            f"error={err}\n\nReinicia el flujo desde el panel.",
            status=400,
        )

    code = qp.get("code")
    state = qp.get("state")
    if not code or not state:
        return _error_page(
            "Callback inválido",
            "Faltan 'code' o 'state' — abre /oauth/start desde el panel para empezar de nuevo.",
            status=400,
        )

    # Validar y decodificar tenant_id del state.
    try:
        data = _state_serializer.loads(state, max_age=_STATE_TTL_SECONDS)
    except SignatureExpired:
        return _error_page(
            "El flujo ha expirado",
            "Han pasado más de 10 minutos desde que pulsaste 'Conectar Google'.\n"
            "Reinicia desde el panel.",
            status=400,
        )
    except BadSignature:
        return _error_page(
            "State inválido",
            "Esto suele pasar si el SESSION_SECRET cambió entre el inicio del flujo y\n"
            "el callback (típico tras un redeploy). Reinicia desde el panel.",
            status=400,
        )

    tenant_id = str(data.get("tenant_id") or "default")
    member_id = data.get("member_id")
    back = str(data.get("back") or "admin")  # default histórico

    try:
        flow = _build_flow(state=state)
        flow.fetch_token(code=code)
        creds = flow.credentials
    except Exception as e:  # pragma: no cover
        log.exception(
            "oauth callback: fetch_token falló tenant=%s member=%s",
            tenant_id, member_id,
        )
        return _error_page(
            "No pude completar el intercambio con Google",
            f"{type(e).__name__}: {e}\n\n"
            "Causas frecuentes:\n"
            "  - El redirect_uri en Google Cloud Console no coincide con\n"
            "    GOOGLE_REDIRECT_URI del backend (mira logs de Railway).\n"
            "  - El 'code' ya se usó (no se puede recargar la página del callback).",
        )

    # Persistir tokens. Si es un miembro del equipo, el token vive con un
    # sufijo "_member_{id}" para no colisionar con el del tenant entero.
    pathlib.Path(TOKENS_DIR).mkdir(parents=True, exist_ok=True)
    if member_id is not None:
        path = pathlib.Path(TOKENS_DIR) / f"{tenant_id}_member_{int(member_id)}.json"
        if back == "portal":
            # Volvemos al portal del cliente. Usamos el hash #equipo para que
            # el SPA aterrice en la pestaña Equipo y refresque al cargar.
            redirect_back = "/app#equipo"
        else:
            redirect_back = f"/admin/clientes/{tenant_id}/equipo"
        scope_label = f"tenant={tenant_id} miembro={member_id}"
    else:
        path = pathlib.Path(TOKENS_DIR) / f"{tenant_id}.json"
        if back == "portal":
            redirect_back = "/app#ajustes"
        else:
            redirect_back = f"/admin/clientes/{tenant_id}/general"
        scope_label = f"tenant={tenant_id}"
    path.write_text(creds.to_json())

    # Solo invalida el cache de calendar_service si es un token de tenant
    # (el del miembro no lo usa ese módulo, solo el CMS).
    if member_id is None:
        try:
            from . import calendar_service as _cal
            _cal._invalidate_service_cache(tenant_id)
        except Exception:  # pragma: no cover
            log.warning("No pude invalidar cache de calendar_service para %s", tenant_id)
    log.info("oauth OK %s tokens_path=%s", scope_label, path)

    body = f"""<!doctype html>
<html lang=\"es\"><head><meta charset=\"utf-8\">
<title>OAuth Google — OK</title>
<style>
  body {{ font-family: system-ui, sans-serif; max-width: 560px; margin: 4rem auto;
          padding: 2rem; background: #0f172a; color: #e2e8f0; border-radius: 8px; }}
  code {{ background: #1e293b; padding: 2px 6px; border-radius: 4px; }}
  a {{ color: #38bdf8; }}
</style></head>
<body>
  <h1>Autorización completada</h1>
  <p>Cuenta conectada para <code>{scope_label}</code>.</p>
  <p><a href=\"{redirect_back}\">Volver al panel</a></p>
  <script>setTimeout(function(){{ window.location='{redirect_back}'; }}, 800);</script>
</body></html>
"""
    return HTMLResponse(body)
