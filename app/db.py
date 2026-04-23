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
    create_engine, select, text,
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

    # ---- Agente de voz (ElevenLabs Conversational AI) -------------------
    # El prompt, voz y parámetros TTS del agente Ana en ElevenLabs. Editables
    # desde /admin/clientes/{id}/voz. Al pulsar "Sincronizar" se hace PATCH al
    # agente remoto mediante app/elevenlabs_client.py.
    #
    # voice_agent_id: si está vacío se cae al ELEVENLABS_AGENT_ID global del
    # .env (MVP monotenant). Al escalar a multi-tenant, cada tenant tendrá el
    # suyo.
    voice_agent_id: Mapped[str] = mapped_column(String(100), default="")
    voice_prompt: Mapped[str] = mapped_column(Text, default="")
    voice_voice_id: Mapped[str] = mapped_column(String(100), default="")
    voice_stability: Mapped[float] = mapped_column(Float, default=0.67)
    voice_similarity_boost: Mapped[float] = mapped_column(Float, default=0.8)
    voice_speed: Mapped[float] = mapped_column(Float, default=1.04)
    # Última sincronización con ElevenLabs (auditoría): null si nunca se ha
    # hecho; status es "ok" o "error: ..." para pintar el banner de la pestaña.
    voice_last_sync_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True, default=None)
    voice_last_sync_status: Mapped[str] = mapped_column(String(400), default="")

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    services: Mapped[list["Service"]] = relationship(
        back_populates="tenant", cascade="all, delete-orphan", order_by="Service.orden"
    )
    equipo: Mapped[list["MiembroEquipo"]] = relationship(
        back_populates="tenant", cascade="all, delete-orphan", order_by="MiembroEquipo.orden"
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
            # `equipo` es el nombre canónico. `peluqueros` se mantiene como
            # alias de compatibilidad mientras eleven_tools y calendar_service
            # siguen usando esa clave (y las tools expuestas a ElevenLabs
            # también, para no romper el agente remoto ya registrado).
            "equipo": [m.to_dict() for m in self.equipo],
            "peluqueros": [m.to_dict() for m in self.equipo],
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
            "voice": {
                "agent_id": self.voice_agent_id,
                "prompt": self.voice_prompt,
                "voice_id": self.voice_voice_id,
                "stability": self.voice_stability,
                "similarity_boost": self.voice_similarity_boost,
                "speed": self.voice_speed,
                "last_sync_at": self.voice_last_sync_at.isoformat() if self.voice_last_sync_at else None,
                "last_sync_status": self.voice_last_sync_status,
            },
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
    # Si está inactivo, el bot no lo ofrece en conversaciones y no aparece en el
    # portal como opción disponible. Por defecto activo para no romper datos
    # existentes.
    activo: Mapped[bool] = mapped_column(Boolean, default=True)
    # IDs (int) de miembros del equipo que pueden hacer este servicio,
    # serializado como JSON. Lista vacía = todos los miembros.
    equipo_json: Mapped[str] = mapped_column(Text, default="[]")

    tenant: Mapped["Tenant"] = relationship(back_populates="services")

    @property
    def equipo_ids(self) -> list[int]:
        try:
            raw = json.loads(self.equipo_json or "[]")
            return [int(x) for x in raw if str(x).lstrip("-").isdigit()]
        except json.JSONDecodeError:
            return []

    @equipo_ids.setter
    def equipo_ids(self, value: list[int]) -> None:
        clean = sorted({int(x) for x in value})
        self.equipo_json = json.dumps(clean)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "nombre": self.nombre,
            "duracion_min": self.duracion_min,
            "precio": self.precio,
            "activo": bool(self.activo),
            "equipo": self.equipo_ids,
        }


# ---------------------------------------------------------------------
#  EQUIPO  (miembros que atienden reservas del tenant)
#
#  Anteriormente se llamaba `peluqueros`. Se renombró a "equipo" para que
#  cubra verticales más allá de peluquería (clínicas, consultas, etc.).
#  Datos equivalentes: cada miembro tiene un calendario Google donde se
#  pintan sus descansos/vacaciones, y una lista de días laborables.
# ---------------------------------------------------------------------

