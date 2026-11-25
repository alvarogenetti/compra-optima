"""Búsqueda real en las tiendas, por plataforma (adaptado del tracker de monitores).

Cada plataforma expone un buscador público que usa su propia web, así que no hace falta navegador:
  - VTEX:        https://<host>/api/catalog_system/pub/products/search?ft=<texto>   (JSON)
  - WooCommerce: https://<host>/wp-json/wc/store/v1/products?search=<texto>        (JSON, Store API)
  - Tiendanube:  https://<host>/search/?q=<texto>                                   (HTML con data-variants)

No se evade ningún control anti-bot: si una tienda bloquea, queda informada como error y listo.
Cada resultado ("listing") es: {store, store_name, title, price, list, url, cuotas, promo}.
"""
from __future__ import annotations

import html as htmllib
import json
import logging
import re
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from urllib.parse import quote, quote_plus

import httpx

from core import settings
from core.price_parser import is_plausible, parse_ars
from core.stores import Store

log = logging.getLogger("search")

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/131.0 Safari/537.36")
DEFAULT_EXCLUDE: list[str] = []

# No es nuevo: outlet, usado, reacondicionado, exhibición, caja abierta... (igual que el tracker de monitores)
USED_RE = re.compile(r"\b(outlet|usad[oa]s?|reacondicionad[oa]s?|refurbish\w*|open\s?box|caja\s+abierta|"
                     r"exhibicion|de\s+muestra|grado\s+[bc]|b-?stock|pixel(es)?\s+(quemad|muert)\w*|"
                     r"detalles?\s+esteticos?)\b")


def is_used(title: str) -> bool:
    return bool(USED_RE.search(_fold(title)))


class BlockedError(RuntimeError):
    pass


# ------------------------------------------------------------------ VTEX
def vtex_api_url(host: str, query: str, size: int = 50) -> str:
    # VTEX espera los espacios como %20 (con "+" algunas tiendas responden 400)
    return f"https://{host}/api/catalog_system/pub/products/search?ft={quote(query)}&_from=0&_to={size - 1}"


def parse_vtex(products: list[dict], store: Store) -> list[dict]:
    """Una oferta por producto: el vendedor más barato con stock."""
    out = []
    for p in products if isinstance(products, list) else []:
        best = None
        for it in p.get("items") or []:
            for s in it.get("sellers") or []:
                o = s.get("commertialOffer") or {}
                price = o.get("Price")
                if not is_plausible(price) or not o.get("AvailableQuantity"):
                    continue
                if best and best["price"] <= price:
                    continue
                # Algunas tiendas (Frávega) devuelven Price SIN IVA y el impuesto aparte en Tax:
                # el precio que se paga es la suma (en las demás Tax viene en 0)
                price = round(float(price) + float(o.get("Tax") or 0), 2)
                lst = o.get("ListPrice") if (o.get("ListPrice") or 0) > price else None
                free = [i.get("NumberOfInstallments") or 0 for i in (o.get("Installments") or [])
                        if not i.get("InterestRate") and (i.get("NumberOfInstallments") or 0) > 1]
                teasers = [t.get("Name") or t.get("name") for t in
                           (o.get("Teasers") or []) + (o.get("PromotionTeasers") or [])
                           if isinstance(t, dict)]
                best = {"store": store.id, "store_name": store.name, "title": p.get("productName") or "",
                        "brand": p.get("brand") or "", "price": float(price),
                        "list": float(lst) if lst else None,
                        "cuotas": max(free) if free else None,
                        "promo": ", ".join(dict.fromkeys(t for t in teasers if t)) or None,
                        "url": product_url(store, p, it),
                        # Para verificar después si se puede comprar en la zona (simulación de VTEX)
                        "vtex": {"host": store.host.split("/")[0], "item": str(it.get("itemId") or ""),
                                 "seller": str(s.get("sellerId") or "1")},
                        "specs": vtex_specs(p)}
        if best:
            out.append(best)
    return out


def product_url(store: Store, p: dict, item: dict) -> str:
    """Link a la ficha. Frávega dejó VTEX para su web: sus fichas son /p/<nombre>-<número de artículo>/
    y el link que da la API lleva a una página vacía."""
    if store.id == "fravega" and item.get("itemId"):
        slug = re.sub(r"[^a-z0-9]+", "-", _fold(p.get("productName") or "")).strip("-")
        return f"https://www.fravega.com/p/{slug}-{item['itemId']}/"
    host_root = store.host.split("/")[0]
    return p.get("link") or f"https://{host_root}/{p.get('linkText', '')}/p"


SPEC_KEYS = re.compile(r"memoria|ram|disco|almacenamiento|ssd|procesador|placa|video|gpu|pantalla|"
                       r"frecuencia|tasa|hz|resoluci|ranura|slot|ampliable|expandible|panel|tama|peso|"
                       r"bater|conectividad|puertos|usb|potencia|watt|modelo", re.I)


