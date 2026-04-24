# Protocolo de cierre de jornada — handoff a #bot-reservas

> Aplica **a cualquier agente LLM** (Claude de Marcos, Claude de Mario, o cualquier otro) que abra sesión sobre este repositorio. Es una regla de proyecto, no de máquina: cada agente debe ejecutarla en su equipo cuando se cumpla el trigger.

## Objetivo

Publicar un **resumen sintético de la jornada** en el canal de Slack `#bot-reservas` (ID `C0AU8MGLKU5`) para que Marcos y Mario compartan contexto entre ellos y entre sus respectivas instancias de Claude sin tener que leerse el `CHANGELOG` completo.

## Trigger (cuándo dispararlo)

Se dispara cuando se cumplen **todas** estas condiciones a la vez:

1. **Han pasado más de 3 horas** desde la última modificación significativa en el repo (ignorando `.venv/`, `.git/`, `__pycache__/`, `.pytest_cache/`, `_test_del`, `data.db`).
2. **Ha habido actividad hoy** en el repo (al menos un commit hecho hoy, o archivos fuera de esos paths tocados hoy).
3. **Todavía no se ha publicado** el handoff de hoy a `#bot-reservas` (el estado vive en `.handoff_state.json`, ver abajo).

Si los tres se cumplen → el agente genera y publica el handoff. Si cualquiera falla → no hace nada.

También puede dispararse **manualmente** si el usuario dice "manda el handoff", "cierra la jornada", "resumen a Slack" o similar, saltándose el trigger automático.

## Contenido del mensaje (formato obligatorio)

El mensaje a Slack usa Markdown y tiene esta estructura, en este orden:

```
*Handoff {YYYY-MM-DD} — bot_reservas_whatsapp*

*Avances*
• <bullet sintético 1>
• <bullet sintético 2>
...

*Quién tocó qué*
• Marcos: <archivos/áreas>
• Mario: <archivos/áreas>
(si solo hubo uno, omitir al otro)

*Bloqueos / pendientes*
• <bullet 1>
(si no hay, escribir "Ninguno.")

*Próximos pasos*
• <bullet 1>
• <bullet 2>
```

Reglas de estilo:

- **Sintético**: cada bullet 1 línea, máximo 2. Tono técnico-directo, sin adjetivos de marketing, sin emojis.
- Si un cambio ya está en `CHANGELOG.md` hoy, resume **sin copiar literal**.
- No incluir rutas absolutas ni secretos. Sí commit hashes cortos (7 chars) entre backticks cuando ayude.
- Zona horaria: `Europe/Madrid`. La fecha del handoff es la local.
- Máximo ~25 bullets en total. Si el día fue enorme, prioriza los de mayor impacto y agrupa el resto.

## Cómo construir el resumen

Fuentes de información, en orden de prioridad:

1. `git log --since="today 00:00" --pretty=format:"%h %an %s"` → commits reales con autor (para "Quién tocó qué").
2. Entrada de `CHANGELOG.md` bajo la fecha de hoy (si existe) → ya es un resumen humano, útil para "Avances".
3. `git status` + `git diff --stat` sobre cambios no commiteados → menciónalos como "en progreso" si parecen relevantes.
4. Conversación de la sesión actual del agente (si sigue viva) → para pendientes y bloqueos que aún no están en el repo.

Si no hay commits hoy y no hay archivos modificados hoy fuera de `.venv`/`.git`: **no mandar nada**, solo actualizar `last_checked_at` en el estado.

## Estado persistente: `.handoff_state.json`

Vive en la raíz del proyecto y **no se commitea** (añadirlo a `.gitignore` si aún no está). Formato:

```json
{
  "last_handoff_sent_date": "2026-04-24",
  "last_handoff_slack_ts": "1745500000.123456",
  "last_checked_at": "2026-04-24T19:42:00+02:00",
  "sent_by_machine": "marcos"
}
```

- `last_handoff_sent_date`: fecha (local) del último handoff publicado. Si es igual a hoy → no publicar otro.
- `last_handoff_slack_ts`: timestamp de Slack del mensaje, por si hay que editarlo.
- `sent_by_machine`: `"marcos"` o `"mario"`. Evita que ambos Claudes publiquen dos handoffs el mismo día. Si uno de los dos ya publicó, el otro no repite.

## Instalación de la tarea programada (una vez por máquina)

En Claude desktop / Cowork, el agente LLM debe registrar una **scheduled task** local con `mcp__scheduled-tasks__create_scheduled_task`:

- **taskId**: `bot-reservas-handoff`
- **cronExpression**: `59 23 * * *` (una sola ejecución al día, 23:59 Europe/Madrid)
- **prompt**: el contenido de `scripts/handoff_closing_prompt.md` de este repo (se mantiene versionado para que ambos Claudes ejecuten exactamente el mismo procedimiento).

Cuando Mario abra Claude en su ordenador por primera vez sobre este proyecto, le basta decirle:

> "Instala la tarea de cierre según `HANDOFF_PROTOCOL.md`."

Y el Claude de Mario leerá este doc + el prompt en `scripts/handoff_closing_prompt.md` y creará la misma scheduled task en su máquina.

## Qué NO hace el handoff

- **No commitea** automáticamente. Si detecta cambios sin commitear, los menciona como "en progreso" pero no hace `git add`/`commit`/`push`.
- **No manda DMs** a nadie. Solo publica en `#bot-reservas`.
- **No etiqueta (`@`)** a Marcos ni a Mario por defecto. Si alguno quiere mención explícita, que lo diga en el hilo.
- **No duplica**: si el estado dice que ya se mandó hoy, no manda otro aunque el trigger se vuelva a cumplir.

## Edge cases

- **Día sin commits pero con trabajo en progreso** (rama sucia): manda handoff igual, con los `git diff --stat` como fuente principal, anotando que está sin commitear.
- **Conflicto de máquinas**: si Marcos y Mario trabajan a la vez y ambos llegan a la ventana >3h al mismo tiempo, el primero en publicar escribe `last_handoff_sent_date` en su `.handoff_state.json`, pero el del otro no lo sabe (porque no se commitea). Mitigación: antes de publicar, el agente consulta los últimos 20 mensajes de `#bot-reservas` con `slack_read_channel`; si encuentra ya un mensaje que empiece por `*Handoff {hoy}` de hoy publicado por el bot del workspace, aborta y actualiza su propio estado local.
- **Fin de semana / festivo**: el trigger es igual. Si no hubo actividad, no manda nada (regla de "ha habido actividad hoy").
