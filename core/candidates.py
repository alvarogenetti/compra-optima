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

import re
import unicodedata

from core import settings, stores
from core.ai import AIError, get_ai
from core.search import mercadolibre_url, search_all, store_search_url

EVAL_SYSTEM = """Sos el asesor de compras de "Compra Óptima" (Argentina). Te paso el perfil de compra de una
persona y una lista de publicaciones REALES de tiendas argentinas (id | título | precio | tienda).
Evaluá cada publicación contra el perfil, sólo con lo que dice el título y lo que sepas con seguridad
de ese modelo. Español rioplatense, simple, sin tecnicismos sin explicar.

Para cada publicación que SEA el producto buscado (no accesorios, repuestos ni otra cosa) devolvé:
- "id": el id de la lista.
- "model": marca + modelo normalizado, igual para la misma cosa en distintas tiendas
  ("Logitech G305", no "Mouse Gamer Logitech G305 Lightspeed Negro").
- "score": de 0 a 100, qué tan bien cumple el perfil (los "must" pesan mucho más que los "nice").
- "meets": hasta 3 cosas del perfil que cumple, en palabras simples.
- "doubts": hasta 2 cosas que no cumple o que no se pueden confirmar por el título.
- "reason": una oración de por qué le sirve (o no) a esta persona.
Las publicaciones que NO son el producto, omitilas.

Respondé SÓLO JSON: {"items": [{"id": 0, "model": "", "score": 0, "meets": [], "doubts": [], "reason": ""}]}"""

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
    return {t for t in re.findall(r"[a-z0-9]+", _fold(s)) if len(t) > 1}


def relevance(listing: dict, queries: list[str]) -> float:
    """Qué tan bien matchea el título alguna de las búsquedas (0 a 1), sin IA."""
    title = _tokens(listing["title"])
    best = 0.0
    for q in queries:
        qt = _tokens(q)
        if qt:
            best = max(best, len(qt & title) / len(qt))
    return best


def prefilter(listings: list[dict], profile: dict, queries: list[str]) -> list[dict]:
    cap = profile.get("budget", {}).get("max_ars")
    keep = [x for x in listings if not cap or x["price"] <= cap * 1.3]
    for x in keep:
        x["_rel"] = relevance(x, queries)
    keep = [x for x in keep if x["_rel"] >= 0.5]
    keep.sort(key=lambda x: (-x["_rel"], x["price"]))
    return keep[:settings.MAX_LISTINGS_TO_EVALUATE]


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


def evaluate(listings: list[dict], profile: dict) -> list[dict]:
    """Pide a la IA la evaluación y la cruza con las publicaciones (por id)."""
    if not listings:
        return []
    lines = "\n".join(f"{i} | {x['title']} | ${x['price']:,.0f} | {x['store_name']}".replace(",", ".")
                      for i, x in enumerate(listings))
    raw = get_ai().json(EVAL_SYSTEM, [{"role": "user", "content":
                                       f"PERFIL\n{profile_text(profile)}\n\nPUBLICACIONES\n{lines}"}],
                        task="evaluate")
    out = []
    for it in raw.get("items") or []:
        try:
            i = int(it.get("id"))
            score = max(0, min(100, int(float(it.get("score") or 0))))
        except (TypeError, ValueError):
            continue
        if not 0 <= i < len(listings):
            continue
        out.append({**listings[i], "model": str(it.get("model") or listings[i]["title"]).strip()[:80],
                    "score": score,
                    "meets": [str(m)[:120] for m in (it.get("meets") or [])][:3],
                    "doubts": [str(d)[:120] for d in (it.get("doubts") or [])][:2],
                    "reason": str(it.get("reason") or "")[:300]})
    return out


def model_key(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", _fold(name))


def group_by_model(evaluated: list[dict], cap: float | None = None) -> list[dict]:
    groups: dict[str, dict] = {}
    for x in evaluated:
        k = model_key(x["model"])
        if not k:
            continue
        g = groups.setdefault(k, {"model": x["model"], "score": x["score"], "meets": x["meets"],
                                  "doubts": x["doubts"], "reason": x["reason"], "offers": []})
        if x["score"] > g["score"]:
            g.update(score=x["score"], meets=x["meets"], doubts=x["doubts"], reason=x["reason"])
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


def build_tiers(models: list[dict]) -> list[dict]:
    """Corta por precio en tercios entre los que cumplen; en cada tramo, los mejores puntajes."""
    good = sorted((m for m in models if m["score"] >= MIN_SCORE), key=lambda m: m["best"]["price"])
    if not good:
        return []
    if len(good) <= PER_TIER:
        buckets = [good, [], []]
    else:
        n = len(good)
        buckets = [good[: n // 3 or 1], good[n // 3 or 1: 2 * n // 3], good[2 * n // 3:]]
    tiers = []
    for (key, label, blurb), bucket in zip(TIERS, buckets):
        if not bucket:
            continue
        top = sorted(bucket, key=lambda m: (-m["score"], m["best"]["price"]))[:PER_TIER]
        tiers.append({"key": key, "label": label, "blurb": blurb,
                      "price_from": bucket[0]["best"]["price"], "price_to": bucket[-1]["best"]["price"],
                      "candidates": sorted(top, key=lambda m: m["best"]["price"])})
    if len(tiers) == 1:
        tiers[0].update(label="Opciones que cumplen", blurb="Encontramos pocas opciones: estas son las que mejor encajan.")
    return tiers


def recommend(tiers: list[dict]) -> str | None:
    """Mi elección: el mejor puntaje de la equilibrada (o del tramo que haya), el más barato si empatan."""
    pool = next((t["candidates"] for t in tiers if t["key"] == "equilibrada"), None) or \
        [c for t in tiers for c in t["candidates"]]
    if not pool:
        return None
    return max(pool, key=lambda m: (m["score"], -m["best"]["price"]))["model"]


def find(profile: dict) -> dict:
    reg = stores.load()
    shops = reg.for_categories([profile["category"]])
    if not shops:
        raise AIError("Todavía no tenemos tiendas para esa categoría.")
    queries = profile["search_queries"] + [m for m in profile.get("suggested_models", [])
                                           if m.lower() not in {q.lower() for q in profile["search_queries"]}]
    report = search_all(shops, queries, profile.get("exclude_words"))
    pool = prefilter(report.listings, profile, queries)
    evaluated = evaluate(pool, profile)
    models = group_by_model(evaluated, profile.get("budget", {}).get("max_ars"))
    tiers = build_tiers(models)
    main_q = profile["search_queries"][0]
    return {
        "tiers": tiers,
        "recommended": recommend(tiers),
        "stores": sorted(report.stores, key=lambda s: (not s["ok"], -s["found"], s["name"])),
        "counts": {"found": len(report.listings), "evaluated": len(pool),
                   "matching": sum(1 for m in models if m["score"] >= MIN_SCORE)},
        "links": {
            "mercadolibre": [{"query": q, "url": mercadolibre_url(q)} for q in profile["search_queries"]],
            "stores": [{"name": s.name, "url": store_search_url(s, main_q)} for s in shops],
        },
    }
