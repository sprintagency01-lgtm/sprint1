# Configuración canónica para bots de voz nuevos (post-ronda 7)

Este documento fija la config ganadora tras 7 rondas de optimización de latencia sobre el agente Ana (pelu_demo). **Cualquier bot nuevo que demos de alta debe nacer con esta config exacta.** Desviaciones deben documentarse y justificarse.

La latencia medida con esta config (bench WS text-only, 4 escenarios):

- TTFR (primer texto del agente tras el mensaje del usuario): **~1200 ms**
- TT_tool_response (fin del webhook contra el backend): **~1950 ms**
- TT_final (respuesta útil después de la tool): **~2900 ms**
- Tool-calling fiable: **4/4 escenarios** (reserva simple, con peluquero, mover, cancelar)

En llamada real de voz con el prefetch activo, el primer `consultar_disponibilidad` devuelve en **<50 ms** en lugar de 500-900 ms → TT_final real esperado: **~1500-1800 ms**.

> Antes de ronda 6: TTFR ~4500 ms, TT_final ~7500-10400 ms. La ronda 7 es **~3× más rápida en TTFR y ~3× en TT_final**.

## Checklist para dar de alta un bot nuevo

1. **Crear el tenant** en el CMS (`/admin/clientes/nuevo`) con servicios, equipo, business_hours.
2. **Autorizar Google Calendar** desde `/oauth/start?tenant=<id>`.
3. **Crear el agente ElevenLabs** usando uno de estos dos caminos, los dos equivalentes:
   - Desde el CMS: botón "Crear agente de voz" (ejecuta `app.elevenlabs_client.create_agent_for_tenant`).
   - A mano: `python scripts/setup_elevenlabs_agent.py https://web-production-98b02b.up.railway.app` (necesita `ELEVENLABS_API_KEY`, `ELEVENLABS_VOICE_ID`, `TOOL_SECRET` en `.env`).
4. **Copiar el `agent_id`** devuelto al campo `voice_agent_id` del tenant en el CMS.
5. **Verificar** con `python scripts/migrate_agent_latency.py` (en modo `--dry-run`). Si detecta drift, aplicar sin dry-run.
6. **Smoke test**: llamada real de 30 s. Ana debe arrancar a hablar <1,5 s tras tu última palabra. Los huecos deben llegar en <3 s.

Los pasos 3-5 aplican la config que viene abajo automáticamente. Mantén esas rutas actualizadas y no toques el agente remoto a mano desde la UI de ElevenLabs — se descuadra.

## Config completa (lo que aplican `setup_elevenlabs_agent.py` y `elevenlabs_client.create_agent_for_tenant`)

### LLM (`conversation_config.agent.prompt`)

| Campo | Valor | Por qué |
|-------|-------|---------|
| `llm` | `gemini-3-flash-preview` | Ronda 6. TTFR ~1200 ms, 4/4 tools OK. Sustituye a `gemini-2.5-flash` que daba ~4500 ms. |
| `temperature` | `0.3` | Determinismo suficiente para tool-calling fiable sin suenarle robot. |
| `max_tokens` | `300` | Evita respuestas largas accidentales. El prompt ya pide brevedad. |
| `thinking_budget` | `0` | Ronda 4. Ahorra 500-2000 ms por turno. Gemini no necesita pensar extra para reservas. |
| `backup_llm_config.preference` | `disabled` | Ronda 7. Libera el cascade de 4 s. El camino hot es más predecible. |

### TTS (`conversation_config.tts`)

| Campo | Valor | Por qué |
|-------|-------|---------|
| `model_id` | `eleven_flash_v2_5` | Ronda 5. 150-400 ms más rápido al primer audio que `eleven_v3_conversational`. |
| `text_normalisation_type` | `elevenlabs` | Ronda 7. Normalización server-side (no vía prompt). |
| `agent_output_audio_format` | `ulaw_8000` | Ronda 4. Match nativo de Twilio → sin transcode. |
| `optimize_streaming_latency` | `4` | Ronda 4. Máximo de la API. |
| `voice_id`, `stability`, `similarity_boost`, `speed` | Se toman del tenant | Editable desde el CMS. |

### Turn-taking (`conversation_config.turn`)

| Campo | Valor | Por qué |
|-------|-------|---------|
| `turn_timeout` | `1.0` | Ronda 4. Mínimo que acepta la API (1 s). |
| `turn_eagerness` | `eager` | Ronda 4. Responde antes en cuanto detecta fin. |
| `speculative_turn` | `true` | Ronda 4. Empieza a procesar antes de confirmar fin de turno. |
| `turn_model` | `turn_v3` | Ronda 6. Nuevo detector de fin de turno, más rápido que v2. |
| `spelling_patience` | `off` | Ronda 7. No espera a que el user deletree. |

