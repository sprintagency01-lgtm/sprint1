# Changelog

Registro vivo de cambios publicados al remoto. Formato: sección por fecha, subsecciones por tipo de cambio. Ver convención completa en `CLAUDE.md`.

Entrada más reciente arriba.

---

## 2026-04-24 (latencia — ronda 7)

Ajustes finos + recorte de prompt + personalization endpoint + prefetch especulativo. Exploración exhaustiva de las palancas que quedaban tras la ronda 6.

### Añadido

- **`/tools/eleven/personalization`** (`app/eleven_tools.py`): webhook `conversation_initiation_client_data_webhook`. ElevenLabs lo llama UNA vez al inicio de cada conversación y recibe `dynamic_variables` precomputadas: `hoy_fecha_iso`, `manana_fecha_iso`, `pasado_fecha_iso`, `hoy_dia_semana`, `manana_dia_semana`, `hoy_natural` ("viernes 25 de abril"), `manana_natural`, `hora_local`, `caller_id_legible`, `tenant_id`, `tenant_name`. El prompt ahora usa `{{hoy_natural}}` / `{{manana_natural}}` en lugar de `{{system__time_utc}}` → Gemini ya no calcula weekday desde UTC, ahorra tokens de prefill y elimina el bug histórico de "mañana el jueves" / "pasado mañana el viernes".

- **Prefetch especulativo de freebusy** dentro de `personalization`: tras responder las dynamic_variables, dispara un `asyncio.create_task` que precalienta `_FREEBUSY_CACHE` para hoy+2 días con duraciones 30 y 45 min. Cuando Ana llama a `consultar_disponibilidad` 2-5s después, el cache está caliente → tool devuelve en <50ms en vez de 500-900ms. Fire-and-forget, no bloquea la respuesta del webhook. **Ganancia estimada ~400-800ms en TT_final en llamadas reales.**

- **Snapshots**: `docs/elevenlabs_agent_snapshot_post_round7_*.json` + `elevenlabs_agent_config.json` al estado final.

### Cambiado

- **Prompt recortado de ~4,6 KB a ~3,3 KB (-28%)** (`ana_prompt_new.txt`): eliminada sección "Fillers antes de tool calls" (redundante con `pre_tool_speech: force` de ronda 5), consolidadas "Reglas duras" 1-10 en una lista compacta, comprimido el paso 6 de "Flujo RESERVA", colapsado "Fechas al hablar" en un párrafo. Tool-calling sigue 4/4 correcto. TTFR medio bajó de ~1258ms a ~1208ms tras el recorte (-50ms).

- **Ajustes finos de turn-taking y backup LLM**:
  - `backup_llm_config.preference`: `default` → `disabled`. Libera el cascade de 4s y hace el camino hot más predecible.
  - `turn.spelling_patience`: `auto` → `off`. El agente no espera a que el user deletree cosas.
  - `tts.text_normalisation_type`: `system_prompt` → `elevenlabs`. Normalización server-side (más rápida que vía prompt).

- **`conversation_initiation_client_data_webhook`** registrado en `platform_settings.workspace_overrides` apuntando a Railway + `overrides.enable_conversation_initiation_client_data_from_webhook: true`.

### Evaluado y rechazado en esta ronda

- **`gemini-3-pro-preview`**: 7-10s TTFR y **cero tool calls** en los dos escenarios probados. Descartado.
- **`gemini-3.1-flash-lite-preview`**: 4/4 tools OK pero varianza 854-2170ms TTFR (empate estadístico con `gemini-3-flash-preview` pero con más ruido). Mantenemos el ganador de ronda 6.
- **Asyncificar cliente Google Calendar (HTTP/2)**: ganancia real 50-100ms por tool call pero trade-off desfavorable — el cache freebusy 8s + el prefetch ya llevan el primer call a <50ms en la llamada real. Se queda como ronda 8 estratégica si aparece un caso de cold cache frecuente.

### Notas

- Los valores aceptados por la API de ElevenLabs descubiertos en esta ronda (para futuras referencias):
  - `turn.spelling_patience`: `auto` | `off`.
  - `turn.turn_model`: `turn_v2` | `turn_v3`.
  - `turn.initial_wait_time`: `-1` (default, espera infinita) o `>=1` segundo. Valores <1 rechazados.
  - `tts.text_normalisation_type`: `system_prompt` | `elevenlabs`.
  - `agent.prompt.backup_llm_config.preference`: `default` | `disabled` | `override`.
  - `tool.pre_tool_speech`: `auto` | `force` | `off` (ya documentado en ronda 5 hotfix).

