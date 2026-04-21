# Empieza aquí — bot de reservas WhatsApp

Esta carpeta contiene **todo lo necesario** para poner en marcha el bot:
código listo para desplegar, documentación del proyecto y guía de alta de
cuentas.

---

## 1. Mientras Meta for Developers se verifica

No necesitas tener Meta aún para empezar a probar el cerebro del bot.
Puedes validar Claude + Google Calendar desde la terminal con el simulador
de chat y así avanzar en paralelo.

### 1.1. Abre una terminal en esta carpeta

```bash
cd ~/Desktop/bot_reservas_whatsapp
```

### 1.2. Crea el entorno virtual e instala dependencias

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 1.3. Copia el `.env` de ejemplo y rellena lo que tengas

```bash
cp .env.example .env
```

Abre `.env` y rellena **como mínimo**:

- `ANTHROPIC_API_KEY`
- `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` (del OAuth client web de
  Google Cloud)
- `DEFAULT_CALENDAR_ID` (puedes dejar `primary`)

El resto (WhatsApp, ElevenLabs) puede quedarse vacío de momento.

### 1.4. Crea el fichero de tenants

```bash
cp tenants.yaml.example tenants.yaml
```

Edita `tenants.yaml` y, como mínimo, ajusta:

- `phone_number_id`: déjalo como placeholder (lo pondrás cuando Meta esté
  listo).
- `calendar_id`: el calendario de Google donde irán las reservas (en MVP,
  `primary`).
- Ajusta servicios, horarios y nombre del negocio si quieres probar con
  datos realistas.

### 1.5. Autoriza Google Calendar (una vez por tenant)

```bash
python -m app.calendar_service authorize
```

Abre el navegador, da consentimiento a tu cuenta de Google. Se guarda el
refresh token en `.tokens/<tenant_id>.json`.

### 1.6. Prueba el bot por terminal

```bash
python -m app.cli_chat
```

Chatea con el bot. Debería:

- Proponerte huecos libres cuando pidas una cita.
- Crear el evento en Google Calendar cuando confirmes.
- Buscar, mover y cancelar citas existentes.

Comandos dentro del chat: `/reset`, `/quit`, `/tenant`.

> Si algo falla aquí, no pases al paso 2. El problema está en el cerebro
> o en Google, no en WhatsApp.

---

## 2. Cuando Meta for Developers ya está verificada

### 2.1. Añade al `.env` las variables de WhatsApp

- `WHATSAPP_VERIFY_TOKEN`: inventa una cadena (por ejemplo
  `verificame_12345`). La pondrás también en el panel de Meta.
- `WHATSAPP_ACCESS_TOKEN`: el token temporal de 24 h que da Meta en
  *WhatsApp → API Setup*. Para producción luego se cambia por un token
  permanente (System User).
- `WHATSAPP_PHONE_NUMBER_ID`: el ID del número de prueba de la app Meta.
- `WHATSAPP_APP_SECRET`: lo encuentras en *Settings → Basic* de tu app
  Meta.

Copia también el `WHATSAPP_PHONE_NUMBER_ID` al tenant correspondiente en
`tenants.yaml`.

### 2.2. Añade tu número personal como receptor de pruebas

En *WhatsApp → API Setup*, en la sección "To", añade los números
personales que vas a usar para probar. Meta te pedirá confirmación por
código.

### 2.3. Arranca el servidor en local

```bash
uvicorn app.main:app --reload --port 8000
```

### 2.4. Expón el puerto con ngrok (u otro túnel HTTPS)

En otra terminal:

```bash
ngrok http 8000
```

Copia la URL HTTPS (algo como `https://abcd-1234.ngrok-free.app`).

### 2.5. Configura el webhook en Meta

Panel Meta → *WhatsApp → Configuration → Webhook*:

- Callback URL: `https://<tu-url-ngrok>/whatsapp`
- Verify token: el mismo valor que pusiste en `WHATSAPP_VERIFY_TOKEN`.
- Suscríbete al evento `messages`.

Meta hará un GET de verificación y debería marcar "Verified".

### 2.6. Prueba