class MiembroEquipo(Base):
    __tablename__ = "equipo"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    nombre: Mapped[str] = mapped_column(String(120))
    # Calendar secundario de Google donde se llevan descansos/vacaciones. La
    # disponibilidad se calcula mirando este calendario; las reservas de voz se
    # siguen creando en el calendario principal del tenant (regla del MVP).
    calendar_id: Mapped[str] = mapped_column(String(200), default="")
    # Días laborables en formato weekday de Python (0=lun ... 6=dom),
    # serializado como JSON: [0,1,2,3,4,5] = lun-sáb.
    dias_trabajo_json: Mapped[str] = mapped_column(Text, default="[0,1,2,3,4,5]")
    orden: Mapped[int] = mapped_column(Integer, default=0)
    # Color hex para visualización en el portal (agenda, etiquetas).
    color: Mapped[str] = mapped_column(String(16), default="#059669")
    # Turnos diarios: lista de [inicio_hhmm, fin_hhmm] en formato 24h,
    # p.ej. [["10:00","14:00"],["17:00","20:30"]] para un turno partido.
    # Se aplica a todos los dias_trabajo.
    turnos_json: Mapped[str] = mapped_column(Text, default='[["10:00","20:00"]]')
    # Periodos de vacaciones: lista de {desde: "YYYY-MM-DD", hasta: "YYYY-MM-DD"}
    vacaciones_json: Mapped[str] = mapped_column(Text, default="[]")

    tenant: Mapped["Tenant"] = relationship(back_populates="equipo")

    @property
    def dias_trabajo(self) -> list[int]:
        try:
            raw = json.loads(self.dias_trabajo_json or "[]")
            return [int(x) for x in raw if isinstance(x, (int, str)) and str(x).lstrip("-").isdigit()]
        except json.JSONDecodeError:
            return []

    @dias_trabajo.setter
    def dias_trabajo(self, value: list[int]) -> None:
        clean = sorted({int(x) for x in value if 0 <= int(x) <= 6})
        self.dias_trabajo_json = json.dumps(clean)

    @property
    def turnos(self) -> list[list[str]]:
        """Turnos del día como lista de [inicio, fin]. Siempre al menos uno."""
        try:
            raw = json.loads(self.turnos_json or "[]")
            out: list[list[str]] = []
            for t in raw or []:
                if isinstance(t, (list, tuple)) and len(t) >= 2:
                    out.append([str(t[0]), str(t[1])])
            return out or [["10:00", "20:00"]]
        except json.JSONDecodeError:
            return [["10:00", "20:00"]]

    @turnos.setter
    def turnos(self, value: list[list[str]]) -> None:
        clean = [[str(t[0])[:5], str(t[1])[:5]] for t in (value or []) if len(t) >= 2]
        self.turnos_json = json.dumps(clean or [["10:00", "20:00"]])

    @property
    def vacaciones(self) -> list[dict[str, str]]:
        try:
            raw = json.loads(self.vacaciones_json or "[]")
            out: list[dict[str, str]] = []
            for v in raw or []:
                if isinstance(v, dict) and v.get("desde") and v.get("hasta"):
                    out.append({"desde": str(v["desde"]), "hasta": str(v["hasta"])})
            return out
        except json.JSONDecodeError:
            return []

    @vacaciones.setter
    def vacaciones(self, value: list[dict[str, str]]) -> None:
        clean = [
            {"desde": str(v.get("desde", "")), "hasta": str(v.get("hasta", ""))}
            for v in (value or [])
            if v.get("desde") and v.get("hasta")
        ]
        self.vacaciones_json = json.dumps(clean)

    def to_dict(self) -> dict[str, Any]:
        """Formato que consumen `eleven_tools.py` y `calendar_service.py`.

        Las keys legacy se mantienen ("nombre", "calendar_id", "dias_trabajo")
        para no romper esos módulos ni las tools expuestas a ElevenLabs. Los
        campos añadidos para el portal (color, turnos, vacaciones) se incluyen
        además pero son ignorados por los consumidores legacy.
        """
        return {
            "id": self.id,
            "nombre": self.nombre,
            "calendar_id": self.calendar_id,
            "dias_trabajo": self.dias_trabajo,
            "color": self.color or "#059669",
            "turnos": self.turnos,
            "vacaciones": self.vacaciones,
        }


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
#  TENANT USERS  (login del portal del cliente)
#
#  Usuarios del portal (/app) — distintos de AdminUser, que es solo para
#  Sprintagency. Cada registro pertenece a un tenant. Roles:
#    - owner      : dueño/a del negocio. Acceso total, incluido Ajustes/Usuarios.
#    - manager    : recepción con acceso de escritura pero sin poder borrar
#                   usuarios ni cambiar datos del negocio.
#    - readonly   : solo lectura (útil para demos o contables).
#
#  Email es único por tenant (un mismo email puede tener cuenta en varios
#  tenants, aunque en la práctica hoy solo tenemos pelu_demo).
# ---------------------------------------------------------------------

class TenantUser(Base):
    __tablename__ = "tenant_users"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[str] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), index=True,
    )
    email: Mapped[str] = mapped_column(String(200), index=True)
    password_hash: Mapped[str] = mapped_column(String(200))
    nombre: Mapped[str] = mapped_column(String(200), default="")
    # owner | manager | readonly
    role: Mapped[str] = mapped_column(String(20), default="owner")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


# ---------------------------------------------------------------------
#  PENDING MENUS  (último menú interactivo ofrecido a un cliente)
#
#  Los canales que NO soportan mensajes interactivos nativos (Twilio sin
#  Content Templates aprobados) rinderizan las opciones como lista
#  numerada en texto. Para resolver la respuesta del cliente ("1", "2",
#  "otra") necesitamos recordar qué menú se le ofreció por última vez.
#
#  También lo usamos en Meta como "última oferta" para detectar selecciones
#  por texto cuando el cliente no pulsa el botón y responde escribiendo
#  ("a las 10 sí").
#
#  Upsert por (tenant_id, phone): cada cliente tiene como mucho UN menú
#  pendiente. Si se ofrece otro, reemplaza al anterior.
# ---------------------------------------------------------------------

