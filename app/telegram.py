"""Integración con Telegram Bot API.

Añade un canal de texto nuevo al producto reutilizando el agente canal-agnóstico
(`app.agent.reply`). Pensado como entorno de staging / canal secundario del
producto de reservas: mismo LLM, mismas reglas de negocio, mismo historial
guardado en la BD, distinto transport.

Arquitectura:

- El bot de Telegram se crea una sola vez vía @BotFather. Su token se guarda en
  `TELEGRAM_BOT_TOKEN`.
- El webhook entrante se configura con `scripts/setup_telegram_bot.py` una vez
  tras cada despliegue; Telegram firma cada update con un secreto que ponemos
  en `TELEGRAM_WEBHOOK_SECRET` para que `/telegram/webhook` pueda autenticar.
- Todos los usuarios que hablan con el bot caen contra un tenant configurable
  vía `TELEGRAM_DEFAULT_TENANT_ID` (MVP monotenant). Si el env está vacío se
  usa el primer tenant `contracted` de la BD como fallback.
- El id de conversación que guardamos en `messages.customer_phone` es
  `tg:<chat_id>` — prefijo explícito para no mezclar con teléfonos reales.
- `AgentReply.interactive` (lista/botones) se traduce a inline_keyboard de
  Telegram; si el cliente pulsa un botón, el `callback_data` se usa como el
  "texto" del siguiente turno, del mismo modo que los interactive_reply de
  WhatsApp mapeaban a texto sintético.

Nada de este módulo abre cuenta nueva ni gasta dinero: el bot es gratis, los
webhooks son gratis, el tráfico corre por Railway.
"""
from __future__ import annotations

import logging
from typing import Any

import httpx

log = logging.getLogger(__name__)

API_BASE = "https://api.telegram.org"
_TIMEOUT = httpx.Timeout(15.0, connect=5.0)
# Telegram impone 4096 caracteres por mensaje. Dejamos margen por si añadimos
# footer de debug en el futuro.
_MAX_MESSAGE_LEN = 4000


class TelegramError(Exception):
    """Error hablando con la Bot API, con mensaje ya formateado."""


# ---------------------------------------------------------------------------
#  Cliente minimalista de la Bot API
# ---------------------------------------------------------------------------

