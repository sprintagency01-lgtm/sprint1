# Bot de reservas por voz (ElevenLabs) + CMS + Portal del cliente

Backend FastAPI que sostiene tres piezas:

1. **Llamadas de voz** — un agente de ElevenLabs Conversational AI habla con
   el cliente final y llama a los server tools expuestos en `/tools/*` para
   consultar disponibilidad y crear / mover / cancelar reservas contra Google
   Calendar.
2. **CMS interno** (`/admin/*`) — el panel que usa Sprintagency para dar de
   alta negocios, revisar métricas y publicar cambios.
3. **Portal del cliente** (`/app`) — lo que ve el dueño del negocio: hoy,
   llamadas, reservas, ingresos, servicios, equipo y ajustes.

> **Histórico:** el proyecto nació como bot de WhatsApp; en abril de 2026
> pivotamos a voz únicamente. Todo el código del webhook de Meta/Twilio se
> retiró. Las guías antiguas (`DEPLOY_RAILWAY.md`, `PLAYBOOK_CLIENTE_NUEVO.md`,
> `START_HERE.md`, `HANDOFF_2026-04-21.md`, `CMS_README.md`) aún describen el
> stack de WhatsApp en algunas secciones — léelas con ese filtro hasta que
> terminemos la revisión.

## Estructura

```
app/
├── main.py                 FastAPI + landing + /api/leads
├── config.py               Settings (variables de entorno)
├── db.py                   SQLAlchemy models + migrations auto
├── calendar_service.py     Google Calendar (leer/crear/mover/cancelar)
├── eleven_tools.py         /tools/* — lo que llama ElevenLabs
├── agent.py                Razonamiento LLM (usado por diag/CLI)
├── diag.py                 /_diag/* endpoints de mantenimiento
├── oauth_web.py            /oauth/start + /oauth/callback
├── cms/                    Panel interno (/admin/*)
└── portal/                 Portal del cliente (/app + /api/portal/*)
tests/
└── test_smoke.py           App arranca, /health y / responden
```

## Puesta en marcha (local)

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env              # rellenar claves
uvicorn app.main:app --reload --port 8000
```

Variables mínimas (ver `.env.example`):

- `OPENAI_API_KEY` — parse IA en el portal + agente en modo CLI.
- `ELEVENLABS_API_KEY`, `ELEVENLABS_VOICE_ID`, `ELEVENLABS_AGENT_ID` — para
  que las llamadas entrantes se puedan originar.
- `TOOL_SECRET` — shared secret que ElevenLabs pone en `X-Tool-Secret` al
  llamar a `/tools/*`.
- `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` — OAuth web de Google Calendar.
- `ADMIN_EMAIL`, `ADMIN_PASSWORD` — bootstrap del primer usuario del CMS.
- `SESSION_SECRET` — firma las cookies de sesión (CMS y portal).
- `LEAD_NOTIFY_WEBHOOK_URL` o `LEAD_NOTIFY_EMAIL_TO` — alerta interna cuando
  entra un lead por la landing. El email requiere `RESEND_API_KEY` y
  `LEAD_EMAIL_FROM`.
- `LEAD_AUTOREPLY_ENABLED=true` — envía autorespuesta al lead si dejó email
  (requiere también `RESEND_API_KEY` y `LEAD_EMAIL_FROM`).
- `BREVO_API_KEY`, `BREVO_LIST_IDS` — sincroniza cada lead como contacto en
  Brevo y lo añade a las listas indicadas.

## Añadir un tenant nuevo

Sigue la checklist de [`BOT_NUEVO_CONFIG.md`](./BOT_NUEVO_CONFIG.md). Para editar el prompt de voz, lee antes [`PROMPT_KNOWLEDGE.md`](./PROMPT_KNOWLEDGE.md) — contiene los gotchas y el proceso de iteración validado con tests.

Resumen:

1. Crear el tenant en el CMS (`/admin/clientes/nuevo`).
2. Autorizar Google Calendar desde `/oauth/start?tenant=<id>`.
3. Crear el agente ElevenLabs con `python scripts/setup_elevenlabs_agent.py
   https://<tu-dominio>` (o botón del CMS). **El script ya aplica la config
   ganadora de baja latencia** (Gemini 3 Flash Preview, TTS Flash v2.5,
   turn_v3, pre_tool_speech=force, personalization webhook, prefetch).
4. Copiar el `agent_id` al tenant.
5. Smoke test: llamada de 30 s. Ana debe empezar a hablar <1,5 s tras tu
   última palabra y dar huecos en <3 s.

El fichero `tenants.yaml` es legacy — ya no se usa para servir tráfico; la
verdad está en la tabla `tenants` de la base de datos.

## Seguridad mínima

- `.env` nunca al repo (ya en `.gitignore`).
- `/tools/*` y `/_diag/*` exigen `X-Tool-Secret`.
- `/admin/*` y `/app/*` autentican con cookies de sesión firmadas.
- Nada de datos de pago — el pago se hace en el local.
