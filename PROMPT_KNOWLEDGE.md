# Knowledge — prompt de voz de Ana y mantenimiento

Todo lo aprendido sobre cómo escribir, mantener y testear el prompt del agente Ana (ElevenLabs Convai) sin romper el comportamiento. Léelo antes de tocar `ana_prompt_new.txt` o la configuración del agente.

Documentos relacionados:
- `BOT_NUEVO_CONFIG.md` — valores canónicos de LLM/TTS/turn/tools para bots nuevos.
- `ELEVENLABS.md` — snapshot de la config y cómo regenerarlo.
- `CHANGELOG.md` — rondas históricas de iteración.

## 1. Principios inalterables del prompt

Estos son los acuerdos de producto. Marcos ha iterado mucho sobre ellos. **No modificar sin permiso explícito suyo** (y aunque lo pida, confirmar antes de aplicar).

### 1.1 Flujo RESERVA canónico

```
servicio → cuándo → consultar → ofrecer → elegir → NOMBRE → crear
```

El nombre del cliente se pide en el **paso 5**, después de que el cliente elige un hueco, **no antes** de `consultar_disponibilidad`. Motivo: pedir nombre antes rompe el flujo natural por teléfono ("dime tu nombre para mirar huecos" es raro y añade una ida y vuelta extra).

Si el cliente se presenta solo ("soy Luis", "de parte de Marta") en cualquier turno anterior, saltarse el paso 5 y NO repreguntar.

### 1.2 UNA pregunta por turno

Cada turno del agente contiene como mucho UNA pregunta que pida un dato nuevo. Las muletillas `¿vale?`, `¿te parece?`, `¿de acuerdo?` NO cuentan como segunda pregunta.

BIEN: "¿qué servicio te hacemos?"
BIEN: "corte de hombre, perfecto. ¿para qué día?"
MAL: "¿qué servicio? ¿y para qué día?"
MAL: "corte de hombre. ¿para qué día? ¿y a qué nombre?"

Este comportamiento requiere ejemplos explícitos en el prompt (no basta con la frase genérica) porque los LLMs tipo Gemini 3 Flash Preview tienden a encadenar preguntas si no lo tienen prohibido con casos concretos.

### 1.3 Otras reglas duras (no alterar sin permiso)

- **Peluquero**: si el cliente NO lo menciona, `peluquero_preferido` vacío. Nunca inventar un nombre. Solo ofrecer nombre si el cliente pregunta o si diferencia importa.
- **Teléfono**: pasa SIEMPRE `{{system__caller_id}}` como `telefono_cliente` en tool calls. NUNCA preguntes el teléfono salvo si `caller_id` es "unknown"/"anonymous"/"null"/"-"/vacío.
- **Máximo 3 huecos por turno**.
- **No inventar huecos**: siempre `consultar_disponibilidad` antes de proponer hora.
- **Retry tras error**: `retryable:true` → 1 solo reintento con filler. Tras fallar, derivar al teléfono de fallback hablado.
- **Lista vacía sin error** → ofrecer otro día/peluquero, no es fallo.
- **Estructura del prompt**: secciones fijas en orden `ATENCIÓN fecha → Negocio → Contexto → Estilo → UNA pregunta por turno → Fechas al hablar → Fillers antes de tool calls → Qué puedes hacer → Reglas duras → Flujo RESERVA → Flujo MOVER → Flujo CANCELAR → Cierre`. No añadir ni quitar secciones sin visto bueno.

### 1.4 Cambios permitidos sin permiso

- Actualizar el catálogo de servicios/peluqueros/horario del negocio.
- Corregir variables del sistema o añadir reglas anti-alucinación sobre fechas / tools.
- Añadir ejemplos BIEN/MAL para reforzar reglas que ya existen (si se saltan).

TODO lo demás → pide confirmación.

## 2. Gotchas descubiertos

Problemas reales que costó encontrar. Conservar aquí para no redescubrirlos.

### 2.1 Gemini 3 Flash Preview ignora `{{system__time}}`

Gemini 3 Flash Preview (y probablemente otros modelos Gemini 3.x) **no respeta la fecha inyectada en `{{system__time}}`** del contexto: usa su training cutoff como "hoy" y genera fechas ISO con ese año. Resultado: llamada en abril 2026 → Ana manda `2025-05-15T09:30:00` al backend → huecos=[] (todo en el pasado) → Ana improvisa horas falsas o dice "no hay huecos" a todo.

**Solución**: hardcodear la fecha como texto literal en el prompt antes de sincronizar. `scripts/refresh_agent_prompt.py` reemplaza macros `__HOY_FECHA__`, `__MANANA_FECHA__`, `__ANO_ACTUAL__`, etc. en el bloque `<!-- REFRESH_BLOCK -->` por valores reales calculados al vuelo.

Ejecutar `refresh_agent_prompt.py` al menos 1 vez al día (idealmente con cron a las 00:05 Europe/Madrid). Es idempotente — se puede ejecutar las veces que quieras. El prompt en disco queda con las macros; solo el agente remoto tiene la fecha renderizada.

