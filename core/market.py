"""Precio de mercado y descuento REAL (traído del tracker de monitores, services/market.py).

Una oferta no es "lo que la tienda dice que bajó", sino cuánto está por debajo del precio al que ese
modelo se vende normalmente en TODAS las tiendas.

  referencia     = mediana del mejor precio de cada escaneo en los últimos 30 días, sin las últimas
                   24 h (así una baja de hoy se compara contra "antes"). Mientras no haya historia
                   suficiente, el precio de cuando la persona empezó a seguirlo.
  descuento real = (referencia - precio) / referencia
  niveles        >= 15 % buena oferta, >= 30 % excepcional
  inflado        la tienda anuncia -20 % o más, pero contra el mercado es menos de la mitad
"""
from __future__ import annotations

import statistics
from datetime import datetime, timedelta, timezone

GOOD = 0.15
GREAT = 0.30
WINDOW_DAYS = 30
EXCLUDE_RECENT_H = 24
MIN_POINTS = 6          # escaneos mínimos en la ventana para confiar en la mediana
MIN_SPAN_DAYS = 2       # ...repartidos en al menos 2 días

LEVELS = {"excepcional": "🔥 Oferta excepcional", "buena": "💰 Buena oferta"}


def level(disc: float | None) -> str | None:
    if disc is None:
        return None
    return "excepcional" if disc >= GREAT else ("buena" if disc >= GOOD else None)


def _aware(t: datetime) -> datetime:
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def median_reference(points: list[tuple[datetime, float]], now: datetime | None = None,
                     min_points: int = MIN_POINTS, min_span_days: int = MIN_SPAN_DAYS) -> tuple[float | None, int]:
    """points: [(ts, mejor precio de ese escaneo)]. Devuelve (mediana, n) o (None, n)."""
    now = now or datetime.now(timezone.utc)
    lo, hi = now - timedelta(days=WINDOW_DAYS), now - timedelta(hours=EXCLUDE_RECENT_H)
    pts = [(_aware(t), v) for t, v in points if v]
    pts = [(t, v) for t, v in pts if lo <= t <= hi]
    if len(pts) < min_points or (max(t for t, _ in pts) - min(t for t, _ in pts)).days < min_span_days:
        return None, len(pts)
    return statistics.median(v for _, v in pts), len(pts)


def reference(points: list[tuple[datetime, float]], seed: float | None = None,
              now: datetime | None = None) -> tuple[float | None, str]:
    """(precio de referencia, de dónde sale): la mediana de la historia o el precio inicial."""
    ref, n = median_reference(points, now)
    if ref:
        return ref, f"precio habitual de los últimos 30 días ({n} escaneos)"
    if seed:
        return seed, "precio de cuando empezaste a seguirlo"
    return None, "sin historia todavía"


def real_discount(price: float | None, ref: float | None) -> float | None:
    if not price or not ref:
        return None
    return (ref - price) / ref


def claimed_discount(price: float | None, list_price: float | None) -> float | None:
    if not price or not list_price or list_price <= price:
        return None
    return (list_price - price) / list_price


def inflated(price: float | None, list_price: float | None, ref: float | None) -> bool:
    claimed = claimed_discount(price, list_price)
    real = real_discount(price, ref)
    return bool(claimed and claimed >= 0.20 and (real is None or real < claimed / 2))
