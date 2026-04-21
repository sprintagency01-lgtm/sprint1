"""Autenticación del CMS: login único de administrador.

- Las credenciales se guardan en la tabla `admin_users`. La primera vez se
  crea automáticamente el usuario usando las variables de entorno
  ADMIN_EMAIL y ADMIN_PASSWORD.
- La sesión se mantiene en una cookie firmada (`itsdangerous`) con el id de
  usuario y un timestamp.
- SESSION_SECRET debe estar en .env (si no, se genera una al vuelo y se pierde
  al reiniciar: forzando re-login).
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

COOKIE_NAME = "reservabot_admin"
SESSION_TTL_SECONDS = 60 * 60 * 24 * 14  # 14 días

_SECRET = os.getenv("SESSION_SECRET") or secrets.token_urlsafe(48)
_serializer = URLSafeTimedSerializer(_SECRET, salt="reservabot-cms")


# --------------------------------------------------------------------------
#  Bootstrap del usuario admin
# --------------------------------------------------------------------------

def ensure_admin_user() -> None:
    """Crea el admin si no existe, usando ADMIN_EMAIL / ADMIN_PASSWORD del .env."""
    email = os.getenv("ADMIN_EMAIL", "admin@reservabot.local").strip().lower()
    password = os.getenv("ADMIN_PASSWORD", "").strip()

    with Session(db_module.engine) as s:
        existing = s.query(db_module.AdminUser).filter(db_module.AdminUser.email == email).first()
        if existing is not None:
            return
        if not password:
            # No queremos crear una cuenta sin password. Log aviso y salir.
            import logging
            logging.getLogger(__name__).warning(
                "ADMIN_PASSWORD no configurado. No se ha creado el usuario admin. "
                "Define ADMIN_EMAIL y ADMIN_PASSWORD en .env y reinicia."
            )
            return
        s.add(db_module.AdminUser(email=email, password_hash=bcrypt.hash(password)))
        s.commit()


# --------------------------------------------------------------------------
#  Login / logout
# --------------------------------------------------------------------------

def verify_credentials(email: str, password: str) -> Optional[int]:
    """Devuelve el id del admin si las credenciales son correctas."""
    with Session(db_module.engine) as s:
        user = (
            s.query(db_module.AdminUser)
            .filter(db_module.AdminUser.email == email.strip().lower())
            .first()
        )
        if user is None:
            return None
        if not bcrypt.verify(password, user.password_hash):
            return None
        return user.id


def sign_session(user_id: int) -> str:
    return _serializer.dumps({"uid": user_id, "ts": int(time.time())})


def read_session(token: str) -> Optional[int]:
    try:
        data = _serializer.loads(token, max_age=SESSION_TTL_SECONDS)
        return int(data.get("uid"))
    except (BadSignature, SignatureExpired, ValueError, TypeError):
        return None


# --------------------------------------------------------------------------
#  Dependencia FastAPI
# --------------------------------------------------------------------------

def current_user_id(request: Request) -> int:
    """Dependencia FastAPI que exige sesión válida. Si no, redirige al login."""
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        raise HTTPException(status_code=status.HTTP_303_SEE_OTHER, headers={"Location": "/admin/login"})
    uid = read_session(token)
    if uid is None:
        raise HTTPException(status_code=status.HTTP_303_SEE_OTHER, headers={"Location": "/admin/login"})
    return uid


def current_user_email(user_id: int) -> str:
    with Session(db_module.engine) as s:
        u = s.get(db_module.AdminUser, user_id)
        return u.email if u else ""
