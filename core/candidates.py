"""Del perfil a los candidatos: busca de verdad en las tiendas y la IA evalúa lo que encontró.

1. Tiendas: las que venden la categoría del perfil (config/stores.json).
2. Búsqueda real: las búsquedas del perfil + los modelos sugeridos (se verifican acá: si no aparecen
   en ninguna tienda, no se recomiendan).
3. Prefiltro sin IA: excluidos, muy por encima del tope, y un tope de publicaciones por relevancia.
4. La IA evalúa cada publicación contra el perfil (¿es el producto? ¿cumple? modelo normalizado).
5. Se agrupa por modelo (la misma cosa en varias tiendas) y se arma por rango de precio:
   económica / equilibrada / premium.
"""
from __future__ import annotations

import hashlib
import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor
import unicodedata

from core import comparaya, settings, specs, stores, upgrades
from core.ai import AIError, get_ai
from core.search import mercadolibre_url, search_all, store_search_url, vtex_available

log = logging.getLogger("candidates")

EVAL_SYSTEM = """Sos el asesor de compras de "Compra Óptima" (Argentina). Te paso el perfil de compra de una
persona y una lista de publicaciones REALES de tiendas argentinas (id | título | precio | tienda | y a
veces "ficha:" con especificaciones de la ficha de la tienda, que valen tanto como el título).
Evaluá cada publicación contra el perfil, sólo con lo que dice el título y lo que sepas con seguridad
de ese modelo. Español rioplatense, simple, sin tecnicismos sin explicar.

Para cada publicación que SEA el producto buscado devolvé:
- "id": el id de la lista.
- "model": marca + modelo normalizado, igual para la misma cosa en distintas tiendas
  ("Logitech G305", no "Mouse Gamer Logitech G305 Lightspeed Negro"). Si el título trae un código de
  modelo, usalo ("MSI Thin GF63 12VF", "HP Victus 15-fb2063dx"). Si no trae código, armalo con marca +
  lo que distingue esa configuración, para no juntar productos distintos ("MSI Ryzen 5 RTX 4060 32GB
  1TB", nunca "MSI Gamer").
- "score": de 0 a 100, qué tan bien cumple el perfil (los "must" pesan mucho más que los "nice").
  Si el título CONTRADICE un "must" (pide Ryzen 7 o superior y dice Ryzen 5; pide 32 GB y dice 16 GB;
  pide IPS y dice VA), el puntaje no puede pasar de 40 y eso va en "doubts". Leé los números del
  título con cuidado: comparalos uno por uno con cada "must" antes de puntuar.
- "fails_must": la lista de requisitos "Obligatorio" que el título CONTRADICE (vacía si no contradice
  ninguno). Contradecir es que el título diga otra cosa (pide 32 GB y dice 16 GB; pide Ryzen 7 o
  superior y dice Ryzen 5), no que no lo mencione. Si en "doubts" vas a decir que NO llega a algo
  obligatorio ("la RAM es de 16 GB", "tiene 512 GB"), entonces va en "fails_must", no sólo en "doubts".
- "fix": sólo si lo que no cumple es RAM o almacenamiento Y sabés (por la ficha o por el modelo) que
  se puede ampliar (ranura de RAM libre o RAM no soldada; segundo M.2 o reemplazar el disco):
  {"ram_add_gb": cuántos GB agregar (16 o 32) o 0, "ssd_tb": TB de SSD a sumar o cambiar (0.5, 1, 2)
  o 0, "note": "tiene 2 ranuras DDR5 (hasta 64 GB)"}. Si no estás seguro de que se pueda, no pongas "fix".
- "meets": hasta 3 cosas del perfil que cumple, en palabras simples.
- "doubts": hasta 2 cosas que no cumple o que no se pueden confirmar por el título.
- "reason": una oración de por qué le sirve (o no) a esta persona.
EQUIVALENCIAS: comparás por gama, no por marca. "Ryzen 7 o superior" lo cumplen también Intel Core i7,
Core Ultra 7, Ryzen AI 7 y cualquier gama 9 (Core i9, Core Ultra 9, Ryzen 9, Ryzen AI 9); "Ryzen 5 o
superior" lo cumplen i5/Core Ultra 5/Ryzen AI 5 y gamas mayores. Lo mismo al revés si piden Intel.
Una placa de video mayor cumple "o superior" (RTX 4060 cumple "RTX 4050 o superior"; RTX 5050 también).
NUNCA digas "es la única", "no hay otra" ni "no existe": sólo ves una parte de las publicaciones.

Devolvé UN elemento por CADA publicación de la lista (el mismo id), sin saltear ninguna: la persona quiere
ver también las que casi cumplen. Si la publicación NO es el producto (accesorio, repuesto, otra cosa), su
elemento es sólo {"id": n, "product": false}. Si es el producto pero el puntaje es MENOR A 30, devolvé
sólo "id", "model", "score" y "fails_must" (sin "meets", "doubts" ni "reason"): ahorra salida.

Respondé SÓLO JSON: {"items": [{"id": 0, "model": "", "score": 0, "fails_must": [], "fix": null, "meets": [], "doubts": [], "reason": ""}, {"id": 1, "product": false}]}"""
FAIL_CAP = 40   # si contradice un obligatorio, como mucho "casi cumple"