class PendingMenu(Base):
    __tablename__ = "pending_menus"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    customer_phone: Mapped[str] = mapped_column(String(32), index=True)
    # kind: slot | team | service | confirm  (para debug/telemetría)
    kind: Mapped[str] = mapped_column(String(20), default="slot")
    # options_json: [{id: str, title: str, description?: str}, ...]
    options_json: Mapped[str] = mapped_column(Text, default="[]")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


# TTL: si el último menú tiene más de 24h se ignora. Es lo que permite
# WhatsApp para sesiones customer-initiated, y evita que un "1" escrito
# muchos días después se interprete como selección de un menú viejo.
_PENDING_MENU_TTL_HOURS = 24


def save_pending_menu(
    tenant_id: str,
    customer_phone: str,
    kind: str,
    options: list[dict[str, Any]],
) -> None:
    """Guarda/actualiza el menú pendiente para (tenant, teléfono).

    Si ya había uno, lo reemplaza. `options` es una lista de dicts con al
    menos `id` y `title` — cada uno corresponde a una opción clicable.
    """
    payload = json.dumps(options, ensure_ascii=False)
    with Session(engine) as db:
        existing = db.scalar(
            select(PendingMenu).where(
                PendingMenu.tenant_id == tenant_id,
                PendingMenu.customer_phone == customer_phone,
            )
        )
        if existing is None:
            db.add(PendingMenu(
                tenant_id=tenant_id,
                customer_phone=customer_phone,
                kind=kind,
                options_json=payload,
            ))
        else:
            existing.kind = kind
            existing.options_json = payload
            existing.created_at = datetime.utcnow()
        db.commit()


def get_pending_menu(
    tenant_id: str,
    customer_phone: str,
) -> dict[str, Any] | None:
    """Devuelve el menú pendiente activo, o None si no hay o expiró.

    Formato: {"kind": str, "options": [{id, title, ...}], "created_at": dt}
    """
    with Session(engine) as db:
        row = db.scalar(
            select(PendingMenu).where(
                PendingMenu.tenant_id == tenant_id,
                PendingMenu.customer_phone == customer_phone,
            )
        )
        if row is None:
            return None
        age = datetime.utcnow() - row.created_at
        if age.total_seconds() > _PENDING_MENU_TTL_HOURS * 3600:
            return None
        try:
            options = json.loads(row.options_json or "[]")
        except json.JSONDecodeError:
            options = []
        return {
            "kind": row.kind,
            "options": options,
            "created_at": row.created_at,
        }


def clear_pending_menu(tenant_id: str, customer_phone: str) -> None:
    """Borra el menú pendiente tras resolverlo (o al empezar un turno nuevo sin opciones)."""
    with Session(engine) as db:
        row = db.scalar(
            select(PendingMenu).where(
                PendingMenu.tenant_id == tenant_id,
                PendingMenu.customer_phone == customer_phone,
            )
        )
        if row is not None:
            db.delete(row)
            db.commit()


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


# Bloque FORMATO universal: se inyecta en todo system_prompt generado.
# Estas reglas son independientes del tenant — son disciplina de escritura
# para WhatsApp. Duplican las reglas más estrictas que teníamos en el YAML
# y mantienen a cualquier cliente nuevo con el mismo nivel de calidad sin
# tener que editar el prompt a mano.
_FORMATO_WHATSAPP = """════════════════════════════════════════════════════════════════════
FORMATO (reglas ESTRICTAS — cualquier infracción arruina el mensaje)
════════════════════════════════════════════════════════════════════

1) NADA DE LISTAS. Nunca uses ninguna de estas formas de listar:
   - Números: "1. 10:00", "2. 10:30"
   - Emojis numerados: "1️⃣ 10:00", "🥇 🥈 🥉"
   - Guiones o asteriscos al inicio de línea: "- 10:00", "* 10:00"
   - Cualquier marcador que ponga cada opción en su propia línea.
   TODAS las opciones van en UNA frase, separadas con "o" y/o comas.
   BIEN:  "tengo a las 10:30, a las 12 o a las 13:30, ¿cuál te cuadra?"
   MAL:   "estas opciones:\\n1️⃣ 10:30\\n2️⃣ 12:00\\n3️⃣ 13:30"

2) NADA DE MARKDOWN. No uses asteriscos, dobles asteriscos ni guiones
   bajos para marcar negritas ni cursivas. Texto plano.

3) UN SOLO EMOJI POR MENSAJE como MÁXIMO, y muchos mensajes van sin
   ninguno. Nunca combines varios. BIEN: "¡hasta mañana!". MAL:
   "¡Perfecto! 🎉 Tu cita está reservada 📅 💇‍♀️ ✨".

4) NADA DE RESÚMENES CON ICONOS. No hagas "fichas" con un emoji
   delante de cada dato (📅/🗓️/👤/⏰). Resume en prosa, dos frases
   naturales máximo, sin decoración.

5) BREVE. Si el cliente escribe corto ("vale", "gracias"), tú también.
   Dos líneas máximo salvo que estés proponiendo huecos o confirmando.

6) VARÍA expresiones: "te va bien", "te cuadra", "¿cómo lo ves?",
   "¿te encaja?". No suenes repetitiva."""

