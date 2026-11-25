"""Registro de uso de la IA y control de cuotas.

Qué resuelve:
  1. MEDIR: cada consulta guarda tokens de entrada y salida reales (los devuelven las dos APIs), tarea,
     IA, modelo y duración. Sin esto no se puede saber si se gasta bien.
  2. PREVENIR: antes de consultar se verifica que el pedido entre en lo que le queda al modelo en el
     minuto y en el día (config/ai_limits.json). Si no entra, ni se intenta: ahorra una consulta
     perdida (un 429 cuesta tiempo y, en algunos servicios, también cuenta contra el límite).
  3. DECIDIR: `headroom()` dice cuánto margen tiene cada IA para repartir el trabajo (ver ai.py).

El conteo está en memoria del servidor y se reconstruye al arrancar con lo guardado en la tabla
`ai_usage` (la base compartida de Neon), así un reinicio no "olvida" lo gastado hoy.
El día de Gemini se reinicia a medianoche de California (hora del Pacífico).
"""
from __future__ import annotations

import fnmatch
import json
import logging
import threading
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from functools import lru_cache

from core import settings

log = logging.getLogger("usage")
WINDOW_S = 60
HEALTH_MIN = 0.5     # por debajo de esta fracción de aciertos, la IA pasa al final del orden


@lru_cache(maxsize=1)
def _config() -> dict:
    return json.loads((settings.CONFIG_DIR / "ai_limits.json").read_text(encoding="utf-8"))


def limits_for(provider: str, model: str) -> dict:
    """Límites del plan gratis para ese modelo ({} si no hay dato)."""
    for rule in _config()["limits"]:
        if rule["provider"] == provider and fnmatch.fnmatch(model, rule["model"]):
            return rule
    return {}


def _day_key(now: datetime | None = None) -> str:
    """Día según el reinicio de Google: medianoche del Pacífico (UTC-8 en invierno, UTC-7 en verano)."""
    now = now or datetime.now(timezone.utc)
    try:
        from zoneinfo import ZoneInfo
        return now.astimezone(ZoneInfo("America/Los_Angeles")).strftime("%Y-%m-%d")
    except Exception:  # noqa: BLE001 — sin base de zonas horarias (Windows sin tzdata): UTC-8 fijo
        return (now - timedelta(hours=8)).strftime("%Y-%m-%d")


def estimate_tokens(text: str) -> int:
    """Aproximación para el español en JSON: ~3,2 caracteres por token. Sólo sirve para decidir si
    un pedido entra en la cuota; el consumo real se mide con lo que devuelve la API."""
    return int(len(text) / 3.2) + 1


# Salida esperada por tarea (medida el 25/11/2026, ver docs/AI.md): la evaluación crece con la tanda
OUT_ESTIMATE = {"interview": 900, "profile": 2200, "evaluate_per_item": 230}


def estimate_call(system: str, messages: list[dict], task: str) -> int:
    """Tokens que gastaría un pedido: entrada (texto) + salida esperada según la tarea. En la evaluación
    la salida crece con la cantidad de publicaciones de la tanda."""
    text = system + "".join(m["content"] for m in messages)
    out = OUT_ESTIMATE.get(task, 900)
    if task == "profile":
        out = OUT_ESTIMATE["profile"]       # el turno que puede cerrar con el perfil: salida larga
    if task == "evaluate":
        last = messages[-1]["content"] if messages else ""
        marker = "PUBLICACIONES" + chr(10)
        items = last.split(marker)[-1].count(chr(10)) + 1 if marker in last else 10
        out = items * OUT_ESTIMATE["evaluate_per_item"]
    return estimate_tokens(text) + out


