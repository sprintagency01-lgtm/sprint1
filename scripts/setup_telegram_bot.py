#!/usr/bin/env python3
"""Configura el webhook del bot de Telegram apuntándolo al backend desplegado.

Hay que ejecutarlo UNA vez después de cada cambio de URL pública (cambio de
proyecto Railway, dominio propio, etc.), o si se rota el
TELEGRAM_WEBHOOK_SECRET.

Uso:

    # Con las env vars del proyecto cargadas (.env):
    python scripts/setup_telegram_bot.py

    # O pasando explícitamente la URL pública:
    python scripts/setup_telegram_bot.py https://sprintiasolutions.com

    # (Antes del cambio de dominio del 2026-04-29 era:
    #  python scripts/setup_telegram_bot.py https://web-production-98b02b.up.railway.app)

Qué hace:

1. Lee TELEGRAM_BOT_TOKEN y TELEGRAM_WEBHOOK_SECRET del .env.
2. Llama getMe para verificar que el token funciona.
3. Llama setWebhook con `url` = `<PUBLIC_URL>/telegram/webhook` y
   `secret_token` = TELEGRAM_WEBHOOK_SECRET.
4. Pide getWebhookInfo y lo imprime para que puedas comprobar que quedó bien.

No modifica la BD ni toca ningún otro servicio. Solo habla con Telegram.
"""
from __future__ import annotations

import json
import os
import sys

import httpx
from dotenv import load_dotenv

load_dotenv()

API_BASE = "https://api.telegram.org"
TIMEOUT = httpx.Timeout(15.0, connect=5.0)


def _die(msg: str, code: int = 1) -> None:
    print(f"✖  {msg}", file=sys.stderr)
    sys.exit(code)


def _public_base_url(argv: list[str]) -> str:
    # Prioridad: argumento de línea de comando > PUBLIC_BASE_URL > RAILWAY_PUBLIC_DOMAIN
    if len(argv) >= 2:
        return argv[1].strip().rstrip("/")
    url = os.getenv("PUBLIC_BASE_URL", "").strip().rstrip("/")
    if url:
        return url
    rp = os.getenv("RAILWAY_PUBLIC_DOMAIN", "").strip()
    if rp:
        # Railway expone solo el dominio, sin scheme.
        return f"https://{rp.rstrip('/')}"
    _die(
        "Necesito la URL pública del backend.\n"
        "Pásala como argumento o define PUBLIC_BASE_URL / RAILWAY_PUBLIC_DOMAIN."
    )
    return ""  # unreachable


def _call(token: str, method: str, payload: dict | None = None) -> dict:
    url = f"{API_BASE}/bot{token}/{method}"
    try:
        if payload is None:
            r = httpx.get(url, timeout=TIMEOUT)
        else:
            r = httpx.post(url, json=payload, timeout=TIMEOUT)
    except httpx.HTTPError as e:
        _die(f"Red caída hablando con Telegram ({method}): {e}")
    try:
        body = r.json()
    except ValueError:
        _die(f"{method} no devolvió JSON. HTTP {r.status_code}: {r.text[:200]}")
    if r.status_code >= 400 or not body.get("ok"):
        _die(f"{method} falló: HTTP {r.status_code} — {body.get('description') or r.text[:200]}")
    return body.get("result") or {}


def main(argv: list[str]) -> None:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    secret = os.getenv("TELEGRAM_WEBHOOK_SECRET", "").strip()

    if not token:
        _die(
            "TELEGRAM_BOT_TOKEN vacío. Ponlo en tu .env (o en Railway) antes de correr esto.\n"
            "Si aún no tienes el bot: habla con @BotFather en Telegram, /newbot, copia el token."
        )
    if not secret:
        _die(
            "TELEGRAM_WEBHOOK_SECRET vacío. Genera uno con, por ejemplo,\n"
            "  python -c \"import secrets; print(secrets.token_urlsafe(32))\"\n"
            "y ponlo en .env como TELEGRAM_WEBHOOK_SECRET."
        )

    public = _public_base_url(argv)
    webhook_url = f"{public}/telegram/webhook"

    print(f"→ Verificando token con getMe…")
    me = _call(token, "getMe")
    print(f"  Bot: @{me.get('username')} ({me.get('first_name')})  id={me.get('id')}")

    print(f"→ Registrando webhook en {webhook_url}")
    _call(
        token,
        "setWebhook",
        {
            "url": webhook_url,
            "secret_token": secret,
            "allowed_updates": ["message", "callback_query"],
            "drop_pending_updates": True,
        },
    )
    print("  setWebhook → ok")

    print(f"→ getWebhookInfo (verificación):")
    info = _call(token, "getWebhookInfo")
    print(json.dumps(info, indent=2, ensure_ascii=False))

    if info.get("last_error_message"):
        print(
            "\n⚠  Telegram reporta un error en el último delivery del webhook: "
            f"{info['last_error_message']}",
            file=sys.stderr,
        )
        print("   Revisa que el backend esté arriba y responda 200 en /telegram/webhook.", file=sys.stderr)

    print("\n✔  Listo. Manda un mensaje al bot desde Telegram para probarlo.")


if __name__ == "__main__":
    main(sys.argv)