def _build_flujo_reserva(has_team: bool, professional_word: str) -> str:
    """Construye el bloque FLUJO DE RESERVA adaptado al tenant.

    - Si el tenant tiene equipo con más de un miembro, incluye el PASO de
      preguntar por el profesional antes de mirar huecos.
    - Si no (equipo vacío o 1 solo), omite ese paso y renumera — si no, el
      agente se queda en bucle pidiendo una preferencia que no tiene sentido
      (caso Test Abogado: 0 abogados en la BD y Ana preguntaba ¿tienes
      preferencia de abogado? sin parar).
    - El `professional_word` se adapta al sector (p.ej. "peluquero/a" vs
      "profesional") para que la frase de pregunta suene natural.
    """
    header = (
        "════════════════════════════════════════════════════════════════════\n"
        "FLUJO DE RESERVA (orden estricto — no saltes pasos)\n"
        "════════════════════════════════════════════════════════════════════\n\n"
        "Antes de llamar a crear_reserva necesitas TODOS estos datos:\n\n"
    )
    pasos: list[str] = []
    pasos.append('PASO {n} — SERVICIO. Si el cliente dice sólo "cita", pregúntaselo.')
    if has_team:
        pasos.append(
            f"PASO {{n}} — {professional_word.upper()}. OBLIGATORIO preguntar ANTES de\n"
            f'   mirar huecos. Frase tipo: "¿tienes preferencia o te da igual?".'
        )
    pasos.append(
        "PASO {n} — HORA. Consulta SIEMPRE disponibilidad con la función antes\n"
        "   de proponer. Máximo 3 opciones, todas en la MISMA frase."
    )
    pasos.append('PASO {n} — NOMBRE DEL CLIENTE. "¿a qué nombre pongo la cita?".')
    pasos.append(
        "PASO {n} — CONFIRMACIÓN EXPLÍCITA. Resume en prosa (sin iconos) y\n"
        '   pregunta "¿lo confirmo?". Espera un "sí" claro antes de crear.'
    )
    numbered = [p.format(n=i + 1) for i, p in enumerate(pasos)]
    return header + "\n".join(numbered)


def _professional_word_for(sector: str | None) -> str:
    """Devuelve cómo llamar al profesional según el sector del tenant.

    Deliberadamente conservador: solo mapeos que ya sabemos que suenan bien
    en español. Por defecto "profesional" — genérico y nunca incorrecto.
    """
    s = (sector or "").lower()
    if "peluqu" in s or "barber" in s or "estétic" in s or "estetic" in s:
        return "peluquero/a"
    if "abogad" in s or "legal" in s or "jurídic" in s or "juridic" in s:
        return "abogado/a"
    if "médic" in s or "medic" in s or "clínic" in s or "clinic" in s or "dental" in s or "odonto" in s:
        return "profesional sanitario"
    return "profesional"


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
    emoji_line = (
        "Puedes usar emojis con moderación (1 por mensaje como máximo)."
        if t.assistant_emoji else "No uses emojis."
    )

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

{_FORMATO_WHATSAPP}

{_build_flujo_reserva(has_team=len(t.equipo) > 1, professional_word=_professional_word_for(t.sector))}