- Bench WS text-only no puede medir la ganancia del prefetch porque el bench no dispara `conversation_initiation_client_data_webhook` (eso ocurre solo en llamadas reales de voz). La ganancia se verá en la primera `consultar_disponibilidad` real tras una llamada.

- **Objetivo <400ms end-to-end sigue sin alcanzarse** con esta stack. TTFR mínimo medido: ~1035ms. Mínimo teórico con LLM-as-a-service + ElevenLabs + webhook: ~800-1100ms. Bajar de ahí requiere custom LLM endpoint (Groq/Cerebras) o eliminar el round-trip webhook.

- Suite de tests: **106/106 verdes** tras todos los cambios.

---

## 2026-04-24 (latencia — ronda 6)

Migración del LLM del agente de voz de `gemini-2.5-flash` a `gemini-3-flash-preview`, más `turn_v3` en turn-taking. La mayor ganancia de latencia medida hasta la fecha.

### Cambiado

- **`llm: gemini-2.5-flash` → `gemini-3-flash-preview`** en el agente `pelu_demo`. Bench con WebSocket text-only contra el agente real:

  | Modelo | TTFR (primer texto post user_msg) | TT_tool_response | TT_final (respuesta útil post-tool) | Tools OK |
  |--------|-----|-----|-----|-----|
  | **gemini-3-flash-preview** (nuevo) | **1062-1340ms** | 1867-2056ms | **2622-3944ms** | **4/4 ✓** |
  | gemini-2.5-flash (antes) | 1803-6940ms | 2770-7871ms | 3900-10410ms | ✓ |
  | gpt-oss-120b | 910-9134ms (alta varianza) | 1167-9567ms | 1615-10405ms | ✓ (experimental) |
  | watt-tool-70b | ~6400ms | ~7000ms | ~12400ms | ✓ (muy lento) |
  | qwen3-30b-a3b | 319ms | — | — | ✗ NO llama a tools |
  | glm-45-air-fp8 | 557ms | — | — | ✗ **alucina** reservas |
  | gemini-2.5-flash-lite | 1061ms | — | — | ✗ NO llama a tools |

  Con `pre_tool_speech: force` activo (desde ronda 5 hotfix), el TTFR es lo que oye el usuario antes del filler "vale, te miro un momento..." — bajamos de ~4500ms a ~1200ms. Para la respuesta útil post-tool (los huecos que Ana dicta), de ~7500ms medio a ~3000ms medio. **Mejora percibida: ~3x más rápido al inicio del turno, ~2.5-3x al resultado.**

- **`turn_model: turn_v2` → `turn_v3`** en `conversation_config.turn`. v3 es más rápido detectando fin de turno del usuario (propiedad interna de ElevenLabs — no hay docs públicos, pero el PATCH lo acepta y los bench posteriores siguen consistentes).

- **`scripts/setup_elevenlabs_agent.py`**: tenants NUEVOS nacen con `llm: gemini-3-flash-preview`, `temperature: 0.3`, `max_tokens: 300`, `thinking_budget: 0`, `turn_model: turn_v3`, `turn_timeout: 1.0`, `speculative_turn: true`. Antes el script no fijaba explícitamente `llm` ni `turn_*` (heredaba defaults).

### Añadido

- **`scripts/bench_llm.py`** (orquestador) y **`scripts/bench_one.py`** (runner por modelo × escenario): harness contra `/v1/convai/agents/{id}/simulate-conversation` que valida tool-calling y mide TTFB. Útil para futuras rondas cuando aparezcan modelos nuevos.
- **`scripts/bench_ws.py`**: harness WebSocket text-only para medir TTFR, TT_tool_response y TT_final con un mensaje real, captando `agent_response` + `agent_tool_response`. Mucho más rápido que `simulate-conversation` (~6-8s por test vs 35-40s). Ideal para iterar.
- **`docs/elevenlabs_agent_snapshot_pre_round6_*.json`** y **`docs/elevenlabs_agent_snapshot_post_round6_*.json`**: snapshots pre/post migración (secrets redacted, prompt externalizado).
- **`elevenlabs_agent_config.json`** actualizado al estado post-round6.
- **`ELEVENLABS.md`** con sección "Modelos descartados en el bench de ronda 6 (guía anti-regresión)" para no repetir pruebas inútiles.

### Notas de diseño