TIERS = [
    ("economica", "Económica", "Lo más barato que cumple lo importante."),
    ("equilibrada", "Equilibrada", "La mejor relación entre lo que pagás y lo que recibís."),
    ("premium", "Premium", "Si querés lo mejor para tu uso y podés gastar más."),
]
MIN_SCORE = 55
PER_TIER = 3


def _fold(s: str) -> str:
    return unicodedata.normalize("NFKD", (s or "").lower()).encode("ascii", "ignore").decode()


def _tokens(s: str) -> set[str]:
    # "RyzenTM 7" / "Core™ i7": se saca la marca registrada pegada a la palabra
    text = re.sub(r"(?<=[a-z])tm\b", "", _fold(s.replace(chr(0x2122), "tm").replace(chr(0xAE), "")))
    toks = {t for t in re.findall(r"[a-z0-9]+", text) if len(t) > 1}
    # "RTX4050" / "16gb": también como "rtx" + "4050" / "16" + "gb", para que coincida con "rtx 4050"
    for t in list(toks):
        m = re.fullmatch(r"([a-z]{2,})(\d{3,})|(\d+)([a-z]{2,})", t)
        if m:
            toks.update(x for x in m.groups() if x and len(x) > 1)
    return toks


MODEL_QUOTA = 6   # publicaciones por modelo sugerido (alcanza para ver varias tiendas)


def query_match(listing: dict, query: str) -> float:
    """Qué tan bien matchea el título una búsqueda (0 a 1), sin IA. Si la búsqueda tiene un código
    con números ("G502", "BHD350"), el título tiene que tenerlo sí o sí."""
    title, qt = _tokens(listing["title"]), _tokens(query)
    if not qt:
        return 0.0
    if any(t not in title for t in qt if any(c.isdigit() for c in t)):
        return 0.0
    return len(qt & title) / len(qt)


def pool_match(listing: dict, query: str) -> bool:
    """¿La publicación entra en el grupo de una búsqueda amplia? La primera palabra es el tipo de producto
    ("notebook") y no alcanza sola: de las demás tienen que estar todas las que tienen números ("4050")
    y, si no hay números, al menos la mitad ("gamer ryzen" → "gamer" o "ryzen")."""
    title, words = _tokens(listing["title"]), [w for w in re.findall(r"[a-z0-9]+", _fold(query)) if len(w) > 1]
    if not words or words[0] not in title:
        return False
    rest = [w for w in words[1:]]
    if not rest:
        return True
    rest_tokens = set().union(*(_tokens(w) for w in rest))
    digits = [w for w in rest_tokens if any(ch.isdigit() for ch in w)]
    if digits:
        return all(d in title for d in digits)
    return len(rest_tokens & title) * 2 >= len(rest_tokens)