Reglas operativas:
- Antes de proponer hora, consulta SIEMPRE disponibilidad con consultar_disponibilidad.
- Confirma SIEMPRE la hora elegida antes de crear, mover o cancelar una reserva.
- No pidas ni almacenes datos bancarios. El pago se hace en el local.
- Si el cliente dice "mover mi cita" o "cambiar", busca primero su reserva por su teléfono.
- Si el cliente pregunta algo fuera de tu alcance, ofrécele contactar al {t.assistant_fallback_phone or '(teléfono de contacto)'}.
"""
    return prompt.strip()


# ---------------------------------------------------------------------
#  Helpers de horario (formato business_hours)
#
#  El formato JSON de `business_hours` soporta múltiples franjas por día:
#    {"mon": ["09:00", "12:00", "14:00", "20:00"]}  → dos franjas 09-12 y 14-20
#    {"mon": ["09:00", "20:00"]}                     → una franja continua
#    {"mon": ["closed"]}                             → cerrado
#  Pares consecutivos = una franja. Fuente canónica para el horario del bot.
# ---------------------------------------------------------------------

_DAY_KEYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


def ranges_for_weekday(business_hours: dict, weekday_py: int) -> list[tuple]:
    """Devuelve las franjas de un día como lista de (time, time).

    `weekday_py`: 0=lunes ... 6=domingo (mismo convenio que `datetime.weekday()`).
    Devuelve [] si el día está cerrado o si los datos son inconsistentes.
    """
    from datetime import time as _time
    if weekday_py < 0 or weekday_py > 6:
        return []
    bh = business_hours or {}

    # Fallback: schema plano {"open": "09:00", "close": "20:00"} (YAML legacy)
    if "open" in bh or "close" in bh:
        try:
            o_h, o_m = (bh.get("open") or "09:00").split(":")
            c_h, c_m = (bh.get("close") or "20:00").split(":")
            return [(_time(int(o_h), int(o_m)), _time(int(c_h), int(c_m)))]
        except (ValueError, AttributeError):
            return []

    v = bh.get(_DAY_KEYS[weekday_py])
    if not v or (isinstance(v, list) and v and v[0] == "closed"):
        return []

    out: list[tuple] = []
    # Pares consecutivos: (v[0],v[1]), (v[2],v[3]), ...
    for i in range(0, len(v) - 1, 2):
        try:
            h0, m0 = str(v[i]).split(":")
            h1, m1 = str(v[i + 1]).split(":")
            open_t = _time(int(h0), int(m0))
            close_t = _time(int(h1), int(m1))
            if close_t > open_t:  # ignora rangos inválidos (fin<=inicio)
                out.append((open_t, close_t))
        except (ValueError, AttributeError):
            continue
    return out


# ---------------------------------------------------------------------
#  Composición del prompt de VOZ (Ana en ElevenLabs)
#
#  El equivalente a render_system_prompt pero para el agente de voz. Tiene una
#  estructura distinta porque el canal es distinto (teléfono vs WhatsApp) y
#  porque el prompt está optimizado para latencia: cada byte que añadas sube
#  el prefill time por turno.
#
#  Los placeholders {{system__time_utc}} y {{system__caller_id}} los inyecta
#  ElevenLabs en runtime — aquí los preservamos escapando las llaves.
# ---------------------------------------------------------------------

_WEEKDAY_ES = {0: "lun", 1: "mar", 2: "mié", 3: "jue", 4: "vie", 5: "sáb", 6: "dom"}
_WEEKDAY_ES_LARGO = {0: "lunes", 1: "martes", 2: "miércoles", 3: "jueves", 4: "viernes", 5: "sábado", 6: "domingo"}


def _horario_legible(business_hours: dict) -> str:
    """Resume el horario semanal en una frase compacta.

    Ejemplos:
      lun-sáb 09:30-20:30. Domingo cerrado.
      lun-vie 09:00-14:00, 17:00-20:00. Sáb-dom cerrado.
      lun 09-20, mar cerrado, mié 10-14, 16-20...  (caso general irregular)

    Considera múltiples franjas por día. Si todos los días abiertos tienen la
    misma lista de franjas, colapsa a un solo rango "L-V ...".
    """
    if not business_hours:
        return ""

    def _fmt_ranges(ranges: list[tuple]) -> str:
        return ", ".join(f"{o.strftime('%H:%M')}-{c.strftime('%H:%M')}" for o, c in ranges)

    dias_orden = list(_DAY_KEYS)
    rangos_por_dia = {d: ranges_for_weekday(business_hours, i) for i, d in enumerate(dias_orden)}
    abiertos = [d for d in dias_orden if rangos_por_dia[d]]
    cerrados = [d for d in dias_orden if not rangos_por_dia[d]]

    if not abiertos:
        return "Cerrado toda la semana."

    # ¿Todos los abiertos con la misma lista de franjas? (comparando strings)
    firmas = {_fmt_ranges(rangos_por_dia[d]) for d in abiertos}
    if len(firmas) == 1 and len(abiertos) >= 2:
        primero = _WEEKDAY_ES[dias_orden.index(abiertos[0])]
        ultimo = _WEEKDAY_ES[dias_orden.index(abiertos[-1])]
        rango_comun = next(iter(firmas))
        cerrado_frase = ""
        if cerrados:
            if len(cerrados) == 1 and cerrados[0] == "sun":
                cerrado_frase = " Domingo cerrado."
            else:
                cerrado_frase = " Cerrado: " + ", ".join(_WEEKDAY_ES[dias_orden.index(d)] for d in cerrados) + "."
        return f"{primero}-{ultimo} {rango_comun}.{cerrado_frase}"

    # Caso irregular: día por día
    partes = []
    for d in dias_orden:
        lbl = _WEEKDAY_ES[dias_orden.index(d)]
        if not rangos_por_dia[d]:
            partes.append(f"{lbl} cerrado")
        else:
            partes.append(f"{lbl} {_fmt_ranges(rangos_por_dia[d])}")
    return ". ".join(partes) + "."


def _peluqueros_legible(peluqueros: list[dict]) -> str:
    """'Mario (lun-sáb), Marcos (solo miércoles).' o '' si no hay."""
    if not peluqueros:
        return ""
    dias_orden = list(range(7))
    frases = []
    for p in peluqueros:
        nombre = p.get("nombre") or "?"
        dias = sorted(set(p.get("dias_trabajo") or dias_orden))
        if dias == dias_orden:
            dias_txt = "todos los días"
        elif dias == [0, 1, 2, 3, 4, 5]:
            dias_txt = "lun-sáb"
        elif dias == [0, 1, 2, 3, 4]:
            dias_txt = "lun-vie"
        elif len(dias) == 1:
            dias_txt = f"solo {_WEEKDAY_ES_LARGO[dias[0]]}"
        else:
            # Rango contiguo
            if dias == list(range(dias[0], dias[-1] + 1)):
                dias_txt = f"{_WEEKDAY_ES[dias[0]]}-{_WEEKDAY_ES[dias[-1]]}"
            else:
                dias_txt = ", ".join(_WEEKDAY_ES[d] for d in dias)
        frases.append(f"{nombre} ({dias_txt})")
    return ", ".join(frases) + "."


def render_voice_prompt(tenant: dict) -> str:
    """Construye el prompt de Ana parametrizado desde los datos del tenant.

    Acepta un dict (el formato que devuelve Tenant.to_dict()) para no acoplar
    este módulo al ORM. Mantiene las reglas duras y el flujo RESERVA que están
    optimizados para latencia; solo cambian nombre del negocio, servicios,
    horario, peluqueros y teléfono de fallback.
    """
    nombre_negocio = tenant.get("name") or "el negocio"
    assistant_name = (tenant.get("assistant") or {}).get("name") or "Ana"
    fallback = ((tenant.get("assistant") or {}).get("fallback_phone") or "").strip()

    # Servicios en línea: "Corte mujer 45min 22€ · Corte hombre 30min 15€"
    servicios = tenant.get("services") or []
    servicios_txt = " · ".join(
        f"{s.get('nombre')} {int(s.get('duracion_min') or 0)}min {_precio_fmt(s.get('precio'))}"
        for s in servicios if s.get("nombre")
    ) or "(sin servicios configurados)"

    horario_txt = _horario_legible(tenant.get("business_hours") or {})
    peluqueros_txt = _peluqueros_legible(tenant.get("peluqueros") or [])

    # Línea de fallback hablable ("nueve uno cero cero cero cero cero cero cero")
    if fallback:
        fallback_hablable = " ".join(list(fallback.replace(" ", "")))
        fallback_frase = f'"me da problemas el sistema, ¿puedes llamar al {fallback_hablable}?"'
    else:
        fallback_frase = '"me da problemas el sistema, ¿puedes volver a intentarlo en un rato?"'

    # Pregunta inicial ante "corte" (solo relevante si hay varios cortes)
    pregunta_corte = ""
    nombres_serv = [s.get("nombre", "").lower() for s in servicios]
    if any("corte" in n and ("mujer" in n or "hombre" in n) for n in nombres_serv):
        pregunta_corte = ' (si "corte" → "¿mujer o hombre?")'

    # Construimos el prompt. Ojo con las llaves: `{{system__...}}` son variables
    # ElevenLabs, NO Python. En f-strings hay que duplicarlas: `{{{{...}}}}`.
    prompt = f"""Eres {assistant_name}, recepcionista de {nombre_negocio}. Hablas por teléfono en español de España. Eres una persona, no un bot. Si preguntan: "Soy {assistant_name}, trabajo aquí".

