"""Simulador de conversación por terminal.

Sirve para probar la pareja OpenAI + Google Calendar SIN WhatsApp.
Usa exactamente el mismo agente y el mismo backend de calendario que usará
el webhook en producción, así que lo que funcione aquí funcionará en WhatsApp.

Uso:
    python -m app.cli_chat
    python -m app.cli_chat --tenant pelu_demo --phone +34666111222

Comandos especiales dentro del chat:
    /reset   → borra el historial de esta conversación
    /quit    → salir
    /tenant  → muestra el tenant cargado
"""
from __future__ import annotations

import argparse
import sys

from . import agent, db, tenants
from .config import settings


def main() -> int:
    parser = argparse.ArgumentParser(description="Simulador de chat del bot de reservas.")
    parser.add_argument("--tenant", default=None, help="ID del tenant (por defecto el primero del YAML)")
    parser.add_argument("--phone", default="+34600000000", help="Teléfono simulado del cliente")
    args = parser.parse_args()

    # Validar configuración mínima
    missing = []
    if not settings.openai_api_key:
        missing.append("OPENAI_API_KEY")
    if not settings.google_client_id or not settings.google_client_secret:
        missing.append("GOOGLE_CLIENT_ID/SECRET")
    if missing:
        print(f"Faltan variables en .env: {', '.join(missing)}", file=sys.stderr)
        return 1

    # Cargar tenant
    tenants_list = tenants.load_tenants()
    if args.tenant:
        tenant = next((t for t in tenants_list if t.get("id") == args.tenant), None)
        if not tenant:
            print(f"Tenant '{args.tenant}' no encontrado. Disponibles: "
                  f"{[t.get('id') for t in tenants_list]}", file=sys.stderr)
            return 1
    else:
        tenant = tenants_list[0]

    print("=" * 60)
    print(f"Simulador de chat — tenant: {tenant.get('name')} ({tenant.get('id')})")
    print(f"Cliente simulado: {args.phone}")
    print(f"Modelo: {settings.openai_model}")
    print(f"Calendario: {tenant.get('calendar_id') or settings.default_calendar_id}")
    print("Comandos: /reset, /quit, /tenant")
    print("=" * 60)
    print()

    tenant_id = tenant.get("id", "default")

    while True:
        try:
            user_msg = input("Tú > ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break

        if not user_msg:
            continue
        if user_msg == "/quit":
            break
        if user_msg == "/tenant":
            print(f"Bot > Tenant actual: {tenant}")
            continue
        if user_msg == "/reset":
            # Borrado físico del historial de ese tenant + phone
            from sqlalchemy import delete
            from sqlalchemy.orm import Session
            from .db import engine, Message
            with Session(engine) as s:
                s.execute(delete(Message).where(
                    Message.tenant_id == tenant_id,
                    Message.customer_phone == args.phone,
                ))
                s.commit()
            print("Bot > [historial borrado]")
            continue

        # Guardar el mensaje del usuario
        db.save_message(tenant_id, args.phone, "user", user_msg)
        history = db.load_history(tenant_id, args.phone)
        # Quitar el último (es el que acabamos de guardar, va en user_message)
        history = [m for m in history[:-1]] if history and history[-1]["role"] == "user" else history

        try:
            reply = agent.reply(
                user_message=user_msg,
                history=history,
                tenant=tenant,
                caller_phone=args.phone,
            )
        except Exception as e:
            print(f"Bot > [ERROR] {type(e).__name__}: {e}")
            continue

        db.save_message(tenant_id, args.phone, "assistant", reply)
        print(f"Bot > {reply}")
        print()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
