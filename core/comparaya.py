"""Precios de Mercado Libre vía ComparaYa (comparaya.net), como en el tracker de monitores.

ComparaYa es un comparador afiliado de Mercado Libre: en cada ficha de producto publica el precio de
ML junto al de otras tiendas. Su robots.txt permite las fichas (/p/...) y prohíbe /api/ (de ahí carga
su buscador y sus listados), así que NO se usa su buscador: se arma la dirección de la ficha con el
tipo de producto, la marca y el código, que es como las nombra ComparaYa:
    monitor-msi-mp241-24        mouse-logitech-g502-lightspeed
Se prueban unas pocas variantes, con una pausa entre cada una, y la que existe se recuerda.

Nosotros no leemos Mercado Libre: leemos una página pública de ComparaYa que permite leerla.
De cada ficha se toman sólo ofertas nuevas cuyo título tiene el código exacto del modelo (la ficha
junta "versiones").
"""
from __future__ import annotations

import html as htmllib
import logging
import re
import time
import unicodedata
from datetime import timedelta

import httpx

log = logging.getLogger("comparaya")
BASE = "https://comparaya.net/p/"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36"
PAUSE_S = 1.2
MAX_TRIES = 8
FOUND_TTL_DAYS = 30
MISSING_TTL_DAYS = 7

_memory: dict[str, tuple[str | None, float]] = {}   # model_key -> (slug o None, cuándo)


def _fold(s: str) -> str:
    return unicodedata.normalize("NFKD", (s or "").lower()).encode("ascii", "ignore").decode()


def _words(s: str) -> list[str]:
    return [w for w in re.findall(r"[a-z0-9]+", _fold(s)) if w]


def _has_digit(w: str) -> bool:
    return any(ch.isdigit() for ch in w)


def slug_candidates(product: str, model: str, title: str = "", cpus: list[str] | None = None) -> list[str]:
    """Direcciones posibles de la ficha. product = "Monitor para oficina" → tipo "monitor"."""
    kind = (_words(product) or ["producto"])[0]
    words = _words(model)
    if len(words) < 2:
        return []
    brand, rest = words[0], words[1:]
    codes = [w for w in rest if _has_digit(w)]
    size = re.search(r"\b(\d{2}(?:[.,]\d)?)\s*(?:\"|''|pulgadas|pulg|in\b)", _fold(title))
    size = size.group(1).replace(",", ".").split(".")[0] if size else ""
    bodies = ["-".join(rest)]
    if codes and codes != rest:
        bodies.append("-".join(codes))
    if codes:
        bodies.append(codes[0])
    out: list[str] = []
    if kind in COMPUTER_KINDS:
        # Notebooks y PCs: ComparaYa las nombra por línea + procesador, no por código
        # (notebook-asus-tuf-gaming-a15-ryzen-7-7435hs), así que se arman desde el título
        out += computer_slugs(kind, brand, title or model, cpus or [], model)
    for b in dict.fromkeys(bodies):
        base = f"{kind}-{brand}-{b}"
        if size:
            out.append(f"{base}-{size}")
        out.append(base)
    return list(dict.fromkeys(out))[:MAX_TRIES]


COMPUTER_KINDS = {"notebook", "laptop", "netbook", "pc", "computadora", "desktop", "ultrabook"}
CPU_RE = re.compile(r"\b(ryzen \d \d{4}[a-z]{0,3}|core ultra \d \d{3}[a-z]{0,2}|core i\d \d{4,5}[a-z]{0,2}|"
                    r"i\d \d{4,5}[a-z]{0,2}|core \d \d{3}[a-z]{0,2}|celeron [a-z]?\d{4}[a-z]?|m\d (?:pro|max)?)\b")
LINE_STOP = {"notebook", "laptop", "gamer", "ryzen", "core", "intel", "amd", "rtx", "gtx",
             "nvidia", "geforce", "radeon", "ram", "ssd", "hdd", "fhd", "qhd", "uhd", "oled", "ips", "win",
             "windows", "w11", "con", "de", "y", "color", "negro", "gris", "plata", "silver", "black"}