## Negocio
Horario {horario_txt or 'a confirmar'}
Servicios: {servicios_txt}.
{f'Peluqueros: {peluqueros_txt}' if peluqueros_txt else ''}
Solo recitas precios/horarios si preguntan.

## Contexto
FECHA: {{{{system__time_utc}}}} (UTC). Zona: {tenant.get('timezone') or 'Europe/Madrid'}.
Fechas a tools en ISO local SIN "Z": 2026-04-22T10:00:00.
Teléfono: pasa SIEMPRE {{{{system__caller_id}}}} como `telefono_cliente` en cada tool call, sin decirlo. NUNCA preguntes el teléfono salvo si caller_id es exactamente "unknown"/"anonymous"/"null"/"-"/vacío.

## Estilo
Frases cortas, tono cercano ("vale", "a ver", "perfecto", "venga"). Varía muletillas. Nunca ISO al hablar — "a las cinco y media". UNA pregunta por turno. Sin listas ni emojis. Gracias → "a ti".

## Fechas al hablar
- Hoy → "hoy", "esta tarde". Mañana → "mañana". Pasado mañana → "pasado mañana".
- 3-6 días → solo día ("el jueves").
- 7+ días → "el [día] [número]" ("el lunes cuatro").
- **PROHIBIDO combinar término relativo + día de semana**. NUNCA digas "mañana el jueves" ni "pasado mañana el viernes". "Hoy/mañana/pasado mañana" van SOLOS, sin día de semana. Solo nombra día de semana cuando NO uses término relativo (3+ días).
Ej: "te espero esta tarde a las seis", "te espero mañana a las diez", "venga, el viernes a las cinco y media".

## Fillers antes de tool calls (obligatorio, nunca silencio)
En el MISMO turno que la tool, varía: "vale, te miro un momento...", "a ver, compruebo la agenda...", "un segundo que lo miro...". Luego sigue: "...pues tengo a las diez, a las once o a la una, ¿cuál te va?"

## Qué puedes hacer
Solo reservar/mover/cancelar. Nada de WhatsApp/SMS/email.

## Reglas duras
1. Antes de proponer hora → consultar_disponibilidad SIEMPRE. Nunca inventes.
2. Antes de confirmar reserva → crear_reserva SIEMPRE.
3. Mover/cancelar → primero buscar_reserva_cliente.
4. Tool error retryable:true → UN reintento con filler. Si falla: {fallback_frase} (hablado). Lista vacía SIN error → ofrece otro día/peluquero, no es fallo.
5. Nombre al final, antes de crear_reserva. Si ya lo dijo, úsalo — nunca repreguntes.
6. Extrae TODOS los datos del turno. No repreguntes nada ya dicho.
7. Máximo tres huecos por turno.
8. Peluquero: si el cliente NO lo menciona, NO preguntes — deja `peluquero_preferido` vacío y ofrece huecos sin nombrar peluquero. Solo nombras si el cliente pregunta o si diferenciar aporta.
9. Si consultar_disponibilidad devuelve `aviso`, léelo.

