# Demo Gemini 3.1 Flash Live (paralelo a ElevenLabs)

POC local para evaluar **Gemini 3.1 Flash Live preview** como sustituto de
ElevenLabs Conversational AI en el bot de reservas.

Reusa el prompt de Ana (`ana_prompt_new.txt`) y las MISMAS tools que el agente
de ElevenLabs llama en producción (`/tools/*` del backend Sprintia en
`sprintiasolutions.com`). La idea es comparar 1:1: misma señora Ana, mismo
flujo de reservas, distinto motor de voz.

## TL;DR

```bash
cd demo_gemini_live
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
# Asegúrate de que .env tiene GEMINI_API_KEY válida (ya está rellenada).
python gemini_live_demo.py
```

Habla por el micro del Mac. Ana te responde por los altavoces. Ctrl+C para colgar.

## Por qué este demo y no abrir AI Studio

AI Studio (`https://aistudio.google.com/live`) sirve para escuchar voces y
probar prompts sueltos. NO te deja conectar tools custom contra Railway. Para
medir el flujo real (consultar disponibilidad → crear reserva → end_call) hay
que montarlo en código. Eso hace este demo.

## Arquitectura del demo

```
Micro Mac ──┐
            │
            ▼ (PCM16 16kHz mono, chunks 50ms)
   ┌──────────────────────────┐
   │   sounddevice InputStream │
   └────────────┬─────────────┘
                ▼
         asyncio.Queue (audio_in)
                │
                ▼
    session.send_realtime_input(Blob)        ← google-genai SDK
                │
                ▼
       ╔═════════════════════════╗
       ║  Gemini 3.1 Flash Live  ║          ← native audio, multilingüe
       ╚═══════════╤═════════════╝
                   │  (audio out 24kHz + tool calls)
                   ▼
        async for response in session.receive():
              ├─ response.data → audio out queue → speaker
              ├─ tool_call → handle_function_call → POST a Railway
              └─ interrupted=True → drain de la queue (barge-in)
```

Tres tasks asyncio corriendo en paralelo:

1. `mic_to_session` — captura del micro y envío al WebSocket.
2. `recv_loop` — recibe del WebSocket, encola audio y resuelve tools.
3. `speaker_consumer` — drena la cola de audio out y la reproduce.

## Diferencias clave vs ElevenLabs (descubiertas en la doc)

| Tema | ElevenLabs Conv. AI | Gemini 3.1 Flash Live |
|---|---|---|
| Idioma | `language_code: es` configurable por agente | NO se fija por código en native audio. Hay que forzarlo en el system prompt (lo hace `prompt.py`). |
| Tools | Server tools síncronas vía webhook con `pre_tool_speech` (Ana dice "un segundo" mientras va) | Function calling SOLO síncrono en 3.1, sin equivalente a `pre_tool_speech`. Modelo se queda mudo bloqueado hasta tu `send_tool_response`. **Si la tool tarda >500ms, el silencio se nota.** |
| Audio in/out | µ-law 8kHz (telefonía) | PCM16 16kHz in / 24kHz out — ojo si lo enchufas a Twilio SIP. |
| Voces | Library propia (Sarah, etc.), prosodia ajustable | Voces nativas multilingües (Kore, Charon, Puck, Fenrir, Aoede…). Cambia con `GEMINI_VOICE`. |
| Interrupciones | VAD nativo + flag de interrupción | VAD nativo + `server_content.interrupted=True`. Implementado: vacía la cola del altavoz al recibirlo. |
| Duración max sesión | sin límite documentado en plan actual | 15 min audio-only. >15 min requiere `session_resumption` (no implementado en este POC). |
| Pre-fetch / personalization | webhook `/tools/eleven/personalization` que precalienta freebusy | NO lo usamos aquí — Gemini no tiene equivalente. Para POC no debería notarse. |

## Voces a probar para castellano peninsular

Las voces nativas de Gemini son **multilingües**. No hay una "para español"
específicamente, pero unas suenan mejor en peninsular que otras. Recomendado
probar en este orden cambiando `GEMINI_VOICE` en `.env`:

1. **Kore** — neutra, claridad alta. Default.
2. **Aoede** — femenina más cálida, suena bien para recepcionista.
3. **Charon** — masculina grave, por si quieres comparar con voz de hombre.
4. **Puck** — más juvenil, prueba "viva".
5. **Fenrir** — masculina alternativa.

Lista completa audible (con previews) en `https://aistudio.google.com/live`,
panel derecho "Voice".

