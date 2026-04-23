"""Codificación/decodificación de IDs para mensajes interactivos.

El agente no inventa los IDs de los botones/filas: los genera el backend al
construir el mensaje interactivo. Cuando el cliente pulsa una opción,
WhatsApp/Twilio nos devuelve el mismo ID y aquí lo decodificamos a su
significado.

Formato: `<kind>:<payload>` — un prefijo corto seguido de datos separados
por dos puntos (`:`). WhatsApp permite 256 chars por id, así que nos
sobra espacio.

Kinds:
  slot:ISO_INICIO:ISO_FIN[:MIEMBRO]
      Hueco horario. Opcionalmente atribuido a un miembro (p.ej. cuando
      la primera oferta era agregada todo-equipo y uno ya salió solo).
  team:<member_id_or_none>
      Miembro del equipo. "team:none" → "sin preferencia / me da igual".
  svc:<slug>
      Servicio identificado por slug.
  confirm:yes | confirm:no
      Respuesta a pedir_confirmacion.
  other:slot | other:team | other:svc
      "Otra..." — el cliente rechaza las opciones mostradas y pide
      alternativa. La semántica exacta (volver al paso anterior vs pedir
      texto libre) la resuelve el handler en main.py.

También expone `make_id()` y `parse_id()` para que los tests no tengan
que duplicar el formato literal.
"""
from __future__ import annotations

from typing import Any


def make_slot_id(inicio_iso: str, fin_iso: str, miembro: str | None = None) -> str:
    base = f"slot:{inicio_iso}:{fin_iso}"
    if miembro:
        base += f":{miembro}"
    return base


def make_team_id(member_id: str | int | None) -> str:
    if member_id is None or str(member_id).strip().lower() in ("", "none", "sin_preferencia"):
        return "team:none"
    return f"team:{member_id}"


def make_service_id(slug: str) -> str:
    return f"svc:{slug}"


def make_confirm_id(yes: bool) -> str:
    return "confirm:yes" if yes else "confirm:no"


def make_other_id(kind: str) -> str:
    return f"other:{kind}"


def parse_id(rid: str) -> dict[str, Any]:
    """Parsea un id y devuelve un dict con la info útil.

    Formato de retorno:
      {"kind": "slot"|"team"|"svc"|"confirm"|"other"|"unknown",
       "raw":  str,
       + campos específicos del kind}

    Jamás lanza: si no reconoce el formato devuelve kind="unknown" con el
    raw intacto. Los handlers deciden cómo degradar elegantemente.
    """
    raw = rid or ""
    parts = raw.split(":")
    kind = (parts[0] if parts else "").lower()

    if kind == "slot":
        # slot:<inicio>:<fin>[:<miembro>]
        # las ISO contienen ":" — la partición real es distinta. Reagrupamos.
        # Formato determinista: split máximo en tokens → los dos primeros
        # "chunks ISO" son fecha+hora con minutos (llevan 1 ":"), y ensamblamos.
        # Ejemplo típico: "slot:2026-04-24T10:00:2026-04-24T10:30"
        #   → split(":") → ["slot","2026-04-24T10","00","2026-04-24T10","30"]
        #   → inicio = "2026-04-24T10:00", fin = "2026-04-24T10:30"
        # Reconstruimos respetando la forma YYYY-MM-DDTHH:MM[:SS][+TZ].
        try:
            inicio, fin, miembro = _split_slot_payload(parts[1:])
            return {
                "kind": "slot",
                "raw": raw,
                "inicio_iso": inicio,
                "fin_iso": fin,
                "miembro": miembro,
            }
        except Exception:
            return {"kind": "unknown", "raw": raw}

    if kind == "team":
        mid = parts[1] if len(parts) > 1 else ""
        if mid in ("", "none", "sin_preferencia"):
            return {"kind": "team", "raw": raw, "member_id": None, "sin_preferencia": True}
        return {"kind": "team", "raw": raw, "member_id": mid, "sin_preferencia": False}

    if kind == "svc":
        slug = ":".join(parts[1:]) if len(parts) > 1 else ""
        return {"kind": "svc", "raw": raw, "slug": slug}

    if kind == "confirm":
        val = (parts[1] if len(parts) > 1 else "").lower()
        return {"kind": "confirm", "raw": raw, "yes": val in ("yes", "si", "sí", "ok", "true", "1")}

    if kind == "other":
        target = (parts[1] if len(parts) > 1 else "").lower()
        return {"kind": "other", "raw": raw, "target": target}

    return {"kind": "unknown", "raw": raw}