## Flujo RESERVA — orden OBLIGATORIO
Orden: **servicio → cuándo → NOMBRE → consultar → ofrecer → elegir → crear**. El nombre SIEMPRE va ANTES de consultar_disponibilidad. Cuando el cliente elige hueco, vas directo a crear_reserva sin preguntar nada.

1. Servicio{pregunta_corte}.
2. Cuándo.
3. **Nombre — OBLIGATORIO antes de consultar**. Si no se presentó, di: "vale, ¿a qué nombre te lo pongo?". Si ya dijo "soy Luis", salta al 4.
4. Filler + consultar_disponibilidad. Rango: mañana=09:30-14:00, tarde=15:00-20:30. `peluquero_preferido` vacío salvo que lo pidiera. UNA sola llamada, no repitas.
5. Ofrece máx 3 huecos naturales, sin nombrar peluquero (regla 8).
6. Cliente elige hueco → MISMO TURNO: "genial, te la dejo apuntada..." + crear_reserva. PROHIBIDO preguntar nombre aquí (ya lo tienes). `telefono_cliente = {{{{system__caller_id}}}}` automático. Título EXACTO: `Nombre — Servicio (Peluquero)` o `Nombre — Servicio (sin preferencia)`. Al ok:true: cierre natural usando Fechas al hablar — "hecho Juan, te espero esta tarde a las seis". Luego "¿algo más?"

## Flujo MOVER
1. NO pidas teléfono. Filler + buscar_reserva_cliente con `telefono_cliente = {{{{system__caller_id}}}}`.
2. Lee cita con fecha natural: "tienes cita mañana a las diez con Mario". ¿Para cuándo la mueves?
3. Nueva franja → filler + consultar_disponibilidad → ofreces.
4. Elegido → filler + mover_reserva con event_id. Confirma natural.

## Flujo CANCELAR
1. NO pidas teléfono. Filler + buscar_reserva_cliente con `telefono_cliente = {{{{system__caller_id}}}}`.
2. Lee cita natural + "¿te la cancelo?".
3. Sí → filler + cancelar_reserva. "Listo, queda cancelada."

