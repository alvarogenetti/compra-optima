"""Escaneo programado de los productos seguidos y avisos por Telegram.

Corre en GitHub Actions cada 3 horas (.github/workflows/watch.yml):  python -m core.watch

Por cada modelo que alguien sigue (una sola vez aunque lo sigan muchos):
  1. Lo busca por nombre en las tiendas de su categoría; sólo cuentan las publicaciones cuyo título
     tiene el modelo (con su código, si tiene números).
  2. Guarda el más barato de cada tienda en el historial (tabla prices).
  3. Calcula el descuento REAL del mejor precio contra la referencia (core/market.py) y, si es de
     15 % o más, avisa a cada persona que lo sigue. La misma oferta no se repite: sólo si mejora
     un 3 % o pasó una semana.
Sin IA: no gasta cupo de Gemini.
"""
from __future__ import annotations

import logging
import re
from collections import defaultdict
from datetime import datetime, timedelta

from sqlalchemy import and_, delete, select

from core import comparaya, db, market, stores, telegram
from core.candidates import _fold
from core.follow import money
from core.search import search_all

log = logging.getLogger("watch")
REALERT_IMPROVEMENT = 0.03
REALERT_DAYS = 7
UNLINKED_TTL_DAYS = 7


ACCESSORY_FIRST = {"cable", "funda", "estuche", "repuesto", "pad", "mousepad", "cargador", "bateria",
                   "soporte", "protector", "correa", "filtro", "skin", "adaptador", "control", "kit"}


def _words(s: str) -> list[str]:
    return [w for w in re.findall(r"[a-z0-9]+", _fold(s)) if len(w) > 1]


def matches(listing: dict, model: str, product: str = "") -> bool:
    """¿La publicación es ESE modelo? Tienen que estar todas las palabras del modelo salvo la marca
    (la primera), así "G502 Hero" no pasa por "G502 Lightspeed". Y no tiene que ser un accesorio:
    "Cable USB para Logitech G502" o un título que arranca con "Funda", "Repuesto"..."""
    title, words = _words(listing["title"]), _words(model)
    if not words:
        return False
    if not all(w in title for w in (words[1:] or words)):
        return False
    first_code = next((w for w in words[1:] if any(ch.isdigit() for ch in w)), words[-1])
    before = title[:title.index(first_code)] if first_code in title else title
    if "para" in before or "compatible" in before:
        return False
    prod = _words(product)[:1]
    return not (title and title[0] in ACCESSORY_FIRST and title[0] not in prod)


def scan_model(model: str, category: str, product: str = "", client=None) -> list[dict]:
    """Mejor publicación nueva de cada tienda para ese modelo."""
    shops = stores.load().for_categories([category])
    report = search_all(shops, [model], client=client)
    best: dict[str, dict] = {}
    for x in report.listings:
        if matches(x, model, product) and (x["store"] not in best or x["price"] < best[x["store"]]["price"]):
            best[x["store"]] = x
    ml = mercadolibre_offer(model, product, min(best.values(), key=lambda x: x["price"])["title"] if best else "")
    if ml:
        best["mercadolibre"] = ml
    return sorted(best.values(), key=lambda x: x["price"])


def mercadolibre_offer(model: str, product: str, title: str = "") -> dict | None:
    """La oferta nueva más barata de Mercado Libre según la ficha de ComparaYa (sin importados)."""
    try:
        r = comparaya.find(model, product, title)
    except Exception as e:  # noqa: BLE001 — ComparaYa caído no corta el escaneo
        log.info("ComparaYa %s: %s", model, e)
        return None
    local = [o for o in r.get("ml", []) if not o.get("intl")]
    if not local:
        return None
    o = local[0]
    return {"store": "mercadolibre", "store_name": "Mercado Libre (vía ComparaYa)", "title": o["title"],
            "price": o["price"], "list": o.get("list"), "cuotas": None, "promo": None, "url": o["url"]}


def history(c, key: str, since: datetime) -> list[tuple[datetime, float]]:
    """Mejor precio de cada escaneo (todas las tiendas juntas)."""
    rows = c.execute(select(db.prices.c.ts, db.prices.c.price)
                     .where(db.prices.c.model_key == key, db.prices.c.ts >= since)).all()
    per_scan: dict[datetime, float] = {}
    for ts, price in rows:
        ts = market._aware(ts)   # SQLite devuelve fechas sin zona horaria
        per_scan[ts] = min(price, per_scan.get(ts, price))
    return sorted(per_scan.items())


def should_alert(f: dict, price: float, now: datetime) -> bool:
    last_p, last_t = f.get("last_alert_price"), f.get("last_alert_at")
    if not last_p:
        return True
    if price <= last_p * (1 - REALERT_IMPROVEMENT):
        return True
    return bool(last_t and market._aware(last_t) <= now - timedelta(days=REALERT_DAYS))