def vtex_specs(p: dict, limit: int = 420) -> str:
    """Especificaciones de la ficha (no sólo el título): Hz, ranuras de RAM, tipo de panel, potencia...
    Se mandan a la evaluación para no adivinar lo que el título no dice."""
    out = []
    for name in p.get("allSpecifications") or []:
        if not SPEC_KEYS.search(name):
            continue
        vals = p.get(name)
        val = ", ".join(str(v) for v in vals) if isinstance(vals, list) else str(vals or "")
        val = re.sub(r"\s+", " ", htmllib.unescape(re.sub(r"<[^>]+>", " ", val))).strip()
        if val and len(val) < 120:
            out.append(f"{name}: {val}")
    return "; ".join(out)[:limit]


def vtex_available(listing: dict, postal_code: str = "1000", client: httpx.Client | None = None) -> bool | None:
    """¿Se puede comprar? Simulación de compra de VTEX (la que usa el carrito de la tienda) con el código
    postal. Frávega muestra con stock en su API publicaciones que en la web dicen "no disponible para tu
    ubicación": la simulación las da "withoutStock". None = no se pudo saber (no se descarta)."""
    v = listing.get("vtex") or {}
    if not v.get("item"):
        return None
    own = client is None
    c = client or httpx.Client(timeout=15, headers={"User-Agent": UA, "Accept": "application/json"})
    try:
        r = c.post(f"https://{v['host']}/api/checkout/pub/orderForms/simulation?sc=1",
                   json={"items": [{"id": v["item"], "quantity": 1, "seller": v["seller"]}],
                         "postalCode": postal_code or "1000", "country": "ARG"})
        if r.status_code != 200:
            return None
        data = r.json()
        items = data.get("items") or []
        if not items:
            return False
        if items[0].get("availability") != "available":
            return False
        slas = ((data.get("logisticsInfo") or [{}])[0]).get("slas") or []
        return bool(slas)
    except (httpx.HTTPError, ValueError):
        return None
    finally:
        if own:
            c.close()


def search_vtex(client: httpx.Client, store: Store, query: str) -> list[dict]:
    r = client.get(vtex_api_url(store.host, query), headers={"Accept": "application/json"})
    if r.status_code in (403, 429):
        raise BlockedError(f"HTTP {r.status_code} (la tienda bloquea consultas automáticas)")
    if r.status_code not in (200, 206):
        raise RuntimeError(f"HTTP {r.status_code}")
    return parse_vtex(r.json() if r.content else [], store)


# ------------------------------------------------------------------ WooCommerce
def parse_woocommerce(items: list[dict], store: Store) -> list[dict]:
    out = []
    for p in items if isinstance(items, list) else []:
        if p.get("is_in_stock") is False or p.get("is_purchasable") is False:
            continue
        pr = p.get("prices") or {}
        div = 10 ** int(pr.get("currency_minor_unit") or 0)
        try:
            price = int(pr.get("price") or 0) / div
            regular = int(pr.get("regular_price") or 0) / div
        except ValueError:
            continue
        if not is_plausible(price):
            continue
        out.append({"store": store.id, "store_name": store.name,
                    "title": htmllib.unescape(p.get("name") or ""), "brand": "", "price": price,
                    "list": regular if regular > price else None, "cuotas": None, "promo": None,
                    "url": p.get("permalink") or ""})
    return out


def search_woocommerce(client: httpx.Client, store: Store, query: str) -> list[dict]:
    r = client.get(f"https://{store.host}/wp-json/wc/store/v1/products?search={quote(query)}&per_page=50")
    if r.status_code in (403, 429):
        raise BlockedError(f"HTTP {r.status_code} (la tienda bloquea consultas automáticas)")
    if r.status_code != 200:
        raise RuntimeError(f"HTTP {r.status_code}")
    return parse_woocommerce(r.json(), store)


# ------------------------------------------------------------------ Tiendanube
TN_ITEM_SPLIT = re.compile(r'(?=<div[^>]*class="[^"]*\bjs-item-product\b)')