## Cierre
"Gracias por llamar, hasta luego." o "Venga, hasta luego."
"""
    # Quitar líneas vacías que hayan quedado por tener peluqueros_txt vacío
    return "\n".join(l for l in prompt.split("\n") if l.strip() != "")


def _precio_fmt(p) -> str:
    try:
        f = float(p or 0)
        if f == int(f):
            return f"{int(f)}€"
        return f"{f:g}€"
    except (TypeError, ValueError):
        return "0€"


# ---------------------------------------------------------------------
#  Crear todas las tablas (idempotente). Lo llamamos al arrancar.
# ---------------------------------------------------------------------

Base.metadata.create_all(engine)


# ---------------------------------------------------------------------
#  Auto-migraciones sencillas (SQLite).
#
#  create_all() sólo añade tablas nuevas; NO altera tablas existentes
#  cuando añadimos columnas al modelo. Aquí parcheamos a mano las
#  columnas que sabemos que pueden faltar en despliegues antiguos.
#
#  Cada bloque es idempotente: si la columna ya existe, no hace nada.
# ---------------------------------------------------------------------

def _auto_migrate_sqlite() -> None:
    """Añade columnas nuevas a tablas existentes cuando faltan, y aplica
    renombrados de tabla que conserven los datos.

    Solo aplica a SQLite (el único motor que usamos hoy). Se ejecuta
    una vez al importar el módulo.
    """
    if not settings.database_url.startswith("sqlite"):
        return

    # Renombrado de tabla `peluqueros` → `equipo` (mantiene filas existentes).
    # Si la tabla nueva ya existe (porque create_all() la acabó de crear en un
    # arranque limpio), no hace nada. Si ambas existen por un deploy raro,
    # dejamos `equipo` y dropeamos `peluqueros` tras mover datos.
    try:
        with engine.begin() as conn:
            names = {
                r[0] for r in conn.execute(text(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )).fetchall()
            }
            if "peluqueros" in names and "equipo" not in names:
                conn.execute(text("ALTER TABLE peluqueros RENAME TO equipo"))
            elif "peluqueros" in names and "equipo" in names:
                # Caso raro: ambas. Mover filas de peluqueros a equipo solo si
                # equipo está vacío, y dropear la vieja.
                existing = conn.execute(text("SELECT COUNT(*) FROM equipo")).scalar() or 0
                if existing == 0:
                    conn.execute(text(
                        "INSERT INTO equipo (id, tenant_id, nombre, calendar_id, dias_trabajo_json, orden) "
                        "SELECT id, tenant_id, nombre, calendar_id, dias_trabajo_json, orden FROM peluqueros"
                    ))
                conn.execute(text("DROP TABLE peluqueros"))
    except Exception as exc:  # pragma: no cover - best-effort
        import logging
        logging.getLogger(__name__).warning(
            "rename peluqueros→equipo falló (%s). Se continúa.", exc,
        )

    # (tabla, columna, DDL para ADD COLUMN)
    migrations: list[tuple[str, str, str]] = [
        ("tenants", "kind",
         "ALTER TABLE tenants ADD COLUMN kind VARCHAR(20) DEFAULT 'contracted'"),
        # --- Agente de voz ElevenLabs ---
        ("tenants", "voice_agent_id",
         "ALTER TABLE tenants ADD COLUMN voice_agent_id VARCHAR(100) DEFAULT ''"),
        ("tenants", "voice_prompt",
         "ALTER TABLE tenants ADD COLUMN voice_prompt TEXT DEFAULT ''"),
        ("tenants", "voice_voice_id",
         "ALTER TABLE tenants ADD COLUMN voice_voice_id VARCHAR(100) DEFAULT ''"),
        ("tenants", "voice_stability",
         "ALTER TABLE tenants ADD COLUMN voice_stability FLOAT DEFAULT 0.67"),
        ("tenants", "voice_similarity_boost",
         "ALTER TABLE tenants ADD COLUMN voice_similarity_boost FLOAT DEFAULT 0.8"),
        ("tenants", "voice_speed",
         "ALTER TABLE tenants ADD COLUMN voice_speed FLOAT DEFAULT 1.04"),
        ("tenants", "voice_last_sync_at",
         "ALTER TABLE tenants ADD COLUMN voice_last_sync_at DATETIME"),
        ("tenants", "voice_last_sync_status",
         "ALTER TABLE tenants ADD COLUMN voice_last_sync_status VARCHAR(400) DEFAULT ''"),
        # --- Service: flags del portal del cliente ---
        ("services", "activo",
         "ALTER TABLE services ADD COLUMN activo BOOLEAN DEFAULT 1"),
        ("services", "equipo_json",
         "ALTER TABLE services ADD COLUMN equipo_json TEXT DEFAULT '[]'"),
        # --- MiembroEquipo: campos visuales/agenda añadidos para el portal ---
        ("equipo", "color",
         "ALTER TABLE equipo ADD COLUMN color VARCHAR(16) DEFAULT '#059669'"),
        ("equipo", "turnos_json",
         "ALTER TABLE equipo ADD COLUMN turnos_json TEXT DEFAULT '[[\"10:00\",\"20:00\"]]'"),
        ("equipo", "vacaciones_json",
         "ALTER TABLE equipo ADD COLUMN vacaciones_json TEXT DEFAULT '[]'"),
    ]

    try:
        with engine.begin() as conn:
            for table, column, ddl in migrations:
                # PRAGMA devuelve (cid, name, type, notnull, dflt_value, pk)
                rows = conn.execute(text(f"PRAGMA table_info({table})")).fetchall()
                cols = {r[1] for r in rows}
                if not rows:
                    # La tabla no existe todavía (p.ej. primer arranque
                    # limpio). create_all la acaba de crear con esquema
                    # completo, así que no hay nada que migrar.
                    continue
                if column not in cols:
                    conn.execute(text(ddl))
    except Exception as exc:  # pragma: no cover - best-effort
        import logging
        logging.getLogger(__name__).warning(
            "auto-migrate falló (%s). Se continúa con el esquema actual.", exc,
        )


_auto_migrate_sqlite()


# ---------------------------------------------------------------------
#  Seed one-shot de peluqueros desde tenants.yaml → BD
#
#  Hasta ahora los peluqueros se leían siempre del YAML y se mergeaban en
#  `tenants.py`. Con la tabla `peluqueros` nueva, la BD manda. Para no romper
#  instalaciones existentes (Railway con pelu_demo ya en vivo), la primera vez
#  que arranca este código copia los peluqueros del YAML a la tabla para cada
#  tenant que todavía no tenga ninguno. Idempotente: una vez copiados, el
#  arranque siguiente no hace nada.
# ---------------------------------------------------------------------

def _seed_equipo_from_yaml() -> None:
    """Copia los 'peluqueros' del YAML legacy a la tabla `equipo` si el tenant
    aún no tiene miembros. Idempotente: una vez copiados, no se vuelven a
    tocar."""
    import logging
    log = logging.getLogger(__name__)
    try:
        import yaml as _yaml
        yaml_path = pathlib.Path(settings.tenants_file)
        if not yaml_path.exists():
            return
        data = _yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}
        tenants_yaml = data.get("tenants") or []
        if not tenants_yaml:
            return

        with Session(engine) as s:
            for yt in tenants_yaml:
                tid = yt.get("id")
                # El YAML antiguo usa la clave "peluqueros"; lo respetamos.
                pelus = yt.get("peluqueros") or yt.get("equipo") or []
                if not tid or not pelus:
                    continue
                t = s.get(Tenant, tid)
                if t is None:
                    continue
                existing = s.query(MiembroEquipo).filter(MiembroEquipo.tenant_id == tid).count()
                if existing > 0:
                    continue
                for i, p in enumerate(pelus):
                    row = MiembroEquipo(
                        tenant_id=tid,
                        nombre=(p.get("nombre") or "").strip() or f"Miembro {i+1}",
                        calendar_id=(p.get("calendar_id") or "").strip(),
                        orden=i,
                    )
                    row.dias_trabajo = p.get("dias_trabajo") or [0, 1, 2, 3, 4, 5]
                    s.add(row)
                log.info("seed equipo desde YAML: tenant=%s insertados=%d", tid, len(pelus))
            s.commit()
    except Exception as exc:  # pragma: no cover - best-effort
        log.warning("seed equipo desde YAML falló (%s). Arranque continúa.", exc)


_seed_equipo_from_yaml()
