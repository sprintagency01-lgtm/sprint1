# Changelog

Registro vivo de cambios publicados al remoto. Formato: sección por fecha, subsecciones por tipo de cambio. Ver convención completa en `CLAUDE.md`.

Entrada más reciente arriba.

---

## 2026-04-28 (PWA — portal y CMS instalables como app móvil)

Se convirtieron en PWAs instalables las dos apps internas: el **portal de cliente** (scope `/app/`) y el **CMS admin** (scope `/admin/`). Cada una se puede añadir a la pantalla de inicio en iOS y Android desde su `/login`, y arranca en modo standalone con su propio icono y color de tema. Sin push notifications en este pase (lo dejamos para otro).

### Añadido

- **Iconos PWA** generados desde una marca común (círculo ink + dot accent + 'S'): mint para portal, terracota para CMS, así son distinguibles en la pantalla de inicio. Tamaños `192`, `512`, `512 maskable`, `apple-touch-icon (180)` y `favicon-32` en `app/portal/static/icons/` y `app/cms/static/icons/`. Script reproducible en `outputs/gen_icons.py`.
- **Manifests** `app/portal/static/manifest.json` (`Sprint`, scope `/app/`, `start_url /app`, theme `#059669`) y `app/cms/static/manifest.json` (`Sprint Admin`, scope `/admin/`, `start_url /admin/dashboard`, theme `#1A100C`). Cada uno con `shortcuts` (Hoy / Nueva reserva en portal; Dashboard / Clientes / Reservas en CMS).
- **Service workers** dedicados (`app/portal/static/sw.js`, `app/cms/static/sw.js`) con estrategia: precache del shell mínimo (login + iconos + offline), `network-first` con fallback al cache para HTML, `stale-while-revalidate` para `/static/*`, y `network-only` para `/api/portal/*` (devolviendo un 503 sintético JSON cuando no hay red, para que el SPA pueda mostrar su propio mensaje). Cada SW versiona su cache (`sprint-portal-v1`, `sprint-admin-v1`) y limpia versiones viejas en `activate`.
- **Páginas offline** `offline.html` por app, sirvidas desde el cache cuando la red falla y la página no está en cache.
- **Endpoints en el scope** para que el SW se registre con scope amplio: `GET /app/sw.js` y `GET /admin/sw.js` devuelven el archivo con `Content-Type: application/javascript`, `Cache-Control: no-cache, no-store, must-revalidate` y `Service-Worker-Allowed: /app/` (resp. `/admin/`). Alias `GET /app/manifest.webmanifest` y `GET /admin/manifest.webmanifest` con MIME `application/manifest+json`.
- **Meta tags PWA** en las plantillas raíz: `<link rel="manifest">`, `theme-color`, `apple-mobile-web-app-*`, `apple-touch-icon`, favicons. Inyectados en `app/portal/templates/portal.html`, `app/portal/templates/login.html` y `app/cms/templates/base.html` (de la que hereda `cms/templates/login.html`).
- **Registro del SW** desde el `<body>` de cada plantilla raíz, con `scope` explícito coincidente.

### Corregido

- **Mount estático del CMS no se propagaba** al hacer `app.include_router(cms_router)` (depende de la versión de Starlette/FastAPI). Antes pasaba desapercibido porque `app/cms/static/` solo contenía un `.gitkeep`. Movido a `cms.routes.router_mounts` para que `main.py` lo monte explícitamente, igual que ya hacía con `portal.routes.router_mounts`. Sin esto, los assets del CMS y del SW del CMS daban 404 en producción.

### Verificación

- Test con `TestClient` simulando la app real: las 10 rutas PWA (sw.js, manifests, iconos y offline para ambas apps) devuelven 200 con los Content-Type y `Service-Worker-Allowed` correctos. El HTML de `/app/login` y `/admin/login` incluye `<link rel="manifest">`, `theme-color`, `apple-touch-icon` y el `<script>` de registro del service worker.

### Notas operativas

- Los iconos actuales son placeholders de marca. Si en algún momento queréis arte definitivo, basta con sustituir los PNG en `app/{portal,cms}/static/icons/` manteniendo los nombres y tamaños — el manifest no necesita cambios.
- En iOS Safari, la PWA solo se instala desde "Compartir → Añadir a pantalla de inicio". Conviene mencionarlo en onboarding del cliente.