class Ledger:
    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.calls: deque[dict] = deque(maxlen=2000)
        self.minute: dict[tuple, deque] = defaultdict(deque)     # (proveedor, modelo) -> [(ts, tokens)]
        self.day: dict[tuple, list] = {}                           # (proveedor, modelo) -> [día, consultas]
        self.loaded = False

    # ---------------------------------------------------------------- lectura
    def _prune(self, key: tuple, now: float) -> deque:
        q = self.minute[key]
        while q and q[0][0] <= now - WINDOW_S:
            q.popleft()
        return q

    def _day_count(self, key: tuple) -> int:
        today = _day_key()
        d = self.day.get(key)
        if not d or d[0] != today:
            self.day[key] = [today, 0]
            return 0
        return d[1]

    def used(self, provider: str, model: str) -> dict:
        with self.lock:
            self._load()
            key, now = (provider, model), time.time()
            q = self._prune(key, now)
            return {"rpm": len(q), "tpm": sum(t for _, t in q), "rpd": self._day_count(key)}

    def headroom(self, provider: str, model: str, est_tokens: int = 0) -> float:
        """Fracción de cuota libre (1 = todo libre, 0 = no entra). Considera la más ajustada entre
        consultas por minuto, tokens por minuto (con lo que gastaría este pedido) y consultas por día."""
        lim, u = limits_for(provider, model), self.used(provider, model)
        margin = _config().get("margin", 0.85)
        fracs = []
        for k, need in (("rpm", 1), ("tpm", est_tokens), ("rpd", 1)):
            cap = lim.get(k)
            if cap:
                fracs.append((cap * margin - u[k] - need) / (cap * margin))
        return max(0.0, min(fracs)) if fracs else 1.0

    def fits(self, provider: str, model: str, est_tokens: int = 0) -> bool:
        return self.headroom(provider, model, est_tokens) > 0

    def health(self, provider: str, window_s: int = 600, last: int = 8) -> float:
        """Fracción de aciertos de esta IA en sus últimas consultas de los últimos 10 minutos (1 = todas
        bien). Con menos de 3 consultas no hay dato y se la considera sana. Los 429 cuentan como fallo."""
        now = time.time()
        with self.lock:
            recent = [c for c in self.calls if c["provider"] == provider and now - c["t"] < window_s][-last:]
        if len(recent) < 3:
            return 1.0
        return sum(1 for c in recent if c["status"] == 200) / len(recent)

    # ---------------------------------------------------------------- escritura
    def record(self, provider: str, model: str, task: str, prompt: int, output: int,
               status: int | str, ms: int, thoughts: int = 0) -> None:
        total = prompt + output + thoughts
        now = time.time()
        row = {"t": now, "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"), "provider": provider,
               "model": model, "task": task, "prompt": prompt, "output": output, "thoughts": thoughts,
               "status": status, "ms": ms}
        with self.lock:
            self._load()
            self.calls.append(row)
            key = (provider, model)
            if status != 429:        # un 429 no gastó cuota
                self._prune(key, now).append((now, total))
                self._day_count(key)
                self.day[key][1] += 1
        _persist(row)

    def _load(self) -> None:
        """Una vez por proceso: trae de la base lo gastado hoy (el servidor pudo haberse reiniciado)."""
        if self.loaded:
            return
        self.loaded = True
        try:
            from sqlalchemy import select

            from core import db
            since = datetime.now(timezone.utc) - timedelta(hours=26)
            with db.engine().connect() as c:
                rows = c.execute(select(db.ai_usage).where(db.ai_usage.c.ts >= since)).mappings().all()
            today, now = _day_key(), time.time()
            for r in rows:
                ts = r["ts"] if r["ts"].tzinfo else r["ts"].replace(tzinfo=timezone.utc)
                if r["status"] == "429":
                    continue
                key = (r["provider"], r["model"])
                if _day_key(ts) == today:
                    self._day_count(key)
                    self.day[key][1] += 1
                if now - ts.timestamp() < WINDOW_S:
                    self.minute[key].append((ts.timestamp(), r["prompt"] + r["output"] + (r["thoughts"] or 0)))
        except Exception as e:  # noqa: BLE001 — sin base, sólo memoria
            log.debug("uso de IA sin base: %s", e)

    # ---------------------------------------------------------------- resumen
    def summary(self) -> dict:
        """Por tarea, IA y modelo: consultas, tokens promedio y totales (de las últimas 2000)."""
        with self.lock:
            agg: dict[tuple, dict] = {}
            for c in self.calls:
                if c["status"] != 200:
                    continue
                a = agg.setdefault((c["task"], c["provider"], c["model"]),
                                   {"calls": 0, "prompt": 0, "output": 0, "thoughts": 0, "ms": 0})
                a["calls"] += 1
                for k in ("prompt", "output", "thoughts", "ms"):
                    a[k] += c[k] or 0
            rows = []
            for (task, prov, model), a in sorted(agg.items()):
                n = a["calls"]
                rows.append({"task": task, "provider": prov, "model": model, "calls": n,
                             "prompt_avg": a["prompt"] // n, "output_avg": a["output"] // n,
                             "thoughts_avg": a["thoughts"] // n, "ms_avg": a["ms"] // n,
                             "tokens_total": a["prompt"] + a["output"] + a["thoughts"]})
            quota = []
            for (prov, model) in sorted({(c["provider"], c["model"]) for c in self.calls}):
                lim, u = limits_for(prov, model), self.used(prov, model)
                quota.append({"provider": prov, "model": model, "used": u,
                              "limits": {k: lim.get(k) for k in ("rpm", "tpm", "rpd")}})
            return {"by_task": rows, "quota": quota, "failed": sum(1 for c in self.calls if c["status"] != 200)}


def _persist(row: dict) -> None:
    try:
        from core import db
        with db.engine().begin() as c:
            c.execute(db.ai_usage.insert().values(
                ts=datetime.fromisoformat(row["ts"]), provider=row["provider"], model=row["model"],
                task=row["task"], prompt=row["prompt"], output=row["output"], thoughts=row["thoughts"],
                status=str(row["status"]), ms=row["ms"]))
    except Exception as e:  # noqa: BLE001 — medir nunca rompe una consulta
        log.debug("no se pudo guardar el uso de IA: %s", e)


ledger = Ledger()
