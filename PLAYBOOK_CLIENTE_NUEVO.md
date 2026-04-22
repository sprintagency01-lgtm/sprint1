# Playbook de alta de cliente nuevo — Bot de reservas por voz

Última actualización: 22/04/2026, después del primer demo completo con pelu_demo + Ana + Mario/Marcos.

Este documento recoge todo lo aprendido setup-eando el primer cliente de punta a punta:
arquitectura, runbook paso a paso, bugs encontrados con su causa raíz, y la checklist que
evita repetirlos. La idea es que un cliente nuevo se pueda montar en ~45 min sin
redescubrir problemas.

---

## 1. Piezas del sistema y cómo encajan

```
  Cliente ─► Twilio número ─► ElevenLabs Agent (Ana) ──┐
                                                       │  tool calls HTTPS
                                                       ▼
                                          Railway: FastAPI /tools/*
                                                       │
                                    ┌──────────────────┼──────────────────┐
                                    ▼                  ▼                  ▼
                              SQLite (CMS)        tenants.yaml     Google Calendar
                              tenants, leads,      peluqueros,     (freeBusy, events)
                              token_usage          days_trabajo
```

Cada cliente (`tenant`) tiene:
- Una fila en la tabla `tenants` (creada vía CMS o el flow OAuth de landing).
- Un bloque en `tenants.yaml` con los peluqueros y sus calendarios Google.
- Un token OAuth en `/app/data/.tokens/{tenant_id}.json` (Railway volume).
- Un agente en ElevenLabs con prompt + 4 herramientas webhook hacia Railway.
- Un número Twilio (o WhatsApp) apuntando al agente.

Reglas clave:
- La BD manda para servicios, precios, horario y prompt base.
- El YAML manda para peluqueros (hasta que el CMS tenga UI para editarlos).
- `load_tenants()` en `app/tenants.py` fusiona los dos: BD + `peluqueros` del YAML por id.

---

## 2. Runbook: dar de alta un cliente nuevo

### 2.1 Prerrequisitos

- Acceso al repo https://github.com/sprintagency01-lgtm/sprint1
- Acceso al panel CMS (`/admin`) de la instancia Railway.
- La cuenta Google del cliente (o Sprintagency como dueño del OAuth si es multi-negocio).
- Credenciales ElevenLabs (`EL_KEY`) y acceso al panel ElevenLabs.
- Número de teléfono disponible (Twilio o nativo ElevenLabs).

### 2.2 Paso 1 — Crear tenant en CMS

En `/admin/clientes/new` rellena:
- id (slug corto y estable, p.ej. `pelu_acme`)
- nombre legal
- timezone (por defecto Europe/Madrid)
- teléfono de contacto humano (el 910 000 000 de fallback)

Después entra en cada pestaña y configura:
- **Horarios**: MARCA SIEMPRE los días reales con el horario real. No dejes el default
  porque el default es 09:00–20:00 L–V con sábado cerrado y muchos negocios abren
  sábados o a otra hora. Esto fue causa de un bug (§4.3).
- **Servicios**: nombre, duración en minutos, precio. La duración es crítica porque se
  usa para buscar huecos.
- **Personalización**: nombre del asistente (p.ej. Ana), tono, teléfono de fallback.

### 2.3 Paso 2 — Autorizar Google Calendar

Desde el propio panel o vía `/oauth/start?tenant_id={id}`:
1. Abrir el link de autorización.
2. Login con la cuenta Google del cliente.
3. Aceptar scopes `calendar.events` + `calendar.readonly` (necesario para freeBusy).
4. El callback guardará `/.tokens/{tenant_id}.json`.

**IMPORTANTE**: si vas a usar varios calendarios (uno por peluquero), esos calendarios
DEBEN estar compartidos con la cuenta OAuth como mínimo con permiso "Ver detalles" (o
"Editar" si el bot tiene que crear/mover eventos en ellos). Si no, el freeBusy devolverá
"notFound" y nadie tendrá huecos.

### 2.4 Paso 3 — Añadir peluqueros al YAML

Edita `tenants.yaml` y añade un bloque bajo el id del tenant:

```yaml
tenants:
  - id: pelu_acme
    peluqueros:
      - nombre: "Laura"
        calendar_id: "xxxxx@group.calendar.google.com"
        dias_trabajo: [0, 1, 2, 3, 4, 5]  # lunes a sábado
      - nombre: "David"
        calendar_id: "yyyyy@group.calendar.google.com"
        dias_trabajo: [3, 4]  # jueves y viernes
```

`dias_trabajo` usa weekday de Python: **0=lunes … 6=domingo**.

Commit + push. Railway re-despliega solo.

**Si no hay peluqueros múltiples** (salón con un único profesional), simplemente no
pongas el bloque `peluqueros` en el YAML. Caerá al modo "calendario único" usando el
`calendar_id` del tenant en la BD, que es lo correcto en ese caso.

### 2.5 Paso 4 — Crear el agente en ElevenLabs

1. Duplica el agente "Ana — Peluquería Ejemplo" (o crea uno nuevo).
2. LLM: **`gemini-2.5-flash`**. NO uses `gemini-2.5-flash-lite`: a temperatura 0 se
   salta los tool calls y acaba inventándose los huecos (§4.6).
3. Pega el prompt plantilla (ver §3). Sustituye los nombres del negocio, peluqueros y
   horario.
4. Copia las 4 herramientas webhook. Para cada una:
   - URL: `https://{tu-railway-domain}/tools/{endpoint}?tenant_id={id_del_tenant}`
   - Header: `X-Tool-Secret: {TOOL_SECRET del .env de Railway}`
   - Header: `Content-Type: application/json`
   - Method: POST
5. Schemas (copiar tal cual):
   - `consultar_disponibilidad`: `fecha_desde_iso`, `fecha_hasta_iso`, `duracion_minutos`,
     `peluquero_preferido` (opcional), `max_resultados` (opcional)
   - `crear_reserva`: `titulo`, `inicio_iso`, `fin_iso`, `peluquero` [required];
     `telefono_cliente`, `notas` [opcional — **NO poner en required**, §4.4]
   - `buscar_reserva_cliente`: `telefono_cliente` [required], `dias_adelante` (opcional)
   - `mover_reserva`: `event_id`, `nuevo_inicio_iso`, `nuevo_fin_iso` [required]
   - `cancelar_reserva`: `event_id` [required]

### 2.6 Paso 5 — Reasignar número a ese agente

Si ya tenías un número ElevenLabs, PATCH la asignación:

```bash
curl -X PATCH "https://api.elevenlabs.io/v1/convai/phone-numbers/$PHONE_ID" \
  -H "xi-api-key: $EL_KEY" -H "Content-Type: application/json" \
  -d "{\"agent_id\":\"$NEW_AGENT_ID\"}"
```

### 2.7 Paso 6 — Smoke test antes de dárselo al cliente

Checklist obligatoria (§7). Con un tenant que aún no tiene huecos reales ocupados,
haz al menos 4 llamadas distintas y verifica todos los paths:

- Reserva simple (cualquier peluquero).
- Reserva pidiendo un peluquero concreto.
- Reserva pidiendo un peluquero que no trabaja ese día.
- Cancelación por teléfono.
- Cliente se niega a dar teléfono (§4.4).

Verifica también que los eventos aparecen realmente en el calendario del peluquero
correcto y que el título sigue el formato `"Servicio — Nombre (con Peluquero)"`.

---

## 3. Prompt plantilla de Ana (y por qué está así)

Está en `tenants.yaml` para el tenant y también en el agente de ElevenLabs. Son dos
copias: la de ElevenLabs es la que se usa al hablar; la del YAML existe por si en el
futuro reconstruimos el prompt desde el CMS. **Mantén las dos en sync manualmente**
hasta que automatices (TODO).

Lecciones de diseño — respeta estas reglas o el agente se vuelve raro:

1. **Una pregunta por turno.** Versiones anteriores encadenaban 3 preguntas ("qué
   servicio, para cuándo, y tu nombre"). El cliente responde solo a la última y hay
   que reiniciar el flujo. El prompt actual marca esto como regla dura.
2. **El nombre va AL FINAL.** Pedir el nombre al principio es lo más antinatural posible
   en España. Ir servicio → cuándo → peluquero → huecos → teléfono → nombre suena humano.
3. **Muletillas prohibidas.** "Un momento, estoy comprobando", "déjame mirar": esto mete
   pausas artificiales y el usuario cree que colgó. Hay que decirle al LLM que llame la
   herramienta DIRECTA y deje al sistema gestionar el silencio (ElevenLabs tiene voz de
   typing/filler integrada).
4. **Nada de ISO.** "A las diecisiete treinta", no "a las 17:30". Y desde luego no
   "dos puntos". Hay un ejemplo explícito en el prompt.
5. **Nada de listas con guiones, emojis ni símbolos.** Ana habla, no escribe.
6. **Ofrece máximo 3 huecos, uno a uno.** Si das 6 opciones lineales, el cliente se
   pierde. Si das uno al tiempo con "¿te viene bien?", la conversación fluye.
7. **Peluqueros específicos**: si sólo uno trabaja ese día, Ana lo dice y ofrece
   alternativa (`Marcos solo los miércoles. ¿Te vale Mario o prefieres un miércoles?`).
8. **Fallback suave en errores**: si una tool falla, no reintentar. Decir "ha habido
   un problema, llama al 910 000 000" y cerrar. Reintentar en bucle genera llamadas
   infinitas con el mismo error.

Ver archivo `/tmp/ana_prompt.txt` en el workspace local — esa es la última versión que
se ha PATCHeado al agente.

---

## 4. Bugs encontrados y su fix

### 4.1 `no such column: tenants.kind` en Railway

**Síntoma**: todos los `/tools/*` devolvían HTTP 500. Ana decía "ha habido un
problema" y colgaba.

**Causa**: se añadió la columna `kind` (lead/contracted) al modelo SQLAlchemy, pero
`Base.metadata.create_all()` solo crea tablas nuevas, no altera tablas existentes.
La tabla `tenants` en producción venía de antes de ese cambio.

**Fix** (commit `0e2f130`): en `app/db.py` al arrancar se ejecuta una micro-migración
idempotente que inspecciona `PRAGMA table_info(tenants)` y añade `kind` si falta.
Hay una lista `migrations: list[tuple[str, str, str]]` para extender con más columnas
en el futuro — siempre en forma `(tabla, columna, DDL)` y siempre idempotente.

**Regla permanente**: **cada vez que añadas una columna a un modelo existente**, añade
también la migración correspondiente a la lista en `_auto_migrate_sqlite()` en el
mismo commit. No fiarse de `create_all`.

### 4.2 Freebusy en UTC vs hora local

**Síntoma**: los huecos devueltos estaban corridos 1–2 horas. El cliente pedía "las 10"
y le ofrecían 9:00 o 11:00.

**Causa**: `datetime.isoformat() + "Z"` enviaba datetimes naive como si fueran UTC,
pero el negocio es Europe/Madrid (UTC+1/+2). freeBusy los interpretaba literalmente
en UTC.

**Fix** (commit `408cd9b`): nueva helper `_ensure_local_tz()` en `calendar_service.py`
que normaliza al huso configurado. Todas las funciones de Google Calendar usan esta
helper y **nunca** añaden `"Z"` al iso string.

**Regla permanente**: **siempre** `_ensure_local_tz(dt)` antes de mandar a Google.
**Nunca** concatenar `"Z"` a isoformat.

### 4.3 Horario del tenant mal (09:00–20:00 vs real 09:30–20:30)

**Síntoma**: los huecos aparecían 30 min antes de la apertura real del salón y
nunca en sábado.

**Causa**: el tenant `pelu_demo` se creó desde el flow OAuth de la landing, que usa
los valores por defecto del CMS (09:00–20:00 L–V, sábado y domingo cerrado). La
peluquería real abre 09:30–20:30 incluido sábado. Nadie tocó el CMS para corregirlo.

**Fix inmediato** (Twilio): actualizar vía CMS o via SQL. Hubo un endpoint temporal
`/_diag/fix_pelu_demo_hours` (ya eliminado en `55c1286`) que normalizaba.

**Fix en `listar_huecos_por_peluqueros`** (commit `cc8d7db`): además de respetar el
horario del negocio, ahora respeta la ventana intra-día pedida por el cliente. Antes,
aunque pidieras "por la tarde 15:00–20:30", se iteraba de 09:00 a 20:00 igualmente.
Ahora se toma `max(apertura, fecha_desde)` y `min(cierre, fecha_hasta)` dentro de cada
día.

**Regla permanente**: al dar de alta un cliente, ENTRA siempre en la pestaña de
horarios y configúralos a mano antes del primer test. No confiar en los defaults.

### 4.4 HTTP 422 cuando el cliente no quiere dar teléfono

**Síntoma**: cliente dice "no te dejo el teléfono". Ana pasa `telefono_cliente: null`
a `crear_reserva`. Railway devuelve 422 ("value is not a valid string"). Ana cierra
con el mensaje de error.

**Causas combinadas**:
- En `app/eleven_tools.py`, `CrearReq.telefono_cliente` era `str` (required).
- En la definición de la tool en ElevenLabs, `telefono_cliente` estaba en la lista
  `required` del JSON schema.

**Fix backend** (commit `55c1286`):
- Modelo Pydantic: `telefono_cliente: str | None = None`.
- Se normaliza a cadena vacía antes de escribir en el calendario.

**Fix ElevenLabs**: PATCH del tool schema de `crear_reserva` quitando
`telefono_cliente` de la lista `required`. (Script en §6.)

**Regla permanente**: cualquier parámetro que el cliente pueda razonablemente NO
facilitar (teléfono, peluquero, notas, nombre si responde "como quieras") debe ser
opcional en ambos lados: el modelo Pydantic y el schema de ElevenLabs. Si está
"required" solo en uno de los dos, habrá 422 en el peor momento.

### 4.5 Peluqueros no aparecían en los huecos (caída a single-calendar)

**Síntoma**: `consultar_disponibilidad` devolvía huecos sin campo `peluquero`. Ana
ofrecía "a las diez" en lugar de "a las diez con Mario". El cliente no podía elegir.

**Causa**: `Tenant.to_dict()` de la BD no incluye `peluqueros` (no existe esa columna).
Y `load_tenants()` solo leía el YAML como fallback cuando la BD estaba VACÍA. Como la
BD tenía `pelu_demo`, el YAML nunca se consultaba → huecos sin peluquero → se usaba el
path `listar_huecos_libres` que no distingue.

**Fix** (commit `bccf01c`): `load_tenants()` y `get_tenant()` enriquecen ahora los
dicts de BD con los campos del YAML que sólo viven allí (`_YAML_ONLY_FIELDS =
("peluqueros",)`). Si en el futuro el CMS gana una pestaña "Peluqueros", se elimina
de esa tupla y el YAML pasa a ser legacy.

**Regla permanente**: cada campo operativo (lo que impacta a reservas) o vive en la
BD o vive en el YAML, pero el `load_tenants()` debe devolverlo. Si añades un campo
nuevo a YAML y el código lo lee del tenant dict, añádelo a `_YAML_ONLY_FIELDS`.

### 4.6 Gemini Flash-Lite saltaba tool calls

**Síntoma**: Ana decía "tengo a las 10 y a las 11" sin haber llamado a
`consultar_disponibilidad`. Inventaba huecos. A veces creaba eventos en el pasado.

**Causa**: al duplicar el agente se había elegido `gemini-2.5-flash-lite` por baratez.
A temperatura 0 ese modelo prefiere generar texto antes que tools.

**Fix**: LLM forzado a `gemini-2.5-flash` (no lite). En nuevos agentes, elegir
**siempre** el modelo no-lite.

**Regla permanente**: si un agente hace tool calls, usa un modelo con buena fidelidad
a function calling. Flash-Lite / Haiku / mini-models son para eco y summarization, no
para agentes con herramientas.

### 4.7 Desktop vs GitHub desincronizados

**Síntoma**: cambios locales se perdían al pushear, o features remotas desaparecían al
editar en Desktop.

**Causa**: no había una regla clara de qué es canónico. Trabajábamos a veces en
`/tmp/sprint1-fork` (efímero entre sesiones) y a veces en `/sessions/.../Desktop/...`.

**Regla permanente adoptada**: **GitHub `main` = fuente de verdad**. Antes de cada
sesión de trabajo, pull en Desktop. Al terminar, commit + push. El sandbox `/tmp` se
borra entre sesiones — no fiarse.

---

## 5. Esquema de calendarios Google

Dos opciones probadas:

**A. Un calendario por peluquero (recomendado)**
- Cada peluquero tiene su calendario secundario dentro de la cuenta Google del cliente.
- Se comparte con la cuenta OAuth con permiso "Hacer cambios en eventos".
- `tenants.yaml` lista cada peluquero con su `calendar_id`.
- Las vacaciones y descansos se gestionan creando eventos manuales en el calendario
  del peluquero → freeBusy los marca como ocupados → el bot no los ofrece.
- El evento de reserva se crea en el calendario del peluquero (via
  `mover_evento`/`crear_evento` con `calendar_id=p["calendar_id"]`).

**B. Un solo calendario del negocio**
- Más simple pero no distingue peluqueros ni sus turnos.
- El tenant tiene `calendar_id` apuntando ahí.
- No hay bloque `peluqueros` en el YAML.
- Cae al modo `listar_huecos_libres`.

Opción A es la que hemos validado con `pelu_demo`. Mario tiene calendario propio, Marcos
también. El calendario "principal" del tenant se usa como `destino_cal` en
`crear_evento` si no se especifica otro — actualmente coincide con el de Mario.

---

## 6. Snippets útiles

### 6.1 PATCH Ana/prompt desde shell

```bash
set -a; source /tmp/el.env; set +a
python3 <<'PY'
import json, os, urllib.request
EL_KEY, ANA = os.environ["EL_KEY"], os.environ["ANA_AGENT"]
prompt = open("/tmp/ana_prompt.txt").read()
body = {"conversation_config":{"agent":{"prompt":{"prompt":prompt,"llm":"gemini-2.5-flash"}}}}
req = urllib.request.Request(
  f"https://api.elevenlabs.io/v1/convai/agents/{ANA}",
  data=json.dumps(body).encode(), method="PATCH",
  headers={"xi-api-key": EL_KEY, "Content-Type": "application/json"})
print(urllib.request.urlopen(req).read()[:200])
PY
```

### 6.2 Quitar un campo de `required` en un tool schema de ElevenLabs

```python
import json, os, urllib.request
EL_KEY, AGENT = os.environ["EL_KEY"], os.environ["ANA_AGENT"]
ag = json.loads(urllib.request.urlopen(
    urllib.request.Request(f"https://api.elevenlabs.io/v1/convai/agents/{AGENT}",
                           headers={"xi-api-key": EL_KEY})).read())
tools = ag["conversation_config"]["agent"]["prompt"]["tools"]
for t in tools:
    if t["name"] == "crear_reserva":
        t["api_schema"]["request_body_schema"]["required"].remove("telefono_cliente")
body = {"conversation_config": {"agent": {"prompt": {"tools": tools}}}}
urllib.request.urlopen(urllib.request.Request(
    f"https://api.elevenlabs.io/v1/convai/agents/{AGENT}",
    data=json.dumps(body).encode(), method="PATCH",
    headers={"xi-api-key": EL_KEY, "Content-Type": "application/json"})).read()
```

### 6.3 Smoke test de `consultar_disponibilidad` desde shell

```bash
curl -X POST "https://{tu-dominio}/tools/consultar_disponibilidad?tenant_id={id}" \
  -H "X-Tool-Secret: $TOOL_SECRET" -H "Content-Type: application/json" \
  -d '{"fecha_desde_iso":"2026-04-23T09:30:00","fecha_hasta_iso":"2026-04-23T20:30:00","duracion_minutos":30,"max_resultados":3}'
```

### 6.4 Listar últimas conversaciones de un agente

```bash
curl -s -H "xi-api-key: $EL_KEY" \
  "https://api.elevenlabs.io/v1/convai/conversations?agent_id=$AGENT&page_size=10" \
  | python3 -m json.tool
```

Y para ver la transcripción de una:

```bash
curl -s -H "xi-api-key: $EL_KEY" \
  "https://api.elevenlabs.io/v1/convai/conversations/$CONV_ID" \
  | python3 -m json.tool
```

### 6.5 Update OAuth callback tras mover de subdominio Railway

Si el dominio de Railway cambia, actualizar la URL autorizada en Google Cloud Console:
`https://{nuevo-dominio}/oauth/callback`. Si no, `/oauth/start` redirige pero el
callback falla con `redirect_uri_mismatch`.

---

## 7. Checklist pre-go-live

Antes de entregar la configuración a un cliente, todas estas casillas deben estar
marcadas. Si falta una, Ana fallará delante del cliente.

**Infraestructura**
- [ ] Tenant creado en CMS con id estable.
- [ ] Horario configurado a mano (no dejar defaults).
- [ ] Servicios con duración y precio reales.
- [ ] Teléfono de fallback humano configurado (en personalización).
- [ ] OAuth completado, token visible en `/app/data/.tokens/`.
- [ ] Bloque `peluqueros` en `tenants.yaml` si aplica.
- [ ] Todos los calendar_ids de peluqueros compartidos con la cuenta OAuth.

**ElevenLabs**
- [ ] Agente creado con LLM `gemini-2.5-flash` (NO lite).
- [ ] Prompt pegado y editado con datos reales (nombre negocio, peluqueros, horario).
- [ ] 4 herramientas con URLs apuntando al tenant correcto (`?tenant_id=...`).
- [ ] `X-Tool-Secret` correcto en headers.
- [ ] `telefono_cliente` NO está en `required` de `crear_reserva`.
- [ ] Número Twilio asignado al agente vía PATCH.

**Pruebas funcionales (smoke test)**
- [ ] Llamada 1: reserva simple sin preferencias → evento creado.
- [ ] Llamada 2: reserva pidiendo peluquero concreto que sí trabaja ese día.
- [ ] Llamada 3: reserva pidiendo peluquero que NO trabaja ese día → Ana ofrece
      alternativa sin colgarse.
- [ ] Llamada 4: reserva con cliente que se niega a dar teléfono → evento creado
      igualmente.
- [ ] Llamada 5: cancelación por teléfono.
- [ ] Llamada 6: mover cita a otro hueco.
- [ ] Todas las citas aparecen en el calendario correcto.
- [ ] Ningún tool call ha devuelto 4xx o 5xx.

**Observabilidad**
- [ ] CMS → conversaciones: aparecen las 6 llamadas de prueba.
- [ ] Nadie tiene eventos basura en el calendario (borrar los de testing).

---

## 8. Cosas que todavía están pendientes (TODOs acumulados)

En orden de impacto:

1. **Panel "Peluqueros" en el CMS**. Hoy vive en YAML y hay que editar con git commit.
   Cuando esto exista, eliminar `peluqueros` de `_YAML_ONLY_FIELDS` en `app/tenants.py`.
2. **Sincronización automática de prompt**. El prompt de Ana vive a la vez en
   `tenants.yaml` y en el agente de ElevenLabs; hoy lo sincronizamos a mano. Ideal:
   el prompt se construye desde el CMS y al guardar se PATCHea el agente.
3. **Retención de contexto entre llamadas del mismo cliente** (para evitar que el
   cliente habitual tenga que dar su nombre cada vez). Requiere índice por teléfono.
4. **Migración real (Alembic)**. `_auto_migrate_sqlite()` cumple pero no escala a
   cambios complejos. Cuando haya N>10 clientes, montar Alembic.
5. **Endurecer prompt contra medianoche**: "mañana" a las 00:15 puede ser ambiguo.
   Hoy Ana calcula correctamente pero no se ha estresado con edge cases nocturnos.
6. **Transferencia a humano** para quejas/reclamaciones. Hoy Ana solo da el 910…
   como fallback; no hay call-forwarding real.

---

## 9. Historial de commits que importan

- `2bee48b` — primera versión con columna `kind` (trigger del bug §4.1)
- `408cd9b` — fix timezone freebusy (§4.2)
- `0e2f130` — auto-migración SQLite para columna `kind` (§4.1)
- `bccf01c` — merge peluqueros YAML → BD (§4.5)
- `cc8d7db` — respeta horario del negocio y ventana intra-día (§4.3)
- `55c1286` — telefono_cliente opcional + limpieza diags (§4.4)

Si el histórico cambia (rebase/squash), actualiza aquí las referencias.
