# Panel de administración (CMS)

Un panel web para gestionar tus clientes (tenants), ver conversaciones,
consumo de tokens y coste por cliente. Va en el mismo proceso que el webhook
del bot — una sola deploy en Railway.

## Lo que añade al proyecto

- **`app/cms/`** — módulo del CMS (rutas, auth, templates Jinja).
- **`app/db.py`** — ahora incluye las tablas `tenants`, `services`,
  `token_usage`, `admin_users`.
- **`app/tenants.py`** — lee de la BD (fallback al YAML si la tabla está vacía).
- **`app/agent.py`** — captura `usage` de cada respuesta y lo persiste.
- **`app/migrate_yaml.py`** — seed inicial desde `tenants.yaml`.

## Cómo arrancarlo en local

```bash
# 1) Dependencias nuevas
pip install -r requirements.txt

# 2) Variables de entorno
cp .env.example .env
# Edita .env y asegúrate de rellenar:
#   ADMIN_EMAIL=tu@email.com
#   ADMIN_PASSWORD=una_contraseña_fuerte
#   SESSION_SECRET=<genera una con `python -c "import secrets; print(secrets.token_urlsafe(48))"`>

# 3) Migrar tu tenants.yaml actual a la BD
python -m app.migrate_yaml

# 4) Arrancar el servidor (webhook + CMS en el mismo proceso)
uvicorn app.main:app --reload --port 8000
```

Abre http://localhost:8000/admin/login y entra con las credenciales del `.env`.

## URLs

| Ruta                                    | Qué hace                         |
|-----------------------------------------|----------------------------------|
| `POST /tools/*`                         | Server tools que llama ElevenLabs|
| `GET  /admin/login`                     | Login del panel                  |
| `GET  /admin/dashboard`                 | Resumen y métricas globales      |
| `GET  /admin/clientes`                  | Listado de clientes + tokens     |
| `GET  /admin/clientes/new`              | Alta de cliente                  |
| `GET  /admin/clientes/{id}/{tab}`       | Detalle con pestañas             |
| `GET  /admin/facturacion`               | Desglose coste / plan / margen   |
| `GET  /admin/ajustes`                   | Estado de API keys y modelo      |

## Despliegue en Railway

1. Sube el repo a GitHub. Railway → *New project → Deploy from GitHub*.
2. En **Variables**, añade TODO lo que hay en tu `.env` + las nuevas:
   - `ADMIN_EMAIL`, `ADMIN_PASSWORD`, `SESSION_SECRET`
3. Monta un **Volume** en `/app/data` (o en la ruta de `DATABASE_URL`) para
   que `data.db` persista entre reinicios. Mismo volumen que usas para
   `.tokens/` de Google Calendar.
4. La primera vez que arranca crea automáticamente el usuario admin con las
   credenciales del `.env`. Si quieres rotar después: borra la fila en
   `admin_users` y vuelve a arrancar, o conecta por shell y haz
   `UPDATE admin_users SET password_hash = ...`.

## Seguridad

- Contraseñas hasheadas con **bcrypt** (`passlib`).
- Sesión firmada con **itsdangerous** + `SESSION_SECRET`, cookie `HttpOnly` y
  `SameSite=lax`. Dura 14 días.
- Panel solo vía HTTPS en producción (Railway te lo da por defecto).
- Si quieres más nivel de seguridad: poner el CMS detrás de Cloudflare Access
  o añadir 2FA (siguiente iteración).

## Cómo se genera el `system_prompt` del bot

- Si el tenant tiene `system_prompt_override` (pestaña **Personalización →
  Avanzado**), se usa tal cual.
- Si no, se **compone automáticamente** a partir de:
  - `assistant_name`, `assistant_tone`, `assistant_formality`, `assistant_emoji`
  - `assistant_greeting`, `assistant_fallback_phone`, `assistant_rules`
  - Catálogo de `services`
  - `business_hours`
  - Más un bloque fijo de reglas operativas.

Ver `app/db.py::render_system_prompt` para el template.

## Tracking de tokens

En `app/agent.py`, tras cada llamada a `client.chat.completions.create()`
guardamos un registro en `token_usage`:

```python
db.save_token_usage(
    tenant_id=...,
    model=settings.openai_model,
    input_tokens=usage.prompt_tokens,
    output_tokens=usage.completion_tokens,
    customer_phone=caller_phone,
)
```

Los precios están en `MODEL_PRICING_EUR` (actualiza cuando cambien).

## Iterar sobre el diseño

Los templates viven en `app/cms/templates/`. Están en Jinja2 + Tailwind
(CDN, sin build). Editar un `.html`, recargar el navegador y ya está.

- `_layout.html` — sidebar + topbar + macros (`avatar`, `status_badge`,
  `delta_badge`, `sparkline`, `bar_chart`).
- `partials/tab_*.html` — cada pestaña del detalle de cliente.

## Siguientes pasos naturales

1. Tabla `bookings` con cada tool_call de `crear_reserva` para poder mostrar
   reservas reales en `/admin/reservas`.
2. Chat de prueba en vivo contra el bot desde el propio panel (botón
   "Probar chat" en el detalle de cliente).
3. Exportar CSV de facturación mensual.
4. Alertas: webhook a Slack/Email cuando un cliente pase cierta cuota de
   tokens.