- El objetivo de `<400ms end-of-speech → primer audio` que pedía Marcos NO se alcanza con esta stack: el LLM más rápido con tools fiables da ~1100ms TTFR, y aún hay que sumar ASR (~150-300ms) y TTS primer audio (~150-200ms). Realidad: **~1400-1700ms end-to-end para el primer audio** (el filler). Y ~3000-3500ms para la información útil (huecos reales).

  Para bajar de ahí hace falta cambiar arquitectura (custom LLM endpoint en Groq/Cerebras con Llama 3.3 70B, modelos edge, o cache de respuestas frecuentes). Queda como ronda 7 estratégica si el nivel actual no basta.

- `gemini-3-flash-preview` lleva el sufijo "preview" — si Google lo deprecata o lo renombra, hay que migrar a su sucesor. La versión "GA" equivalente cuando exista será el siguiente paso.

- `turn_v3` aplicado sin tests A/B prolongados. Observación subjetiva en el bench: tiempos consistentes con `turn_v2`. Si surge regresión (cortes prematuros, agente que interrumpe), rollback con un PATCH a `turn_v2`.

### Breaking

- Ninguno visible al usuario final. Cambios son en la config remota del agente ElevenLabs; el backend sigue igual.

---

## 2026-04-24 (latencia — ronda 5 hotfix)

### Corregido

- **`force_pre_tool_speech` no se aplicaba vía el flag booleano suelto.** Al ejecutar `scripts/migrate_agent_latency.py` contra el agente remoto de `pelu_demo` se observó que el PATCH respondía 200 pero las 5 tools seguían con `force_pre_tool_speech=false`. El campo real que controla el comportamiento es el enum `pre_tool_speech: 'auto' | 'force' | 'off'`; solo con `'force'` se activa. Además, las tools NO se editan vía `PATCH /v1/convai/agents/{id}` — son entidades independientes con su propio `tool_id`, hay que patchearlas en `/v1/convai/tools/{tool_id}`.
- `app/elevenlabs_client._build_tools` emite ahora `pre_tool_speech: "force"` además del booleano, para que los tenants creados desde el CMS nazcan con el TTS del filler paralelizado.
- `scripts/migrate_agent_latency.py` refactorizado: (a) PATCH agente solo para TTS, (b) iteración por tool_id con PATCH `{tool_config: {...}}` para `pre_tool_speech='force'` + `calendar_id` en schemas. Verificado en vivo contra `pelu_demo`: las 5 tools quedan con `pre_tool_speech=force`, `force_pre_tool_speech=True`.

### Notas

- Esta entrada complementa al commit `e557dcb` que se pusheó fuera de la convención (sin tocar CHANGELOG). La convención pide tocar CHANGELOG antes de cada push; este hotfix lo arregla retroactivamente.

---

## 2026-04-24 (latencia — ronda 5)

Quinta ronda de optimización de latencia del canal voz. Las 4 anteriores recortaron lo obvio (cache del cliente Google, freebusy 8s, prompt 7KB→4,5KB, `thinking_budget:0`, `turn_timeout:1s`, `optimize_streaming_latency:4`, `ulaw_8000`, `tool_call_sound:typing`, `cascade_timeout:4s`, `max_tokens:300`, flujo RESERVA reordenado). Esta ronda ataca el siguiente escalón: caché de tenant, idempotencia, fast path de mover/cancelar, warm-up de Google y TTS flash.

### Añadido