## 2026-04-28 (landing — pivote a voz 24/7 + Telegram)

Se reescribió la landing pública (`app/templates/landing.html`) para alinearla con la propuesta de valor actual tras el retiro de WhatsApp en abril. La oferta queda planteada como dos cosas que Sprint hace: **atender las llamadas que tú no puedes coger** y **montar un chat automático en Telegram** con la misma agenda. Copy reorientada a beneficios (qué te resuelve), no a jerarquía interna de canales. Eliminadas todas las menciones a WhatsApp en copy, integraciones, planes, FAQ JSON-LD y modal de leads.

### Cambiado

- **Hero rebrandeado a voz + Telegram**: nuevo H1 ("Tu teléfono, atendido siempre."), subline en clave de beneficio ("atiende las llamadas que tú no puedes coger… y monta también un chat automático en Telegram"), eyebrow y meta items revisados.
- **Demo del teléfono pasa de chat tipo WhatsApp a llamada en vivo**: header oscuro estilo "En llamada · 00:42" con dot rojo pulsando, avatar de Ana, transcripción en bubbles con etiquetas TÚ / ANA, waveform animado al pie y contador de llamada que avanza en JS. La calendar card se convierte en toast "✓ Cita confirmada · enviada a Google Calendar".
- **Sección "Lo que hace Sprint"** (antes "Canales"): dos tarjetas con titulares de venta — "Atiende tus llamadas, también las de las 23:00" y "Tu chat automático en Telegram, listo en un día" — en lugar de etiquetas tipo "producto principal / add-on". Se quitan las tarjetas de WhatsApp Business y Chat web.
- **Pricing reescrito**: Solo incluye ya la voz 24/7 (antes era solo WhatsApp + Chat web), Estudio añade el bot de Telegram, Equipo es multi-sede/números. Métrica de uso pasa de "conversaciones/mes" a "llamadas/mes".
- **Integraciones** sustituye WhatsApp Business API e Instagram DM por Telegram (bot opcional), ElevenLabs (voz IA) y SIP (telefonía). Se elimina la celda Twilio.
- **Cómo funciona — Paso 03**: ahora "Enlazamos tu teléfono, tu Google Calendar y, si lo activas, tu bot de Telegram" en lugar de "tu WhatsApp".
- **Sectores** (peluquerías y restaurantes en `SECTORS` JS): copy ajustada para que la entrada de la reserva sea por llamada de voz, con Telegram como complemento.
- **Marquee** sustituye "Funciona por WhatsApp" por "Bot de Telegram opcional" y "Habla con tu tono" → "Habla con tu voz".
- **Footer foot-tag** reescrito: "Asistente de llamadas con IA 24/7 (y bot de Telegram opcional). Menos teléfono, más citas."
- **Modal de leads**: label del campo teléfono pasa de "Tu WhatsApp o teléfono" a "Tu teléfono"; disclaimer ya no menciona WhatsApp.

### SEO

- Title, meta description y keywords reorientados a llamadas IA 24/7 / recepcionista virtual / bot de Telegram para reservas.
- OG y Twitter cards alineados al nuevo posicionamiento.
- JSON-LD `SoftwareApplication`, `Organization` y `FAQPage` actualizados: la pregunta sobre cambiar de número de WhatsApp se sustituye por una sobre desvío de teléfono, y se añade una FAQ explícita sobre el bot de Telegram opcional.

### Notas

- No se ha tocado código del backend ni rutas. La landing sigue sirviéndose desde `app/templates/landing.html`.
- Cualquier referencia restante a WhatsApp en docs internos (`HANDOFF_*.md`, `README.md`, etc.) queda como histórico explícito según `CLAUDE.md`.

## 2026-04-28 (voz — guardrails de saludo, tenant propio y deploy de fixes)

Se cerró la tanda de hardening del canal de voz tras una llamada real con saludo ofensivo y varios roces de UX. Además, `pelu_demo` dejó de depender del agente global de entorno y pasó a tener `voice_agent_id` propio en producción.