### ASR (`conversation_config.asr`)

| Campo | Valor | Por qué |
|-------|-------|---------|
| `quality` | `high` | Obligatorio según la API. `low`/`medium` están bloqueados. |
| `user_input_audio_format` | `pcm_16000` | Default. Más calidad que `pcm_8000`; el cuello está en el LLM, no en el ASR. |
| `provider` | `scribe_realtime` | Default. No he encontrado alternativa medible. |

### Tools (`conversation_config.agent.prompt.tools`)

5 webhooks contra el backend Railway, todos con:

- `pre_tool_speech: "force"` y `force_pre_tool_speech: true` — ronda 5 hotfix. El enum es lo que manda, el booleano solo no basta. Arranca el TTS del filler en paralelo a la HTTP call.
- `tool_call_sound: "typing"` — ronda 4. Suena tecleo mientras la tool ejecuta.
- `response_timeout_secs: 20` — 20 s es holgado; el backend suele responder <1 s.
- `disable_interruptions: false` — el cliente puede interrumpir mientras Ana habla.

Contrato de los schemas (definidos en `app/elevenlabs_client._build_tools`):

- `consultar_disponibilidad`: fecha_desde_iso, fecha_hasta_iso, duracion_minutos, peluquero_preferido?, max_resultados?
- `crear_reserva`: titulo, inicio_iso, fin_iso, telefono_cliente?, peluquero, notas?
- `buscar_reserva_cliente`: telefono_cliente?, dias_adelante?
- `mover_reserva`: event_id, nuevo_inicio_iso, nuevo_fin_iso, peluquero?, **calendar_id? ← ronda 5 (fast path)**
- `cancelar_reserva`: event_id, **calendar_id? ← ronda 5 (fast path)**

### Personalization webhook (`platform_settings.workspace_overrides`)

| Campo | Valor | Por qué |
|-------|-------|---------|
| `conversation_initiation_client_data_webhook.url` | `https://<backend>/tools/eleven/personalization?tenant_id=<tid>` | Ronda 7. |
| `conversation_initiation_client_data_webhook.request_headers.X-Tool-Secret` | El `TOOL_SECRET` del `.env` | Autenticación. |
| `overrides.enable_conversation_initiation_client_data_from_webhook` | `true` | Activa la interpolación de dynamic_variables en el prompt. |

### Placeholders de dynamic_variables (OBLIGATORIO)

**Gotcha encontrado en producción el 2026-04-24**: ElevenLabs **ignora** lo que devuelve el personalization webhook si las keys NO están pre-declaradas como `dynamic_variable_placeholders` en el agente. Sin esto, el prompt ve literalmente `{{manana_fecha_iso}}` como texto y el LLM alucina fechas (observado: cita creada en mayo 2025 cuando hoy era abril 2026).

Declaración obligatoria en `conversation_config.agent.dynamic_variables.dynamic_variable_placeholders`:

```json
{
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
  "tenant_name": ""
}
```

`setup_elevenlabs_agent.py` y `elevenlabs_client.create_agent_for_tenant` ya incluyen estos placeholders por defecto. Si algún día añades una variable nueva en `app/eleven_tools.py::eleven_personalization`, **añádela también a esta lista en los dos sitios**.

**Failsafe**: el prompt actual (`ana_prompt_new.txt`) usa `{{system__time}}` como fuente primaria de fecha (variable de sistema, siempre inyectada por ElevenLabs sin depender del webhook). Las variables custom son *mejora* (prefetch + tokens ahorrados), no requisito para que el bot funcione. Si el webhook falla o los placeholders se borran, el bot sigue sabiendo la fecha.

El endpoint devuelve `hoy_fecha_iso`, `manana_fecha_iso`, `pasado_fecha_iso`, `hoy_dia_semana`, `manana_dia_semana`, `hoy_natural`, `manana_natural`, `hora_local`, `caller_id_legible`, `tenant_id`, `tenant_name`. El prompt los usa como `{{manana_natural}}`, `{{manana_fecha_iso}}`, etc.

**Efecto colateral crítico**: tras responder, el handler dispara un `asyncio.create_task` que precalienta el cache freebusy para hoy + 2 días con duraciones 30 y 45 min. Cuando Ana llama a `consultar_disponibilidad` 2-5 s después, el cache está caliente → tool devuelve en <50 ms.

### Prompt (`ana_prompt_new.txt`)