Desde tu móvil, envía un WhatsApp al número de prueba. Deberías ver logs
en la terminal donde corre `uvicorn` y recibir respuesta en WhatsApp.

---

## 3. Activar notas de voz (Fase 2)

Todo el código ya está listo (`app/voice.py`). Solo necesitas:

1. Rellenar `ELEVENLABS_API_KEY` en `.env`.
2. (Opcional pero recomendado para español) rellenar `ELEVENLABS_VOICE_ID`
   con una voz castellana del marketplace de ElevenLabs. Si lo dejas
   vacío se usa una voz estándar en inglés.
3. Reiniciar el servidor.

Cuando envíes una nota de voz al número de WhatsApp, el bot:

1. Descarga el audio.
2. Lo transcribe con ElevenLabs Scribe (STT).
3. Lo pasa al agente Claude como si fuera un mensaje de texto.
4. Genera la respuesta en audio con ElevenLabs TTS.
5. La envía como nota de voz.

El historial queda marcado con `[voz]` para diferenciarlo de los
mensajes de texto.

---

## 4. Desplegar en Railway (producción)

La carpeta ya tiene `Procfile` y `runtime.txt`, así que el despliegue es
directo:

1. Sube esta carpeta a un repo privado de GitHub.
2. En Railway → *New Project → Deploy from GitHub*.
3. Railway detecta Python y usa `Procfile` para arrancar uvicorn.
4. En *Variables* de Railway, pega TODAS las variables del `.env` (menos
   `.env` mismo, que no se sube al repo — ya está en `.gitignore`).
5. Railway te da una URL pública HTTPS. Úsala en el webhook de Meta en
   lugar de ngrok.

> Importante: en Railway añade un *Volume* montado en `/app/.tokens`
> para que los refresh tokens de Google Calendar sobrevivan a los
> reinicios del servicio. Si no, tendrás que reautorizar cada vez.

---

## 5. Documentación completa

Dentro de `docs/` tienes:

- `plan_proyecto.docx` — plan completo del proyecto: fases, arquitectura,
  opciones de voz, presupuesto, riesgos, verticales.
- `guia_alta_cuentas.docx` — guía paso a paso para dar de alta Anthropic,
  ElevenLabs, Google Cloud, Meta for Developers y Railway.
- `presupuesto_fases.xlsx` — presupuesto por fase con tres escenarios
  (conservador, base, optimista) y pricing recomendado para el cliente.

---

## 6. Estructura del proyecto

```
bot_reservas_whatsapp/
├── START_HERE.md             ← estás aquí
├── README.md                 Visión técnica del esqueleto
├── .env.example              Copia a .env y rellena
├── .gitignore
├── Procfile                  Para Railway
├── runtime.txt               Versión de Python
├── requirements.txt
├── tenants.yaml.example      Copia a tenants.yaml
├── app/
│   ├── main.py               FastAPI + webhook
│   ├── config.py             Variables de entorno
│   ├── whatsapp.py           Cliente Meta Cloud API (texto + detección audio)
│   ├── voice.py              ElevenLabs STT + TTS + envío nota de voz
│   ├── agent.py              Agente Claude con tool use
│   ├── calendar_service.py   Google Calendar
│   ├── cli_chat.py           Simulador de chat por terminal
│   ├── tenants.py            Carga de tenants
│   └── db.py                 SQLite para historial
├── tests/
└── docs/
    ├── plan_proyecto.docx
    ├── guia_alta_cuentas.docx
    └── presupuesto_fases.xlsx
```

---

## 7. Hoja de ruta inmediata

1. [ ] Cuentas creadas: Anthropic, ElevenLabs, Google Cloud, Meta (pending),
       Railway.
2. [ ] `.env` relleno con Anthropic + Google.
3. [ ] `python -m app.cli_chat` funciona end-to-end.
4. [ ] Meta verificada, webhook conectado.
5. [ ] WhatsApp en tu número personal conversa con el bot (texto).
6. [ ] ElevenLabs API key → notas de voz funcionando.
7. [ ] Despliegue Railway → URL pública estable.
8. [ ] Primer cliente real (peluquería / hotel demo).