### Corregido

- **Saludo ofensivo eliminado**: el `first_message` del agente remoto quedó fijado a `Hola, soy Ana de la peluquería. ¿En qué te puedo ayudar?` para evitar saludos creativos o tóxicos editados desde la UI de ElevenLabs.
- **"sin preferencia" vuelve a funcionar** en `consultar_disponibilidad` y `crear_reserva`: el backend ya normaliza variantes (`sin preferencia`, vacío, `me da igual`, `cualquiera`) y no las trata como nombre de peluquero inexistente.
- **Healthcheck de voz más fiel al estado real**: `/_diag/elevenlabs/healthcheck` y `/_diag/tenant/voice` ahora reportan prompt efectivo, drift y origen del `agent_id`; además se arregló un `DetachedInstanceError` al inspeccionar tenants fuera de sesión SQLAlchemy.
- **Prompt de voz regenerable y sincronizable** desde CMS/diag: se añadió flujo para regenerar el prompt desde datos del tenant y empujarlo a ElevenLabs sin editarlo a mano.

### Cambiado

- **Prompt de `pelu_demo` endurecido**: añade regla explícita de no insultar / no repetir tacos del cliente, evita diminutivos raros, reduce repeticiones del nombre y mejora el lenguaje al confirmar, mover o cancelar.
- **Prompt comprimido** de ~4.4 KB a ~3.2 KB para rascar algo de latencia de prefill.
- **`max_tokens` por defecto baja de 300 a 220** en creación/sincronización de agentes para recortar divagaciones y tiempo de respuesta.
- **Creación de agentes nuevos** (`create_agent_for_tenant` y `scripts/setup_elevenlabs_agent.py`) nace ya con saludo seguro y el cap de 220 tokens.
- **`pelu_demo` usa agente propio** en producción (`voice_agent_id` guardado en BD) en vez de heredar `ELEVENLABS_AGENT_ID` global. Así los cambios del tenant quedan aislados y no contaminan otros bots.

### Deploy / verificación

- Push remoto de los fixes backend/diag/voz.
- Redeploy manual en Railway y verificación post-deploy:
  - `/_diag/tenant/voice` OK
  - `/_diag/elevenlabs/healthcheck` OK
  - smoke test de `consultar_disponibilidad` con `peluquero_preferido="sin preferencia"` OK
- Regeneración y sincronización del prompt en `pelu_demo` y `test_mario`.

## 2026-04-24 (ronda 9 hotfix — paginación + filtro por nombre)

Tras subir la búsqueda por nombre en la ronda 9, Marcos probó con una cita real (`"Mario — Corte hombre (Mario)"` del lunes 27 a las 12:30) y el endpoint respondió `encontrada:false` aunque el evento existe. Dos fixes consecutivos:

### Corregido

- **Filtro demasiado laxo**: la primera versión matcheaba cualquier ocurrencia del nombre en el summary, de modo que buscar `"Mario"` devolvía citas como `"Eva Test — Corte (con Mario)"` donde Mario es el peluquero, no el cliente. Endurecido a dos criterios estrictos:
  - **Match A**: `extendedProperties.private.client_name` coincide (exacto o substring bidireccional).
  - **Match B**: `summary` empieza por `"<nombre> —"` (convención del título canónico "Nombre — Servicio (con Peluquero)").
  Si ninguno matchea, `encontrada:false` — mejor vacío que falso positivo.

- **maxResults=20 cortaba la respuesta antes del evento real**: el calendario de `pelu_demo` tiene decenas de citas donde "Mario" aparece como peluquero en `(con Mario)`; `events.list?q=Mario` devolvía 27+ resultados y el evento real estaba hacia el final. Ahora **paginamos hasta 5 páginas × 100 resultados (500 eventos máx)** con `pageToken`. Cubre >2 meses de agenda densa.

Verificado contra producción:
- `"Mario"` → `Mario — Corte hombre (Mario)` del lunes 27 a las 12:30 ✓
- `"Eva"` → `Eva Test — Corte hombre (con Mario)` ✓
- `"Marcos"` → cita del cliente Marcos (no del peluquero) ✓
- Falsos positivos del peluquero Mario → descartados ✓

