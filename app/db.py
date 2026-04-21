"""Persistencia con SQLite + SQLAlchemy.

Tablas:
- messages:        historial de conversaciones (ya existía).
- tenants:         configuración de cada negocio.
- services:        catálogo de servicios por tenant (relación 1-N).
- token_usage:     consumo de tokens por llamada (para métricas y facturación).
- admin_users:     usuarios del CMS (login).

Diseñado para que el bot y el CMS compartan la misma BD (data.db).
"""
from __future__ import annotations

import json
import pathlib
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import (
    String, Text, DateTime, Integer, Float, Boolean, ForeignKey,
    create_engine, select,
)
from sqlalchemy.orm import (
    DeclarativeBase, Mapped, mapped_column, Session, relationship,
)

from .config import settings


def _ensure_sqlite_dir(url: str) -> None:
    """Si la BD vive en una ruta absoluta (p.ej. volumen de Railway:
    /app/data/data.db), nos aseguramos de que el directorio exista.
    SQLAlchemy no crea carpetas automáticamente y SQLite fallaría."""
    if url.startswith("sqlite:///"):
        path = url.replace("sqlite:///", "", 1)
        if path:
            parent = pathlib.Path(path).parent
            if str(parent) not in ("", "."):
                parent.mkdir(parents=True, exist_ok=True)


_ensure_sqlite_dir(settings.database_url)

engine = create_engine(settings.database_url, echo=False, future=True)


class Base(DeclarativeBase):
    pass


# ---------------------------------------------------------------------
#  MESSAGES  (ya existía — historial de conversaciones)
# ---------------------------------------------------------------------

class Message(Base):
    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    customer_phone: Mapped[str] = mapped_column(String(32), index=True)
    role: Mapped[str] = mapped_column(String(16))  # user | assistant
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


# ---------------------------------------------------------------------
#  TENANTS  (configuración del negocio)
# ---------------------------------------------------------------------