def _is_code(w: str) -> bool:
    """Código de modelo largo (FA506NCQ, 15-fb2063dx): letras y números, 5 o más caracteres."""
    return len(w) >= 5 and _has_digit(w) and any(ch.isalpha() for ch in w)


def _line(text: str, brand: str) -> list[str]:
    """Palabras de la línea después de la marca, hasta un código, el procesador o una especificación."""
    words = re.sub(r"[^a-z0-9]+", " ", _fold(text)).split()
    if brand not in words:
        return []
    line = []
    for w in words[words.index(brand) + 1:]:
        if w in LINE_STOP or _is_code(w) or re.fullmatch(r"\d+(gb|tb|hz|w)", w) or len(line) >= 4:
            break
        line.append(w)
    return line


def cpu_of(text: str) -> str | None:
    m = CPU_RE.search(re.sub(r"[^a-z0-9]+", " ", _fold(text)))
    return m.group(1).replace(" ", "-") if m else None


def computer_slugs(kind: str, brand: str, title: str, cpus: list[str], model: str = "") -> list[str]:
    """La línea sale del nombre del modelo ("TUF Gaming A15") y/o del título; el procesador, del título o
    de los que se vieron en publicaciones de esa línea (ComparaYa tiene una ficha por procesador)."""
    # Primero la línea más completa ("tuf gaming a15" antes que "tuf a15"): ComparaYa usa el nombre largo
    lines = sorted((l for l in (_line(model, brand), _line(title, brand)) if l), key=len, reverse=True)
    cpu_list = list(dict.fromkeys([c for c in [cpu_of(title)] + list(cpus) if c]))[:3]
    out = []
    for line in dict.fromkeys("-".join(l) for l in lines):
        base = f"{kind}-{brand}-{line}"
        out += [f"{base}-{c}" for c in cpu_list] + [base]
    return out


def _txt(s: str) -> str:
    return htmllib.unescape(re.sub(r"<!-- -->", "", re.sub(r"<[^>]+>", "", s))).strip()


def _money(s: str | None) -> float | None:
    digits = re.sub(r"[^\d]", "", s or "")
    return float(digits) if digits else None


def parse(page: str) -> list[dict]:
    """Ofertas de una ficha: [{shop, title, price, list, url, intl}] (igual que el tracker)."""
    i = page.find("Precios por tienda")
    if i < 0:
        return []
    j = page.find("</ol>", i)
    block = page[i:j if j > 0 else None]
    out = []
    for li in re.findall(r"<li\b.*?</li>", block, re.S):
        shop = re.search(r"<span[^>]*>([^<]+)</span>", li)
        title = re.search(r'<p[^>]*title="([^"]*)"', li)
        price = re.search(r"<p[^>]*>\$(?:<!-- -->)?([\d.]+)</p>", li)
        lst = re.search(r"<span[^>]*>\$(?:<!-- -->)?([\d.]+)</span>", li)
        url = re.search(r'<a[^>]*href="(https?://[^"]+)"', li)
        if not (shop and price and url):
            continue
        p = _money(price.group(1))
        lp = _money(lst.group(1)) if lst else None
        out.append({"shop": _txt(shop.group(1)), "title": htmllib.unescape(title.group(1)) if title else "",
                    "price": p, "list": lp if lp and p and lp > p else None,
                    "url": htmllib.unescape(url.group(1)), "intl": ">Internacional<" in li})
    return out


def is_ml(o: dict) -> bool:
    return "mercadolibre.com" in o["url"] or o["shop"].lower().startswith("mercado libre")


USED_RE = re.compile(r"\b(usad[oa]s?|reacondicionad[oa]s?|refurbish\w*|outlet|open\s?box|caja\s+abierta)\b")


def exact_offers(page: str, model: str) -> list[dict]:
    """Ofertas nuevas del modelo exacto: el título tiene todas las palabras del modelo salvo la marca
    ("G502 Hero" no pasa por "G502 Lightspeed"; la ficha junta versiones)."""
    need = _words(model)[1:] or _words(model)
    out = []
    for o in parse(page):
        t = _words(o["title"])
        if o["price"] and all(w in t for w in need) and not USED_RE.search(_fold(o["title"])):
            out.append(o)
    return sorted(out, key=lambda o: o["price"])


