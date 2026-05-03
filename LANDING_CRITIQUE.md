# Critique de la landing — Sprint

_Fecha: 2026-05-03 · Revisor: Claude (Cowork)_

## Impresión general

Estructura sólida (nav sticky, hero, marquee, canales, cómo funciona, sectores, voz, integraciones, precios, CTA final, footer). Tipografía bien elegida (Fraunces + Inter Tight) y paleta mint con buen contraste. **Pero** vende un producto de **voz** con un mockup que visualmente _es_ WhatsApp, y los copies están salpicados de tics que delatan generación rápida ("sin café", "+38% de reservas" sin fuente, voces con edad de Bumble bio, "Hecho en Madrid con mucho café ☕"). El conjunto pasa el filtro de "se ve majo" pero no el de "esto lo hizo alguien que sabe lo que hace".

La buena noticia: la mayoría son arreglos de superficie. La página tiene buen esqueleto.

---

## 1. Hero — el problema de fondo

| Hallazgo | Severidad | Recomendación |
|---|---|---|
| El mockup del hero usa lenguaje visual de WhatsApp (clases CSS `.wa-bar`, `.wa-body`, fondo `#ECE5DD`, burbujas con cola, doble check de leído ✓✓) cuando el producto es **voz**. El badge "En llamada · 00:42" es una tirita, no resuelve la disonancia. | 🔴 Crítico | Sustituir por un mockup de **llamada en vivo**: pantalla oscura tipo iOS in-call, avatar con anillos pulsantes, transcripción tipo subtítulos (no burbujas), waveform abajo, y cartel de reserva confirmada al final. Lo dejo implementado en el archivo. |
| Los stickers flotantes "+38% de reservas" y "24/7, sin café" son ruido. El primero es una métrica inventada sin fuente (penaliza credibilidad), el segundo es el tipo de chiste que una IA pondría por defecto. | 🟡 Moderado | Quitar el +38% (o respaldarlo con un caso real cuando lo tengas). Cambiar "sin café" por algo más concreto, p.ej. "Responde en 0,8 s" o eliminar. |
| Eyebrow "Tu teléfono nunca queda sin contestar" es redundante con el H1 "Tu teléfono, atendido siempre". Repites la misma idea dos veces seguidas. | 🟡 Moderado | El eyebrow puede llevar el _proof_ ("Atendiendo llamadas reales · 24/7") y el H1 lleva la promesa. |
| El subtítulo del hero mete el bot de Telegram en la primera frase. Es un add-on opcional, no la propuesta de valor central — diluye el mensaje. | 🟡 Moderado | Subtítulo solo de voz. Telegram entra en la sección "Canales" como complemento. |

**Propuestas de copy alternativas** para el H1 (te dejo aplicada la primera, las otras quedan como variantes para A/B):

1. **"Coge cada llamada. _Sin coger el teléfono._"** ← aplicada
2. "Una recepcionista que _no cuelga nunca_."
3. "Atendiendo llamadas, _mientras tú trabajas_."

---

## 2. Usabilidad y jerarquía

| Hallazgo | Severidad | Recomendación |
|---|---|---|
| La nav lleva 6 enlaces + 2 CTAs en desktop. En el viewport medio cuesta encontrar el CTA principal. | 🟢 Menor | Reducir a 4 ítems (Cómo funciona / Sectores / Precios / Integraciones) y quitar "Entrar" (no aporta nada hasta que tengas portal de cliente público). |
| H2 de "Canales" mete dos cosas en la misma frase con la misma jerarquía: voz y Telegram. Pero voz es el producto principal y Telegram es el add-on. | 🟡 Moderado | Reescribir para que voz mande: "Tu recepcionista de voz. _Y un chat opcional para los que no llaman._" |
| Los chips de sectores en `.sec-tabs` con emojis (✂️ 🦷 💼 🐾 🍽️) funcionan pero refuerzan el "AI generated" vibe. | 🟢 Menor | Reemplazar emojis por iconos SVG monocromos del set ya usado, o eliminar el icono y dejar solo texto. |
| Final CTA "Ahora toca _no hacer nada_. De eso nos encargamos." está bien, pero el botón pone "Habla con nosotros" igual que los otros tres CTAs de la página. | 🟢 Menor | Diferenciar el último CTA: "Reserva tu llamada de 20 min" o "Quiero ver una demo". |

---

## 3. Sección "Voz y personalidad"

| Hallazgo | Severidad | Recomendación |
|---|---|---|
| Las voces tienen edad ("ES · cálida · 28 años", "ES · profesional · 40 años", "LATAM · fresca · 24 años"). Es raro, ligeramente _creepy_, y suena a perfil de app de citas. Además abre la puerta a comparaciones y prejuicios que no necesitas. | 🟡 Moderado | Quitar la edad. Dejar acento + adjetivo ("ES · cálida", "ES · profesional", "LATAM · fresca"). Aplicado. |
| El bloque "¿Y si _tu voz_ contestara por ti?" presenta clonación como ya disponible (con pasos numerados) pero la etiqueta dice "Próximamente · Beta". | 🟢 Menor | Si está en beta cerrada, decirlo: "Beta privada — apúntate a la lista". Si no, quitar los pasos y dejar solo una promesa. |

