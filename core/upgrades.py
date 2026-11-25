"""Cuánto cuesta completar un "casi cumple": si a una notebook le falta RAM o disco y se puede ampliar
(ranura libre, RAM no soldada), se busca el precio real del módulo o del SSD en las tiendas y se suma.

La IA dice en la evaluación si se puede ampliar y cuánto falta ("fix"); acá sólo se buscan precios:
  - RAM: "memoria sodimm ddr5 16gb" (o DDR4 según el título/la ficha)
  - Disco: "disco ssd m.2 nvme 1tb"
El precio de referencia es la mediana de las 5 publicaciones más baratas que encajan (no la más barata,
que suele ser rara), con un link de ejemplo.
"""
from __future__ import annotations

import re
import statistics
import unicodedata

from core import stores
from core.search import search_all


def _fold(s: str) -> str:
    return unicodedata.normalize("NFKD", (s or "").lower()).encode("ascii", "ignore").decode()


def ram_type(text: str) -> str:
    t = _fold(text)
    return "ddr4" if "ddr4" in t and "ddr5" not in t else "ddr5"


def part_queries(kind: str, size: float, ddr: str = "ddr5") -> tuple[str, re.Pattern]:
    if kind == "ram":
        gb = int(size)
        return (f"memoria sodimm {ddr} {gb}gb",
                re.compile(rf"(?=.*\b{gb}\s?gb\b)(?=.*\b{ddr}\b)(?=.*(sodimm|so-dimm))"))
    tb = size
    label = f"{int(tb)}tb" if tb >= 1 else f"{int(tb * 1000)}gb"
    return (f"disco ssd m.2 nvme {label}",
            re.compile(rf"(?=.*\b{re.escape(label[:-2])}\s?{label[-2:]}\b)(?=.*\bssd\b)(?=.*(m\.?2|nvme))"))


# Equipos completos que mencionan "32GB DDR5 SODIMM" o "SSD M.2" en el título: no son la pieza
NOT_A_PART = re.compile(r"\b(notebook|laptop|netbook|pc|computadora|all in one|mini pc|kit)\b")


def part_price(kind: str, size: float, ddr: str = "ddr5") -> dict | None:
    """{label, price, url, title} o None si no se encontró."""
    query, pattern = part_queries(kind, size, ddr)
    shops = stores.load().for_categories(["informatica"])
    report = search_all(shops, [query])
    hits = sorted((x for x in report.listings if pattern.search(_fold(x["title"]))
                   and not NOT_A_PART.search(_fold(x["title"]))), key=lambda x: x["price"])
    if not hits:
        return None
    cheapest = hits[:5]
    ref = statistics.median(x["price"] for x in cheapest)
    example = min(cheapest, key=lambda x: abs(x["price"] - ref))
    label = (f"Módulo de RAM {int(size)} GB {ddr.upper()}" if kind == "ram"
             else f"SSD M.2 NVMe de {int(size) if size >= 1 else int(size * 1000)} {'TB' if size >= 1 else 'GB'}")
    return {"label": label, "price": round(ref), "url": example["url"], "title": example["title"],
            "store_name": example["store_name"]}


def add_costs(near: list[dict], full_price: float | None) -> None:
    """Agrega m["upgrade"] a los "casi cumplen" que se pueden completar. Busca cada pieza una sola vez."""
    cache: dict[tuple, dict | None] = {}
    for m in near:
        fix = m.get("fix") or {}
        parts = []
        ddr = ram_type(m["best"].get("title", "") + " " + (m.get("specs") or ""))
        for kind, size in (("ram", fix.get("ram_add_gb")), ("ssd", fix.get("ssd_tb"))):
            if not size:
                continue
            key = (kind, size, ddr if kind == "ram" else "")
            if key not in cache:
                cache[key] = part_price(kind, size, ddr if kind == "ram" else "")
            if cache[key]:
                parts.append(cache[key])
        if not parts:
            continue
        total = m["best"]["price"] + sum(p["price"] for p in parts)
        m["upgrade"] = {"parts": parts, "total": total, "note": fix.get("note", ""),
                        "full_price": full_price,
                        "saves": round(full_price - total) if full_price and full_price > total else None}
