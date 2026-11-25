"""Especificaciones del fabricante para lo que la ficha de la tienda no dice.

Para el "costo de completar" (¿la RAM está soldada o en ranuras? ¿cuántas ranuras M.2?) y para datos
que el pedido exige (Hz), se busca la ficha oficial del modelo exacto con Gemini + búsqueda de Google
(grounding): Google lee las páginas, nosotros no scrapeamos sitios de fabricantes, y la respuesta viene
con las fuentes. Se guarda por modelo en la tabla `model_specs`.

La búsqueda de Google en Gemini NO está en el plan gratis (responde 429 "revisá tu plan"): mientras no
esté habilitada, todo queda "sin confirmar" y no se inventa nada. Se activa sola al habilitar la
facturación en Google AI Studio.
"""
from __future__ import annotations

import logging
import re
import time
from datetime import timedelta

import httpx

from core import settings
from core.ai import GEMINI_URL, parse_json_text

log = logging.getLogger("specs")
CONFIRMED_TTL_DAYS = 60
UNKNOWN_TTL_DAYS = 7
UNAVAILABLE_COOLDOWN_S = 3600
FIELDS = ("hz", "ram_slots", "ram_max_gb", "ram_soldered", "m2_slots")

_unavailable_until = 0.0
_memory: dict[str, dict] = {}

PROMPT = """Buscá la ficha técnica OFICIAL del fabricante (o una reseña técnica confiable) de este equipo
exacto: "{model}" (título en la tienda: "{title}").
Respondé SÓLO un objeto JSON, sin texto alrededor:
{{"hz": frecuencia de la pantalla en Hz o null, "ram_slots": cantidad de ranuras de RAM o null,
"ram_max_gb": RAM máxima soportada en GB o null, "ram_soldered": true si la RAM está soldada (no se
puede cambiar/ampliar), false si va en ranuras, o null, "m2_slots": cantidad de ranuras M.2 para discos
o null}}
Poné null en todo lo que no puedas confirmar con una fuente. No adivines por otros modelos parecidos."""


def _key(model: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (model or "").lower())[:120]


def available() -> bool:
    return bool(settings.gemini_key()) and settings.ai_provider() != "demo" and time.time() >= _unavailable_until


def _cache_get(key: str) -> dict | None:
    if key in _memory:
        return _memory[key]
    try:
        from sqlalchemy import select

        from core import db
        with db.engine().connect() as c:
            row = c.execute(select(db.model_specs).where(db.model_specs.c.model_key == key)).first()
        if row:
            confirmed = bool((row.data or {}).get("confirmed"))
            ttl = timedelta(days=CONFIRMED_TTL_DAYS if confirmed else UNKNOWN_TTL_DAYS)
            checked = row.checked_at if row.checked_at.tzinfo else row.checked_at.replace(tzinfo=db.now().tzinfo)
            if db.now() - checked < ttl:
                _memory[key] = row.data
                return row.data
    except Exception as e:  # noqa: BLE001
        log.debug("caché de fichas sin base: %s", e)
    return None


def _cache_put(key: str, data: dict) -> None:
    _memory[key] = data
    try:
        from core import db
        with db.engine().begin() as c:
            c.execute(db.model_specs.delete().where(db.model_specs.c.model_key == key))
            c.execute(db.model_specs.insert().values(model_key=key, data=data, checked_at=db.now()))
    except Exception as e:  # noqa: BLE001
        log.debug("caché de fichas sin base: %s", e)


def _clean(raw: dict) -> dict:
    def num(v, lo, hi):
        try:
            f = float(v)
        except (TypeError, ValueError):
            return None
        return int(f) if lo <= f <= hi else None
    out = {"hz": num(raw.get("hz"), 30, 600), "ram_slots": num(raw.get("ram_slots"), 0, 8),
           "ram_max_gb": num(raw.get("ram_max_gb"), 2, 512), "m2_slots": num(raw.get("m2_slots"), 0, 6),
           "ram_soldered": raw.get("ram_soldered") if isinstance(raw.get("ram_soldered"), bool) else None}
    return out


def lookup(model: str, title: str = "", client: httpx.Client | None = None) -> dict:
    """{"confirmed": bool, "hz", "ram_slots", "ram_max_gb", "ram_soldered", "m2_slots", "sources": [urls]}.
    confirmed=False y todo en None = sin confirmar."""
    global _unavailable_until
    key = _key(model)
    cached = _cache_get(key)
    if cached is not None:
        return cached
    unknown = {"confirmed": False, **{f: None for f in FIELDS}, "sources": []}
    if not available():
        return unknown
    body = {"contents": [{"role": "user", "parts": [{"text": PROMPT.format(model=model, title=title[:200])}]}],
            "tools": [{"google_search": {}}], "generationConfig": {"temperature": 0.1}}
    model_name = settings.gemini_model().split(",")[0].strip()
    own = client is None
    c = client or httpx.Client(timeout=40)
    try:
        r = c.post(GEMINI_URL.format(model=model_name), json=body, headers={"x-goog-api-key": settings.gemini_key()})
        if r.status_code in (400, 403, 429):
            # Sin búsqueda de Google en este plan (o sin cupo): no se vuelve a intentar por un rato
            _unavailable_until = time.time() + UNAVAILABLE_COOLDOWN_S
            log.info("Búsqueda de Google en Gemini no disponible (HTTP %s): fichas sin confirmar", r.status_code)
            return unknown
        if r.status_code != 200:
            return unknown
        cand = (r.json().get("candidates") or [{}])[0]
        text = "".join(p.get("text", "") for p in (cand.get("content") or {}).get("parts", []))
        sources = [ch.get("web", {}).get("uri") for ch in (cand.get("groundingMetadata") or {}).get("groundingChunks", [])]
        data = _clean(parse_json_text(text))
    except Exception as e:  # noqa: BLE001 — sin ficha, se dice "sin confirmar"
        log.info("ficha de %s: %s", model, e)
        return unknown
    finally:
        if own:
            c.close()
    data["sources"] = [s for s in sources if s][:3]
    data["confirmed"] = bool(data["sources"]) and any(data[f] is not None for f in FIELDS)
    if not data["confirmed"]:
        data = {**unknown}
    _cache_put(key, data)
    return data


def check_upgrade(spec: dict, fix: dict) -> str:
    """'si' (la ficha confirma que se puede), 'no' (la ficha dice que no) o 'sin_confirmar'."""
    if not spec.get("confirmed"):
        return "sin_confirmar"
    if fix.get("ram_add_gb"):
        if spec.get("ram_soldered") is True:
            return "no"
        if spec.get("ram_soldered") is None and not spec.get("ram_slots"):
            return "sin_confirmar"
    if fix.get("ssd_tb") and not spec.get("m2_slots"):
        return "sin_confirmar"
    return "si"


def describe(spec: dict) -> str:
    """Texto corto: "144 Hz · 2 ranuras de RAM (hasta 64 GB) · 2 M.2"."""
    if not spec.get("confirmed"):
        return ""
    parts = []
    if spec.get("hz"):
        parts.append(f"{spec['hz']} Hz")
    if spec.get("ram_soldered") is True:
        parts.append("RAM soldada")
    elif spec.get("ram_slots"):
        parts.append(f"{spec['ram_slots']} ranura{'s' if spec['ram_slots'] != 1 else ''} de RAM"
                     + (f" (hasta {spec['ram_max_gb']} GB)" if spec.get("ram_max_gb") else ""))
    if spec.get("m2_slots"):
        parts.append(f"{spec['m2_slots']} M.2")
    return " · ".join(parts)
