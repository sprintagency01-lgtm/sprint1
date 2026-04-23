"""Autenticación del portal del cliente (/app).

- Credenciales en la tabla `tenant_users`. Cada usuario pertenece a UN tenant.
- Cookie firmada (`itsdangerous`) con {uid, tid, ts}. Salt distinto del CMS
  para que las sesiones no sean intercambiables entre /admin y /app.
- Bootstrap: al arranque, para cada Tenant que aún no tenga ningún TenantUser
  intentamos crear un owner usando `tenant.contact_email` (si existe) y una
  contraseña inicial leída de PORTAL_BOOTSTRAP_PASSWORD. Idempotente.
"""
from __future__ import annotations

import os
import secrets
import time
from typing import Optional

from fastapi import Request, HTTPException, status
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from passlib.hash import bcrypt
from sqlalchemy.orm import Session

from .. import db as db_module

COOKIE_NAME = "reservabot_portal"
SESSION_TTL_SECONDS = 60 * 60 * 24 * 14  # 14 días

# Reutilizamos el mismo SESSION_SECRET del .env que usa el CMS, pero con un
# `salt` distinto → los tokens de CMS no valen en portal ni al revés.
_SECRET = os.getenv("SESSION_SECRET") or secrets.token_urlsafe(48)
_serializer = URLSafeTimedSerializer(_SECRET, salt="reservabot-portal")


# --------------------------------------------------------------------------
#  Bootstrap: asegurar un owner por tenant
# --------------------------------------------------------------------------

def ensure_portal_users() -> None:
    """Crea un owner por tenant si ninguno existe.

    - El email se toma de `tenant.contact_email`. Si está vacío, se usa
      `{tenant_id}@portal.local`.
    - La contraseña inicial se lee de PORTAL_BOOTSTRAP_PASSWORD. Si no está
      definida, no se crea nada (log warning) — preferimos no dejar cuentas
      con contraseñas generadas que no podamos mostrar al usuario.
    """
    import logging
    log = logging.getLogger(__name__)

    password = (os.getenv("PORTAL_BOOTSTRAP_PASSWORD") or "").strip()
    if not password:
        log.info(
            "PORTAL_BOOTSTRAP_PASSWORD no definido; no se crearán usuarios "
            "automáticos del portal. Usa /admin para crearlos a mano."
        )
        return

    with Session(db_module.engine) as s:
        tenants = s.query(db_module.Tenant).filter(
            db_module.Tenant.kind == "contracted",
        ).all()
        created = 0
        for t in tenants:
            existing = (
                s.query(db_module.TenantUser)
                .filter(db_module.TenantUser.tenant_id == t.id)
                .first()
            )
            if existing is not None:
                continue
            email = (t.contact_email or f"{t.id}@portal.local").strip().lower()
            nombre = t.contact_name or t.name or "Propietario"
            s.add(db_module.TenantUser(
                tenant_id=t.id,
                email=email,
                password_hash=bcrypt.hash(password),
                nombre=nombre,
                role="owner",
            ))
            created += 1
        if created:
            s.commit()
            log.info("portal bootstrap: creados %d owners iniciales", created)


# --------------------------------------------------------------------------
#  Login / logout
# --------------------------------------------------------------------------

def verify_credentials(email: str, password: str) -> Optional[tuple[int, str]]:
    """Devuelve (user_id, tenant_id) si las credenciales son correctas."""
    with Session(db_module.engine) as s:
        # Email puede existir en varios tenants; cogemos la primera coincidencia
        # con password válida. En MVP (un solo tenant real) es trivial.
        users = (
            s.query(db_module.TenantUser)
            .filter(db_module.TenantUser.email == email.strip().lower())
            .all()
        )
        for u in users:
            try:
                ok = bcrypt.verify(password, u.password_hash)
            except Exception:
                ok = False
            if ok:
                return (u.id, u.tenant_id)
        return None


def sign_session(user_id: int, tenant_id: str) -> str:
    return _serializer.dumps({"uid": user_id, "tid": tenant_id, "ts": int(time.time())})


def read_session(token: str) -> Optional[tuple[int, str]]:
    try:
        data = _serializer.loads(token, max_age=SESSION_TTL_SECONDS)
        return (int(data.get("uid")), str(data.get("tid")))
    except (BadSignature, SignatureExpired, ValueError, TypeError):
        return None


# --------------------------------------------------------------------------
#  Dependencias FastAPI
# --------------------------------------------------------------------------

def _unauthorized_api():
    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="no autenticado")


def _redirect_login():
    raise HTTPException(status_code=status.HTTP_303_SEE_OTHER, headers={"Location": "/app/login"})


def current_session(request: Request) -> tuple[int, str]:
    """Devuelve (user_id, tenant_id). Redirige al login si no hay sesión.

    Usar en rutas que renderizan HTML (p.ej. /app).
    """
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        _redirect_login()
    sess = read_session(token)
    if sess is None:
        _redirect_login()
    return sess


def current_api_session(request: Request) -> tuple[int, str]:
    """Igual que current_session pero responde 401 JSON (para /api/portal/*)."""
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        _unauthorized_api()
    sess = read_session(token)
    if sess is None:
        _unauthorized_api()
    return sess


def get_user(user_id: int) -> Optional[db_module.TenantUser]:
    with Session(db_module.engine) as s:
        return s.get(db_module.TenantUser, user_id)