- **Middleware de timing en `/tools/*` y `/_diag/*`** en `app/main.py`: log `timing path=... tenant=... status=... dur_ms=...` y header `X-Backend-Duration-MS` en cada respuesta. Base para medir el impacto real de las palancas que vienen detrás.
- **Caché in-memory del tenant en `app/tenants.py`** (TTL 30s, clave por `tenant_id`), con invalidación automática vía listener `before_commit`/`after_commit` de SQLAlchemy cuando se escribe un `Tenant`, `Service` o `MiembroEquipo`. Ahorra ~10-30ms por tool call en caché caliente. Incluye helper `invalidate_tenant_cache(tid|None)` para casos manuales.
- **`Tenant.to_dict(include_system_prompt: bool = True)`** en `app/db.py`: el hot path de voz NO usa `system_prompt` (usa `voice.prompt`) — `eleven_tools._resolve_tenant` ahora pide la versión ligera y se ahorra ~1-3ms de render por llamada.
- **Fast path en `/tools/mover_reserva` y `/tools/cancelar_reserva`**: aceptan `calendar_id` opcional en el body. Si el agente lo reenvía (lo devuelve `buscar_reserva_cliente`), el backend hace un único PATCH/DELETE sin iterar peluqueros. Ahorra 200-1500ms en tenants con varios calendarios. Schemas remotos actualizados en `elevenlabs_client._build_tools` y `scripts/setup_elevenlabs_agent.py`.
- **Idempotencia en `/tools/crear_reserva`**: antes de insertar, busca un evento del mismo teléfono en ±5min; si existe, devuelve `ok:true, duplicate:true, event_id=<existente>`. Evita cita duplicada cuando ElevenLabs reintenta tras timeout de red (observado en el audit como H-3).
- **Warm-up de Google Calendar en startup** (`@app.on_event("startup")` en `app/main.py`): precalienta `_service(tid)` para tenants `contracted+active`, de modo que la primera tool call tras un redeploy no paga el coste (~200-400ms) de construir el cliente googleapiclient.
- **`force_pre_tool_speech: true`** por defecto en las 5 tools generadas por `elevenlabs_client._build_tools`. Arranca el TTS del filler en paralelo a la HTTP call. Palanca 4 documentada en memoria, ahora aplicada por defecto en agentes nuevos.
- **`scripts/migrate_agent_latency.py`**: script one-shot que patchea un agente existente en ElevenLabs a: (1) `tts.model_id = eleven_flash_v2_5`, (2) `force_pre_tool_speech: true` en las 5 tools, (3) `calendar_id` opcional en los schemas de mover/cancelar. Soporta `--dry-run`.
- **Tests nuevos**: `tests/test_eleven_tools_latency.py` con 8 tests (fast path con/sin `calendar_id`, idempotencia con/sin duplicado previo, caché de tenant sirve sin re-query, invalidación borra entrada, retry con backoff reintenta transitorios y aborta permanentes). `tests/conftest.py` aísla DB/tokens/env de los tests para que no toquen `data.db` real. Suite completa: **106 tests, 0 fallos**.
- **`AUDITORIA_2026-04-24.md`**: auditoría profunda previa (arquitectura, seguridad, fiabilidad, testing, observabilidad, latencia con presupuesto por tramo y plan priorizado). Documento de referencia en el workspace; no va al repo.

### Cambiado

- **`_retry_google` con backoff exponencial + jitter y cap** (`app/eleven_tools.py`): sustituye `time.sleep(0.8 * (i + 1))` lineal por `random.uniform(0, min(max, base * 2^i))` con `base=0.4s` y `max=1.5s`. Reduce la mediana del backoff y limita el peor caso. Sin cambio funcional en el caso nominal (0 reintentos).
- **`elevenlabs_client.sync_agent(...)`** acepta `model_id: str | None`: ahora se puede propagar el TTS model desde código (además de `prompt` y `voice`). Usado por `scripts/migrate_agent_latency.py` para migrar agentes ya creados.
- **`DEFAULT_TTS_MODEL_ID = "eleven_flash_v2_5"`** en `app/elevenlabs_client.py`: constante explícita para el TTS de baja latencia. `create_agent_for_tenant` ya la usaba; ahora queda centralizada.

### Corregido

- **TTS drift `eleven_v3_conversational` → `eleven_flash_v2_5`** en el agente remoto (pelu_demo). `ELEVENLABS.md` ya documentaba flash pero el agente vivo quedaba en v3 — más expresivo pero con 150-400ms extra al primer audio. El script `scripts/migrate_agent_latency.py` lo revierte. Requiere ejecución manual tras este deploy (ver abajo).

### Env / despliegue

- Sin variables de entorno nuevas. `TOOL_SECRET`, `ELEVENLABS_API_KEY`, `ELEVENLABS_AGENT_ID`, `DATABASE_URL`, `TOKENS_DIR` siguen igual.
- **Post-deploy manual (una vez)**: ejecutar `python scripts/migrate_agent_latency.py` contra Railway local o con `ELEVENLABS_API_KEY` en el entorno. Aplica: TTS flash + `force_pre_tool_speech` + `calendar_id` en schemas de mover/cancelar del agente remoto. Usar `--dry-run` antes de aplicar.
- El middleware de timing emite logs nuevos con prefijo `timing path=...`. Si se centralizan logs en Railway, crear una query/filtro por ese prefijo para ver p50/p95.

### Notas de diseño

- El caché de tenant tiene TTL intencionalmente corto (30s) + invalidación automática por listener. Si alguien escribe a la BD por fuera del CMS (p.ej. ejecutando SQL directo en el volumen de Railway), el caché tarda como mucho 30s en refrescarse. Aceptable.
- `force_pre_tool_speech: true` hace que ElevenLabs empiece a hablar el filler ("vale, te miro un momento...") ANTES de recibir el resultado de la tool. Si el backend responde súper rápido (caso cache hit freebusy), el filler suena un poco "de más"; pero si tarda 500ms+, enmascara la latencia auditivamente. Trade-off aceptado.
- Palancas no aplicadas en esta ronda: región EU en Railway (L5), HTTP/2 + async Google client (L8), LLM custom en Groq/Cerebras (L10), prefetch especulativo (L9). Ver `AUDITORIA_2026-04-24.md` § 4.3 para el roadmap.