class Tenant(Base):
    __tablename__ = "tenants"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    sector: Mapped[str] = mapped_column(String(120), default="")
    status: Mapped[str] = mapped_column(String(20), default="active")  # active | paused
    # Etapa del cliente en el funnel:
    #   lead       → vino del form de la landing, aún no contratado
    #   contracted → cliente activo con el bot configurado
    kind: Mapped[str] = mapped_column(String(20), default="contracted", index=True)
    plan: Mapped[str] = mapped_column(String(40), default="Básico")

    # Integraciones
    phone_number_id: Mapped[str] = mapped_column(String(64), default="", index=True)
    phone_display: Mapped[str] = mapped_column(String(40), default="")
    calendar_id: Mapped[str] = mapped_column(String(200), default="primary")
    timezone: Mapped[str] = mapped_column(String(64), default="Europe/Madrid")
    language: Mapped[str] = mapped_column(String(40), default="Español")

    # Contacto
    contact_name: Mapped[str] = mapped_column(String(200), default="")
    contact_email: Mapped[str] = mapped_column(String(200), default="")

    # Horario (JSON serializado; dict día → lista turnos)
    business_hours_json: Mapped[str] = mapped_column(Text, default="{}")

    # Personalización del asistente
    assistant_name: Mapped[str] = mapped_column(String(80), default="Asistente")
    assistant_tone: Mapped[str] = mapped_column(String(40), default="cercano")
    assistant_formality: Mapped[str] = mapped_column(String(10), default="tu")  # tu | usted
    assistant_emoji: Mapped[bool] = mapped_column(Boolean, default=True)
    assistant_greeting: Mapped[str] = mapped_column(Text, default="")
    assistant_fallback_phone: Mapped[str] = mapped_column(String(40), default="")
    assistant_rules_json: Mapped[str] = mapped_column(Text, default="[]")

    # Prompt: si override_prompt tiene valor se usa tal cual; si no, se genera.
    system_prompt_override: Mapped[str] = mapped_column(Text, default="")

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    services: Mapped[list["Service"]] = relationship(
        back_populates="tenant", cascade="all, delete-orphan", order_by="Service.orden"
    )

    # ----- helpers para (de)serializar los campos JSON -----

    @property
    def business_hours(self) -> dict[str, list[str]]:
        try:
            return json.loads(self.business_hours_json or "{}")
        except json.JSONDecodeError:
            return {}

    @business_hours.setter
    def business_hours(self, value: dict[str, list[str]]) -> None:
        self.business_hours_json = json.dumps(value, ensure_ascii=False)

    @property
    def assistant_rules(self) -> list[str]:
        try:
            return json.loads(self.assistant_rules_json or "[]")
        except json.JSONDecodeError:
            return []

    @assistant_rules.setter
    def assistant_rules(self, value: list[str]) -> None:
        self.assistant_rules_json = json.dumps(value, ensure_ascii=False)

    def to_dict(self) -> dict[str, Any]:
        """Devuelve el tenant en el formato dict que espera el resto del código
        (compatibilidad con el antiguo YAML)."""
        return {
            "id": self.id,
            "name": self.name,
            "sector": self.sector,
            "status": self.status,
            "kind": self.kind,
            "plan": self.plan,
            "phone_number_id": self.phone_number_id or None,
            "phone_display": self.phone_display,
            "calendar_id": self.calendar_id or None,
            "timezone": self.timezone,
            "language": self.language,
            "contact_name": self.contact_name,
            "contact_email": self.contact_email,
            "business_hours": self.business_hours,
            "services": [s.to_dict() for s in self.services],
            "assistant": {
                "name": self.assistant_name,
                "tone": self.assistant_tone,
                "formality": self.assistant_formality,
                "emoji": self.assistant_emoji,
                "greeting": self.assistant_greeting,
                "fallback_phone": self.assistant_fallback_phone,
                "rules": self.assistant_rules,
            },
            "system_prompt_override": self.system_prompt_override,
            "system_prompt": render_system_prompt(self),
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class Service(Base):
    __tablename__ = "services"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    nombre: Mapped[str] = mapped_column(String(200))
    duracion_min: Mapped[int] = mapped_column(Integer, default=30)
    precio: Mapped[float] = mapped_column(Float, default=0.0)
    orden: Mapped[int] = mapped_column(Integer, default=0)

    tenant: Mapped["Tenant"] = relationship(back_populates="services")

    def to_dict(self) -> dict[str, Any]:
        return {"nombre": self.nombre, "duracion_min": self.duracion_min, "precio": self.precio}


# ---------------------------------------------------------------------
#  TOKEN USAGE  (para métricas y facturación)
# ---------------------------------------------------------------------

# Precio por token en EUR (aprox. EUR = USD × 0.92). Mantener sincronizado con
# https://openai.com/pricing.
MODEL_PRICING_EUR = {
    "gpt-4o-mini":    {"input": 0.138 / 1_000_000, "output": 0.552 / 1_000_000},
    "gpt-4o":         {"input": 2.300 / 1_000_000, "output": 9.200 / 1_000_000},
    "gpt-4.1":        {"input": 1.840 / 1_000_000, "output": 7.360 / 1_000_000},
    "gpt-4.1-mini":   {"input": 0.368 / 1_000_000, "output": 1.472 / 1_000_000},
}


def estimate_cost_eur(model: str, input_tokens: int, output_tokens: int) -> float:
    pricing = MODEL_PRICING_EUR.get(model, MODEL_PRICING_EUR["gpt-4o-mini"])
    return input_tokens * pricing["input"] + output_tokens * pricing["output"]


class TokenUsage(Base):
    __tablename__ = "token_usage"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    customer_phone: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    model: Mapped[str] = mapped_column(String(80))
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_eur: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)


def save_token_usage(
    tenant_id: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
    customer_phone: Optional[str] = None,
) -> None:
    """Guarda un registro de consumo. Nunca lanza excepción (tracking no debe
    romper el bot si falla)."""
    try:
        cost = estimate_cost_eur(model, input_tokens, output_tokens)
        with Session(engine) as s:
            s.add(TokenUsage(
                tenant_id=tenant_id,
                model=model,
                input_tokens=int(input_tokens or 0),
                output_tokens=int(output_tokens or 0),
                cost_eur=cost,
                customer_phone=customer_phone,
            ))
            s.commit()
    except Exception:
        import logging
        logging.getLogger(__name__).exception("No se pudo guardar token_usage")


# ---------------------------------------------------------------------
#  LEADS  (capturas desde la landing pública)
# ---------------------------------------------------------------------

