# Instrucciones para agentes (Claude Code y similares)

Este documento establece convenciones de trabajo para cualquier agente LLM que opere sobre este repositorio. Leerlo completo al arrancar sesión. Aplica a todos los pushes al remoto.

## Contexto mínimo del proyecto

Backend FastAPI para bot de reservas por **voz** con Google Calendar. Desplegado en Railway (`web-production-98b02b.up.railway.app`). Multi-tenant, agente LLM configurable (OpenAI u Anthropic). El canal activo es voz vía ElevenLabs Conversational AI (SIP directo + webhooks de tools `/tools/*`).

El canal WhatsApp fue **retirado del producto en abril 2026** (commit `d9e1435`). Si ves referencias a Meta Cloud API, Twilio WhatsApp, webhooks `/whatsapp` o archivos `app/whatsapp.py` / `app/twilio_wa.py` / `app/voice.py` en cualquier doc o código, trátalas como histórico y no como parte del producto actual.

Documentos canónicos que complementan este fichero:

- `README.md` — puesta en marcha general (post-pivote).
- `START_HERE.md` — onboarding rápido.
- `DEPLOY_RAILWAY.md` — despliegue.
- `ELEVENLABS.md` — configuración del agente de voz.
- `PLAYBOOK_CLIENTE_NUEVO.md` — alta de tenants.
- `CMS_README.md` — panel de administración.
- `BOT_NUEVO_CONFIG.md` — **config canónica de baja latencia** que debe aplicarse a cualquier bot nuevo (post-ronda 7). Valores de LLM, TTS, turn, tools, personalization webhook. Antes de crear un tenant nuevo, leerlo.
- `HANDOFF_YYYY-MM-DD.md` — handoffs técnicos densos de sesiones puntuales.
- `HANDOFF_PROTOCOL.md` — protocolo automático de cierre de jornada a `#bot-reservas`.
- `scripts/handoff_closing_prompt.md` — prompt que ejecuta la scheduled task de cierre.
- `CHANGELOG.md` — registro vivo de cambios por push (ver sección siguiente).

## Regla obligatoria: actualizar `CHANGELOG.md` antes de cada push

Antes de cada `git push` al remoto, el agente o desarrollador **debe** añadir una entrada en `CHANGELOG.md` resumiendo los commits nuevos que se van a publicar.

### Procedimiento

1. Antes de pushear, ejecuta `scripts/update_changelog.sh` para generar el borrador de entrada (lista los commits que no están aún en `origin/main`).
2. Revisa el borrador, edítalo (resume en lenguaje humano, agrupa por tema, destaca breaking changes o cambios de env vars).
3. Inserta la entrada al principio de `CHANGELOG.md` bajo la cabecera de fecha del día (formato `## 2026-04-24`).
4. `git add CHANGELOG.md` y commitea — puede ser un commit separado tipo `docs(changelog): update for push YYYY-MM-DD` o, si aún no has pusheado, un amend al último commit.
5. `git push`.

### Formato de entrada

Cada push genera una entrada bajo la fecha del día. Estructura:

```markdown
## 2026-04-24

### Añadido
- Descripción corta del feature nuevo. (commit `abc1234`)

### Cambiado
- Descripción del cambio en comportamiento existente. (commit `def5678`)

### Corregido
- Bug resuelto y su impacto. (commit `9012ghi`)

### Env / despliegue
- Variables de entorno nuevas, cambios en Railway, migraciones. Destacar siempre.

### Breaking
- Cambios incompatibles. Destacar siempre. Si no hay, omitir la subsección.
```

Si el push solo arregla un typo o es trivial, basta una línea bajo la fecha sin subsecciones.

### Qué NO va en CHANGELOG

- Cambios internos sin impacto visible (renames privados, refactors sin efecto externo) salvo que afecten a collaborators.
- Secretos, tokens, URLs con credenciales.
- Detalles internos de debug — para eso está el commit message.

## Hook git de apoyo (opcional pero recomendado)

El repositorio incluye `.githooks/pre-push`. Para activarlo en tu clon local, una sola vez:

```bash
git config core.hooksPath .githooks
```

El hook bloquea el push si detecta commits nuevos pero `CHANGELOG.md` no ha sido tocado en ese rango. Para saltarlo en casos excepcionales: `git push --no-verify` (desaconsejado).

## Handoffs técnicos largos

Los `HANDOFF_YYYY-MM-DD.md` siguen teniendo sentido para cierres de sesión grandes donde haga falta pasar contexto denso a otro agente/humano. `CHANGELOG.md` y handoffs son complementarios, no sustitutivos: el changelog es la vista lineal para cualquier lector, el handoff es el briefing profundo de un momento concreto.

## Cierre de jornada automático a Slack (obligatorio)

Este repo tiene un **protocolo de handoff diario** a `#bot-reservas` (Slack ID `C0AU8MGLKU5`) definido en `HANDOFF_PROTOCOL.md`. Aplica a cualquier agente LLM — el Claude de Marcos y el de Mario — que trabaje sobre este repositorio.

Resumen para agentes que abren sesión por primera vez:

1. Lee `HANDOFF_PROTOCOL.md` entero.
2. Si el usuario dice algo como "manda el handoff", "cierra la jornada" o "resumen a Slack", ejecuta el procedimiento manualmente siguiendo `scripts/handoff_closing_prompt.md`.
3. La primera vez que abras Claude en esta máquina sobre este repo, instala la scheduled task local:
   - `taskId`: `bot-reservas-handoff`
   - `cronExpression`: `*/30 * * * *`
   - `prompt`: el contenido literal de `scripts/handoff_closing_prompt.md`
   - Se registra con `mcp__scheduled-tasks__create_scheduled_task`.
4. El estado local vive en `.handoff_state.json` (ignorado por git). Para evitar handoffs duplicados, antes de publicar el agente chequea los últimos mensajes de `#bot-reservas`.

Trigger: >3h sin actividad en el repo + hay actividad hoy + no se ha publicado ya. Contenido: bullets de avances, quién tocó qué, bloqueos/pendientes, próximos pasos. Formato completo en `HANDOFF_PROTOCOL.md`.

## Otras convenciones relevantes

- Mensajes de commit en español, formato `tipo(scope): descripción` tipo conventional commits (`feat`, `fix`, `docs`, `diag`, `chore`, `refactor`, `test`).
- Zona horaria canónica: `Europe/Madrid`. Nunca asumir UTC naive.
- Tenants: nunca commitear `tenants.yaml` real, solo `tenants.yaml.example`.
- `.env` jamás se commitea.