---

## 2026-04-24 (parche pm 5)

### Corregido

- **Resumen de confirmación decía "con sin preferencia".** Caso real: *"Corte de hombre, sábado 25 a las 16:30 con sin preferencia, a nombre de Anabel Prueba. ¿Lo confirmo?"* — la composición automática `con {peluquero}` se rompía cuando `{peluquero}` era literalmente `"sin preferencia"`. Endurecida la description de `pedir_confirmacion`: estructura explícita, prohibición literal de `"con sin preferencia"` / `"con cualquiera"`, y ejemplo correcto sin peluquero (*"Corte mujer, sábado 25 a las 16:30. ¿Te lo confirmo?"*). Verificado en producción: Ana dice ahora *"Corte hombre, sábado 25 a las 19:00. ¿Te lo confirmo?"* cuando no hay preferencia.

### Tests

- Test de regresión en `test_prompt_confirmation_and_title.py` sobre la description del tool.
- 2 tests nuevos en `test_telegram.py` sobre el envío del .ics tras `handle_update`: verifica que cuando `AgentReply.calendar_event` está poblado, `handle_update` llama a `send_document` con el contenido iCal correcto y filename `cita-YYYYMMDD-HHMM.ics`; y que si no hay `calendar_event`, no se envía documento. Suite pasa a **97/97**.

---

## 2026-04-24 (parche pm 4)

### Corregido

- **Ana ofrecía huecos que ya habían pasado.** Caso real: Anabel preguntó a las 12:00 y el bot le propuso hueco a las 9:00 del mismo día. Añadido filtro `_descartar_huecos_pasados` / `_descartar_slots_pasados` en `app/eleven_tools.py` y filtro equivalente en `app/agent.py::_execute_tool(consultar_disponibilidad)`. Se descartan huecos cuyo `inicio < now + 10 min` (margen para no ofrecer algo inminente al que el cliente no llega físicamente). Se usa `_tz_now()` (timezone-aware en la TZ del tenant, por defecto Europe/Madrid) para evitar desfases con Railway corriendo en UTC.
- **Como consecuencia, Ana a veces no ponía botones de horas cuando el cliente pedía cita "hoy"**: recibía una lista contaminada con slots pasados que la confundía. Al filtrar, si quedan ≥1 huecos válidos llama a `ofrecer_huecos` (botones); si no queda ninguno, dice en texto que no hay disponibilidad y ofrece otra fecha.

### Añadido

- **Archivo `.ics` adjunto tras crear una reserva.** Petición del cliente: el enlace "Añadir a Google Calendar" lleva a Google Workspace (web) en vez de abrir la app nativa del teléfono. Solución: nueva función `_build_ics_content` (RFC 5545 válido, con `TZID`, escape de `, ; \ \n`, folding a 75 cols). El canal Telegram envía el .ics vía `sendDocument` con MIME `text/calendar` justo después del mensaje de texto de confirmación. Al pulsarlo en móvil:
  - iOS → pregunta si añadirlo a Apple Calendar (o Google Calendar si está instalada).
  - Android → abre Google Calendar app (o Samsung Calendar, o cualquier app de calendario instalada que acepte .ics).
  - Desktop → abre el cliente de correo / calendario configurado.
  Sin depender de Google Workspace ni de login.
- Nuevo campo en `AgentReply`: `calendar_event: dict | None`. `agent_anthropic.reply` (y equivalente OpenAI) lo rellena cuando `crear_reserva` devuelve `ok:true`, con los datos necesarios para generar el .ics (titulo, inicio_iso, fin_iso, descripcion, ubicacion, tz, event_id).
- Método `TelegramClient.send_document` con multipart/form-data, tolerando errores de red con mensaje legible.
- 12 tests nuevos en `tests/test_past_slots_and_ics.py`: filtros de pasado con objetos dict/namedtuple y buffer de 10 min, integración con `_execute_tool(consultar_disponibilidad)`, generación RFC 5545 (escapes, TZ aware, TZ inválida, omisión de campos vacíos), propiedad `AgentReply.has_calendar_attachment`. Suite **94/94**.

### Notas

