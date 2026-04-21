# Desplegar el bot + CMS en Railway

Guía paso a paso. Tiempo estimado: **10 minutos**.

Partes:
1. Subir el código a GitHub (1 min).
2. Apuntar tu servicio Railway al nuevo repo (2 min).
3. Añadir variables de entorno nuevas (2 min).
4. Crear volumen persistente (1 min).
5. Primer deploy + migración (3 min).
6. Login en el CMS (1 min).

---

## 1) Subir a GitHub

### Opción A — con GitHub CLI (lo más rápido)

```bash
# Solo la primera vez:
brew install gh      # Mac
gh auth login        # login por navegador

# En la raíz del proyecto descomprimido:
cd bot_reservas_whatsapp
chmod +x deploy.sh
./deploy.sh bot-reservas-whatsapp
```

### Opción B — a mano

1. Ve a <https://github.com/new> → crea un repo **privado** llamado `bot-reservas-whatsapp`.
   - NO marques "Add a README" ni "Add .gitignore" ni "license" — crea vacío.
2. En tu terminal, dentro de la carpeta del proyecto:

```bash
git init -b main
git add .
git commit -m "deploy: bot + CMS v0.2"
git remote add origin git@github.com:<TU_USUARIO>/bot-reservas-whatsapp.git
git push -u origin main
```

---

## 2) Apuntar tu servicio Railway al nuevo repo

Tu proyecto Railway ya existe. Dos caminos según cómo lo montaste:

### Si tu servicio actual ya está conectado al repo antiguo

1. Railway → tu proyecto → click en el servicio (normalmente "web" o el nombre del repo viejo).
2. **Settings → Source** → **Disconnect**.
3. **Connect Repo** → elige el nuevo `bot-reservas-whatsapp`.
4. Rama: `main`. Root directory: vacío (raíz). Branch deploy automático: ON.

### Si prefieres un servicio nuevo (sin tocar el viejo)

1. Railway → tu proyecto → **New → GitHub Repo** → elige `bot-reservas-whatsapp`.
2. Te creará un servicio nuevo. Puedes borrar el antiguo después si ves que todo va bien.

---

## 3) Variables de entorno

En Railway → tu servicio → pestaña **Variables**. Pega en bloque con *Raw Editor* (hay un
botón *"Raw Editor"*). Sustituye los valores que empiezan por `<...>`:

```
# === WhatsApp Cloud API (Meta) ===
WHATSAPP_VERIFY_TOKEN=<mismo_que_tenías_antes>
WHATSAPP_ACCESS_TOKEN=<token_permanente_o_temporal>
WHATSAPP_PHONE_NUMBER_ID=<tu_phone_number_id>
WHATSAPP_APP_SECRET=<app_secret_de_meta>

# === OpenAI ===
OPENAI_API_KEY=<sk-...>
OPENAI_MODEL=gpt-4o-mini

# === ElevenLabs (opcional, voz) ===
ELEVENLABS_API_KEY=
ELEVENLABS_VOICE_ID=

# === Google Calendar ===
GOOGLE_CLIENT_ID=<tu_client_id>
GOOGLE_CLIENT_SECRET=<tu_client_secret>
GOOGLE_REDIRECT_URI=https://<TU_DOMINIO_RAILWAY>.up.railway.app/oauth/callback
DEFAULT_CALENDAR_ID=primary
DEFAULT_TIMEZONE=Europe/Madrid

# === App ===
# IMPORTANTE: la BD y los tokens OAuth viven en el Volume (/app/data).
# 4 barras en DATABASE_URL = ruta absoluta Unix.
DATABASE_URL=sqlite:////app/data/data.db
TOKENS_DIR=/app/data/.tokens
TENANTS_FILE=./tenants.yaml
LOG_LEVEL=INFO

# === CMS (nuevas variables) ===
ADMIN_EMAIL=<tu_email@dominio.com>
ADMIN_PASSWORD=<pon_una_contraseña_fuerte_aquí>
SESSION_SECRET=<pega_el_secreto_generado_abajo>
```

### SESSION_SECRET generado para ti

Pega esto tal cual (es único y seguro; si quieres otro, corre
`python -c "import secrets; print(secrets.token_urlsafe(48))"`):

```
ia1A4MVGu-9DupFwS_bmUTGuanzwb5gUU6RdEmI7UlNl01ZMU3oqcuUep3p2xaeH
```

> ⚠️ **No vuelvas a cambiar `SESSION_SECRET`** después del primer login. Si lo cambias,
> todas las sesiones activas se invalidarán (tendrás que loguear otra vez — no
> pasa nada grave, pero por si acaso).

---

## 4) Volumen persistente

Sin volumen, `data.db` y los tokens de Google se borran en cada redeploy.

