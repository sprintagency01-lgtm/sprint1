# Migración de dominio → `sprintiasolutions.com` (2026-04-29)

Guía operativa de la migración del backend desde el subdominio interno
`web-production-98b02b.up.railway.app` al dominio canónico
**`sprintiasolutions.com`**, con ALIAS apex contra Railway.

> Estado al cerrar la guía: el custom domain ya está dado de alta en Railway
> (servicio `web` del proyecto `marvelous-charm`). Falta DNS, OAuth y rebote
> de webhooks externos. El subdominio `*.up.railway.app` queda activo como
> fallback durante un par de días para no romper integraciones legacy.

---

## 0) Pre-requisitos

- Acceso a Porkbun (registrar de `sprintiasolutions.com`).
- Acceso al proyecto Sprint en Google Cloud Console (mismo OAuth client ID
  que ya usa `app/oauth_web.py`).
- `RAILWAY_API_TOKEN` cargado en `.env` para mutar variables del servicio
  desde CLI o vía GraphQL v2.
- `TOOL_SECRET` y credenciales de ElevenLabs / Telegram disponibles en
  `.env` para reapuntar webhooks.

---

## 1) DNS en Porkbun

Registros que da Railway al añadir el custom domain:

| Tipo  | Host               | Valor                                                                                                  | TTL |
|-------|--------------------|--------------------------------------------------------------------------------------------------------|-----|
| ALIAS | (apex, vacío)      | `4p43tgc8.up.railway.app`                                                                              | 600 |
| TXT   | `_railway-verify`  | `railway-verify=10e13822169a8ee6153a98bf521df6dc111b01ddfcc630d6f4884e51111beecf`                      | 600 |

Pasos en `porkbun.com/account/domainsSpeedy`:

1. Entrar en `sprintiasolutions.com` → **DNS Records**.
2. **Borrar** los A/CNAME por defecto que Porkbun deja del parking.
3. Añadir el ALIAS apex con el host vacío (algunos UI lo llaman "@"). Si
   Porkbun rechaza ALIAS en apex (depende de la versión del panel), el
   fallback aceptado es CNAME `@` apuntando al mismo target — Railway
   tolera ambos.
4. Añadir el TXT de verificación con host `_railway-verify`.
5. Guardar y confirmar leyendo la lista final que ambos registros aparezcan.

---

## 2) Esperar propagación DNS

Loop hasta que `dig` resuelva al target Railway (puede tardar de 1 a 10 min):

```bash
while true; do
  RES=$(dig sprintiasolutions.com +short)
  echo "[$(date +%H:%M:%S)] $RES"
  echo "$RES" | grep -q "railway" && break
  sleep 60
done
```

También conviene verificar el TXT:

```bash
dig _railway-verify.sprintiasolutions.com TXT +short
```

Una vez resuelto, Railway emite el certificado Let's Encrypt en ~1-2 min
sin intervención. Si sigue en "issuing" pasados 10 min, comprobar en el
panel de Railway que ambos registros se ven verdes.

---

## 3) Google Cloud Console — OAuth redirect URI

1. `console.cloud.google.com` → proyecto **Sprint**.
2. **APIs & Services → Credentials**.
3. Editar el `OAuth 2.0 Client ID` que usa el bot
   (el mismo que tiene la URI `https://web-production-98b02b.up.railway.app/oauth/callback`).
4. En **Authorized redirect URIs** añadir:

   ```
   https://sprintiasolutions.com/oauth/callback
   ```

   **No quitar la antigua** todavía — la dejamos durante la ventana de
   convivencia para que tokens en proceso no se rompan.
5. Save.

---

## 4) Smoke test — alcance HTTP/SSL del dominio nuevo

Antes de cambiar variables del backend, comprobar que el custom domain
sirve la app y el certificado:

```bash
# 4.1 — health endpoint debe responder 200
curl -s -o /dev/null -w "%{http_code}\n" https://sprintiasolutions.com/health
# esperado: 200

# 4.2 — root devuelve el JSON del servicio
curl -s https://sprintiasolutions.com/ | jq
# esperado: {"ok":true,"service":"bot_reservas","version":"0.2.0"}

# 4.3 — admin redirige a /admin/login
curl -s -I https://sprintiasolutions.com/admin/dashboard | head -3
# esperado: HTTP/2 303  location: /admin/login

# 4.4 — certificado vivo y emisor Let's Encrypt
echo | openssl s_client -servername sprintiasolutions.com \
  -connect sprintiasolutions.com:443 2>/dev/null \
  | openssl x509 -noout -issuer -subject -dates
```

Si 4.1 da 502/503: el deploy del custom domain todavía no ha cogido el cert.
Esperar 60 s y reintentar. Si persiste, mirar logs del servicio en Railway.

---

## 5) Railway — `GOOGLE_REDIRECT_URI`