class TelegramClient:
    """Wrapper fino sobre httpx para los métodos que usa el bot de reservas.

    No pretende cubrir toda la API — solo los 6 métodos que necesitamos:
    sendMessage, sendChatAction, answerCallbackQuery, setWebhook, deleteWebhook
    y getMe. Cada método devuelve el dict `result` de la Bot API o lanza
    `TelegramError` con mensaje legible.
    """

    def __init__(self, bot_token: str, *, timeout: httpx.Timeout = _TIMEOUT) -> None:
        token = (bot_token or "").strip()
        if not token:
            raise TelegramError("TELEGRAM_BOT_TOKEN vacío. Configúralo en Railway.")
        self._token = token
        self._timeout = timeout

    def _url(self, method: str) -> str:
        return f"{API_BASE}/bot{self._token}/{method}"

    def _call(self, method: str, payload: dict[str, Any]) -> dict[str, Any]:
        url = self._url(method)
        try:
            r = httpx.post(url, json=payload, timeout=self._timeout)
        except httpx.HTTPError as e:
            raise TelegramError(f"Red caída hablando con Telegram ({method}): {e}") from e
        # Telegram devuelve SIEMPRE 200 + JSON {ok, result|description}. Los
        # errores (chat no existe, bot baneado, etc.) vienen con ok=false.
        try:
            body = r.json()
        except ValueError as e:
            raise TelegramError(
                f"Telegram respondió algo que no es JSON en {method}: {r.text[:200]}"
            ) from e
        if r.status_code >= 400 or not body.get("ok"):
            desc = body.get("description") or r.text[:200]
            raise TelegramError(f"{method} → HTTP {r.status_code}: {desc}")
        return body.get("result") or {}

    # --- Envíos al usuario --------------------------------------------------

    def send_message(
        self,
        chat_id: int | str,
        text: str,
        *,
        reply_markup: dict[str, Any] | None = None,
        parse_mode: str | None = None,
        disable_web_page_preview: bool = True,
    ) -> dict[str, Any]:
        """sendMessage. Trunca `text` a `_MAX_MESSAGE_LEN` si hace falta."""
        body = (text or "").strip()
        if len(body) > _MAX_MESSAGE_LEN:
            body = body[: _MAX_MESSAGE_LEN - 1] + "…"
        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "text": body,
            "disable_web_page_preview": disable_web_page_preview,
        }
        if parse_mode:
            payload["parse_mode"] = parse_mode
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup
        return self._call("sendMessage", payload)

    def send_chat_action(self, chat_id: int | str, action: str = "typing") -> dict[str, Any]:
        """sendChatAction — pinta "escribiendo..." para que el UX no cuelgue."""
        return self._call("sendChatAction", {"chat_id": chat_id, "action": action})

    def answer_callback_query(
        self,
        callback_query_id: str,
        *,
        text: str = "",
        show_alert: bool = False,
    ) -> dict[str, Any]:
        """answerCallbackQuery — obligatorio tras recibir un callback_query.

        Si no se llama, Telegram deja el botón pulsado en estado "cargando"
        indefinidamente para el cliente. Incluso con texto vacío hay que
        llamarlo para cerrar el loading spinner.
        """
        payload: dict[str, Any] = {"callback_query_id": callback_query_id}
        if text:
            payload["text"] = text[:200]  # Telegram corta a 200
            payload["show_alert"] = show_alert
        return self._call("answerCallbackQuery", payload)

    # --- Configuración del webhook -----------------------------------------

    def set_webhook(
        self,
        url: str,
        *,
        secret_token: str = "",
        allowed_updates: list[str] | None = None,
        drop_pending_updates: bool = False,
    ) -> dict[str, Any]:
        """setWebhook — registra la URL pública donde Telegram POSTeará updates.

        Limitamos `allowed_updates` a los 2 tipos que procesamos ("message" y
        "callback_query") para no recibir ruido (edits, channel posts, etc.).
        """
        payload: dict[str, Any] = {
            "url": url,
            "allowed_updates": allowed_updates or ["message", "callback_query"],
            "drop_pending_updates": drop_pending_updates,
        }
        if secret_token:
            payload["secret_token"] = secret_token
        return self._call("setWebhook", payload)

    def delete_webhook(self, *, drop_pending_updates: bool = False) -> dict[str, Any]:
        """deleteWebhook — usado al rotar token o para debug con getUpdates."""
        return self._call("deleteWebhook", {"drop_pending_updates": drop_pending_updates})

    def get_me(self) -> dict[str, Any]:
        """getMe — verifica que el token es válido y devuelve info del bot."""
        try:
            r = httpx.get(self._url("getMe"), timeout=self._timeout)
        except httpx.HTTPError as e:
            raise TelegramError(f"Red caída hablando con Telegram (getMe): {e}") from e
        body = r.json() if r.content else {}
        if r.status_code >= 400 or not body.get("ok"):
            raise TelegramError(
                f"getMe → HTTP {r.status_code}: {body.get('description') or r.text[:200]}"
            )
        return body.get("result") or {}


# ---------------------------------------------------------------------------
#  Traducción AgentReply → payload de Telegram
# ---------------------------------------------------------------------------

# Máximo de botones por fila en un inline_keyboard. Telegram permite hasta 8,
# pero visualmente 3 ya es el límite cómodo en móvil.
_MAX_BUTTONS_PER_ROW_COMPACT = 3
# Límite duro del callback_data en Telegram (bytes).
_MAX_CALLBACK_DATA_BYTES = 64


def _truncate_callback_data(value: str) -> str:
    """Garantiza que el callback_data quepa en el límite de Telegram (64B).

    Como fallback si un id de interactive excediese (lo cual no debería pasar
    con nuestro formato `slot:YYYY-MM-DDTHH:MM:...`, pero defensivo). Si
    trunca, logea warning porque significa que el id perdió información.
    """
    raw = value or ""
    if len(raw.encode("utf-8")) <= _MAX_CALLBACK_DATA_BYTES:
        return raw
    # Recorta por bytes, no por caracteres, para respetar el límite de la API.
    encoded = raw.encode("utf-8")[: _MAX_CALLBACK_DATA_BYTES]
    truncated = encoded.decode("utf-8", errors="ignore")
    log.warning("callback_data truncado de %d a %d bytes: %s → %s",
                len(raw), len(truncated.encode("utf-8")), raw[:30] + "…", truncated)
    return truncated


