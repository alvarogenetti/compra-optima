"""Protección de la versión publicada: código de acceso y límite de pedidos por persona.

En memoria (se reinicia con el servidor): alcanza para una prueba con amigos y familia.
"""
from __future__ import annotations

import hmac
import threading
import time
from collections import defaultdict, deque

from core import settings


class Denied(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


def check_code(given: str | None) -> None:
    expected = settings.access_code()
    if expected and not hmac.compare_digest((given or "").strip().encode(), expected.encode()):
        raise Denied(401, "Código de acceso incorrecto.")


class RateLimiter:
    def __init__(self, window_s: float = 3600):
        self.window = window_s
        self.hits: dict[tuple[str, str], deque] = defaultdict(deque)
        self.lock = threading.Lock()

    def hit(self, who: str, bucket: str, limit: int, now: float | None = None) -> None:
        now = time.time() if now is None else now
        with self.lock:
            q = self.hits[(who, bucket)]
            while q and q[0] <= now - self.window:
                q.popleft()
            if len(q) >= limit:
                wait = int((q[0] + self.window - now) / 60) + 1
                raise Denied(429, f"Llegaste al límite de la prueba por ahora. Probá de nuevo en {wait} min.")
            q.append(now)


limiter = RateLimiter()


def client_ip(headers, fallback: str | None) -> str:
    """Detrás del proxy de Render la IP real viene en X-Forwarded-For (la primera)."""
    fwd = headers.get("x-forwarded-for", "")
    return fwd.split(",")[0].strip() or fallback or "?"