class Lead(Base):
    __tablename__ = "leads"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), default="")
    phone: Mapped[str] = mapped_column(String(40), index=True)
    email: Mapped[str] = mapped_column(String(200), default="")
    company: Mapped[str] = mapped_column(String(200), default="")
    sector: Mapped[str] = mapped_column(String(80), default="")
    message: Mapped[str] = mapped_column(Text, default="")
    # Origen del lead (landing_final_cta, hero, nav, etc.) y UTMs
    source: Mapped[str] = mapped_column(String(80), default="")
    utm_source: Mapped[str] = mapped_column(String(120), default="")
    utm_medium: Mapped[str] = mapped_column(String(120), default="")
    utm_campaign: Mapped[str] = mapped_column(String(120), default="")
    utm_term: Mapped[str] = mapped_column(String(120), default="")
    utm_content: Mapped[str] = mapped_column(String(120), default="")
    # Estado de seguimiento: new, contacted, qualified, converted, lost
    status: Mapped[str] = mapped_column(String(20), default="new", index=True)
    # Metadatos de la petición
    ip: Mapped[str] = mapped_column(String(64), default="")
    user_agent: Mapped[str] = mapped_column(String(400), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)


def _slug(s: str, maxlen: int = 32) -> str:
    """Normaliza una cadena para usarla en un tenant_id (solo a-z0-9_)."""
    import re as _re
    import unicodedata as _ud
    s = _ud.normalize("NFD", s)
    s = "".join(c for c in s if _ud.category(c) != "Mn")
    s = _re.sub(r"[^a-zA-Z0-9]+", "_", s.strip().lower()).strip("_")
    return (s or "lead")[:maxlen]


def upsert_tenant_from_lead(
    *,
    lead_id: int,
    name: str,
    phone: str,
    email: str,
    company: str,
    sector: str,
) -> str:
    """Crea (o actualiza) un Tenant en estado 'lead' con los datos del form de
    la landing. Devuelve el tenant_id."""
    # Prioridad para el id: empresa > nombre > lead_id. Prefijo 'lead_' para
    # distinguirlo visualmente y evitar colisionar con tenants contratados.
    seed = _slug(company) or _slug(name) or f"nuevo_{lead_id}"
    tid = f"lead_{seed}"

    with Session(engine) as s:
        # Si ya existe un tenant con ese id (porque el mismo negocio envió dos
        # veces el form), actualizamos en vez de crear uno nuevo.
        t = s.get(Tenant, tid)
        if t is None:
            # Añadir sufijo numérico si coincide con un tenant contratado (raro)
            base = tid
            i = 2
            while s.get(Tenant, tid) is not None:
                tid = f"{base}_{i}"
                i += 1
            t = Tenant(id=tid, kind="lead", status="paused")
            s.add(t)

        t.name = company or name or f"Lead {lead_id}"
        t.sector = sector or t.sector
        t.contact_name = name or t.contact_name
        t.contact_email = email or t.contact_email
        t.phone_display = phone or t.phone_display
        # No tocamos phone_number_id / calendar_id / servicios: se configuran
        # cuando promocionemos el lead a 'contracted'.
        if not t.assistant_name:
            t.assistant_name = "Asistente"
        s.commit()
        return tid


def save_lead(
    *,
    name: str,
    phone: str,
    email: str = "",
    company: str = "",
    sector: str = "",
    message: str = "",
    source: str = "",
    utm_source: str = "",
    utm_medium: str = "",
    utm_campaign: str = "",
    utm_term: str = "",
    utm_content: str = "",
    ip: str = "",
    user_agent: str = "",
) -> int:
    """Persiste un lead de la landing. Devuelve el id del registro creado."""
    with Session(engine) as s:
        lead = Lead(
            name=name[:200], phone=phone[:40], email=email[:200],
            company=company[:200], sector=sector[:80], message=message[:2000],
            source=source[:80],
            utm_source=utm_source[:120], utm_medium=utm_medium[:120],
            utm_campaign=utm_campaign[:120], utm_term=utm_term[:120],
            utm_content=utm_content[:120],
            ip=ip[:64], user_agent=user_agent[:400],
        )
        s.add(lead)
        s.commit()
        s.refresh(lead)
        return lead.id


# ---------------------------------------------------------------------
#  ADMIN USERS  (login del CMS)
# ---------------------------------------------------------------------

class AdminUser(Base):
    __tablename__ = "admin_users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(200), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