### 2.2 `dynamic_variable_placeholders` son OBLIGATORIOS

ElevenLabs **ignora** lo que devuelve el `conversation_initiation_client_data_webhook` si las keys custom NO están pre-declaradas en `conversation_config.agent.dynamic_variables.dynamic_variable_placeholders`. Sin declaración previa, `{{hoy_fecha_iso}}` aparece literal en el prompt y el LLM lo trata como texto, no como dato.

Las 11 keys que expone nuestro `/tools/eleven/personalization` están pre-declaradas en `scripts/setup_elevenlabs_agent.py` y en `app/elevenlabs_client.create_agent_for_tenant`. Si añades una variable nueva en el endpoint, **añádela también a ambos scripts** y re-sincroniza el agente existente.

### 2.3 `pre_tool_speech` es enum, no booleano

Para que el agente hable el filler en paralelo a la tool call (y paralelice latencia), NO basta con `force_pre_tool_speech: true`. El flag que manda es el enum `pre_tool_speech` con valores `auto | force | off`. Solo `force` activa el comportamiento. Valores descubiertos vía prueba: los otros valores (`"always"`, `"on"`) devuelven 422.

### 2.4 `simulate-conversation` NO ejecuta tools reales

El endpoint `/v1/convai/agents/{id}/simulate-conversation` es útil para validar el flujo del agente (orden de tools, preguntas, reglas de nombre) pero **no ejecuta los webhooks reales**. Devuelve `result_value: "Tool Called."` como string literal, y el agente improvisa la respuesta del backend.

Consecuencias para `test_dialog.py`:
- Chequeos de flujo (nombre al final, una pregunta por turno, año, orden tools) SÍ son válidos.
- Chequeos que dependen del contenido real de la tool (no_alucina_huecos, event_id real para mover/cancelar) NO se pueden validar con este endpoint. El test los marca como SKIP.
- `mover_cita` falla con 500 porque el flujo necesita `event_id` que el sim no devuelve. No es bug nuestro — se verifica con llamada real.

### 2.5 Gemini 3 Flash Preview tiende a encadenar preguntas

Sin ejemplos explícitos, Gemini 3 Flash Preview pregunta 2 cosas a la vez en el primer turno ("¿qué servicio? ¿y a qué nombre?"). Se corrige con la sección `## UNA pregunta por turno (regla crítica)` del prompt que incluye 3 ejemplos MAL y 2 ejemplos BIEN. Validado con `test_dialog.py` — 7/7 checks pasan tras el refuerzo.

Regla general: para cualquier comportamiento que el modelo tienda a violar, añadir **ejemplo concreto de lo que NO debe hacer**. Las instrucciones abstractas no bastan.

### 2.6 Del hijoputa en el first_message

El `first_message` remoto del agente contiene "¡Hola hijoputa!". Es broma de Marcos, no tocar. Si lo cambiaste por error, el mensaje canónico anterior era "¡Hola! Soy Ana de la peluquería. ¿En qué te puedo ayudar?" pero consulta antes de revertir.

## 3. Proceso de mantenimiento

### 3.1 Editar el prompt

1. Editar `ana_prompt_new.txt` localmente. Mantener el bloque `<!-- REFRESH_BLOCK -->...<!-- /REFRESH_BLOCK -->` intacto (macros son reemplazados por el script de refresh).
2. `python scripts/refresh_agent_prompt.py --print` — ver cómo quedará el prompt tras render.
3. `python scripts/refresh_agent_prompt.py` — aplica al agente remoto.
4. `python scripts/test_dialog.py` — valida los 7 checks en los 4 escenarios (tarda ~45s × 4 escenarios, corre cada uno en su propio comando si el entorno tiene timeout por comando).
5. Si algún check FAIL, ajustar el prompt y volver a 2.
6. Si ALL GREEN en ≥3 de 4 escenarios, commit + push.
7. CHANGELOG entry describiendo la ronda.

### 3.2 Cron diario (recomendado)

Para que el prompt siempre tenga la fecha del día sin depender de deploy manual, configurar una scheduled task:

- **Via Cowork scheduled task**: `taskId=bot-reservas-refresh-prompt`, `cronExpression="5 0 * * *"` (00:05 Europe/Madrid), `prompt="python /Users/marcosfernandezcarrillo/Desktop/bot_reservas_whatsapp/scripts/refresh_agent_prompt.py"`. Se registra con `mcp__scheduled-tasks__create_scheduled_task`.
- **Via GitHub Actions en Railway**: añadir un job en `.github/workflows/daily-refresh.yml` que ejecute el script al mediodía UTC.
- **Via Railway cron**: Railway soporta `cron` en el `railway.toml` para jobs periódicos.

Si no hay cron, ejecutar el script a mano cuando se note que las fechas están desalineadas (el test `año_correcto` lo detecta rápido).

### 3.3 Añadir un escenario al harness

Editar `scripts/test_dialog.py` → lista `SCENARIOS`. Cada escenario es un `Scenario`:

- `name`: slug para llamarlo desde CLI.
- `user_prompt`: instrucciones al simulated_user (cliente simulado por LLM).
- `first_message`: el primer mensaje que el cliente envía tras saludo.
- `expected_tools`: tuple de nombres de tool que deberían aparecer en orden.
- `user_mentioned_lower`: texto en minúsculas que menciona el user (para `check_peluquero_vacio`).

### 3.4 Añadir un check al harness

Editar `scripts/test_dialog.py`:
1. Definir función `check_mi_check(turns) -> tuple[bool, str]`.
2. Añadirla a la lista `checks` en `run_scenario`.
3. Si el sim no puede validar el comportamiento (porque no ejecuta tools), devolver `(True, "SKIP — ...")` con motivo.

### 3.5 Añadir una variable dinámica nueva

Cuando se necesite una nueva `dynamic_variable` precomputada (p.ej. `semana_que_viene_dia`):
1. Añadirla al dict `dynamic_variables` de `app/eleven_tools.py::eleven_personalization`.
2. Añadirla como key en `dynamic_placeholders` en **los dos** sitios:
   - `scripts/setup_elevenlabs_agent.py`.
   - `app/elevenlabs_client.create_agent_for_tenant`.
3. Re-sincronizar el agente existente con `PATCH dynamic_variable_placeholders` (ver `BOT_NUEVO_CONFIG.md` § "Placeholders de dynamic_variables").
4. Usar `{{semana_que_viene_dia}}` en el prompt.

## 4. Modelos LLM evaluados

Para cada modelo, el bench midió TTFR (time-to-first-response tras user message), TT_final (tiempo hasta respuesta útil post-tool) y tool-calling reliability. Ver `CHANGELOG.md` rondas 6-8 para datos.

### 4.1 Modelos elegibles

| Modelo | TTFR | TT_final | Tool calls | Notas |
|--------|------|----------|------------|-------|
| `gemini-3-flash-preview` (actual) | ~1200 ms | ~3000 ms | 4/4 | **Elegido.** Rápido y obediente con prompt reforzado. |
| `gemini-2.5-flash` | ~4500 ms | ~7500 ms | 4/4 | Fallback si 3-flash-preview deprecia. Más lento pero muy obediente. |

### 4.2 Modelos descartados (no re-probar sin razón nueva)

- `qwen3-30b-a3b`: 319 ms TTFR pero **0 tool calls** en 8 turnos.
- `glm-45-air-fp8`: 557 ms TTFR pero **alucina reservas** sin llamar a `consultar_disponibilidad`.
- `gpt-oss-120b`: tool calls OK pero varianza 910-9134 ms TTFR (inaceptable).
- `watt-tool-70b`: 6400 ms TTFR, 12 s TT_final.
- `gemini-2.5-flash-lite`: 1061 ms pero no llama tools.
- `gemini-3-pro-preview`: 7-10 s TTFR y cero tool calls.
- `gemini-3.1-flash-lite-preview`: funciona pero varianza más alta que 3-flash-preview, sin ganancia clara.

De rondas anteriores:
- `gemini-2.0-flash`, `gemini-2.0-flash-lite`: alucinan huecos, confirman cancelaciones sin llamar tool.
- `claude-haiku-4-5`: pide nombre/teléfono contra reglas.
- `gpt-4.1-nano`, `gpt-4o-mini`: alucinan cancelación sin tool.

**Criterio para sustituir el modelo actual** (`gemini-3-flash-preview`): un candidato nuevo debe superar en ≥3 de los 4 escenarios de `test_dialog.py` **con los 7 checks en verde** y TTFR medio menor. Si solo baja latencia a costa de fiabilidad, descartado.

## 5. Checklist rápido antes de pushear cambios de prompt

- [ ] `scripts/refresh_agent_prompt.py --print` enseña el prompt con fechas reales.
- [ ] `scripts/refresh_agent_prompt.py` sincroniza sin error (PATCH 200).
- [ ] `scripts/test_dialog.py reserva_sin_peluquero` → 7/7 checks OK.
- [ ] `scripts/test_dialog.py reserva_con_peluquero` → 7/7 checks OK.
- [ ] `scripts/test_dialog.py cancelar_cita` → 7/7 checks OK.
- [ ] `scripts/test_dialog.py mover_cita` → OK o 500 del simulator (aceptable).
- [ ] `pytest` del backend → verde (106/106 actualmente).
- [ ] CHANGELOG.md con entrada explicando la ronda.
- [ ] Snapshot del agente en `docs/elevenlabs_agent_snapshot_post_round*_*.json`.
- [ ] Llamada real de verificación (30 s) antes de cerrar la sesión.

## 6. Regla de oro

> Si no tengo permiso explícito de Marcos y el cambio no es una corrección evidente de bug, no toco el prompt. Ante la duda, pregunto antes de subir al agente remoto.

Ver `feedback_prompt_ana_no_tocar.md` en memoria personal del agente.
