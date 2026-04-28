# Sync CMS → Google Sheets — setup

Última actualización: 2026-04-28.

Este documento explica cómo dejar el sync del CMS al Sheet operativo. Sólo
hay que hacerlo una vez por instancia. Después, cada cambio en el CMS aparece
en la pestaña **Tenants** del Sheet de onboarding sin tocar nada.

## Qué hace el sync

- Cada vez que el CMS commitea un cambio en `tenants`, `services` o
  `equipo` (cualquier endpoint `/admin/clientes/...`), un listener de
  SQLAlchemy detecta el `tenant_id` afectado y dispara un push a Sheets.
- El push corre en un ThreadPoolExecutor para no bloquear la respuesta
  HTTP del CMS.
- El sync es **unidireccional**: lo que escribas en el Sheet se sobrescribe
  la próxima vez que toques ese tenant en el CMS.
- Si las env vars no están configuradas, todo el módulo se queda en no-op
  silencioso. La app sigue arrancando y funcionando.

## Pasos (10-15 min, una vez)

### 1. Crear el Form + Sheet con Apps Script

Si aún no has corrido `scripts/crear_form_onboarding.gs`, sigue las
instrucciones de su cabecera. El script crea automáticamente el Sheet con
una pestaña **Respuestas** (la del Form) y otra **Tenants** vacía con
cabecera, lista para que el backend escriba.

Apunta el `GOOGLE_SHEETS_ID` que el script imprime al final del log: es la
parte larga del URL del Sheet (lo que va entre `/d/` y `/edit`).

### 2. Crear un Service Account en Google Cloud

1. Entra a https://console.cloud.google.com/.
2. Selecciona el proyecto que ya usamos para Google Calendar (mismo donde
   están las credenciales OAuth). Si quieres aislarlo, puedes crear uno
   nuevo, pero reutilizar es lo lógico.
3. Menú → **APIs & Services → Library**. Busca "Google Sheets API" y
   habilítala.
4. Menú → **APIs & Services → Credentials → Create credentials → Service
   account**.
   - Nombre: `bot-reservas-sheets-sync`.
   - Descripción: "Empuja cambios del CMS al Sheet de onboarding".
   - No hace falta darle roles a nivel de proyecto. Skip.
   - Skip también el paso de "Grant users access".
5. Una vez creado, click sobre el Service Account → pestaña **Keys → Add
   Key → Create new key → JSON**.
6. Se descarga un `.json`. Guárdalo (no lo subas a git).

### 3. Compartir el Sheet con el Service Account

Abre el JSON. Busca el campo `client_email` — es algo tipo
`bot-reservas-sheets-sync@<proyecto>.iam.gserviceaccount.com`.

En el Google Sheet creado por el Apps Script:

1. Click "Compartir".
2. Pega ese email.
3. Permiso **Editor**.
4. Desmarca "Notificar a la persona" (es un robot, no le va a llegar nada).
5. Compartir.

### 4. Subir credenciales a Railway

En el dashboard de Railway, servicio del bot, pestaña **Variables**:

- `GOOGLE_SHEETS_ID` = el ID que apuntaste en el paso 1.
- `GOOGLE_SERVICE_ACCOUNT_JSON` = el contenido **entero** del JSON
  descargado, **en una sola línea**. Railway acepta el valor con saltos
  de línea pero gspread espera el JSON parseable; si lo pegas tal cual
  desde el archivo, la mayoría de las veces funciona porque Railway
  preserva los `\n` literales del JSON. Si te da problemas, conviértelo
  a una línea con:

  ```bash
  python -c "import json,sys; print(json.dumps(json.load(open(sys.argv[1]))))" /ruta/al/sa.json
  ```

  y pega el resultado.

Railway re-despliega el servicio automáticamente al guardar variables.

### 5. Forzar un primer dump (opcional)

Para llenar el Sheet con todos los tenants existentes sin esperar al
próximo cambio del CMS, abre una shell en Railway o en local y haz:

```bash
python -c "from app.sheets_sync import push_all_tenants; print(push_all_tenants())"
```

Devuelve el número de tenants empujados. Verifica en el Sheet que la
pestaña **Tenants** se ha llenado.

## Verificación rápida

Después del setup, edita un tenant en el CMS (por ejemplo, cambia el
`assistant_name` de `pelu_demo` y guarda). En menos de 2 segundos la fila
correspondiente del Sheet debe reflejar el cambio.

Si no se actualiza:

1. Mira los logs de Railway. Busca líneas `sheets_sync` o `Sheets sync`.
   - "deshabilitado (faltan…)" → revisa env vars.
   - "No se pudo abrir el Sheet" → el Service Account no tiene permiso.
2. Comprueba que el email del SA está como Editor en el Sheet.
3. Comprueba que el ID del Sheet en Railway es el correcto (parte larga
   del URL).

## Qué columnas se sincronizan

Definidas en `HEADERS` de `app/sheets_sync.py`. Si quieres añadir o
quitar columnas:

1. Edita la lista `HEADERS` en `app/sheets_sync.py`.
2. Edita `tenantsHeaders` en `scripts/crear_form_onboarding.gs` para que
   los Sheets nuevos arranquen con la cabecera correcta.
3. Edita `_tenant_to_row()` para construir la fila acorde.
4. En Sheets ya existentes, borra la fila 1 a mano (o llama a
   `_get_worksheet()._sheet.update("A1:U1", [HEADERS])`) — el módulo
   sobrescribe la cabecera al detectar mismatch al arrancar.

## Coste y rate limits

Google Sheets API gratis, 60 reads/min y 60 writes/min por usuario por
proyecto. Para una operación normal del CMS (pocos cambios al día) es más
que de sobra. El executor tiene 2 workers para evitar romper el límite si
alguien hace un `push_all_tenants()` con 100+ tenants.

## Cómo desactivar temporalmente

Borra `GOOGLE_SHEETS_ID` o `GOOGLE_SERVICE_ACCOUNT_JSON` de Railway.
La app sigue funcionando, los listeners siguen registrados, pero cada
push detecta que no está configurado y devuelve sin hacer nada.