### Notas

- `scripts/setup_elevenlabs_agent.py` y `app/elevenlabs_client.create_agent_for_tenant` declaran los `dynamic_variable_placeholders` por defecto al crear un agente. Tenants nuevos nacen con el schema correcto y no caen en el bug.

---

## 2026-04-24 (ronda 9 — búsqueda por nombre, soft_timeout y end_call)

Tres fallos de UX observados en llamadas reales; corregidos los tres.

### Corregido

- **Ana no podía buscar una cita por nombre** cuando el cliente decía "está a nombre de Mario" o llamaba desde otro número. `buscar_reserva_cliente` solo aceptaba `telefono_cliente` → Ana quedaba bloqueada ("el sistema solo me deja buscar por teléfono"). Ahora la tool acepta `nombre_cliente` opcional y el backend usa `events.list?q=<nombre>` de Google Calendar, filtrando por `summary` y `extendedProperties.private.client_name` para evitar falsos positivos. Nuevo helper `cal.buscar_evento_por_nombre` en `app/calendar_service.py`. El handler `/tools/buscar_reserva_cliente` intenta primero por teléfono (body o caller_id) y si no encuentra, prueba por nombre; devuelve `via_busqueda: "telefono"` o `"nombre"` para que Ana sepa confirmarlo antes de mover/cancelar.

- **"¿Sigues ahí?" se disparaba casi inmediatamente**. `turn.soft_timeout_config.timeout_seconds` estaba en `-1.0` (desactivado) pero el `turn_timeout` de 1s hacía que Ana retomara el turno casi al instante. Ahora `soft_timeout_config = {timeout_seconds: 3.5, use_llm_generated_message: true, message: "¿Sigues ahí?"}`. Espera 3.5s de silencio y entonces Ana improvisa un "¿sigues ahí, Juan?" natural (no el literal fijo).

- **Llamada no colgaba al terminar**. `silence_end_call_timeout` estaba en `-1.0` y `built_in_tools.end_call` estaba en `null`. Ahora:
  - `silence_end_call_timeout: 25.0` → si tras el "¿sigues ahí?" pasan otros ~25s sin respuesta, la llamada se cierra sola.
  - `end_call` habilitado como built-in tool con una descripción que indica cuándo usarla: tras cierre natural, tras confirmar reserva sin nada más, o tras derivar al fallback.
  - Prompt `ana_prompt_new.txt` añadida sección `## Cierre y colgar` que le dice a Ana que tras su última frase de despedida llame a `end_call`. Verificado: el bench muestra `tools=['consultar_disponibilidad', 'crear_reserva', 'end_call']` al final de un flujo de reserva completo.

### Añadido / Cambiado

- `app/calendar_service.py::buscar_evento_por_nombre(nombre, desde, hasta, ...)`: usa `events.list?q=<nombre>` con `maxResults=10` y filtra por `summary`/`client_name` para evitar matches falsos.
- `app/eleven_tools.py`: `BuscarReq` tiene `nombre_cliente: str | None = None`. El handler busca por teléfono, si no encuentra prueba por nombre, devuelve `via_busqueda` distinguiendo origen. Mensaje de error graceful si fallan los dos.
- `app/elevenlabs_client.create_agent_for_tenant` y `scripts/setup_elevenlabs_agent.py` aplican `soft_timeout_config`, `silence_end_call_timeout: 25`, `built_in_tools.end_call` y el schema nuevo de `buscar_reserva_cliente` por defecto. Cualquier bot nuevo nace con esto.
- `ana_prompt_new.txt`: nueva sección `## Flujo MOVER / CANCELAR — si no encuentras por teléfono` para instruir explícitamente que pregunte el nombre tras un fallo de búsqueda; sección `## Cierre y colgar` con la regla de llamar a `end_call`.
- Snapshot: `docs/elevenlabs_agent_snapshot_post_round9_*.json`.

### Tests

- Suite backend: 106/106 verdes.
- `scripts/test_dialog.py` en `reserva_sin_peluquero`: **7/7 checks OK**. Detecta `end_call` al final del flujo como tool válida adicional.

---

## 2026-04-24 (docs — consolidar knowledge del prompt)

### Añadido

