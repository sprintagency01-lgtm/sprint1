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

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from google_auth_oauthlib.flow import Flow
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from .config import settings
from .calendar_service import SCOPES, TOKENS_DIR, _client_config
from .cms.auth import current_user_id, _SECRET as _SESSION_SECRET  # type: ignore

log = logging.getLogger(__name__)

router = APIRouter(prefix="/oauth", tags=["oauth"])

# Serializador para firmar el parámetro `state` (tenant_id firmado).
# Usa la misma SESSION_SECRET que el CMS pero con salt distinto.
_state_serializer = URLSafeTimedSerializer(_SESSION_SECRET, salt="reservabot-oauth-state")
_STATE_TTL_SECONDS = 600  # 10 min para completar el flujo

# Permite el callback sobre HTTP sólo en dev.
if os.getenv("OAUTHLIB_INSECURE_TRANSPORT"):
    os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"


def _build_flow(state: str | None = None) -> Flow:
    flow = Flow.from_client_config(
        _client_config(),
        scopes=SCOPES,
        state=state,
    )
    flow.redirect_uri = settings.google_redirect_uri
    return flow


@router.get("/start", response_class=RedirectResponse)
async def oauth_start(
    tenant_id: str = "default",
    member_id: int | None = None,
    _user_id: int = Depends(current_user_id),
):
    """Arranca el flujo de OAuth.

    Dos modos:
      - sin `member_id`: el token se guarda para el tenant entero, en
        `TOKENS_DIR/{tenant_id}.json`. Es el que usa el bot (agent.py y
        eleven_tools) para leer/escribir en el calendario principal.
      - con `member_id`: el token se guarda para un miembro específico del
        equipo, en `TOKENS_DIR/{tenant_id}_member_{member_id}.json`. Solo lo
        usa el CMS para listar/crear calendarios bajo la cuenta Google propia
        de ese miembro.
    Firma `tenant_id` + (opcional) `member_id` dentro de `state`.
    """
    payload: dict = {"tenant_id": tenant_id}
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
        "oauth start tenant=%s member=%s redirect=%s",
        tenant_id, member_id, settings.google_redirect_uri,
    )
    return RedirectResponse(auth_url, status_code=302)


@router.get("/callback")
async def oauth_callback(request: Request):
    """Recibe `code` de Google, lo canjea y guarda los tokens."""
    qp = request.query_params
    err = qp.get("error")
    if err:
        raise HTTPException(status_code=400, detail=f"Google devolvió error: {err}")

    code = qp.get("code")
    state = qp.get("state")
    if not code or not state:
        raise HTTPException(status_code=400, detail="Faltan 'code' o 'state' en el callback")

    # Validar y decodificar tenant_id del state.
    try:
        data = _state_serializer.loads(state, max_age=_STATE_TTL_SECONDS)
    except SignatureExpired:
        raise HTTPException(status_code=400, detail="Flujo expirado, reinicia desde /oauth/start")
    except BadSignature:
        raise HTTPException(status_code=400, detail="State inválido — posible CSRF")
    tenant_id = str(data.get("tenant_id") or "default")
    member_id = data.get("member_id")

    flow = _build_flow(state=state)
    flow.fetch_token(code=code)
    creds = flow.credentials

    # Persistir tokens. Si es un miembro del equipo, el token vive con un
    # sufijo "_member_{id}" para no colisionar con el del tenant entero.
    pathlib.Path(TOKENS_DIR).mkdir(parents=True, exist_ok=True)
    if member_id is not None:
        path = pathlib.Path(TOKENS_DIR) / f"{tenant_id}_member_{int(member_id)}.json"
        redirect_back = f"/admin/clientes/{tenant_id}/equipo"
        scope_label = f"tenant={tenant_id} miembro={member_id}"
    else:
        path = pathlib.Path(TOKENS_DIR) / f"{tenant_id}.json"
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
