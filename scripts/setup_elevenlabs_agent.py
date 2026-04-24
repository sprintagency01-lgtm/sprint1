"""Crea/actualiza el agente de voz en ElevenLabs Conversational AI.

Uso:
    python scripts/setup_elevenlabs_agent.py <TOOL_BASE_URL>

Ejemplo con ngrok:
    python scripts/setup_elevenlabs_agent.py https://abcd1234.ngrok.app

El script:
  1. Lee el tenant desde tenants.yaml (el primero; MVP monotenant).
  2. Construye el system prompt con el contexto de peluqueros y reglas.
  3. Registra los 5 server tools (webhooks) apuntando a TOOL_BASE_URL/tools/*.
  4. Si ELEVENLABS_AGENT_ID está en .env, hace PATCH (update).
     Si está vacío, hace POST (create) y guarda el id en .env.
"""
from __future__ import annotations

import os
import sys
import json
import pathlib

import httpx
from dotenv import load_dotenv

# Cargar .env de la raíz del proyecto
HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
load_dotenv(ROOT / ".env")

# Permitir importar el paquete app.* al correr el script desde /scripts
sys.path.insert(0, str(ROOT))
from app import tenants as tn  # noqa: E402

API_BASE = "https://api.elevenlabs.io"
DIAS_NOMBRE = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]


def build_system_prompt(tenant: dict) -> str:
    """Prompt de voz: corto, concreto, optimizado para latencia baja.

    Versión voz (distinta del prompt de texto de tenants.yaml: aquel es muy largo
    porque el canal es chat; en teléfono queremos ~500 tokens como mucho para que
    el LLM responda en <400ms)."""
    nombre_negocio = tenant.get("name", "la peluquería")

    # Servicios en una sola línea
    servicios = tenant.get("services") or []
    servicios_txt = "; ".join(
        f"{s['nombre']} ({s['duracion_min']}min, {s['precio']}€)"
        for s in servicios
    )

    # Peluqueros con sus días
    pelus = tenant.get("peluqueros") or []
    pelu_txt = ""
    if pelus:
        lineas = []
        for p in pelus:
            dias = p.get("dias_trabajo") or list(range(7))
            nombres = ", ".join(DIAS_NOMBRE[d] for d in dias)
            lineas.append(f"{p['nombre']} ({nombres})")
        pelu_txt = "Peluqueros: " + "; ".join(lineas) + ". "

    # Horario (soporta esquema plano {open,close} y por-día {mon:[...], ...})
    bh = tenant.get("business_hours") or {}
    if "open" in bh or "close" in bh:
        horario_txt = f"Horario: {bh.get('open','9:30')} a {bh.get('close','20:30')}. "
    else:
        # Busca el primer día abierto para un texto legible
        abre = cierra = None
        for k in ("mon", "tue", "wed", "thu", "fri", "sat", "sun"):
            h = bh.get(k)
            if h and h != ["closed"] and h[0] != "closed" and len(h) >= 2:
                abre, cierra = h[0], h[1]
                break
        horario_txt = f"Horario: {abre or '09:30'} a {cierra or '20:30'}. "

    prompt = (
        f"Eres Ana, recepcionista de {nombre_negocio}. Hablas por teléfono, en "
        f"español de España, con tono cálido y natural — frases cortas, como una "
        f"persona real. No enumeres servicios a menos que te los pidan.\n\n"
        f"{horario_txt}{pelu_txt}\n"
        f"Servicios: {servicios_txt}.\n\n"
        "FECHA ACTUAL: {{system__time_utc}} (UTC). Zona: Europe/Madrid. "
        "Si el cliente dice 'mañana' o 'el jueves', calcúlalo desde esa fecha.\n\n"
        "REGLAS DURAS (no te las saltes):\n"
        "1. Antes de proponer hora, LLAMA a consultar_disponibilidad. NO inventes huecos.\n"
        "2. Antes de crear, LLAMA a crear_reserva. No digas 'reservado' sin haberlo llamado.\n"
        "3. NO digas rellenos como 'déjame mirar' o 'un segundo' — pide el tool "
        "directamente, ElevenLabs gestiona el silencio.\n"
        "4. Pregunta siempre: servicio, día/hora aproximada, peluquero preferido, nombre. "
        "Si el cliente dice 'me da igual', úsalo como 'sin preferencia'.\n"
        "5. Título del evento: 'Servicio — Nombre (con Peluquero)'.\n"
        "6. Confirma hora y peluquero antes de crear.\n"
        "7. Si el cliente pide a un peluquero en un día que no trabaja, dilo y ofrece "
        "alternativa. Si no hay huecos, ofrece otro día o el otro peluquero.\n"
        "8. Envía fechas ISO sin Z al final (hora local): 2026-04-22T10:00:00.\n"
        "9. En este entorno de pruebas, cuando crees la reserva quedará guardada en el calendario principal del negocio (Sprintagency), aunque estés usando la disponibilidad por peluquero.\n"
        "10. Si una herramienta falla, dilo con naturalidad y pide al cliente que llame "
        "al 910 000 000 en horario de tienda — no prometas reintentar 3 veces."
    )
    return prompt