# ------------------------------------------------------------------ caché de direcciones
def _cache_get(key: str) -> tuple[bool, str | None]:
    """(sé algo, slug). En memoria; si hay base, también ahí (sobrevive a reinicios y escaneos)."""
    now = time.time()
    if key in _memory:
        slug, when = _memory[key]
        ttl = (FOUND_TTL_DAYS if slug else MISSING_TTL_DAYS) * 86400
        if now - when < ttl:
            return True, slug
    try:
        from sqlalchemy import select

        from core import db
        with db.engine().connect() as c:
            row = c.execute(select(db.comparaya_slugs).where(db.comparaya_slugs.c.model_key == key)).first()
        if row:
            age = db.now() - _aware(row.checked_at)
            ttl = timedelta(days=FOUND_TTL_DAYS if row.slug else MISSING_TTL_DAYS)
            if age < ttl:
                _memory[key] = (row.slug, now - age.total_seconds())
                return True, row.slug
    except Exception as e:  # noqa: BLE001 — sin base, sólo memoria
        log.debug("caché ComparaYa sin base: %s", e)
    return False, None


def _cache_put(key: str, slug: str | None) -> None:
    _memory[key] = (slug, time.time())
    try:
        from core import db
        with db.engine().begin() as c:
            c.execute(db.comparaya_slugs.delete().where(db.comparaya_slugs.c.model_key == key))
            c.execute(db.comparaya_slugs.insert().values(model_key=key, slug=slug, checked_at=db.now()))
    except Exception as e:  # noqa: BLE001
        log.debug("caché ComparaYa sin base: %s", e)


def _aware(t):
    from datetime import timezone
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


SLUG_VERSION = "v3"   # subirlo cuando cambia cómo se arman las direcciones: invalida los "no existe" viejos


def _key(model: str) -> str:
    return f"{SLUG_VERSION}:" + "".join(_words(model))[:110]


def find(model: str, product: str, title: str = "", client: httpx.Client | None = None,
         cpus: list[str] | None = None, max_tries: int = MAX_TRIES) -> dict:
    """Busca la ficha del modelo. Devuelve {found, page_url, ml: [...], others: [...]}."""
    key = _key(model)
    known, slug = _cache_get(key)
    own = client is None
    c = client or httpx.Client(timeout=15, follow_redirects=True,
                               headers={"User-Agent": UA, "Accept-Language": "es-AR,es;q=0.9"})
    try:
        page = None
        if known and slug:
            r = c.get(BASE + slug)
            page = r.text if r.status_code == 200 and "Precios por tienda" in r.text else None
            if page is None:
                known = False   # la ficha ya no está: se vuelve a buscar
        if not known:
            slug = None
            for i, cand in enumerate(slug_candidates(product, model, title, cpus)[:max_tries]):
                if i:
                    time.sleep(PAUSE_S)
                r = c.get(BASE + cand)
                if r.status_code == 200 and "Precios por tienda" in r.text:
                    slug, page = cand, r.text
                    break
            _cache_put(key, slug)
    except httpx.HTTPError as e:
        log.info("ComparaYa %s: %s", model, e)
        return {"found": False, "page_url": None, "ml": [], "others": [], "error": str(e)[:120]}
    finally:
        if own:
            c.close()
    if not page:
        return {"found": False, "page_url": None, "ml": [], "others": []}
    offers = exact_offers(page, model)
    own = _own_store_names()
    return {"found": True, "page_url": BASE + slug,
            "ml": [o for o in offers if is_ml(o)][:3],
            # Tiendas que no leemos directo (las nuestras ya están en los resultados, más frescas)
            "others": [o for o in offers if not is_ml(o) and not _known(o["shop"], own)][:4]}


def _own_store_names() -> set[str]:
    from core import stores
    return {"".join(_words(s.name)) for s in stores.load().stores}


def _known(shop: str, own: set[str]) -> bool:
    n = "".join(_words(shop))
    return len(n) >= 3 and any(n == o or o.startswith(n) or n.startswith(o) for o in own)
