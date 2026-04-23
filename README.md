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

## Añadir un tenant nuevo

Desde el CMS (`/admin/clientes/nuevo`) se crea el registro. Luego se autoriza
el calendario de Google desde `/oauth/start?tenant=<id>` y se configura el
agente de ElevenLabs apuntando sus server tools al dominio del deploy
(`/tools/*`) con el `TOOL_SECRET`.

El fichero `tenants.yaml` es legacy — ya no se usa para servir tráfico; la
verdad está en la tabla `tenants` de la base de datos.

## Seguridad mínima

- `.env` nunca al repo (ya en `.gitignore`).
- `/tools/*` y `/_diag/*` exigen `X-Tool-Secret`.
- `/admin/*` y `/app/*` autentican con cookies de sesión firmadas.
- Nada de datos de pago — el pago se hace en el local.