- El enlace "Añadir a Google Calendar" en texto plano se mantiene como fallback (sirve a usuarios desktop que prefieran Google). La adjunto .ics es la vía principal para móvil.

---

## 2026-04-24 (parche pm 3)

### Añadido

- **Preferencia de peluquero y huecos de hora ahora son botones clicables** en Telegram. Ana ya ofrecía los servicios como `inline_keyboard` (gustó a Mario al probarlo), ahora extiende el patrón a los dos pasos que listaba en texto: preferencia inicial de equipo y propuesta de horas. Cambios técnicos:
  - `ofrecer_equipo` acepta `modo_preferencia: bool` nuevo. Si `true`, el botón extra es **"Me da igual"** (id `team:none`) para la pregunta inicial. Si `false`/omitido, mantiene el comportamiento original "Otro miembro" (id `other:team`) para uso tras `equipo_disponible_en`.
  - El FLUJO del prompt obliga ahora: paso peluquero → `ofrecer_equipo` con `modo_preferencia=true`; paso hora → `consultar_disponibilidad` seguido SIEMPRE de `ofrecer_huecos`. Prohibido listar en texto.
- **Enlace "Añadir a mi Google Calendar"** en el mensaje de confirmación. Nueva función `_build_google_add_to_calendar_url` que construye la URL de Google Calendar TEMPLATE (patrón público oficial `calendar.google.com/calendar/render?action=TEMPLATE&...`) con título, fechas, timezone del tenant, descripción y ubicación. `crear_reserva` devuelve ahora `add_to_calendar_url` además del `event_id` y `link`. El prompt le dice a Ana que incluya ese enlace en el mensaje de confirmación para que el cliente lo añada a su propia agenda.

### Tests

- `tests/test_interactive_and_calendar_link.py` con 10 tests: modo_preferencia con "Me da igual", modo normal con "Otro miembro", instrucciones del flujo para ofrecer_equipo/ofrecer_huecos/add_to_calendar_url, construcción correcta de la URL (básico, con TZ aware, con details, con TZ inválida). Suite **82/82**.

---

## 2026-04-24 (parche pm 2)

### Corregido

- **Agente no llamaba a `crear_reserva` cuando el cliente confirmaba en texto libre.** Flujo observado en producción: Ana pedía "¿lo confirmo?", cliente respondía "Sí, confirma", y el modelo volvía a ofrecer huecos en lugar de ejecutar la reserva. Arreglado con una **REGLA DE CIERRE** añadida al final de `_build_flujo_reserva` en `app/db.py`: ante variantes afirmativas ("sí", "confirma", "ok", "dale", "perfecto", "adelante", "venga") tras un "¿lo confirmo?", el agente llama a `crear_reserva` inmediatamente sin reconsultar disponibilidad. La `description` de la tool `crear_reserva` en `app/agent.py::TOOLS` también se ha reforzado en esa línea.
- **Título del evento guardaba el servicio antes del nombre**. Ejemplo real: `"Corte hombre — Javier Test (sin preferencia)"` cuando la convención (y el canal voz) era `"Javier Test — Corte hombre (sin preferencia)"`. Se endurece la `description` de `titulo` en la tool `crear_reserva` con formato exacto "Nombre — Servicio (con Peluquero)", un ejemplo correcto y un ejemplo INCORRECTO explícito para que el LLM no caiga en la inversa.
- **Alucinación: decir "reservado" sin haber ejecutado la tool.** Tras los dos fixes anteriores, en 1 de 4 tests end-to-end el modelo decía *"¡listo, reservado!"* sin llamar realmente a `crear_reserva`. La cita no se creaba en calendario pero el cliente creía que sí. Se añade **REGLA ANTI-ALUCINACIÓN** al prompt: *"NUNCA digas 'reservado/confirmado/hecho/listo' si en ESE turno no ejecutaste crear_reserva. Si `retryable:true`, reinténtalo; si sigue fallando, avisa de problema técnico"*. Verificado: tras el parche, 6/6 tests posteriores crean la reserva en el calendario real y los bloqueos cuando falta info siguen comportándose bien (Ana pide hora válida en vez de alucinar).

### Añadido

- `tests/test_prompt_confirmation_and_title.py` con 8 tests de regresión: regla de cierre presente con variantes afirmativas, prohibición de reconsultar disponibilidad, anti-alucinación con palabras concretas ("reservado", "confirmado", "hecho", "listo"), manejo de `retryable`, título con Nombre primero, ejemplo incorrecto explícito, description reforzada. Suite **72/72**.

---

## 2026-04-24 (parche pm)

### Cambiado