# ---------------------------------------------------------------------
#  HISTORIAL DE CONVERSACIONES  (API compartida)
# ---------------------------------------------------------------------

MAX_HISTORY = 12


def save_message(tenant_id: str, customer_phone: str, role: str, content: str) -> None:
    with Session(engine) as db:
        db.add(Message(
            tenant_id=tenant_id,
            customer_phone=customer_phone,
            role=role,
            content=content,
        ))
        db.commit()


def load_history(tenant_id: str, customer_phone: str) -> list[dict[str, Any]]:
    with Session(engine) as db:
        stmt = (
            select(Message)
            .where(Message.tenant_id == tenant_id, Message.customer_phone == customer_phone)
            .order_by(Message.created_at.desc())
            .limit(MAX_HISTORY)
        )
        rows = list(reversed(db.scalars(stmt).all()))
    return [{"role": r.role, "content": r.content} for r in rows]


# ---------------------------------------------------------------------
#  Composición dinámica del system_prompt del tenant
# ---------------------------------------------------------------------

_TONE_LABELS = {
    "cercano":     "cercano y natural",
    "profesional": "profesional y neutro",
    "juvenil":     "joven y desenfadado",
    "calido":      "cálido y acogedor",
    "formal":      "formal y respetuoso",
}


def render_system_prompt(t: "Tenant") -> str:
    """Construye el system_prompt que se envía al LLM a partir de los campos
    editables del tenant. Si hay override_prompt, se usa tal cual."""
    if t.system_prompt_override and t.system_prompt_override.strip():
        return t.system_prompt_override

    services = t.services
    services_lines = "\n".join(
        f"- {s.nombre} — {s.duracion_min} min — {s.precio:g}€" for s in services
    ) or "- (Sin servicios configurados)"

    # Horario en texto legible
    hours = t.business_hours or {}
    day_names = {"mon":"L","tue":"M","wed":"X","thu":"J","fri":"V","sat":"S","sun":"D"}
    hours_lines = []
    for k, lbl in day_names.items():
        h = hours.get(k)
        if not h or h == ["closed"] or h[0] == "closed":
            hours_lines.append(f"{lbl}: cerrado")
        else:
            pairs = [f"{h[i]}-{h[i+1]}" for i in range(0, len(h), 2) if i+1 < len(h)]
            hours_lines.append(f"{lbl}: {', '.join(pairs)}")
    hours_block = " · ".join(hours_lines)

    tone = _TONE_LABELS.get(t.assistant_tone, t.assistant_tone or "natural")
    treatment = "de tú" if t.assistant_formality == "tu" else "de usted"
    emoji_line = "Puedes usar emojis con moderación." if t.assistant_emoji else "No uses emojis."

    rules_block = "\n".join(f"- {r}" for r in t.assistant_rules) if t.assistant_rules else ""

    prompt = f"""Eres {t.assistant_name or 'el asistente'}, la asistente virtual de {t.name}.
Tu trabajo es reservar, mover o cancelar citas por WhatsApp en {t.language}, con un tono {tone}.
Trata al cliente {treatment}. {emoji_line}

Saludo inicial cuando el cliente escribe por primera vez (puedes adaptarlo):
{t.assistant_greeting or '¡Hola! ¿Te ayudo a reservar?'}

Catálogo de servicios (con duración y precio):
{services_lines}

Horario de atención:
{hours_block}

Reglas generales del negocio:
{rules_block}

Reglas operativas (siempre):
- Antes de proponer hora, consulta SIEMPRE disponibilidad con la función consultar_disponibilidad.
- Propón hasta 3 huecos como máximo, los primeros que encuentres.
- Confirma SIEMPRE la hora elegida antes de crear, mover o cancelar una reserva.
- No pidas ni almacenes datos bancarios. El pago se hace en el local.
- Si el cliente dice "mover mi cita" o "cambiar", busca primero su reserva por su teléfono.
- Si el cliente pregunta algo fuera de tu alcance, ofrécele contactar al {t.assistant_fallback_phone or '(teléfono de contacto)'}.
"""
    return prompt.strip()


# ---------------------------------------------------------------------
#  Crear todas las tablas (idempotente). Lo llamamos al arrancar.
# ---------------------------------------------------------------------

Base.metadata.create_all(engine)