- **`PROMPT_KNOWLEDGE.md`**: documento maestro con todo lo aprendido sobre el prompt de voz de Ana y su mantenimiento. Contenido:
  - Principios inalterables (flujo RESERVA canónico, UNA pregunta por turno, reglas duras). Política de "no tocar sin permiso" + cambios permitidos sin permiso.
  - 6 gotchas descubiertos con diagnóstico y solución (Gemini 3 ignora `{{system__time}}`, `dynamic_variable_placeholders` obligatorios, `pre_tool_speech` es enum, `simulate-conversation` no ejecuta tools, Gemini 3 encadena preguntas sin ejemplos explícitos, del hijoputa en el first_message).
  - Proceso de mantenimiento paso a paso: editar prompt → refresh → test → commit. Incluye cron diario recomendado.
  - Cómo añadir un escenario/check al harness, cómo añadir una dynamic_variable nueva.
  - Tabla de los ~13 modelos LLM evaluados con criterio de sustitución.
  - Checklist pre-push (7 items).
  - Regla de oro: si no hay permiso explícito, no tocar.

### Cambiado

- **`CLAUDE.md`, `README.md`, `BOT_NUEVO_CONFIG.md`** enlazan a `PROMPT_KNOWLEDGE.md` como punto de entrada para cualquier edición del prompt de voz. `BOT_NUEVO_CONFIG.md` redirige el detalle del prompt ahí (una sola fuente de verdad).

---

## 2026-04-24 (ronda 8 — restaurar jerarquía del prompt + test automatizado)

Bug de producto: al recortar el prompt en la ronda 7, moví **"nombre al FINAL antes de crear_reserva"** a **"nombre antes de consultar"**, eliminé la repetición enfática de **"UNA pregunta por turno"** y suprimí la sección de fillers. Marcos cazó la regresión en una llamada real ("te pregunta 27 cosas, habíamos establecido una jerarquía"). Corregido.

### Corregido

- **Prompt `ana_prompt_new.txt` restaurado a la jerarquía original** (5 KB): `servicio → cuándo → consultar → ofrecer → elegir → NOMBRE → crear`. Nombre al FINAL, justo antes de `crear_reserva`, no antes. Incluye EJEMPLO DE TURNOS BIEN ORDENADOS + ejemplos explícitos de MAL (encadenar dos preguntas en un turno) para que el LLM no se desvíe. Sección `## Estilo` pura + sección `## UNA pregunta por turno (regla crítica)` separada.
- **Bug de año de las fechas**: Gemini 3 Flash Preview ignora `{{system__time}}` del contexto y usa su training cutoff → enviaba fechas de 2025 cuando estábamos en 2026 → huecos=[] → Ana alucinaba horas. **Nuevo `scripts/refresh_agent_prompt.py`** que renderiza el bloque `<!-- REFRESH_BLOCK -->` con macros `__HOY_FECHA__`, `__MANANA_FECHA__`, `__ANO_ACTUAL__`, etc. como **texto literal** antes de sincronizar el prompt. Ya no depende de la variable — la fecha queda hardcodeada en el prompt subido. Ejecutar al menos 1x al día (idealmente cron).

### Añadido

- **`scripts/test_dialog.py`**: harness de tests de flujo contra el agente real vía `/v1/convai/agents/{id}/simulate-conversation`. Valida 7 checks por escenario:
  1. `orden_tools_correcto` — `consultar_disponibilidad` antes de `crear_reserva`, etc.
  2. `nombre_al_final` — primera pregunta de nombre después del primer `consultar_disponibilidad`.
  3. `una_pregunta_por_turno` — 0 ó 1 interrogación por turno de agente (ignora muletillas).
  4. `año_correcto` — todas las fechas ISO con año actual.
  5. `peluquero_vacio` — `peluquero_preferido` vacío cuando el user no lo menciona.
  6. `telefono_no_none` — en `crear_reserva`, `telefono_cliente` no es literal "None".
  7. `no_alucina_huecos` — si el backend devuelve `huecos=[]`, Ana no propone horas (SKIP en sim porque `simulate-conversation` no ejecuta tools realmente).
  4 escenarios (reserva_sin_peluquero, reserva_con_peluquero, mover_cita, cancelar_cita). Resultado tras fix: **3/4 escenarios ALL GREEN (7/7 checks)**. `mover_cita` falla con 500 del simulator de ElevenLabs (bug del simulator, no del prompt — en llamadas reales la tool sí devuelve event_id).