def agent_reply_to_payload(reply: Any, chat_id: int | str) -> dict[str, Any]:
    """Convierte un `AgentReply` en payload listo para `send_message`.

    Si la reply tiene interactivos (lista/botones), construye un
    `inline_keyboard` con una tecla por opción. El `id` de cada opción
    (formato documentado en `app.interactive`) viaja como `callback_data` y
    vuelve tal cual cuando el cliente pulsa.

    Diseño: lista → 1 botón por fila (legibilidad + muchos huecos suele ser lista).
             botones → filas de hasta 3 (confirmar sí/no, equipo corto).
    """
    text = getattr(reply, "text", "") or ""
    interactive = getattr(reply, "interactive", None)

    payload: dict[str, Any] = {"chat_id": chat_id, "text": text}

    if not interactive or not interactive.get("options"):
        return payload

    options = interactive["options"] or []
    kind = (interactive.get("type") or "list").lower()

    keyboard: list[list[dict[str, str]]] = []
    if kind == "buttons" and len(options) <= _MAX_BUTTONS_PER_ROW_COMPACT:
        row = [
            {"text": opt.get("title") or "", "callback_data": _truncate_callback_data(opt.get("id") or "")}
            for opt in options
        ]
        keyboard.append(row)
    else:
        # Lista: 1 botón por fila. Legible en móvil incluso con 5-6 huecos.
        for opt in options:
            keyboard.append([
                {"text": opt.get("title") or "", "callback_data": _truncate_callback_data(opt.get("id") or "")}
            ])

    payload["reply_markup"] = {"inline_keyboard": keyboard}
    return payload


# ---------------------------------------------------------------------------
#  Handler de update: pieza central del webhook
# ---------------------------------------------------------------------------

# Tipos de update que procesamos. El resto los descartamos silenciosamente.
_SUPPORTED_UPDATE_KEYS = ("message", "edited_message", "callback_query")


def _extract_turn(update: dict[str, Any]) -> tuple[int | None, str, str | None]:
    """Devuelve (chat_id, user_text, callback_query_id).

    - chat_id: None si la update no trae chat identificable.
    - user_text: texto del mensaje o `callback_data` del botón pulsado. "" si
      no hay nada utilizable.
    - callback_query_id: id del callback si es una pulsación (para hacer
      answer_callback_query), o None.
    """
    # Pulsación de un botón inline.
    cb = update.get("callback_query")
    if cb:
        chat = (cb.get("message") or {}).get("chat") or {}
        chat_id = chat.get("id")
        data = (cb.get("data") or "").strip()
        return chat_id, data, cb.get("id")

    # Mensaje normal (o edición, que procesamos igual).
    msg = update.get("message") or update.get("edited_message")
    if msg:
        chat = msg.get("chat") or {}
        chat_id = chat.get("id")
        text = (msg.get("text") or "").strip()
        return chat_id, text, None

    return None, "", None


def _resolve_tenant_id(preferred_id: str) -> str | None:
    """Elige el tenant al que dirigir los mensajes entrantes de Telegram.

    Orden:
    1. `TELEGRAM_DEFAULT_TENANT_ID` si está configurado Y existe en la BD.
    2. Primer tenant con kind='contracted' y status='active'.
    3. Primer tenant existente.
    4. None (no hay tenants).
    """
    # Import tardío para no circular-import en tiempo de carga de módulo.
    from . import tenants as tn

    if preferred_id:
        t = tn.get_tenant(preferred_id)
        if t is not None:
            return t.get("id") or preferred_id

    all_tenants = tn.load_tenants()
    if not all_tenants:
        return None
    # Preferimos contracted + active
    for t in all_tenants:
        if (t.get("kind") or "").lower() == "contracted" and (t.get("status") or "").lower() == "active":
            return t.get("id")
    return all_tenants[0].get("id")