def relevance(listing: dict, queries: list[str]) -> float:
    return max((query_match(listing, q) for q in queries), default=0.0)


def spread(items: list[dict], n: int, level: str) -> list[dict]:
    """Elige n publicaciones repartidas por precio, según el presupuesto: económico mira el 60 % más
    barato, alto el 60 % más caro, el resto todo el rango. Así se evalúan opciones de cada rango y no
    sólo las más baratas."""
    if n <= 0 or not items:
        return []
    items = sorted(items, key=lambda x: x["price"])
    cut = max(1, round(len(items) * 0.6))
    if level == "economico":
        items = items[:cut]
    elif level == "alto":
        items = items[-cut:]
    if len(items) <= n:
        return items
    step = (len(items) - 1) / (n - 1) if n > 1 else 0
    return [items[round(i * step)] for i in range(n)] if n > 1 else [items[len(items) // 2]]


def prefilter(listings: list[dict], profile: dict, broad: list[str], models: list[str] | None = None) -> list[dict]:
    """Qué publicaciones se le mandan a la IA (no se pueden mandar todas).

    Primero las de cada modelo sugerido (hasta MODEL_QUOTA cada uno, las más baratas), después el
    cupo restante repartido en partes iguales entre las búsquedas amplias, cada parte repartida
    por precio según el presupuesto."""
    budget = profile.get("budget", {})
    cap, level = budget.get("max_ars"), budget.get("level", "sin_definir")
    if cap and level == "alto":
        level = "medio"   # con tope en pesos, el tope ya acota: mirar todo el rango hasta ahí
    keep = [x for x in listings if not cap or x["price"] <= cap * 1.3]
    # La misma publicación en varias tiendas (On City, Provincia, Carrefour comparten catálogo): para la
    # evaluación alcanza con la más barata; las demás tiendas se suman después al agrupar por modelo
    by_title: dict[str, dict] = {}
    for x in sorted(keep, key=lambda x: x["price"]):
        by_title.setdefault(title_key(x["title"]), x)
    keep = list(by_title.values())
    limit = settings.MAX_LISTINGS_TO_EVALUATE
    chosen: list[dict] = []
    seen: set[int] = set()

    def take(items):
        for x in items:
            if id(x) not in seen and len(chosen) < limit:
                seen.add(id(x))
                chosen.append(x)

    for m in models or []:
        # Repartidas por precio según el presupuesto (las más baratas de una línea suelen ser las de menos)
        take(spread([x for x in keep if query_match(x, m) >= 0.6], MODEL_QUOTA, level))
    pools = [[x for x in keep if pool_match(x, q)] for q in broad]
    pools = [p for p in pools if p]
    if pools:
        share = max(1, (limit - len(chosen)) // len(pools))
        for p in pools:
            take(spread([x for x in p if id(x) not in seen], share, level))
        for p in pools:   # lo que sobró del reparto, por si alguna búsqueda trajo poco
            take(spread([x for x in p if id(x) not in seen], limit - len(chosen), level))
    return chosen


def profile_text(profile: dict) -> str:
    def feats(items):
        return "\n".join(f"  - {f['feature']}" for f in items) or "  (nada)"
    b = profile.get("budget", {})
    tope = f", tope ${b['max_ars']:,.0f}".replace(",", ".") if b.get("max_ars") else ""
    return (f"Producto: {profile['product']}\nResumen: {profile.get('summary', '')}\n"
            f"Obligatorio:\n{feats(profile.get('must', []))}\n"
            f"Deseable:\n{feats(profile.get('nice', []))}\n"
            f"No pagar extra por:\n{feats(profile.get('avoid_paying_for', []))}\n"
            f"Presupuesto: {b.get('level', 'sin_definir')}{tope}. {b.get('note', '')}")


FIXABLE_RE = re.compile(r"ram|memoria|ssd|almacenamiento|disco|\btb\b|\bgb\b")
EVAL_BATCH = 30      # publicaciones por pedido: con muchas juntas la IA devuelve sólo unas pocas
EVAL_PARALLEL = 5


EVAL_CACHE_TTL_S = 6 * 3600
EVAL_CACHE_MAX = 5000
_eval_cache: dict[str, tuple[float, dict]] = {}     # clave -> (cuándo, campos de la evaluación)
_EVAL_FIELDS = ("model", "score", "fix", "meets", "doubts", "reason", "rejected")


def _cache_key(profile: dict, listing: dict) -> str:
    """La evaluación depende de la persona (perfil) y de lo que dice la publicación (título y ficha); no
    del precio ni de la tienda: la misma publicación en otra tienda comparte evaluación."""
    raw = profile_text(profile) + "|" + title_key(listing["title"]) + "|" + (listing.get("specs") or "")
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def _cache_get(key: str) -> dict | None:
    hit = _eval_cache.get(key)
    if hit and time.time() - hit[0] < EVAL_CACHE_TTL_S:
        return hit[1]
    return None


def _cache_put(key: str, e: dict) -> None:
    if len(_eval_cache) >= EVAL_CACHE_MAX:
        for k in sorted(_eval_cache, key=lambda k: _eval_cache[k][0])[:EVAL_CACHE_MAX // 10]:
            del _eval_cache[k]
    _eval_cache[key] = (time.time(), {k: e[k] for k in _EVAL_FIELDS if k in e})


def evaluate(listings: list[dict], profile: dict) -> list[dict]:
    """Evalúa las publicaciones. Ahorra tokens de dos maneras:
    - CACHÉ (6 h): lo ya evaluado para este perfil no se vuelve a pedir (reintentos, cambio de código
      postal, segundo clic en "Buscar precios").
    - Tandas en paralelo (respuestas más completas y más rápidas que un pedido gigante)."""
    if not listings:
        return []
    cached, todo = [], []
    for x in listings:
        hit = _cache_get(_cache_key(profile, x))
        if hit is None:
            todo.append(x)
        elif not hit.get("rejected"):       # (los descartados por no ser el producto también se recuerdan)
            cached.append({**x, **hit})
    if cached:
        log.info("evaluación: %d de %d salieron de la caché", len(cached), len(listings))
    if not todo:
        return cached
    batches = [todo[i:i + EVAL_BATCH] for i in range(0, len(todo), EVAL_BATCH)]
    rejected: list[dict] = []
    if len(batches) == 1:
        fresh = evaluate_batch(batches[0], profile, rejected=rejected)
    else:
        fresh, errors = [], []
        with ThreadPoolExecutor(max_workers=EVAL_PARALLEL) as ex:
            futures = [ex.submit(evaluate_batch, b, profile, True, rejected) for b in batches]
            for f in futures:
                try:
                    fresh += f.result()
                except AIError as e:   # una tanda que falla no tira las demás
                    errors.append(e)
        if errors and not fresh and not cached:
            raise errors[0]
    for e in fresh:
        _cache_put(_cache_key(profile, e), e)
    for x in rejected:
        _cache_put(_cache_key(profile, x), {"rejected": True})
    return cached + fresh


MIN_SPLIT = 8


RETRY_CHUNK = 10


def evaluate_batch(listings: list[dict], profile: dict, retry: bool = True,
                   rejected: list[dict] | None = None) -> list[dict]:
    """Evalúa una tanda, con dos protecciones:
    - Si la IA no puede con tantas juntas (Groq rechaza respuestas largas), se divide en mitades.
    - Si la respuesta viene INCOMPLETA (medido: modelos Flash Lite que contestan 2 de 30 o 0), se
      vuelve a pedir sólo lo que faltó, en tandas chicas. Cada publicación tiene que quedar respondida
      (como evaluada o como "no es el producto"); si no, una notebook buena puede perderse sin aviso."""
    try:
        out, missing, no_product = _evaluate_once(listings, profile)
    except AIError:
        if len(listings) <= MIN_SPLIT:
            raise
        half = len(listings) // 2
        return (evaluate_batch(listings[:half], profile, retry, rejected)
                + evaluate_batch(listings[half:], profile, retry, rejected))
    if rejected is not None:
        rejected.extend(no_product)
    if missing and retry:
        log.info("evaluación incompleta: faltaron %d de %d; se pide de nuevo sólo eso", len(missing), len(listings))
        for k in range(0, len(missing), RETRY_CHUNK):
            try:
                out += evaluate_batch(missing[k:k + RETRY_CHUNK], profile, retry=False, rejected=rejected)
            except AIError as e:
                log.info("no se pudo completar la evaluación: %s", e)
    return out


def _evaluate_once(listings: list[dict], profile: dict) -> tuple[list[dict], list[dict], list[dict]]:
    """Una consulta a la IA para una tanda. Devuelve (evaluadas, sin respuesta, descartadas por no ser
    el producto)."""
    lines = "\n".join((f"{i} | {x['title']} | ${x['price']:,.0f} | {x['store_name']}".replace(",", ".")
                       + (f" | ficha: {x['specs']}" if x.get("specs") else ""))
                      for i, x in enumerate(listings))
    raw = get_ai().json(EVAL_SYSTEM, [{"role": "user", "content":
                                       f"PERFIL\n{profile_text(profile)}\n\nPUBLICACIONES\n{lines}"}],
                        task="evaluate")
    out, answered, rejected = [], set(), []
    for it in raw.get("items") or []:
        try:
            i = int(it.get("id"))
        except (TypeError, ValueError):
            continue
        if not 0 <= i < len(listings):
            continue
        answered.add(i)
        if it.get("product") is False:
            rejected.append(listings[i])    # respondida: no es el producto (accesorio, repuesto...); no se muestra
            continue
        try:
            score = max(0, min(100, int(float(it.get("score") or 0))))
        except (TypeError, ValueError):
            continue
        fails = [str(f)[:80] for f in (it.get("fails_must") or []) if str(f).strip()][:3]
        doubts = [str(d)[:120] for d in (it.get("doubts") or [])][:2]
        if fails:
            # La IA a veces puntúa alto igual: el tope lo pone el código
            score = min(score, FAIL_CAP)
            doubts = [f"No cumple: {', '.join(fails)}"] + doubts[:1]
        fix = it.get("fix") if isinstance(it.get("fix"), dict) else None
        if fix:
            try:
                fix = {"ram_add_gb": int(float(fix.get("ram_add_gb") or 0)) if float(fix.get("ram_add_gb") or 0) in (8, 16, 32, 64) else 0,
                       "ssd_tb": float(fix.get("ssd_tb") or 0) if float(fix.get("ssd_tb") or 0) in (0.5, 1, 2, 4) else 0,
                       "note": str(fix.get("note") or "")[:120]}
            except (TypeError, ValueError):
                fix = None
            if fix and not (fix["ram_add_gb"] or fix["ssd_tb"]):
                fix = None
            # Sólo si TODO lo que falta es RAM o disco: un procesador o una placa de video no se amplían
            if fix and any(not FIXABLE_RE.search(_fold(f)) for f in fails):
                fix = None
        out.append({**listings[i], "model": str(it.get("model") or listings[i]["title"]).strip()[:80],
                    "score": score, "fix": fix if fails else None,
                    "meets": [str(m)[:120] for m in (it.get("meets") or [])][:3],
                    "doubts": doubts,
                    "reason": str(it.get("reason") or "")[:300]})
    missing = [x for i, x in enumerate(listings) if i not in answered]
    return out, missing, rejected


def model_key(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", _fold(name))


def group_by_model(evaluated: list[dict], cap: float | None = None) -> list[dict]:
    groups: dict[str, dict] = {}
    for x in evaluated:
        k = model_key(x["model"])
        if not k:
            continue
        g = groups.setdefault(k, {"model": x["model"], "score": x["score"], "meets": x["meets"],
                                  "doubts": x["doubts"], "reason": x["reason"], "fix": x.get("fix"),
                                  "specs": x.get("specs", ""), "offers": []})
        if x["score"] > g["score"]:
            g.update(score=x["score"], meets=x["meets"], doubts=x["doubts"], reason=x["reason"],
                     fix=x.get("fix"), specs=x.get("specs", ""))
        g["offers"].append({k2: x[k2] for k2 in ("store", "store_name", "title", "price", "list",
                                                  "cuotas", "promo", "url")})
    out = []
    for g in groups.values():
        offers = sorted(g["offers"], key=lambda o: o["price"])
        # Una oferta por tienda (la más barata de cada una)
        per_store, uniq = set(), []
        for o in offers:
            if o["store"] not in per_store:
                per_store.add(o["store"])
                uniq.append(o)
        g["offers"] = uniq[:6]
        g["best"] = uniq[0]
        g["over_budget"] = bool(cap and uniq[0]["price"] > cap)
        out.append(g)
    return out


def price_buckets(good: list[dict]) -> list[list[dict]]:
    """Tres tramos cortados por PRECIO (no por cantidad), en escala proporcional: entre el más barato y
    el más caro, los cortes van a 1/3 y 2/3 del camino en proporción. Así dos modelos de casi el mismo
    precio caen juntos, y $20.000 contra $40.000 pesa lo mismo que $100.000 contra $200.000."""
    lo, hi = good[0]["best"]["price"], good[-1]["best"]["price"]
    if len(good) <= PER_TIER or hi <= lo * 1.15:
        return [good, [], []]
    ratio = hi / lo
    t1, t2 = lo * ratio ** (1 / 3), lo * ratio ** (2 / 3)
    return [[m for m in good if m["best"]["price"] < t1],
            [m for m in good if t1 <= m["best"]["price"] < t2],
            [m for m in good if m["best"]["price"] >= t2]]


def build_tiers(models: list[dict]) -> list[dict]:
    """Corta por precio en tres tramos entre los que cumplen; en cada tramo, los mejores puntajes."""
    good = sorted((m for m in models if m["score"] >= MIN_SCORE), key=lambda m: m["best"]["price"])
    if not good:
        return []
    buckets = price_buckets(good)
    tiers = []
    for (key, label, blurb), bucket in zip(TIERS, buckets):
        if not bucket:
            continue
        top = sorted(bucket, key=lambda m: (-m["score"], m["best"]["price"]))[:PER_TIER]
        tiers.append({"key": key, "label": label, "blurb": blurb,
                      "price_from": bucket[0]["best"]["price"], "price_to": bucket[-1]["best"]["price"],
                      "candidates": sorted(top, key=lambda m: m["best"]["price"])})
    if len(tiers) == 1:
        tiers[0].update(label="Opciones que cumplen", blurb="Son pocas entre las publicaciones que revisamos hoy; puede haber otras en tiendas que no leemos.")
    return tiers


PREFERRED_TIER = {"economico": "economica", "alto": "premium"}


def recommend(tiers: list[dict], level: str = "sin_definir") -> str | None:
    """Mi elección: el mejor puntaje del tramo que va con el presupuesto (económico → económica,
    alto → premium, el resto → equilibrada); si ese tramo no existe, de todos. El más barato si empatan."""
    want = PREFERRED_TIER.get(level, "equilibrada")
    pool = next((t["candidates"] for t in tiers if t["key"] == want), None) or \
        [c for t in tiers for c in t["candidates"]]
    if not pool:
        return None
    return max(pool, key=lambda m: (m["score"], -m["best"]["price"]))["model"]


NEAR_MIN = 25


def near_misses(models: list[dict], n: int = 6) -> list[dict]:
    """Los que casi cumplen (les falta algo): se muestran aparte, con lo que les falta."""
    near = [m for m in models if NEAR_MIN <= m["score"] < MIN_SCORE]
    # Primero las que se pueden completar (ampliando RAM o disco), después por puntaje y precio
    return sorted(near, key=lambda m: (not m.get("fix"), -m["score"], m["best"]["price"]))[:n]


CY_MODELS = 5    # modelos sugeridos que se buscan en ComparaYa por búsqueda
CY_TRIES = 4     # en la búsqueda en vivo, pocas direcciones por modelo (el botón y el escaneo prueban más)
CY_WORKERS = 3   # de a pocos: cada modelo prueba algunas direcciones con pausa


def comparaya_listings(models: list[str], profile: dict, store_listings: list[dict]) -> tuple[list[dict], dict]:
    """Ofertas de Mercado Libre (y de tiendas que no leemos) para los modelos sugeridos, vía las fichas
    de ComparaYa. Para armar la dirección de la ficha se usa el título de una publicación de ese modelo
    en nuestras tiendas, si la hay (las notebooks se nombran por línea + procesador)."""
    def one(model: str) -> list[dict]:
        titles = [x["title"] for x in store_listings if query_match(x, model) >= 0.6]
        # Procesadores que aparecen en esa línea (los más vistos primero): ComparaYa tiene una ficha por cada uno
        seen_cpus = [c for c in (comparaya.cpu_of(x) for x in titles) if c]
        cpus = sorted(set(seen_cpus), key=lambda c: -seen_cpus.count(c))[:3]
        try:
            r = comparaya.find(model, profile.get("product", ""), titles[0] if titles else "", cpus=cpus,
                               max_tries=CY_TRIES)
        except Exception as e:  # noqa: BLE001 — ComparaYa caído no corta la búsqueda
            log.info("ComparaYa %s: %s", model, e)
            return []
        out = []
        for o in r.get("ml", []) + r.get("others", []):
            ml = comparaya.is_ml(o)
            shop = "Mercado Libre" if ml else o["shop"]
            out.append({"store": "mercadolibre" if ml else "cy-" + re.sub(r"[^a-z0-9]", "", _fold(shop))[:30],
                        "store_name": shop + (" · importado" if o.get("intl") else ""),
                        "title": o["title"] + (" (importado)" if o.get("intl") else ""),
                        "price": o["price"], "list": o.get("list"), "cuotas": None,
                        "promo": "vía ComparaYa", "url": o["url"], "query": model})
        return out

    found: list[dict] = []
    with ThreadPoolExecutor(max_workers=CY_WORKERS) as ex:
        for items in ex.map(one, models[:CY_MODELS]):
            found += items
    cap = profile.get("budget", {}).get("max_ars")
    found = [x for x in found if not cap or x["price"] <= cap * 1.3]
    status = {"id": "comparaya", "name": "Mercado Libre y otras (vía ComparaYa)", "ok": True,
              "found": len(found), "error": None}
    return found, status


def title_key(title: str) -> str:
    return " ".join(sorted(_tokens(title)))


def add_siblings(evaluated: list[dict], listings: list[dict]) -> list[dict]:
    """La evaluación vio una sola publicación por título (la más barata): las mismas en otras tiendas
    heredan su evaluación, así aparecen en "También en N tiendas"."""
    by_key: dict[str, list[dict]] = {}
    for x in listings:
        by_key.setdefault(title_key(x["title"]), []).append(x)
    seen = {(e["store"], e["url"]) for e in evaluated}
    out = list(evaluated)
    for e in evaluated:
        for s in by_key.get(title_key(e["title"]), []):
            if (s["store"], s["url"]) not in seen:
                seen.add((s["store"], s["url"]))
                out.append({**s, **{k: e[k] for k in ("model", "score", "fix", "meets", "doubts", "reason") if k in e}})
    return out


SPEC_LOOKUPS = 6


def confirm_upgrades(near: list[dict]) -> None:
    """Para los "casi cumplen" que se podrían completar, la ficha del fabricante dice si de verdad se
    puede (RAM en ranuras, M.2 libre). Si dice que no, se saca la propuesta; si no se puede confirmar,
    queda marcada "sin confirmar" (no se inventa)."""
    todo = [m for m in near if m.get("upgrade")][:SPEC_LOOKUPS]
    if not todo:
        return
    with ThreadPoolExecutor(max_workers=3) as ex:
        found = list(ex.map(lambda m: specs.lookup(m["model"], m["best"].get("title", "")), todo))
    for m, spec in zip(todo, found):
        status = specs.check_upgrade(spec, m.get("fix") or {})
        m["maker_specs"] = {"text": specs.describe(spec), "sources": spec.get("sources", [])}
        if status == "no":
            m.pop("upgrade", None)
            m["doubts"] = (m.get("doubts") or [])[:1] + ["La ficha del fabricante dice que la RAM está soldada: no se puede ampliar"]
        else:
            m["upgrade"]["status"] = status


AVAIL_WORKERS = 8


def drop_unavailable(evaluated: list[dict], postal_code: str | None) -> tuple[list[dict], int]:
    """Saca las publicaciones que no se pueden comprar en la zona (sólo las que se podrían mostrar:
    puntaje de "casi cumple" para arriba). Devuelve (las que quedan, cuántas se sacaron)."""
    check = [e for e in evaluated if e["score"] >= NEAR_MIN and e.get("vtex")]
    with ThreadPoolExecutor(max_workers=AVAIL_WORKERS) as ex:
        results = list(ex.map(lambda e: vtex_available(e, postal_code or "1000"), check))
    gone = {id(e) for e, ok in zip(check, results) if ok is False}
    return [e for e in evaluated if id(e) not in gone], len(gone)


def find(profile: dict, postal_code: str | None = None) -> dict:
    reg = stores.load()
    shops = reg.for_categories([profile["category"]])
    if not shops:
        raise AIError("Todavía no tenemos tiendas para esa categoría.")
    broad = profile["search_queries"]
    models_q = [m for m in profile.get("suggested_models", []) if m.lower() not in {q.lower() for q in broad}]
    # Búsquedas por especificación dura ("notebook rtx 4050", "notebook rtx 4060"...): traen publicaciones
    # que las amplias no traen (pasó con una TUF FA607NUQ de Frávega). Cuentan como amplias en el reparto.
    spec_q = [q for q in profile.get("spec_queries", []) if q.lower() not in {x.lower() for x in broad + models_q}]
    cap = profile.get("budget", {}).get("max_ars")
    report = search_all(shops, broad + spec_q + models_q, profile.get("exclude_words"),
                        price_range=(1000, cap * 1.3) if cap else None, deep=set(spec_q))
    via_cy, cy_status = comparaya_listings(models_q, profile, report.listings)
    pool = prefilter(report.listings, profile, broad + spec_q, models_q)
    # Las de ComparaYa (Mercado Libre y tiendas que no leemos) se evalúan siempre
    pool = via_cy + pool[:max(0, settings.MAX_LISTINGS_TO_EVALUATE - len(via_cy))]
    report.stores.append(cy_status)
    evaluated = add_siblings(evaluate(pool, profile), report.listings)
    evaluated, unavailable = drop_unavailable(evaluated, postal_code)
    budget = profile.get("budget", {})
    models = group_by_model(evaluated, budget.get("max_ars"))
    tiers = build_tiers(models)
    near = near_misses(models)
    full = [c["best"]["price"] for t in tiers for c in t["candidates"]]
    try:
        upgrades.add_costs(near, min(full) if full else None)
    except Exception as e:  # noqa: BLE001 — sin precios de piezas, igual se muestran
        log.info("precios de piezas: %s", e)
    confirm_upgrades(near)
    main_q = broad[0]
    return {
        "tiers": tiers,
        "near": near,
        "recommended": recommend(tiers, budget.get("level", "sin_definir")),
        "stores": sorted(report.stores, key=lambda s: (not s["ok"], -s["found"], s["name"])),
        "counts": {"found": len(report.listings), "evaluated": len(pool),
                   "matching": sum(1 for m in models if m["score"] >= MIN_SCORE),
                   "unavailable": unavailable},
        "postal_code": postal_code or None,
        "links": {
            # Mercado Libre devuelve poco con búsquedas amplias ("notebook gamer 32gb"): primero los
            # modelos (marca + modelo corto) y después una búsqueda amplia
            "mercadolibre": [{"query": q, "url": mercadolibre_url(q)} for q in (models_q[:4] + broad[:1])],
            "stores": [{"name": s.name, "url": store_search_url(s, main_q)} for s in shops],
        },
    }