- **`/_diag/telegram/status` ahora devuelve un campo `status` categórico** para diagnóstico rápido: `healthy` | `not_configured` | `token_invalid` | `webhook_missing` | `webhook_mismatched` | `webhook_errors`. Cada estado no-healthy incluye `hint` accionable. Escenario disparador: hoy el bot heredado `@dmarco2_bot` tenía otro servicio (OpenClaw) haciendo `getUpdates` contra él, lo que sobreescribía nuestro webhook y dejaba la columna `url` vacía sin explicación. El endpoint ahora lo detecta y lo explica.
- `webhook_errors` solo se activa si `last_error_date` es de los últimos 10 minutos; errores antiguos ya resueltos no alarman.

### Añadido

- `tests/test_diag_telegram_status.py` con 8 tests que cubren los 5 estados + caso sin auth. Suite pasa a **64/64**.

### Notas operativas

- Bot de producción: `@sprintagency_reservas_bot` (id `8759954298`). Creado fresco para evitar conflicto con `@dmarco2_bot`, que pertenecía a OpenClaw.
- `TELEGRAM_BOT_TOKEN` en Railway actualizado al token del bot nuevo. Webhook registrado apuntando a Railway, verificado con `getWebhookInfo` y con smoke test sintético contra `/telegram/webhook` (ejecuta el pipeline entero: auth → load_history → agent.reply → save_message → sendMessage).

---

## 2026-04-24

### Añadido

- **Canal Telegram como entorno de staging del agente.** `app/telegram.py` (350 líneas) con cliente mínimo de Bot API, handler de updates defensivo, y traducción de `AgentReply.interactive` a `inline_keyboard` (listas 1-por-fila, botones horizontales hasta 3). Endpoint nuevo `POST /telegram/webhook` autenticado por header `X-Telegram-Bot-Api-Secret-Token`. El agente canal-agnóstico (`app.agent.reply`) se reutiliza sin tocar una línea. Persiste histórico en `messages.customer_phone` con el convenio `tg:<chat_id>`.
- **Script `scripts/setup_telegram_bot.py`** para registrar el webhook en Telegram con una orden (llama a `getMe` + `setWebhook` + `getWebhookInfo`).
- **Diagnóstico `/_diag/telegram/status`**: valida token, obtiene info del bot y estado del webhook. Protegido con `X-Tool-Secret`.
- **Diagnóstico `/_diag/elevenlabs/healthcheck`**: valida API key, TOOL_SECRET, existencia del agente remoto del tenant y que las 5 tools esperadas (`consultar_disponibilidad`, `crear_reserva`, `buscar_reserva_cliente`, `mover_reserva`, `cancelar_reserva`) están registradas. Protegido con `X-Tool-Secret`. No gasta dinero — solo GET.
- **Tests nuevos (`tests/test_telegram.py`, 20 tests)**: payload builder con/sin interactivos, truncado UTF-8 de `callback_data` a 64 bytes, `handle_update` feliz con mocks de agente/db/tenants/client, callback_query acknowledged, fallback sin tenants, resolución de tenant preferido vs primer `contracted+active`, y 3 tests de integración contra el endpoint FastAPI. Suite completa: **56 tests, 0 fallos**.
- Convención de actualización de `CHANGELOG.md` antes de cada push, documentada en el nuevo `CLAUDE.md`.
- Hook git opcional `.githooks/pre-push` que bloquea el push si los commits nuevos no tocan `CHANGELOG.md`.
- Script auxiliar `scripts/update_changelog.sh` para generar un borrador de entrada a partir de los commits no pusheados.

### Env / despliegue

- Nuevas env vars opcionales (el backend arranca sin ellas y el endpoint Telegram responde 501 hasta que se configuren):
  - `TELEGRAM_BOT_TOKEN` — token del bot dado por @BotFather (gratuito).
  - `TELEGRAM_WEBHOOK_SECRET` — secreto compartido con Telegram para autenticar los webhook entrantes. Generable con `python -c "import secrets; print(secrets.token_urlsafe(32))"`.
  - `TELEGRAM_DEFAULT_TENANT_ID` — tenant al que dirigir los mensajes entrantes. Si vacío se usa el primer `contracted+active` como fallback.
- Después de desplegar: ejecutar `python scripts/setup_telegram_bot.py https://web-production-98b02b.up.railway.app` (o el dominio que toque) **una sola vez** para registrar el webhook.

### Notas de diseño

