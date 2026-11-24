"""Bot de Telegram de Compra Óptima: mandar mensajes y recibir los /start del link "Seguir precio".

El bot (uno nuevo, sólo de Compra Óptima) se crea con @BotFather y su token va en COMPRA_BOT_TOKEN
(Render y GitHub Actions). No usar el bot del tracker: se le registraría el webhook.
En Render se registra solo el webhook al arrancar: Telegram le manda cada mensaje del bot a
/api/telegram/<secreto>, donde el secreto sale del token (no hace falta otra variable).
"""
from __future__ import annotations

import hashlib
import logging
import os
from functools import lru_cache

import httpx

log = logging.getLogger("telegram")
API = "https://api.telegram.org/bot{token}/{method}"


def token() -> str:
    return (os.getenv("COMPRA_BOT_TOKEN") or "").strip()


def enabled() -> bool:
    return bool(token())


def webhook_secret() -> str:
    return hashlib.sha256(("compra-optima:" + token()).encode()).hexdigest()[:32]


def call(method: str, payload: dict | None = None, client: httpx.Client | None = None) -> dict:
    c = client or httpx.Client(timeout=20)
    r = c.post(API.format(token=token(), method=method), json=payload or {})
    data = r.json() if r.content else {}
    if not data.get("ok"):
        raise RuntimeError(f"Telegram {method}: {data.get('description') or r.status_code}")
    return data.get("result") or {}


def send(chat_id: str | int, text: str, client: httpx.Client | None = None) -> bool:
    try:
        call("sendMessage", {"chat_id": chat_id, "text": text, "disable_web_page_preview": True}, client)
        return True
    except Exception as e:  # noqa: BLE001 — un chat que bloqueó al bot no corta los demás avisos
        log.warning("No se pudo mandar a %s: %s", chat_id, e)
        return False


@lru_cache(maxsize=1)
def bot_username() -> str:
    return call("getMe").get("username", "")


def deep_link(start_token: str) -> str:
    return f"https://t.me/{bot_username()}?start={start_token}"


def set_webhook(base_url: str) -> None:
    url = f"{base_url.rstrip('/')}/api/telegram/{webhook_secret()}"
    call("setWebhook", {"url": url, "allowed_updates": ["message"], "drop_pending_updates": False})
    log.info("Webhook de Telegram registrado")