## Métricas a comparar con ElevenLabs

Mide en cada llamada (con cronómetro o instrumentando logs):

| Métrica | Cómo medir | Objetivo |
|---|---|---|
| **TTFA** (Time To First Audio) | desde que terminas de hablar hasta que oyes la primera sílaba de Ana | <1500ms (ElevenLabs en producción ronda 800-1200ms tras hardening) |
| **Latencia tool** | desde que Ana dice "te miro un momento" hasta que vuelve con la respuesta | <2s (en el caso de `consultar_disponibilidad` típico) |
| **Naturalidad** | A/B ciego — graba 3 llamadas con cada motor y compara con un tester | subjetivo |
| **Tasa de error de tool** | `[TOOL←] ... ok=False` en los logs | <2% |
| **Coste estimado/min** | revisa pricing en `https://ai.google.dev/gemini-api/docs/pricing` para `gemini-3.1-flash-live-preview` y compáralo con el cuádruple gancho de ElevenLabs (Conv AI + LLM + TTS + STT) | objetivo: <50% del coste actual de ElevenLabs |

## Avisos importantes

1. **La GEMINI_API_KEY del .env está pegada en plano**. Marcos: revoca y regenera en
   `https://aistudio.google.com/apikey` cuando termines de probar. Pegar keys en
   chats de IA es la versión moderna de dejarte el coche en marcha en el centro
   de Madrid.
2. **El TOOL_SECRET es el de PRODUCCIÓN** (mismo que el `.env` del backend).
   Las tools van a Railway en vivo: `crear_reserva` crea un evento real en
   Google Calendar de pelu_demo. Para probar sin contaminar, usa fechas
   futuras lejanas (p.ej. 6 meses adelante) o limpia el calendario después.
3. **macOS pedirá permiso de micrófono** la primera vez. Si no aparece el
   prompt y el script falla con `PortAudioError`, mira System Settings →
   Privacy & Security → Microphone → permite Terminal/Python.
4. **PortAudio no instalado**: si `pip install sounddevice` da error, antes
   `brew install portaudio`.

## Limitaciones conocidas del POC

- **Sin `pre_tool_speech`**: cuando Ana llama a `consultar_disponibilidad`, se
  queda muda 200-800ms hasta que llega la respuesta del backend. En ElevenLabs
  hay un hueco con voz ("un momento que lo miro"). En Gemini 3.1 Flash Live
  esto NO es configurable. Workaround posible (no implementado): inyectar tú
  un audio de "un momento" antes de mandar la tool call.
- **Sin reconexión automática a 15 min**: si la conversación pasa de 15
  minutos, la sesión se cierra. El backend de producción debería implementar
  `session_resumption` con `handle` token para extender — fuera del scope del
  POC.
- **Sin SIP/telefonía**: este demo va por micro/altavoz local. Para meterlo
  por SIP (Twilio, ElevenLabs SIP, etc.) hay que añadir un audio bridge que
  resamplee 8kHz µ-law ↔ 16/24kHz PCM16. Gemini no lo trae.
- **Sin sesión persistente**: cada vez que arrancas el script, conversación
  nueva. No se guardan transcripciones (los `print` van a stdout, redirige
  con `python gemini_live_demo.py | tee llamada.log` si quieres).

## Estructura

```
demo_gemini_live/
├── README.md                # este fichero
├── requirements.txt         # google-genai, sounddevice, httpx, dotenv
├── .env.example             # plantilla
├── .env                     # secretos (gitignored)
├── .gitignore               # protección extra
├── config.py                # carga settings desde .env
├── prompt.py                # render del ana_prompt_new.txt con fechas locales
├── tools_adapter.py         # schemas Gemini + handler HTTP a Railway
└── gemini_live_demo.py      # main: sesión Live + audio I/O + tool loop
```

## Próximos pasos sugeridos (si el POC cuaja)

1. Añadir `session_resumption` para superar el límite de 15 minutos.
2. Probar `gemini-2.5-flash-native-audio-preview-12-2025` para A/B contra 3.1
   (la 2.5 sí soporta function calling asíncrono y `affective_dialog`).
3. Bridge SIP: si decidimos tirar Gemini en producción, integrar Twilio Media
   Streams (8kHz µ-law) ↔ Gemini (16kHz/24kHz PCM16).
4. Telemetría: enchufar las latencias de tool y TTFA al CMS para tener
   dashboards comparativos con ElevenLabs.