- **`scripts/refresh_agent_prompt.py`**: renderiza fechas reales en el prompt y sincroniza. Idempotente, ejecutable vía cron.

### Cambiado

- **LLM vuelve a `gemini-3-flash-preview`** con prompt reforzado. Gemini 2.5 Flash era más obediente pero 3x más lento. El refuerzo con ejemplos explícitos hace que 3-flash-preview siga el flujo (verificado en los 7 checks de 3/4 escenarios).
- **`ana_prompt_new.txt`**: nueva sección `## ATENCIÓN — FECHA ACTUAL` al principio con bloque `<!-- REFRESH_BLOCK -->` que `refresh_agent_prompt.py` reemplaza por fecha literal.

### Política

- **No volver a tocar la jerarquía del prompt de Ana sin permiso explícito de Marcos.** Se ha añadido feedback a la memoria personal (`feedback_prompt_ana_no_tocar.md`) para que futuras sesiones lo tengan presente.

---

## 2026-04-24 (ronda 7 — hotfix fechas alucinadas)

Bug crítico descubierto al probar el bot con una llamada real: Ana alucinaba fechas de **mayo 2025** cuando hoy era abril 2026, creaba la cita pero en el año pasado, y respondía "no hay huecos" en cuanto el cliente mencionaba cualquier día.

### Corregido

- **Placeholders de dynamic_variables no estaban declarados**. ElevenLabs **ignora** lo que devuelve el personalization webhook si las keys custom NO están pre-registradas como `conversation_config.agent.dynamic_variables.dynamic_variable_placeholders`. Sin esto, el prompt veía literal `{{manana_fecha_iso}}` y el LLM improvisaba. Se han declarado las 11 keys (`hoy_fecha_iso`, `manana_fecha_iso`, `pasado_fecha_iso`, `hoy_dia_semana`, `manana_dia_semana`, `hoy_natural`, `manana_natural`, `hora_local`, `caller_id_legible`, `tenant_id`, `tenant_name`) en el agente remoto `pelu_demo` vía PATCH.
- **Prompt revertido a usar `{{system__time}}` (variable del sistema, siempre inyectada)** como fuente primaria de fecha, con regla 11 explícita "el año de las fechas ISO SIEMPRE coincide con el año de {{system__time}}". Así el bot funciona con o sin webhook; las custom dynamic_variables son ahora una mejora (tokens ahorrados), no requisito.
- **Evento fantasma creado en 2025-05-20 12:30 borrado** (`event_id=8bh7rp74o477sum6cuuj9govb8` en el calendario principal del tenant). Diagnosticado via `GET /v1/convai/conversations/{id}` que mostró args de tool_calls con fechas de mayo 2025.

### Cambiado

- **`scripts/setup_elevenlabs_agent.py`** y **`app/elevenlabs_client.create_agent_for_tenant`** declaran los `dynamic_variable_placeholders` por defecto al crear un agente. Tenants nuevos nacen con esto resuelto.
- **`BOT_NUEVO_CONFIG.md`**: nueva sección "Placeholders de dynamic_variables (OBLIGATORIO)" con el gotcha y la lista completa. Mantiene el checklist alineado.
- **`ana_prompt_new.txt`**: usa `{{system__time}}` en lugar de `{{manana_fecha_iso}}` etc. Incluye regla dura sobre año.

### Notas

- El personalization webhook sigue desplegado y funcional (`/tools/eleven/personalization` responde 200 con las variables correctas). Con los placeholders declarados ahora ElevenLabs debería inyectarlas en la próxima llamada real via Twilio. En WS text-only el webhook puede saltarse porque el cliente envía su propio `conversation_initiation_client_data` en el primer frame — verificación real requiere llamada inbound Twilio.
- Snapshot post-fix: `docs/elevenlabs_agent_snapshot_post_round7_hotfix_2026-04-24T181207Z.json`.

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