- Tamaño objetivo: **≤3,3 KB**. El prompt actual es el que hay en ese fichero. **No añadir secciones sin recortar otras.**
- Usa `{{hoy_natural}}`, `{{manana_natural}}`, `{{manana_fecha_iso}}`, `{{hora_local}}`, `{{system__caller_id}}`.
- No incluye sección "Fillers antes de tool calls" — `pre_tool_speech: force` ya lo inyecta.
- No pide al LLM que calcule weekday desde UTC — el personalization endpoint se lo da precomputado.

Cuando adaptes el prompt a un tenant distinto (peluquería → clínica dental, abogado, etc.), conserva: `## Contexto`, `## Reglas duras`, `## Flujo RESERVA/MOVER/CANCELAR` y `## Cierre`. Cambia solo `## Negocio`, los servicios y el nombre del asistente.

## Verificaciones post-alta

```bash
# 1) Personalization endpoint responde en el tenant nuevo
curl -s -H "X-Tool-Secret: $TOOL_SECRET" -H "Content-Type: application/json" \
  -d '{"caller_id":"+34600000001","tenant_id":"<tu_tid>"}' \
  https://web-production-98b02b.up.railway.app/tools/eleven/personalization | jq

# 2) Consultar disponibilidad real (sin pasar por LLM)
curl -s -H "X-Tool-Secret: $TOOL_SECRET" -H "Content-Type: application/json" \
  -d '{"fecha_desde_iso":"2026-04-25T15:00:00","fecha_hasta_iso":"2026-04-25T20:30:00","duracion_minutos":30,"peluquero_preferido":"","max_resultados":5}' \
  "https://web-production-98b02b.up.railway.app/tools/consultar_disponibilidad?tenant_id=<tu_tid>" | jq

# 3) Healthcheck del agente
curl -s -H "X-Tool-Secret: $TOOL_SECRET" \
  "https://web-production-98b02b.up.railway.app/_diag/elevenlabs/healthcheck?tenant_id=<tu_tid>" | jq

# 4) Bench WS contra el agente remoto (requiere ELEVENLABS_AGENT_ID apuntando al agente nuevo)
python scripts/bench_ws.py gemini-3-flash-preview "Hola soy Juan, quiero cita mañana por la tarde para corte de hombre"
```

Objetivo en el bench: `TTFR ~1200 ms`, `TT_final ~3000 ms`, `tools=['consultar_disponibilidad']`.

## Qué NO hacer (anti-regresión)

Palancas ya probadas y descartadas en el bench de ronda 6:

- **`qwen3-30b-a3b`**: 319 ms TTFR pero 0 tool calls en 8 turnos. Inútil.
- **`glm-45-air-fp8`**: 557 ms TTFR pero alucina reservas ("te apunto un corte" sin consultar disponibilidad).
- **`gpt-oss-120b`**: llama tools pero varianza 910-9134 ms en TTFR. No producción.
- **`watt-tool-70b`**: 6400 ms TTFR, 12 s TT_final. Muy lento.
- **`gemini-2.5-flash-lite`**: 1061 ms pero no llama tools.
- **`gemini-3-pro-preview`**: 7-10 s TTFR y cero tool calls.
- **`gemini-3.1-flash-lite-preview`**: funciona pero con más varianza que `gemini-3-flash-preview`, sin ganancia clara.

Y descartados de rondas anteriores (no re-probar sin razón nueva):

- `gemini-2.0-flash`, `gemini-2.0-flash-lite`, `claude-haiku-4-5`, `gpt-4.1-nano`, `gpt-4o-mini` — todos alucinaban o se saltaban tools.

## Palancas que quedan por explorar (ronda 8+)

Si la latencia actual no basta:

- **Custom LLM endpoint en Groq/Cerebras con Llama 3.3 70B fine-tuned para tool-calling**: potencialmente TTFR <500 ms. Refactor grande, riesgo de romper tools.
- **Cache pre-renderizado de respuestas triviales** ("hola", "gracias", "a ti"): evita LLM + TTS en turnos simples. Hay que medir si aporta — la ganancia principal ya viene de turnos con tool-call.
- **Asyncificar cliente Google Calendar a httpx HTTP/2**: 50-100 ms por tool call. Evaluado y diferido porque el cache freebusy + prefetch ya cubren el flujo típico.

## Mantenimiento

- Cuando ElevenLabs saque la versión GA de `gemini-3-flash-preview`, migrar con un PATCH simple.
- Si surge un modelo nuevo interesante, correr `scripts/bench_ws.py` con los 4 escenarios estándar. Criterio de sustitución: **tool-calling 4/4 Y TTFR mejor en ≥3 de los 4 escenarios respecto al actual**.
- Rehacer el snapshot del agente con `scripts/migrate_agent_latency.py --dry-run` tras cualquier cambio manual en la UI de ElevenLabs para detectar drift.