---

## 4. Consistencia visual

| Elemento | Issue | Recomendación |
|---|---|---|
| Bordes redondeados | Mezclas `--radius` 14px, `--radius-lg` 22px, 999px (pills), 44px (phone), 32px (phone screen), 12px (chat cards). Demasiados valores. | Reducir a 3 escalas: 12px / 22px / pill. |
| Sombras | `--shadow-1` y `--shadow-2` se usan poco; el resto son sombras inline ad-hoc en `.phone`, `.sticker`, `.modal`. | Mover todas a tokens y reusar. |
| Iconos | Mezcla de SVG inline (la mayoría) y emojis (sectores, footer, sectores cards). | Decidir: o todo SVG monocromo o todo emoji. Lo coherente en un producto B2B serio es SVG. |
| Footer | "Hecho en Madrid con mucho café ☕" — el cliché AI-startup por excelencia. | Cambiar a "Hecho en Madrid." (Aplicado.) |

---

## 5. Accesibilidad (rápido — no es auditoría completa)

- **Contraste**: `--muted` (`#5E6E62`) sobre `--bg` (`#EEF3EC`) está cerca del límite WCAG AA para texto pequeño. El subtítulo del hero (`--ink-2` sobre `--bg`) está bien. Verificar con Stark/contrast checker.
- **Reduced motion**: ya respetas `prefers-reduced-motion`, bien. Pero el `loop()` de la transcripción del hero sigue corriendo — añadir `if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;` antes del loop.
- **Botones-chip de sectores**: usan `<button>` con `role="tab"` pero no manejan teclado (flechas izquierda/derecha). Para tabs ARIA accesibles falta el patrón completo.
- **Stickers flotantes** del hero: el SVG no tiene `aria-label` sobre el contenedor — un lector de pantalla leerá "+38% de reservas" sin contexto.

---

## 6. SEO y meta

Bien resuelto: `og:image`, JSON-LD para `SoftwareApplication`, `Organization` y `FAQPage`. El JSON-LD de `Offer` describe planes con `price` correcto. Una mejora: añadir `BreadcrumbList` y `LocalBusiness` si tiene sentido (Madrid).

---

## Lo que ya funciona bien

- **Tipografía**: Fraunces para titulares + Inter Tight para texto corre como un reloj. La cursiva acentuada del Fraunces para la palabra clave es un recurso bonito y consistente.
- **Paleta mint**: cálida, no saturada, no es ni el "Anthropic-orange" ni el "todo el mundo usa indigo". Diferencia.
- **Sección "Cómo funciona"**: 4 pasos en bloque oscuro contrastado, descripciones cortas, números mono — limpio.
- **Tabla de precios**: 3 planes, claras diferencias, plan recomendado destacado sin agresividad. Buen trabajo.
- **Marquee**: lista de _benefits_ scrolleando — divide bien hero y siguiente sección.
- **Tweak panel** lateral para editar copy en vivo: muy útil para iterar sin tocar código.

---

## Recomendaciones priorizadas

1. **🔴 Sustituir el mockup-WhatsApp del hero por un mockup de llamada real.** Es el cambio que más mueve la aguja. El visual actual hace que el visitante piense "ah, otro chatbot" en lugar de "una recepcionista que coge el teléfono". → _Aplicado._
2. **🟡 Limpiar copies con tics de IA**: "sin café" (×2), "+38% sin fuente", "Hecho en Madrid con café", edades de las voces. → _Aplicado parcialmente._
3. **🟡 Reescribir H1 y subtítulo** para que voz mande y Telegram baje a sección 1. → _Aplicado._
4. **🟢 Reducir variables de radio/sombra** y unificar iconos vs emojis. _Para una segunda pasada._
5. **🟢 Diferenciar el CTA final** del resto. _Para una segunda pasada._

---

## Cambios aplicados al archivo `app/templates/landing.html`

Resumen de los edits hechos directamente:

- Hero phone mockup: reescrito como **pantalla de llamada in-progress**, sin barra de WhatsApp, fondo oscuro tipo iOS, avatar central con anillos pulsantes, transcripción tipo subtítulos, action row de llamada decorativa abajo, y card de "reserva confirmada" que aparece al final.
- Stickers flotantes: el "+38% de reservas" se ha eliminado (sin caso real que lo respalde) y el "24/7, sin café" se ha cambiado a "Responde antes del 2.º tono".
- H1 default: `"Coge cada llamada. _Sin coger el teléfono._"`
- Subtítulo default: solo voz, Telegram fuera del hero.
- Eyebrow: "En una llamada real, ahora mismo" (proof, no repetición del H1).
- Voces: quitada la edad. "ES · cálida", "ES · profesional", "LATAM · fresca".
- Footer: "Hecho en Madrid con mucho café ☕" → "Hecho en Madrid".
- CSS: añadidos tokens para la nueva pantalla de llamada (`.call-*`), conservando los `.wa-*` por si quieres revertir, pero el markup ya no los usa.
- JS: el `playScript` ahora pinta `caption` (subtítulos), no `bubble`, y respeta `prefers-reduced-motion`.

Para ver los cambios: levanta el server local (uvicorn o el script habitual) y abre `https://sprintiasolutions.com` o el local equivalente.