def handle_update(
    update: dict[str, Any],
    *,
    bot_token: str,
    preferred_tenant_id: str = "",
) -> dict[str, Any]:
    """Punto de entrada desde el endpoint `/telegram/webhook`.

    - No lanza jamás: cualquier error se logea y se devuelve status dict.
      Telegram espera 200 OK rápido o reintenta; dejar que una excepción
      suba a FastAPI provocaría 500 y reintentos infinitos.
    - Persiste el par (user msg, assistant reply) en la tabla `messages`
      usando `tg:<chat_id>` como `customer_phone`.
    - Si la update no trae contenido procesable (stickers, fotos, un join a
      un grupo...) responde ok y se ignora sin llamar al LLM.
    """
    # Parseo defensivo.
    if not isinstance(update, dict):
        return {"ok": False, "error": "update no es dict"}

    # Descarta tipos de update que no procesamos.
    if not any(k in update for k in _SUPPORTED_UPDATE_KEYS):
        return {"ok": True, "ignored": "tipo de update no soportado"}

    chat_id, user_text, callback_query_id = _extract_turn(update)
    if chat_id is None:
        return {"ok": True, "ignored": "sin chat_id"}
    if not user_text:
        # Foto/sticker/ubicación sin caption. Por ahora solo procesamos texto.
        if callback_query_id:
            # Si fue pulsación de botón sin data, ack al menos para cerrar el loading.
            try:
                TelegramClient(bot_token).answer_callback_query(callback_query_id)
            except Exception:
                log.exception("answer_callback_query sin data falló")
        return {"ok": True, "ignored": "sin texto procesable"}

    tenant_id = _resolve_tenant_id(preferred_tenant_id)
    if not tenant_id:
        log.error("Telegram: no hay tenants en la BD; no puedo contestar a chat=%s", chat_id)
        try:
            TelegramClient(bot_token).send_message(
                chat_id,
                "El bot no está conectado a ningún negocio aún. "
                "Configura un tenant desde el panel.",
            )
        except Exception:
            log.exception("Error avisando al usuario de falta de tenant")
        return {"ok": False, "error": "no hay tenants"}

    # Imports tardíos para que este módulo sea importable en tests sin
    # arrastrar calendar_service, openai, etc.
    from . import agent as agent_mod
    from . import db as db_module
    from . import tenants as tn

    tenant = tn.get_tenant(tenant_id)
    if tenant is None:
        log.error("Telegram: tenant %s no existe tras resolver", tenant_id)
        return {"ok": False, "error": f"tenant {tenant_id} no encontrado"}

    customer_key = f"tg:{chat_id}"
    client = TelegramClient(bot_token)

    # ACK del callback_query primero para que el spinner desaparezca.
    if callback_query_id:
        try:
            client.answer_callback_query(callback_query_id)
        except Exception:
            log.exception("answer_callback_query falló (no-op)")

    # Indicador de "escribiendo…" mientras el LLM piensa.
    try:
        client.send_chat_action(chat_id, "typing")
    except Exception:
        log.warning("send_chat_action falló (no-op), seguimos")

    # Historial + persistencia del turno del usuario ANTES del LLM. Si algo
    # falla después, al menos queda el rastro de lo que preguntó.
    try:
        history = db_module.load_history(tenant_id=tenant["id"], customer_phone=customer_key)
    except Exception:
        log.exception("load_history falló — seguimos con historial vacío")
        history = []

    try:
        db_module.save_message(
            tenant_id=tenant["id"],
            customer_phone=customer_key,
            role="user",
            content=user_text,
        )
    except Exception:
        log.exception("save_message (user) falló — seguimos")

    # Llama al agente. Si revienta, mandamos un mensaje de error legible al
    # usuario en vez de dejarle colgado.
    try:
        reply = agent_mod.reply(
            user_message=user_text,
            history=history,
            tenant=tenant,
            caller_phone=customer_key,
        )
    except Exception as e:  # noqa: BLE001
        log.exception("agent.reply falló en telegram handler")
        try:
            client.send_message(
                chat_id,
                "Lo siento, ha habido un error interno. Vuelve a intentarlo en un momento.",
            )
        except Exception:
            log.exception("No he podido avisar al cliente del error")
        return {"ok": False, "error": f"agent: {str(e)[:200]}"}

    # Persiste respuesta y envía.
    try:
        db_module.save_message(
            tenant_id=tenant["id"],
            customer_phone=customer_key,
            role="assistant",
            content=reply.text,
        )
    except Exception:
        log.exception("save_message (assistant) falló — seguimos")

    try:
        payload = agent_reply_to_payload(reply, chat_id=chat_id)
        client.send_message(**payload)
    except TelegramError as e:
        log.error("Telegram sendMessage falló: %s", e)
        return {"ok": False, "error": str(e)[:200]}
    except Exception as e:  # noqa: BLE001
        log.exception("send_message inesperado falló")
        return {"ok": False, "error": str(e)[:200]}

    return {"ok": True, "chat_id": chat_id, "tenant_id": tenant["id"]}