def parse_tiendanube(page_html: str, store: Store) -> list[dict]:
    """Cada tarjeta trae `data-variants` (JSON con precio de lista y precio por transferencia)."""
    out = []
    for chunk in TN_ITEM_SPLIT.split(page_html)[1:]:
        m = re.search(r'data-variants="([^"]*)"', chunk)
        url = re.search(r'href="([^"]*/productos/[^"]*)"', chunk)
        name = re.search(r'item-name[^>]*>\s*([^<]+?)\s*<', chunk) or re.search(r'title="([^"]+)"', chunk)
        if not (url and name):
            continue
        base = {"store": store.id, "store_name": store.name, "title": htmllib.unescape(name.group(1)),
                "brand": "", "cuotas": None, "promo": None, "url": url.group(1)}
        if not m:
            # Otros temas: el precio va en data-product-price (en centavos)
            pp = re.search(r'data-product-price="(\d+)"', chunk)
            lab = re.search(r'data-label="Sin stock"[^>]*style="([^"]*)"', chunk)
            no_stock = bool(lab) and "display:none" not in lab.group(1).replace(" ", "").lower()
            if pp and not no_stock and is_plausible(int(pp.group(1)) / 100):
                out.append({**base, "price": int(pp.group(1)) / 100, "list": None})
            continue
        try:
            variants = json.loads(htmllib.unescape(m.group(1)))
        except ValueError:
            continue
        avail = [v for v in variants if v.get("available")]
        if not avail:
            continue
        v = min(avail, key=lambda v: v.get("price_number") or 1e18)
        lst = v.get("price_number")
        cash = parse_ars(v.get("price_with_payment_discount_short")) or lst
        if is_plausible(cash):
            out.append({**base, "price": float(cash), "list": lst if lst and lst > cash else None,
                        "promo": "precio por transferencia" if lst and lst > cash else None})
    return out


def search_tiendanube(client: httpx.Client, store: Store, query: str) -> list[dict]:
    r = client.get(f"https://{store.host}/search/?q={quote_plus(query)}")
    if r.status_code in (403, 429):
        raise BlockedError(f"HTTP {r.status_code} (la tienda bloquea consultas automáticas)")
    if r.status_code != 200:
        raise RuntimeError(f"HTTP {r.status_code}")
    return parse_tiendanube(r.text, store)


PLATFORMS = {"vtex": search_vtex, "woocommerce": search_woocommerce, "tiendanube": search_tiendanube}


# ------------------------------------------------------------------ links para abrir a mano
def store_search_url(store: Store, query: str) -> str:
    host = store.host
    if store.platform == "vtex":
        if store.id == "fravega":
            return f"https://{host}/l/?keyword={quote(query)}"
        return f"https://{host}/{quote(query)}?_q={quote(query)}&map=ft"
    if store.platform == "woocommerce":
        return f"https://{host}/?s={quote_plus(query)}&post_type=product"
    return f"https://{host}/search/?q={quote_plus(query)}"


def mercadolibre_url(query: str) -> str:
    """Mercado Libre no permite leer precios con programas: sólo se ofrece el link."""
    slug = re.sub(r"[^a-z0-9]+", "-", _fold(query)).strip("-")
    return f"https://listado.mercadolibre.com.ar/{slug}"


# ------------------------------------------------------------------ búsqueda en paralelo
def _fold(s: str) -> str:
    return unicodedata.normalize("NFKD", (s or "").lower()).encode("ascii", "ignore").decode()


def is_excluded(title: str, words: list[str]) -> bool:
    t = _fold(title)
    return any(re.search(rf"\b{re.escape(_fold(w))}\b", t) for w in words if w and w.strip())


@dataclass
class SearchReport:
    listings: list[dict] = field(default_factory=list)
    stores: list[dict] = field(default_factory=list)   # [{id, name, ok, found, error}]


def search_all(stores: list[Store], queries: list[str], exclude: list[str] | None = None,
               client: httpx.Client | None = None, workers: int = 16) -> SearchReport:
    """Busca cada query en cada tienda (en paralelo), sin repetidos ni excluidos."""
    exclude = DEFAULT_EXCLUDE + list(exclude or [])
    own = client is None
    client = client or httpx.Client(timeout=settings.SEARCH_TIMEOUT_S, follow_redirects=True,
                                    headers={"User-Agent": UA, "Accept-Language": "es-AR,es;q=0.9"})
    jobs = [(s, q) for s in stores for q in queries if s.platform in PLATFORMS]

    def run(job):
        s, q = job
        try:
            return s, q, PLATFORMS[s.platform](client, s, q), None
        except Exception as e:  # noqa: BLE001 — una tienda caída no corta las demás
            log.info("%s %r: %s", s.id, q, e)
            return s, q, [], str(e)[:160] or e.__class__.__name__

    try:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            results = list(pool.map(run, jobs))
    finally:
        if own:
            client.close()

    report = SearchReport()
    seen: set[tuple[str, str]] = set()
    per_store: dict[str, dict] = {s.id: {"id": s.id, "name": s.name, "ok": False, "found": 0, "error": None}
                                  for s in stores}
    for s, q, items, err in results:
        st = per_store[s.id]
        if err:
            st["error"] = st["error"] or err
        else:
            st["ok"] = True
        for it in items:
            key = (it["store"], it["url"] or it["title"])
            if key in seen or is_used(it["title"]) or is_excluded(it["title"], exclude):
                continue
            seen.add(key)
            it["query"] = q
            report.listings.append(it)
            st["found"] += 1
    report.stores = list(per_store.values())
    return report
