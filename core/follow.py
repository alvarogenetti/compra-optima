"""Seguir precio: crear el seguimiento, vincularlo a un chat de Telegram y los comandos del bot.

1. En la web, "Seguir precio" crea el seguimiento (sin dueño todavía) con un token y devuelve el link
   t.me/<bot>?start=<token>.
2. La persona toca Iniciar en Telegram: llega "/start <token>" y el seguimiento queda a su nombre.
3. Comandos: /lista (lo que sigue), /dejar_<n> (dejar de seguir), cualquier otra cosa → ayuda.
"""
from __future__ import annotations

import re
import secrets

from sqlalchemy import select

from core import db
from core.candidates import model_key

MAX_FOLLOWS_PER_CHAT = 20


def money(v: float | None) -> str:
    return "$" + f"{v:,.0f}".replace(",", ".") if v else "?"


def create(model: str, category: str, product: str, best: dict) -> str:
    """Guarda un seguimiento todavía sin dueño y devuelve el token para el link de Telegram."""
    tok = secrets.token_urlsafe(16)[:22]
    with db.engine().begin() as c:
        c.execute(db.follows.insert().values(
            token=tok, model=model[:120], model_key=model_key(model)[:120], category=category[:40],
            product=(product or "")[:120], start_price=float(best["price"]),
            start_store=(best.get("store_name") or "")[:80], start_url=best.get("url") or "",
            active=True, created_at=db.now()))
    return tok


def _watcher(c, chat_id: str, name: str) -> int:
    row = c.execute(select(db.watchers.c.id).where(db.watchers.c.chat_id == chat_id)).first()
    if row:
        return row.id
    return c.execute(db.watchers.insert().values(chat_id=chat_id, name=name[:120], created_at=db.now())
                     ).inserted_primary_key[0]


def link(tok: str, chat_id: str, name: str = "") -> tuple[str, dict | None]:
    """Vincula el seguimiento del token al chat. Devuelve (estado, seguimiento):
    'ok', 'ya' (ya lo seguía), 'lleno' (llegó al máximo) o 'no' (token inválido)."""
    with db.engine().begin() as c:
        f = c.execute(select(db.follows).where(db.follows.c.token == tok)).mappings().first()
        if not f:
            return "no", None
        wid = _watcher(c, chat_id, name)
        mine = c.execute(select(db.follows).where(db.follows.c.watcher_id == wid,
                                                  db.follows.c.active.is_(True))).mappings().all()
        if any(m["model_key"] == f["model_key"] for m in mine):
            return "ya", dict(f)
        if len(mine) >= MAX_FOLLOWS_PER_CHAT:
            return "lleno", dict(f)
        if f["watcher_id"] is None:
            c.execute(db.follows.update().where(db.follows.c.id == f["id"]).values(watcher_id=wid))
        else:
            # El link lo abrió otra persona (lo compartieron): le hacemos su propio seguimiento
            data = {k: v for k, v in f.items() if k not in ("id", "token", "watcher_id")}
            data.update(token=secrets.token_urlsafe(16)[:22], watcher_id=wid, active=True,
                        last_alert_price=None, last_alert_at=None, created_at=db.now())
            c.execute(db.follows.insert().values(**data))
        return "ok", dict(f)


def active_for_chat(chat_id: str) -> list[dict]:
    with db.engine().connect() as c:
        q = (select(db.follows).join(db.watchers, db.follows.c.watcher_id == db.watchers.c.id)
             .where(db.watchers.c.chat_id == chat_id, db.follows.c.active.is_(True))
             .order_by(db.follows.c.id))
        return [dict(r) for r in c.execute(q).mappings()]


def stop(chat_id: str, follow_id: int) -> dict | None:
    for f in active_for_chat(chat_id):
        if f["id"] == follow_id:
            with db.engine().begin() as c:
                c.execute(db.follows.update().where(db.follows.c.id == follow_id).values(active=False))
            return f
    return None


HELP = ("Soy el bot de Compra Óptima 👋\n\n"
        "Te aviso cuando un producto que seguís baja DE VERDAD: contra el precio al que se vende "
        "normalmente en todas las tiendas, no contra el \"precio de lista\" inflado.\n\n"
        "Para seguir un producto, buscalo en la web y tocá \"Seguir precio\".\n"
        "/lista: lo que estás siguiendo")


def handle(message: dict) -> str | None:
    """Respuesta del bot a un mensaje (o None si no hay que responder)."""
    chat = message.get("chat") or {}
    if chat.get("type") not in (None, "private"):
        return None
    chat_id = str(chat.get("id", ""))
    text = (message.get("text") or "").strip()
    name = " ".join(x for x in (chat.get("first_name"), chat.get("last_name")) if x)
    if not chat_id or not text:
        return None
    m = re.match(r"^/start(?:@\w+)?\s+(\S+)", text)
    if m:
        state, f = link(m.group(1), chat_id, name)
        if state == "no":
            return "Ese link ya no sirve 🤔. Volvé a la web y tocá \"Seguir precio\" de nuevo.\n\n" + HELP
        if state == "lleno":
            return (f"Ya seguís {MAX_FOLLOWS_PER_CHAT} productos, que es el máximo de la prueba. "
                    "Dejá alguno con /lista y probá de nuevo.")
        if state == "ya":
            return f"Ya estabas siguiendo {f['model']} 👍. Te aviso cuando baje de verdad."
        return (f"Listo ✅ Sigo {f['model']} por vos.\n"
                f"Hoy: {money(f['start_price'])} en {f['start_store'] or 'la tienda más barata'}.\n\n"
                "Lo reviso cada pocas horas en todas las tiendas y te escribo cuando esté al menos 15 % "
                "más barato que su precio habitual. Las primeras semanas voy aprendiendo cuál es ese precio.\n\n"
                "/lista: lo que estás siguiendo")
    if re.match(r"^/lista\b", text):
        fs = active_for_chat(chat_id)
        if not fs:
            return "No estás siguiendo nada todavía. Buscá un producto en la web y tocá \"Seguir precio\"."
        lines = [f"• {f['model']}: arrancó en {money(f['start_price'])} (dejar: /dejar_{f['id']})" for f in fs]
        return "Estás siguiendo:\n" + "\n".join(lines)
    m = re.match(r"^/dejar_(\d+)", text)
    if m:
        f = stop(chat_id, int(m.group(1)))
        return f"Listo, dejé de seguir {f['model']}." if f else "No encontré ese seguimiento. Mirá /lista."
    return HELP
