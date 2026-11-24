"""Comentarios de "¿Te sirvió?": llegan por Telegram al dueño (el mismo bot del tracker sirve).

Sin TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID sólo se escriben en el log (en Render se ven en Logs).
No se guarda nada de quien lo manda: sólo lo que escribió y de qué producto era la búsqueda.
"""
from __future__ import annotations

import logging
import os

import httpx

log = logging.getLogger("feedback")


def format_message(fb: dict) -> str:
    head = "👍 Le sirvió" if fb.get("useful") else "👎 No le sirvió mucho"
    lines = [f"{head} · Compra Óptima", f"Producto: {fb.get('product') or '?'}"]
    if fb.get("recommended"):
        lines.append(f"Recomendado: {fb['recommended']} ({fb.get('matching', '?')} modelos cumplían)")
    if fb.get("queries"):
        lines.append("Búsquedas: " + ", ".join(fb["queries"]))
    if fb.get("comment"):
        lines += ["", f"«{fb['comment']}»"]
    return "\n".join(lines)


def send(fb: dict, client: httpx.Client | None = None) -> bool:
    text = format_message(fb)
    log.info("Comentario recibido:\n%s", text)
    token, chat = os.getenv("TELEGRAM_BOT_TOKEN", "").strip(), os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if not (token and chat):
        return False
    c = client or httpx.Client(timeout=15)
    try:
        r = c.post(f"https://api.telegram.org/bot{token}/sendMessage", json={"chat_id": chat, "text": text})
        return r.status_code == 200
    except httpx.HTTPError as e:
        log.warning("No se pudo mandar a Telegram: %s", e)
        return False