Servicio `web` del proyecto `marvelous-charm` → **Variables**.

| Variable                | Valor nuevo                                          |
|-------------------------|------------------------------------------------------|
| `GOOGLE_REDIRECT_URI`   | `https://sprintiasolutions.com/oauth/callback`       |

Railway redespliega automáticamente al guardar. El deploy dura 2-3 min.

Si se prefiere CLI:

```bash
railway variables --set GOOGLE_REDIRECT_URI=https://sprintiasolutions.com/oauth/callback
```

---

## 6) Smoke test post-redeploy — backend hablando con el dominio nuevo

Ya con el redeploy live, verificar los endpoints que firma `X-Tool-Secret`
y los pinta el agente de voz:

```bash
# 6.1 — personalization webhook (lo que ElevenLabs llama al inicio de la sesión)
curl -s -H "X-Tool-Secret: $TOOL_SECRET" -H "Content-Type: application/json" \
  -d '{"caller_id":"+34600000001","tenant_id":"pelu_demo"}' \
  https://sprintiasolutions.com/tools/eleven/personalization | jq

# 6.2 — consultar disponibilidad (sin LLM, directo al backend)
curl -s -H "X-Tool-Secret: $TOOL_SECRET" -H "Content-Type: application/json" \
  -d '{"fecha_desde_iso":"2026-04-30T15:00:00","fecha_hasta_iso":"2026-04-30T20:30:00","duracion_minutos":30,"peluquero_preferido":"","max_resultados":5}' \
  "https://sprintiasolutions.com/tools/consultar_disponibilidad?tenant_id=pelu_demo" | jq

# 6.3 — healthcheck del agente ElevenLabs
curl -s -H "X-Tool-Secret: $TOOL_SECRET" \
  "https://sprintiasolutions.com/_diag/elevenlabs/healthcheck?tenant_id=pelu_demo" | jq
```

Las tres respuestas deben tener `"ok": true` (o el equivalente) y código
200. Si 6.1 devuelve `tenant_name` distinto de `Peluquería Demo` o las
fechas vienen en el año equivocado, **parar** — el redeploy no ha cogido
las env vars correctas.

---

## 7) Reapuntar webhooks externos

### 7.1 ElevenLabs

```bash
python scripts/setup_elevenlabs_agent.py https://sprintiasolutions.com
```

El script reescribe los `url` de los 5 server tools y del personalization
webhook con la nueva base. Necesita `ELEVENLABS_API_KEY`,
`ELEVENLABS_VOICE_ID` y `TOOL_SECRET` en `.env`.

### 7.2 Telegram

```bash
python scripts/setup_telegram_bot.py https://sprintiasolutions.com
```

Llama `setWebhook` apuntando a `/telegram/webhook` y verifica con
`getWebhookInfo`. Si el bot no necesita webhook (canal apagado en este
tenant), saltar.

### 7.3 Twilio (voz por SIP)

El SIP trunk no apunta a HTTP, no hace falta tocarlo. Las llamadas
entrantes siguen llegando a ElevenLabs por SIP y de ahí a los webhooks
ya reapuntados en 7.1.

---

## 8) Verificación final manual (humano)

1. Llamada real al número del tenant. Ana debe arrancar a hablar < 1.5 s
   tras la última palabra del cliente, igual que antes.
2. Login en `https://sprintiasolutions.com/admin/login` con las
   credenciales de admin.
3. Probar el flujo OAuth de Google Calendar: `/oauth/start?tenant=pelu_demo`
   y completar el callback. El consent screen debe redirigir a
   `https://sprintiasolutions.com/oauth/callback` y volver al CMS.

---

## 9) Rollback plan

Si algo se rompe:

1. **DNS roto**: en Porkbun, restaurar los A/CNAME default y borrar los
   nuevos. Mientras propaga, el subdominio `*.up.railway.app` sigue vivo.
2. **OAuth roto**: revertir `GOOGLE_REDIRECT_URI` en Railway al valor
   antiguo `https://web-production-98b02b.up.railway.app/oauth/callback`
   y dejar la URI nueva en Google Cloud (no estorba).
3. **Webhooks rotos**: re-ejecutar `setup_elevenlabs_agent.py` y
   `setup_telegram_bot.py` pasando el subdominio Railway antiguo como
   argumento.

---

## 10) Cleanup posterior (cuando todo lleve 1-2 días estable)

- Quitar la URI antigua de Google Cloud Console.
- Actualizar referencias residuales al subdominio Railway en docs/snapshots
  legacy si aún molestan (no críticas — los snapshots son históricos).
- Decidir si `hola@sprint.agency` migra a `hola@sprintiasolutions.com` y
  actualizar la landing en consecuencia (ahora mismo se conserva el email
  antiguo a propósito).

---

**Responsable de la migración:** Marcos.
**Ventana:** 2026-04-29 sesión de tarde.