def _split_slot_payload(chunks: list[str]) -> tuple[str, str, str | None]:
    """Reconstruye (inicio, fin, miembro?) desde los trozos de un slot id.

    Problema: las fechas ISO contienen `:` (minutos), así que un naïve
    split(":") trocea de más. Reensamblamos apoyándonos en dos hechos del
    formato que generamos en make_slot_id:

    - Las fechas son `YYYY-MM-DDTHH:MM` (sin segundos, sin TZ) — al
      splittear por `:` dan exactamente 2 chunks: `["YYYY-MM-DDTHH", "MM"]`.
    - Cada id tiene exactamente 2 fechas (inicio + fin).
    - El miembro, si está, llega detrás como un chunk extra.

    Por tanto: encuentra las 2 posiciones con 'T' y agrupa cada fecha como
    [Tpos, Tpos+1]. Lo que quede después es miembro.

    Nota: si algún día cambiamos make_slot_id para incluir segundos/TZ,
    habrá que revisar esta función en paralelo.
    """
    if not chunks:
        raise ValueError("slot vacío")

    t_positions = [i for i, c in enumerate(chunks) if "T" in c]
    if len(t_positions) < 2:
        raise ValueError("slot sin 2 fechas con 'T'")
    i_inicio = t_positions[0]
    i_fin = t_positions[1]

    # Cada fecha ocupa exactamente 2 chunks: la parte hasta HH y la MM.
    # Si el formato sube de granularidad (añadir SS, TZ), ajustar.
    if i_fin < i_inicio + 2 or i_fin + 1 >= len(chunks):
        raise ValueError("slot con chunks insuficientes alrededor de las fechas")

    inicio = ":".join(chunks[i_inicio:i_inicio + 2])
    fin = ":".join(chunks[i_fin:i_fin + 2])

    rest = chunks[i_fin + 2:]
    miembro: str | None = ":".join(rest) if rest else None
    return inicio, fin, miembro


def resolve_from_pending_menu(
    pending: dict[str, Any] | None,
    text_reply: str,
) -> dict[str, Any] | None:
    """Resuelve la selección de un cliente que respondió en TEXTO a un menú.

    Lo usa Twilio (que no tiene interactivos nativos sin templates): el bot
    mandó "1. X\\n2. Y\\n3. Otra" y el cliente responde "1", "2", "3", "otra".

    Acepta también:
      - número escrito ("dos", "tres") limitado a 1-10
      - "otra", "otro" → matchea el `other:...` si existe en el menú
      - primera opción cuyo título contenga el texto (heurístico)

    Devuelve el dict de la opción con su `id`, o None si no ha podido
    mapear.
    """
    if not pending:
        return None
    options = pending.get("options") or []
    if not options:
        return None

    txt = (text_reply or "").strip().lower()
    if not txt:
        return None

    # 1) número directo
    if txt.isdigit():
        idx = int(txt)
        if 1 <= idx <= len(options):
            return options[idx - 1]

    # 2) números escritos en español (1-10)
    _NUMS = {
        "uno": 1, "dos": 2, "tres": 3, "cuatro": 4, "cinco": 5,
        "seis": 6, "siete": 7, "ocho": 8, "nueve": 9, "diez": 10,
        "primera": 1, "primero": 1, "segunda": 2, "segundo": 2,
        "tercera": 3, "tercero": 3,
    }
    first_word = txt.split()[0] if txt else ""
    if first_word in _NUMS:
        idx = _NUMS[first_word]
        if 1 <= idx <= len(options):
            return options[idx - 1]

    # 3) "otra/otro" → match al other:* si existe
    if txt.startswith("otra") or txt.startswith("otro"):
        for opt in options:
            if (opt.get("id") or "").startswith("other:"):
                return opt

    # 4) título contenido (heurístico) — p.ej. "10:00" matchea "vie 24, 10:00"
    for opt in options:
        title = (opt.get("title") or "").lower()
        if txt in title or title in txt:
            return opt

    return None