def build_tools(tool_base_url: str, tool_secret: str) -> list[dict]:
    """Definiciones de los 5 server tools que ElevenLabs llamará via HTTP."""
    base_headers = {"X-Tool-Secret": tool_secret, "Content-Type": "application/json"}

    def webhook(name, description, url_path, body_schema):
        return {
            "type": "webhook",
            "name": name,
            "description": description,
            "api_schema": {
                "url": f"{tool_base_url.rstrip('/')}{url_path}",
                "method": "POST",
                "request_headers": base_headers,
                "request_body_schema": body_schema,
            },
        }

    return [
        webhook(
            "consultar_disponibilidad",
            "Consulta huecos libres en los calendarios de los peluqueros. "
            "USA SIEMPRE esta función antes de proponer una hora al cliente. "
            "Si el cliente tiene preferencia de peluquero/a, pásalo en "
            "peluquero_preferido; si no, déjalo vacío.",
            "/tools/consultar_disponibilidad",
            {
                "type": "object",
                "properties": {
                    "fecha_desde_iso": {"type": "string", "description": "Inicio del rango, ISO 8601. Ej: 2026-04-22T09:30:00"},
                    "fecha_hasta_iso": {"type": "string", "description": "Fin del rango, ISO 8601."},
                    "duracion_minutos": {"type": "integer", "description": "Duración del servicio (30, 45, 90...)."},
                    "peluquero_preferido": {"type": "string", "description": "Nombre del peluquero si hay preferencia. Vacío = cualquiera."},
                    "max_resultados": {"type": "integer", "description": "Máximo de huecos. Por defecto 5."},
                },
                "required": ["fecha_desde_iso", "fecha_hasta_iso", "duracion_minutos"],
            },
        ),
        webhook(
            "crear_reserva",
            "Crea la cita en el calendario del peluquero. SOLO tras confirmación "
            "explícita del cliente. El título debe ser: "
            "'Servicio — Nombre cliente (con Peluquero)'.",
            "/tools/crear_reserva",
            {
                "type": "object",
                "properties": {
                    "titulo": {"type": "string", "description": "Título del evento, formato 'Servicio — Nombre cliente (con Peluquero)' o '(sin preferencia)'."},
                    "inicio_iso": {"type": "string", "description": "Fecha/hora de inicio, ISO 8601."},
                    "fin_iso": {"type": "string", "description": "Fecha/hora de fin, ISO 8601."},
                    "telefono_cliente": {"type": "string", "description": "Número de teléfono del cliente."},
                    "peluquero": {"type": "string", "description": "Nombre del peluquero asignado o 'sin preferencia'."},
                    "notas": {"type": "string", "description": "Notas extra (alergias, detalles). Puede estar vacío."},
                },
                "required": ["titulo", "inicio_iso", "fin_iso", "telefono_cliente", "peluquero"],
            },
        ),
        webhook(
            "buscar_reserva_cliente",
            "Busca la próxima reserva del cliente cuando pide mover/cancelar. "
            "Primero prueba con `telefono_cliente` (suele ser el caller_id). "
            "Si el cliente dice que la cita está 'a nombre de X' o no recuerda "
            "con qué teléfono reservó, llama otra vez con `nombre_cliente`.",
            "/tools/buscar_reserva_cliente",
            {
                "type": "object",
                "properties": {
                    "telefono_cliente": {"type": "string", "description": "Teléfono del cliente, con prefijo si lo da."},
                    "nombre_cliente": {"type": "string", "description": "Nombre del cliente tal y como lo dio al reservar ('Mario', 'Ana López'). Alternativa al teléfono."},
                    "dias_adelante": {"type": "integer", "description": "Cuántos días mirar hacia adelante. Por defecto 30."},
                },
                "required": [],
            },
        ),
        webhook(
            "mover_reserva",
            "Mueve una reserva a nueva fecha/hora. Reenvía calendar_id si lo obtuviste de buscar_reserva_cliente — el backend mueve directo y baja latencia.",
            "/tools/mover_reserva",
            {
                "type": "object",
                "properties": {
                    "event_id": {"type": "string", "description": "ID del evento devuelto por buscar_reserva_cliente."},
                    "nuevo_inicio_iso": {"type": "string", "description": "Nuevo inicio, ISO 8601."},
                    "nuevo_fin_iso": {"type": "string", "description": "Nuevo fin, ISO 8601."},
                    "calendar_id": {"type": "string", "description": "calendar_id devuelto por buscar_reserva_cliente. Si viene, el backend mueve directo sin iterar peluqueros."},
                },
                "required": ["event_id", "nuevo_inicio_iso", "nuevo_fin_iso"],
            },
        ),
        webhook(
            "cancelar_reserva",
            "Cancela una reserva. SOLO tras confirmación explícita. Reenvía calendar_id si lo obtuviste de buscar_reserva_cliente.",
            "/tools/cancelar_reserva",
            {
                "type": "object",
                "properties": {
                    "event_id": {"type": "string", "description": "ID del evento a cancelar."},
                    "calendar_id": {"type": "string", "description": "calendar_id devuelto por buscar_reserva_cliente. Si viene, el backend cancela directo sin iterar peluqueros."},
                },
                "required": ["event_id"],
            },
        ),
    ]


