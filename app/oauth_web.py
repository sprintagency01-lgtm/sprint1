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
    _user_id: int = Depends(current_user_id),
):
    """Arranca el flujo de OAuth para `tenant_id`.

    Firma `tenant_id` dentro del parámetro state. En el callback se verifica.
    """
    state = _state_serializer.dumps({"tenant_id": tenant_id})
    flow = _build_flow(state=state)
    auth_url, _ = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",  # fuerza refresh_token la primera vez
    )
    log.info("oauth start tenant=%s redirect=%s", tenant_id, settings.google_redirect_uri)
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

    flow = _build_flow(state=state)
    flow.fetch_token(code=code)
    creds = flow.credentials

    # Persistir tokens bajo TOKENS_DIR/{tenant_id}.json.
    pathlib.Path(TOKENS_DIR).mkdir(parents=True, exist_ok=True)
    path = pathlib.Path(TOKENS_DIR) / f"{tenant_id}.json"
    path.write_text(creds.to_json())
    # Si el tenant ya tenía un servicio cacheado con un token viejo, invalídalo
    # para que la próxima tool call lea el nuevo token refrescado.
    try:
        from . import calendar_service as _cal
        _cal._invalidate_service_cache(tenant_id)
    except Exception:  # pragma: no cover
        log.warning("No pude invalidar cache de calendar_service para %s", tenant_id)
    log.info("oauth OK tenant=%s tokens_path=%s", tenant_id, path)

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
  <h1>Autorizacion completada</h1>
  <p>Tokens guardados para el tenant <code>{tenant_id}</code>.</p>
  <p>Ruta en el contenedor: <code>{path}</code></p>
  <p><a href=\"/admin\">Volver al panel</a></p>
</body></html>
"""
    return HTMLResponse(body)
