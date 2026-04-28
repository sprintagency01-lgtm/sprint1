# Checklist de información a pedir al cliente — Bot de reservas por voz

Documento de uso interno (Sprintagency). Lo que hay que sacarle al cliente **antes** de empezar a montarle nada. Si falta cualquier punto bloqueante, no se arranca: se montan medias tintas y luego Ana se inventa horarios.

Última actualización: 2026-04-28.

---

## 0. TL;DR — bloqueantes mínimos para arrancar

Sin estos cinco no hay servicio, ni demo, ni nada:

1. Datos legales y de contacto del negocio.
2. Horario real (apertura/cierre por día, festivos).
3. Catálogo de servicios con duración y precio.
4. Equipo (uno o varios profesionales) y a qué calendario va cada uno.
5. Acceso a Google Calendar (cuenta con la que vamos a OAuth-ear).

Lo demás se puede ir afinando, pero estos cinco van sí o sí.

---

## 1. Datos del negocio

- **Nombre comercial** (lo que dirá Ana al descolgar).
- **Nombre legal / razón social** (para facturación interna y términos legales).
- **Dirección física** (Ana puede mencionarla si el cliente pregunta cómo llegar).
- **Web** (si la hay; útil para landing y para que el cliente la recite a Ana si pregunta).
- **Email de contacto operativo** (incidencias, no marketing).
- **Teléfono humano de fallback** — el número al que Ana redirige cuando todo falla. **Tiene que ser un teléfono que conteste alguien**, no el del fundador en silencio.
- **Sector / vertical** (peluquería, abogado, fisio, etc.). Cambia el tono y el prompt.
- **Idiomas en los que se atiende** (por ahora ES-ES, pero conviene saberlo).
- **Zona horaria** (por defecto Europe/Madrid; preguntar si es Canarias u otro).

## 2. Horario real

Por cada día de la semana:

- ¿Abre o cierra?
- Hora de apertura y cierre.
- ¿Hay descanso intermedio (turno partido)? Hora exacta de cierre y reapertura.

Adicional:

- **Festivos locales y autonómicos** que cierran (lista del año).
- **Vacaciones programadas** (semanas concretas).
- **Excepciones recurrentes** ("los lunes solo por la tarde", "el primer sábado de mes cerramos").

> Nota interna: el default del CMS es 09:00–20:00 L–V con sábado cerrado. Casi nunca coincide con la realidad. Si el cliente dice "lo de siempre" hay que sacárselo a punta de bisturí, porque en cuanto despleguemos Ana ofrecerá huecos a las 9:00 cuando abre a las 9:30 y entonces el cliente se enfada con razón.

## 3. Catálogo de servicios

Por cada servicio que se pueda reservar por teléfono:

- **Nombre exacto** (cómo lo va a pedir un cliente real, no el nombre interno: "corte de hombre", no "SVC-001").
- **Duración en minutos** (real, no aspiracional).
- **Precio** (con o sin IVA — decidir y aplicar coherentemente).
- **¿Requiere profesional concreto?** (algunos servicios solo los hace una persona).
- **¿Combinaciones frecuentes?** (corte + barba = 45 min, no 30).
- **Servicios que NO se reservan por teléfono** (consulta inicial gratuita que prefieren gestionar a mano, lo que sea).

## 4. Equipo / profesionales

Por cada persona que atiende (peluquero, abogado, fisio, etc.):

- **Nombre de pila** (el que va a usar el cliente al pedir cita).
- **Calendario Google asociado** (id del calendario; uno por persona, recomendado).
- **Días que trabaja** (lunes a sábado, solo miércoles, etc.).
- **Horario individual** si es distinto al del negocio (algunos hacen jornada reducida).
- **Servicios que SÍ hace** (no todos hacen todo).
- **Servicios que NO hace** (más fácil escribirlo en negativo a veces).
- **Vacaciones / días libres conocidos**.

> Nota interna: si solo hay una persona, no metemos bloque `peluqueros` en `tenants.yaml`. Caemos al modo single-calendar y listo. Si hay dos o más, sí o sí YAML.

## 5. Google Calendar — accesos

- **Cuenta Google con la que vamos a hacer OAuth** (la del negocio, no la personal del fundador si se puede evitar).
- **Confirmación de que cada calendario individual está compartido con esa cuenta OAuth** con permiso "Hacer cambios en eventos". Sin esto, freeBusy devuelve `notFound` y nadie tiene huecos. Es el bug más tonto y más recurrente.
- **¿Hay calendarios "personales" del profesional con compromisos no laborales** que también deban bloquear huecos? Si sí, hay que añadirlos a freeBusy.
- **¿Hay reglas para eventos manuales?** (p.ej. "si pongo un evento llamado BLOQUEADO ese hueco no se ofrece"). Por ahora todo evento bloquea, simple.

## 6. Reglas de reserva del negocio

Lo que define qué citas son legales:

- **Antelación mínima**: ¿se puede reservar para dentro de 10 minutos o solo con 2 horas?
- **Antelación máxima**: ¿hasta dónde mira la agenda? (30 días, 60, ilimitado).
- **Política de cancelación**: ¿se cancela libre? ¿con cuánta antelación? ¿hay penalización?
- **Política de no-show** (si la hay).
- **Slots / granularidad**: ¿citas alineadas a en punto y media? ¿cada 15 min? Por defecto seguimos la duración del servicio.
- **Doble-booking**: ¿permitido en algún caso? Por defecto, NO.
- **¿Se permite reservar varios servicios seguidos en la misma llamada?** (típico en peluquería).
- **¿Se piden datos extra del cliente?** (por defecto: nombre + teléfono. Algunos sectores piden DNI, motivo de la consulta, etc.).

