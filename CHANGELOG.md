# Changelog

Registro vivo de cambios publicados al remoto. Formato: sección por fecha, subsecciones por tipo de cambio. Ver convención completa en `CLAUDE.md`.

Entrada más reciente arriba.

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
