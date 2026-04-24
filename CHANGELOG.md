# Changelog

Registro vivo de cambios publicados al remoto. Formato: sección por fecha, subsecciones por tipo de cambio. Ver convención completa en `CLAUDE.md`.

Entrada más reciente arriba.

---

## 2026-04-24

### Añadido

- Convención de actualización de `CHANGELOG.md` antes de cada push, documentada en el nuevo `CLAUDE.md`.
- Hook git opcional `.githooks/pre-push` que bloquea el push si los commits nuevos no tocan `CHANGELOG.md`.
- Script auxiliar `scripts/update_changelog.sh` para generar un borrador de entrada a partir de los commits no pusheados.

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