def upsert_env(key: str, value: str) -> None:
    """Añade o actualiza KEY=VALUE en .env manteniendo el resto."""
    env_path = ROOT / ".env"
    lines = env_path.read_text().splitlines()
    found = False
    for i, line in enumerate(lines):
        if line.startswith(f"{key}="):
            lines[i] = f"{key}={value}"
            found = True
            break
    if not found:
        lines.append(f"{key}={value}")
    env_path.write_text("\n".join(lines) + "\n")


def main() -> None:
    if len(sys.argv) < 2:
        print("Uso: python scripts/setup_elevenlabs_agent.py <TOOL_BASE_URL>")
        print("Ej:  python scripts/setup_elevenlabs_agent.py https://abcd1234.ngrok.app")
        sys.exit(1)
    tool_base_url = sys.argv[1].rstrip("/")

    api_key = os.environ.get("ELEVENLABS_API_KEY")
    voice_id = os.environ.get("ELEVENLABS_VOICE_ID")
    tool_secret = os.environ.get("TOOL_SECRET")
    agent_id = os.environ.get("ELEVENLABS_AGENT_ID") or None

    if not api_key:
        print("ERROR: ELEVENLABS_API_KEY no está en .env"); sys.exit(1)
    if not voice_id:
        print("ERROR: ELEVENLABS_VOICE_ID no está en .env"); sys.exit(1)
    if not tool_secret:
        print("ERROR: TOOL_SECRET no está en .env. Genera uno con `python -c 'import secrets; print(secrets.token_urlsafe(32))'`"); sys.exit(1)

    tenant = tn.load_tenants()[0]
    system_prompt = build_system_prompt(tenant)
    tools = build_tools(tool_base_url, tool_secret)

    # Config ganadora tras rondas 1-7 de optimización de latencia.
    # Ver BOT_NUEVO_CONFIG.md para el detalle y la justificación de cada valor.
    #
    # Placeholders de dynamic_variables: ElevenLabs ignora lo que devuelve el
    # personalization webhook si las keys no están pre-declaradas aquí como
    # `dynamic_variable_placeholders`. Sin esto, el prompt ve literal
    # "{{manana_fecha_iso}}" y el LLM alucina fechas (bug real detectado en
    # producción 2026-04-24).
    dynamic_placeholders = {
        "hoy_fecha_iso": "",
        "manana_fecha_iso": "",
        "pasado_fecha_iso": "",
        "hoy_dia_semana": "",
        "manana_dia_semana": "",
        "hoy_natural": "",
        "manana_natural": "",
        "hora_local": "",
        "caller_id_legible": "",
        "tenant_id": "",
        "tenant_name": "",
    }
    payload = {
        "name": f"Ana - {tenant.get('name', 'Peluquería')}",
        "conversation_config": {
            "agent": {
                "prompt": {
                    "prompt": system_prompt,
                    "tools": tools,
                    "llm": "gemini-3-flash-preview",   # ronda 6 — 3x más rápido que 2.5-flash, 4/4 tools OK
                    "temperature": 0.3,
                    "max_tokens": 300,
                    "thinking_budget": 0,
                    # ronda 7 — desactiva cascade de 4s, camino hot más predecible
                    "backup_llm_config": {"preference": "disabled"},
                    # ronda 9 — end_call built-in para cerrar la llamada
                    "built_in_tools": {
                        "end_call": {
                            "name": "end_call",
                            "description": (
                                "Cuelga la llamada cuando la conversación ha terminado: "
                                "tras un cierre natural, tras confirmar una reserva y que "
                                "el cliente no quiera nada más, o tras derivar al teléfono "
                                "de fallback por un error irreparable."
                            ),
                        },
                    },
                },
                "first_message": "¡Hola! Soy Ana de la peluquería. ¿En qué te puedo ayudar?",
                "language": "es",
                "dynamic_variables": {
                    "dynamic_variable_placeholders": dynamic_placeholders,
                },
            },
            "tts": {
                "voice_id": voice_id,
                "model_id": "eleven_flash_v2_5",       # ronda 5 — flash, no v3 conversational
                "text_normalisation_type": "elevenlabs",  # ronda 7 — server-side, evita prompt
                "agent_output_audio_format": "ulaw_8000",  # ronda 4 — match Twilio sin transcode
                "optimize_streaming_latency": 4,        # ronda 4 — máximo
            },
            "turn": {
                "turn_timeout": 1.0,                    # ronda 4 — mínimo de la API
                "turn_eagerness": "eager",
                "speculative_turn": True,
                "turn_model": "turn_v3",                # ronda 6
                "spelling_patience": "off",             # ronda 7
                # Ronda 9: si el cliente calla 3.5s, Ana dice "¿sigues ahí?"
                # generado por el LLM; tras otros ~25s de silencio cuelga sola.
                "soft_timeout_config": {
                    "timeout_seconds": 3.5,
                    "use_llm_generated_message": True,
                    "message": "¿Sigues ahí?",
                },
                "silence_end_call_timeout": 25.0,
            },
            "asr": {
                "quality": "high",                      # obligatorio por la API
                "user_input_audio_format": "pcm_16000",
            },
        },
        # Personalization webhook (ronda 7): ElevenLabs llama a este endpoint
        # una vez al inicio de cada llamada y recibe dynamic_variables
        # precomputadas (hoy_fecha_iso, manana_natural, hora_local, etc.).
        # Además dispara un prefetch especulativo de freebusy que deja el
        # cache caliente cuando Ana pide huecos 2-5s después.
        "platform_settings": {
            "workspace_overrides": {
                "conversation_initiation_client_data_webhook": {
                    "url": f"{tool_base_url.rstrip('/')}/tools/eleven/personalization?tenant_id={tenant.get('id','default')}",
                    "request_headers": {
                        "X-Tool-Secret": tool_secret,
                        "Content-Type": "application/json",
                    },
                },
            },
            "overrides": {
                "enable_conversation_initiation_client_data_from_webhook": True,
            },
        },
    }

    headers = {"xi-api-key": api_key, "Content-Type": "application/json"}

    if agent_id:
        url = f"{API_BASE}/v1/convai/agents/{agent_id}"
        print(f"PATCH {url}")
        r = httpx.patch(url, headers=headers, json=payload, timeout=30)
    else:
        url = f"{API_BASE}/v1/convai/agents/create"
        print(f"POST {url}")
        r = httpx.post(url, headers=headers, json=payload, timeout=30)

    if r.status_code >= 400:
        print(f"ERROR {r.status_code}: {r.text[:2000]}")
        sys.exit(2)

    body = r.json()
    final_agent_id = body.get("agent_id") or agent_id
    print(f"OK agent_id: {final_agent_id}")
    print(f"OK voice_id: {voice_id}")
    print(f"OK tools registradas: {len(tools)}")
    print(f"OK tool_base_url: {tool_base_url}")

    if not agent_id and final_agent_id:
        upsert_env("ELEVENLABS_AGENT_ID", final_agent_id)
        print("Guardado en .env: ELEVENLABS_AGENT_ID")

    print("\nPrueba desde: https://elevenlabs.io/app/conversational-ai/agents/" + (final_agent_id or ""))


if __name__ == "__main__":
    main()