- Telegram es canal secundario / de staging — no sustituye a voz. Mismo agente, mismas tools, mismo histórico en BD (con prefijo `tg:` para no mezclarse con teléfonos).
- `handle_update` nunca lanza: cualquier error se captura y se devuelve 200 OK a Telegram para evitar reintentos infinitos, mientras logeamos el fallo.
- `callback_data` se trunca a 64 bytes respetando UTF-8 (Telegram lo exige). Con el formato actual de ids (`slot:YYYY-MM-DDTHH:MM:...`) no se alcanza el límite, pero la salvaguarda queda por si crecemos.

### Breaking

- **Retirado el canal WhatsApp.** El producto pasa a voz-only vía ElevenLabs. (commit `d9e1435`)
  - Borrados: `app/whatsapp.py`, `app/twilio_wa.py`, `app/voice.py`.
  - Eliminado el webhook `/whatsapp` de `app/main.py`.
  - CMS: fuera pestaña "Conversaciones" y campo "WhatsApp Phone Number ID".
  - Portal cliente: pantalla "Conversaciones" → "Llamadas" (`screen_llamadas.jsx`); fuera toggle bot WA; filtro WA en Reservas; gráfico y leyenda de Ingresos simplificados a voz/manual.
  - `WHATSAPP_APP_SECRET` sustituido por `TOOL_SECRET` en el monitor de ajustes del CMS.
  - Helpers de tenants por número WA (`find_tenant_by_phone_number_id`, `find_tenant_for_twilio`) retirados.
  - `.env.example` y `tenants.yaml.example` sin bloque WhatsApp ni `phone_number_id`.
  - Docs: `README.md` reescrito como "bot de reservas por voz"; resto con banner "pivot abril 2026".

### Env / despliegue

- `WHATSAPP_*` y `TWILIO_*` dejan de ser necesarias. Si están en Railway, se pueden quitar sin impacto en producción.
- `TOOL_SECRET` sigue siendo la credencial usada para autenticar llamadas de ElevenLabs a `/tools/*`.

### Notas operativas (contexto externo al repo)

- Cuenta Twilio suspendida (fraud review) y WABA de Meta en BM Sprint Agency `1465050358445201` restringida permanentemente. El pivote a voz-only hace esto irrelevante para el producto — se deja anotado por si se retoma WhatsApp en v2 bajo BM de cliente.
- Voice stack verificado agnóstico de carrier: ElevenLabs recibe SIP directo y llama a `/tools/*`. Migrar de carrier (Telnyx u otro) es configuración de trunk, no código.
- Telegram evaluado como entorno de staging: `app/agent.py` es canal-agnóstico, ~4-6h de trabajo para bot operativo si se quiere añadir en el futuro.

---

## Entradas anteriores (reconstruidas desde git log)

Esta sección es aproximada — los commits previos a la adopción del changelog no tienen entradas detalladas. Para el detalle técnico ver `HANDOFF_2026-04-21.md` y `git log`.

Muchos de los commits listados abajo tocaban el canal WhatsApp retirado hoy (`d9e1435`); quedan aquí como histórico, no como estado actual del producto.

### Hasta `e568832` (pre-pivote abril 2026)

- `e568832` feat(cms): gestión de accesos al portal en la pestaña General de cada cliente.
- `0fcfd9e` feat(cms): el alta de cliente crea también su owner del portal.
- `cf38b7c` feat(portal): SPA del cliente — auth, reservas, servicios, equipo, ajustes.
- `1bed628` feat(wa): tool `ofrecer_servicio` lista servicios clicables en PASO 1. *(retirado en `d9e1435`)*
- `6d7ff45` feat(wa): mensajes interactivos clicables con flujo secuencial hora → equipo. *(retirado en `d9e1435`)*
- `34e1721` diag: `/_diag/tenant/voice/update` — escribir prompt + sync ElevenLabs.
- `4a48eeb` diag: `/_diag/tenant/voice` — ver config ElevenLabs del tenant.
- `fe00c53` fix(prompt): FLUJO condicional al equipo + wording por sector.
- `75882f2` diag: `/_diag/tenants/list` para enumerar tenants de la BD.
- `f8195ec` diag: `/_diag/services/sync_from_yaml` — copia servicios del YAML a la BD.
- `b06acbd` diag: devolver `system_prompt` completo para facilitar debug.
- `a4ecb96` fix(prompt): inyectar FORMATO y FLUJO en system_prompt generado + cap emojis.
- `70823aa` diag: endpoint `/_diag/tenant` para inspeccionar el tenant que ve el agente.
- `9c9fb83` fix(agent): footer unificado — negocio + fecha + teléfono (no preguntar).
- `91d8074` fix(agent): tabla de fechas en el prompt + aplanar fichas con emojis.