## 7. Personalización del bot (Ana / abogado / lo-que-sea)

- **Nombre del asistente** (Ana es el default; hay clientes que prefieren "Lucía", "Carmen", lo que les case).
- **Voz** (por defecto la voz ES-ES que tenemos clavada en ElevenLabs; si el cliente quiere clonar voz propia, eso es un proyecto aparte y se cobra).
- **Tono**: cercano y de tú / profesional y de usted / mixto.
- **Frases de saludo y despedida** preferidas si las hay ("Peluquería Acme, dime", o lo que sea).
- **Cosas que Ana NO puede decir** (precios si son variables, plazos legales, lo que pongan).
- **¿Qué hace Ana si no sabe?** (transferir al humano / decir "te llamamos" / colgar amablemente). Por defecto: redirige al teléfono humano de fallback.
- **¿Qué hace Ana si el cliente pregunta si es un bot?** Por defecto: "Soy Ana, trabajo aquí". Si el cliente quiere honestidad explícita, lo cambiamos.

## 8. Telefonía

- **Número que va a llamar el cliente final**. Tres opciones:
  - Reciclamos un número Twilio nuestro y se lo damos.
  - Compramos un Twilio nuevo a su nombre.
  - Portamos el número actual del negocio (esto lleva tiempo y papeleo, avisar al cliente).
- **Si portan**: copia del último recibo de la operadora actual + autorización firmada para portar.
- **¿Se queda el número como entrante puro o también marca saliente?** (por ahora solo entrante).
- **Mensaje fuera de horario** — qué dice el sistema si llaman a las 3 de la mañana. Por defecto Ana coge igual y agenda; pero algunos prefieren contestador.

## 9. Política de datos / legal

- **Confirmación de que el cliente tiene base legal para almacenar datos personales** (nombre + teléfono de quien reserva). Mínimo: privacy policy publicada.
- **Aviso al usuario en la llamada de que la conversación se graba/transcribe** — texto exacto de la frase. Lo dice Ana al inicio.
- **Retención**: cuánto tiempo guardamos transcripciones (por defecto 90 días en ElevenLabs + nuestro CMS). Si quieren menos, ajustamos.
- **DPA / contrato de encargado del tratamiento** firmado entre Sprintagency y el cliente. Plantilla nuestra.
- **Datos sensibles**: en sectores como salud o legal hay que pactar explícitamente qué se puede pedir por teléfono. Por defecto, NO pedimos nada que no sea estrictamente necesario para agendar.

## 10. Material para el agente / contenido

Para que Ana suene a alguien que de verdad trabaja allí:

- **Preguntas frecuentes reales** que reciben hoy ("¿hacéis decoloraciones?", "¿aceptáis tarjeta?"). Sirven para enriquecer el prompt o como base de un mini-knowledge.
- **Cosas que SIEMPRE preguntan los clientes y que no son reservas** (parking, ubicación, métodos de pago).
- **Promociones activas** si las hay (Ana puede mencionarlas o no, según el cliente).
- **Casos especiales con los que el equipo está cansado de lidiar** ("siempre llaman pidiendo cita en domingo y no abrimos") — útil para tunear respuestas.

## 11. Coordinación operativa con el cliente

No es info del negocio, es del proyecto:

- **Persona de contacto técnico** (alguien que conteste WhatsApp si Ana se rompe a las 19:00).
- **Persona de contacto comercial / decisor** (quien firma cambios y aprueba pruebas).
- **Ventana de pruebas acordada** (días/horas en los que vamos a hacer llamadas de smoke test sin atascar el teléfono real).
- **Criterios de éxito del piloto** ("X reservas correctas", "Y semanas sin incidencias", lo que sea).
- **Quién avisa al equipo del negocio** de que el bot existe (importante: si los profesionales no saben que llamadas las coge Ana, se asustan al ver citas en su Google sin haberlas creado).

---

## Anexo — Plantilla rápida para mandar al cliente

Versión limpia, sin notas internas. Cópiala y mándala tal cual:

```
Para arrancar tu bot de reservas necesitamos esta información:

DATOS DEL NEGOCIO
- Nombre comercial y legal
- Dirección y web
- Email de contacto operativo
- Teléfono humano de fallback (al que redirigir si el bot no puede ayudar)

HORARIO
- Apertura y cierre por cada día de la semana (con descansos si los hay)
- Festivos en los que cerráis
- Vacaciones programadas

SERVICIOS
Para cada servicio reservable:
- Nombre tal y como lo pediría un cliente
- Duración real en minutos
- Precio
- ¿Algún profesional específico lo hace?

EQUIPO
Para cada profesional:
- Nombre
- Días que trabaja
- Horario si es distinto al general
- Servicios que hace (y los que no)

GOOGLE CALENDAR
- Cuenta Google con la que vamos a conectar
- Calendarios individuales de cada profesional (compartidos con la cuenta anterior con permiso de edición)

REGLAS DEL NEGOCIO
- Antelación mínima y máxima para reservar
- Política de cancelaciones
- ¿Se pueden encadenar varios servicios en la misma cita?

PERSONALIZACIÓN
- Nombre del asistente (por defecto: Ana)
- Tono (cercano / profesional)
- Cosas que NO debe decir
- Saludo de bienvenida si tenéis preferencia

LEGAL
- Confirmación de política de privacidad publicada
- Firma del DPA (plantilla que os pasamos)

TELEFONÍA
- ¿Número nuevo o portar el actual?
- Si portáis: último recibo + autorización firmada

CONTACTO
- Persona de contacto técnico (incidencias)
- Persona decisora (aprobaciones)
```