def alert_text(f: dict, best: dict, disc: float, ref: float, src: str) -> str:
    head = market.LEVELS[market.level(disc)]
    lines = [f"{head}: {f['model']}",
             f"{money(best['price'])} en {best['store_name']}: {disc * 100:.0f} % menos que el {src} ({money(ref)})."]
    if market.inflated(best["price"], best.get("list"), ref):
        lines.append(f"Ojo: la tienda dice -{market.claimed_discount(best['price'], best['list']) * 100:.0f} %, "
                     "pero contra el mercado la baja es menor.")
    if best.get("cuotas"):
        lines.append(f"{best['cuotas']} cuotas sin interés.")
    lines += [best["url"], "", f"Dejar de seguirlo: /dejar_{f['id']}"]
    return "\n".join(lines)


def baseline(c, f: dict, best: dict, tg_client=None) -> bool:
    """Si el primer escaneo lo encuentra bastante más barato que lo que se vio en la web (otra
    publicación del mismo modelo), lo cuenta como dato y toma ese precio como punto de partida.
    Devuelve True si mandó el aviso."""
    if best["price"] >= f["start_price"] * (1 - market.GOOD):
        if best["price"] < f["start_price"]:
            c.execute(db.follows.update().where(db.follows.c.id == f["id"]).values(start_price=best["price"]))
        return False
    c.execute(db.follows.update().where(db.follows.c.id == f["id"]).values(
        start_price=best["price"], start_store=best["store_name"][:80], start_url=best["url"]))
    text = (f"Dato: encontré {f['model']} más barato que lo que viste en la web: {money(best['price'])} "
            f"en {best['store_name']} (vos lo viste a {money(f['start_price'])}). No es una baja de precio, "
            f"es otra publicación del mismo modelo.\n{best['url']}\n\n"
            "Desde ahora tomo ese precio como punto de partida para avisarte bajas reales.")
    return telegram.send(f["chat_id"], text, tg_client)


def run(client=None, tg_client=None, now: datetime | None = None) -> dict:
    now = now or db.now()
    eng = db.engine()
    with eng.begin() as c:
        # Links de "Seguir precio" que nadie abrió en una semana
        c.execute(delete(db.follows).where(and_(db.follows.c.watcher_id.is_(None),
                                                db.follows.c.created_at < now - timedelta(days=UNLINKED_TTL_DAYS))))
        rows = c.execute(select(db.follows, db.watchers.c.chat_id)
                         .join(db.watchers, db.follows.c.watcher_id == db.watchers.c.id)
                         .where(db.follows.c.active.is_(True))).mappings().all()
    groups: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        groups[r["model_key"]].append(dict(r))

    summary = {"models": len(groups), "found": 0, "alerts": 0, "notes": 0}
    for key, fs in groups.items():
        model, category = fs[0]["model"], fs[0]["category"]
        try:
            offers = scan_model(model, category, fs[0].get("product") or "", client)
        except Exception as e:  # noqa: BLE001 — un modelo que falla no corta los demás
            log.warning("%s: %s", model, e)
            continue
        log.info("%s: %d tiendas", model, len(offers))
        if not offers:
            continue
        summary["found"] += 1
        best = offers[0]
        with eng.begin() as c:
            points = history(c, key, now - timedelta(days=market.WINDOW_DAYS + 1))
            c.execute(db.prices.insert(), [{
                "model_key": key, "ts": now, "store": o["store"], "store_name": o["store_name"],
                "price": o["price"], "list_price": o.get("list"), "title": o["title"][:500], "url": o["url"],
                "extra": {"cuotas": o.get("cuotas"), "promo": o.get("promo")}} for o in offers])
            for f in fs:
                if not any(ts >= market._aware(f["created_at"]) for ts, _ in points):
                    # Primer escaneo de este seguimiento: fija el punto de partida real, sin avisar
                    # ofertas (la web pudo haber mostrado otra publicación del mismo modelo).
                    if baseline(c, f, best, tg_client):
                        summary["notes"] += 1
                    continue
                ref, src = market.reference(points, seed=f["start_price"], now=now)
                disc = market.real_discount(best["price"], ref)
                if not market.level(disc) or not should_alert(f, best["price"], now):
                    continue
                if telegram.send(f["chat_id"], alert_text(f, best, disc, ref, src), tg_client):
                    summary["alerts"] += 1
                    c.execute(db.follows.update().where(db.follows.c.id == f["id"])
                              .values(last_alert_price=best["price"], last_alert_at=now))
    return summary


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    print(run())