1. Railway → tu servicio → pestaña **Volumes** → **New Volume**.
2. **Mount path**: `/app/data`
3. **Size**: 1 GB (más que suficiente).
4. Click **Create**.

El volumen persistirá:
- `data.db` → porque `DATABASE_URL=sqlite:////app/data/data.db`
- Tokens OAuth de Google → porque `TOKENS_DIR=/app/data/.tokens`

> `calendar_service.py` ya lee `TOKENS_DIR` del entorno, así que con esas dos
> variables apuntando al volumen, **todo lo persistente está cubierto**.

---

## 5) Primer deploy + migración

Railway detecta el push y despliega automáticamente. Dura 2-3 minutos.

### Migrar `tenants.yaml` a la BD (una sola vez)

Al ser la primera vez, la tabla `tenants` está vacía. Tienes dos opciones:

**Opción A — dejar que lo haga el fallback del código (fácil):**
El código detecta que la tabla está vacía y lee del YAML. No necesitas migrar —
pero entonces tampoco puedes editar los tenants desde el CMS (solo leerlos).

**Opción B — migrar formalmente (recomendado):**
Railway CLI o shell. Con la CLI:

```bash
# Solo la primera vez:
brew install railway     # Mac  (o: npm install -g @railway/cli)
railway login
railway link             # selecciona tu proyecto y servicio

# Ejecutar la migración:
railway run python -m app.migrate_yaml
```

O desde el botón **"Shell"** de Railway (si tu plan lo permite):

```bash
python -m app.migrate_yaml
```

Salida esperada: `[ok] Tenants insertados: 1  actualizados: 0`

---

## 6) Login en el CMS

Railway te habrá dado un dominio tipo `https://bot-reservas-whatsapp-production.up.railway.app`.

1. Ve a `https://<tu-dominio>/admin/login`
2. Email: el que pusiste en `ADMIN_EMAIL`
3. Password: el que pusiste en `ADMIN_PASSWORD`

Si todo va bien, verás el dashboard.

### Si el login falla

Mira los logs del servicio. Si ves:

> `ADMIN_PASSWORD no configurado. No se ha creado el usuario admin.`

Significa que la variable se quedó vacía. Ponla y haz Redeploy.

Si ves `404 /admin/login`, el servicio aún está arrancando — espera 30 seg.

---

## 7) Actualizar el webhook de Meta

El dominio de Railway no cambia salvo que lo muevas. Si es la primera vez que conectas
Meta a este dominio:

1. Panel Meta → tu app → **WhatsApp → Configuration → Webhook**.
2. Callback URL: `https://<tu-dominio>/whatsapp`
3. Verify Token: el valor de `WHATSAPP_VERIFY_TOKEN` en Railway.
4. Suscribir al evento `messages`.

---

## Verificaciones finales

```bash
# 1. Healthcheck público
curl https://<tu-dominio>/
# Debe responder: {"ok":true,"service":"bot_reservas","version":"0.2.0"}

# 2. CMS devuelve redirect a login
curl -I https://<tu-dominio>/admin/dashboard
# HTTP/1.1 303 See Other  location: /admin/login

# 3. Webhook responde verify
curl "https://<tu-dominio>/whatsapp?hub.mode=subscribe&hub.verify_token=<tu_verify_token>&hub.challenge=test"
# Debe responder: test
```

---

## Rotar la contraseña de admin

Desde la shell de Railway (o localmente con la misma BD):

```python
from passlib.hash import bcrypt
from sqlalchemy.orm import Session
from app import db
with Session(db.engine) as s:
    u = s.query(db.AdminUser).first()
    u.password_hash = bcrypt.hash("nueva_contraseña_segura")
    s.commit()
```

O más simple: borra la fila y pon `ADMIN_PASSWORD=<nueva>` en Railway → al reiniciar
se recrea.

---

## Troubleshooting

| Síntoma                              | Causa probable                    | Solución                                                               |
|--------------------------------------|-----------------------------------|------------------------------------------------------------------------|
| 500 en `/admin/dashboard`            | BD vacía o ruta incorrecta        | Verificar `DATABASE_URL` (4 barras) + ejecutar `migrate_yaml`          |
| data.db se borra en cada deploy      | Volume no montado en `/app/data`  | Crear el Volume en `/app/data` (paso 4)                                |
| Login OK pero tenants vacíos         | No migraste YAML → DB             | `railway run python -m app.migrate_yaml`                               |
| Bot no responde por WhatsApp         | Webhook mal conectado             | Re-verificar webhook en panel Meta (paso 7)                            |
| Error "SESSION_SECRET no configurado"| Variable no definida              | Añadirla en Railway → redeploy                                         |

---

**Eso es todo.** Cualquier duda que tengas durante el proceso, dímela y te ayudo con el paso concreto en que estés atascado.
