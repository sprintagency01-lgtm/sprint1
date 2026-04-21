# Bot de reservas por WhatsApp (MVP)

Esqueleto de backend para el bot de reservas por WhatsApp con Google Calendar
y agente LLM con function calling. Listo para Fase 1 del plan.

## Qué hace hoy este esqueleto

- Expone un webhook `/whatsapp` para Meta Cloud API (verificación GET + eventos POST).
- Recibe el mensaje, identifica al tenant (negocio) por el número de WhatsApp destino.
- Manda el mensaje al agente (Claude con tool use) con herramientas:
  `consultar_disponibilidad`, `crear_reserva`, `mover_reserva`, `cancelar_reserva`.
- El agente decide qué función llamar. El backend ejecuta contra Google Calendar.
- Responde por WhatsApp con el texto del agente.
- Persiste el historial de conversaciones en SQLite.

## Estructura

```
bot_reservas/
├── app/
│   ├── main.py              FastAPI + webhook
│   ├── config.py            Variables de entorno
│   ├── whatsapp.py          Cliente de Meta Cloud API
│   ├── agent.py             Agente Claude con tool use
│   ├── calendar_service.py  Google Calendar (leer/crear/mover/cancelar)
│   ├── tenants.py           Carga de tenants y su prompt
│   └── db.py                SQLite para historial
├── tests/
│   └── test_smoke.py        Test mínimo de que arranca
├── requirements.txt
├── .env.example             Copiar a .env y rellenar
├── .gitignore
└── README.md
```

## Puesta en marcha (día 1)

### 1. Requisitos

- Python 3.11+
- Cuenta en Meta for Developers con una app y tu número de WhatsApp como
  test number.
- API key de Anthropic (https://console.anthropic.com).
- Proyecto en Google Cloud con Calendar API habilitada y un OAuth client
  (type: Web) con redirect `http://localhost:8000/oauth/callback`.
- (Producción) Railway o Render para exponer HTTPS público al webhook.

### 2. Instalar

```bash
cd bot_reservas
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env         # y rellenar
```

### 3. Configurar .env

Variables mínimas:

- `WHATSAPP_VERIFY_TOKEN`: inventa una cadena. La pegas también en el panel de Meta.
- `WHATSAPP_ACCESS_TOKEN`: token temporal de 24 h de tu app Meta (para desarrollo).
  Para producción se genera un token permanente con System User.
- `WHATSAPP_PHONE_NUMBER_ID`: el ID del número de prueba en tu app Meta.
- `ANTHROPIC_API_KEY`: la API key de vuestro console.anthropic.com.
- `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET`: del OAuth client.
- `DEFAULT_CALENDAR_ID`: en MVP puedes dejar tu calendario primario (`primary`).

### 4. Arrancar en local

```bash
uvicorn app.main:app --reload --port 8000
```

Expón el puerto 8000 a internet con ngrok (u otro túnel):

```bash
ngrok http 8000
```

Coge la URL HTTPS pública, ve al panel Meta → WhatsApp → Configuration →
Webhook: pega `<URL_PUBLICA>/whatsapp` y el `WHATSAPP_VERIFY_TOKEN`.
Suscríbete a `messages`. Envía un 'hola' desde tu móvil al número de prueba.

### 5. Autorizar Google Calendar (una vez)

```bash
python -m app.calendar_service authorize
```

Abre el navegador, das consentimiento, y guarda el refresh token en `.tokens/`.

## Cómo añadir un segundo tenant

Editad `tenants.yaml` (crearlo siguiendo el ejemplo comentado en
`app/tenants.py`). Cada tenant tiene:

- `name`: nombre del negocio.
- `phone_number_id`: el ID del número de WhatsApp asignado.
- `calendar_id`: su calendario de Google.
- `system_prompt`: plantilla del agente, personalizada con servicios y reglas.
- `services`: lista de servicios con duración y precio.
- `business_hours`: horario de apertura.

## Roadmap del código

- [x] Fase 1: webhook + agente + Calendar básico (este esqueleto).
- [ ] Fase 2: notas de voz (entrante: Whisper, saliente: ElevenLabs).
- [ ] Fase 2: multi-tenant con BD y dashboard mínimo.
- [ ] Fase 3: integración con Twilio Voice + ElevenLabs Conversational AI.
- [ ] Fase 4: panel de administración del cliente y OAuth onboarding.

## Seguridad mínima desde el día uno

- `.env` nunca al repo (ya está en `.gitignore`).
- Valida la firma del webhook de Meta (`X-Hub-Signature-256`) — ver TODO
  en `whatsapp.py`.
- No guardes mensajes con datos personales más de lo necesario. Añade un
  job que borre conversaciones con > 90 días.
- Nunca dejes que el LLM construya URLs o mande a terceros: solo puede
  llamar a las funciones whitelist.
