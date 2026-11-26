"""Trabajos largos en segundo plano, con progreso.

Una búsqueda completa tarda de 40 segundos a 3 minutos (varias consultas a tiendas e IA). Como un solo pedido
HTTP tan largo es frágil (proxies con límites de tiempo, la persona sin saber qué pasa), la búsqueda corre en
un hilo y la página pregunta cada pocos segundos cómo viene:

    POST /api/candidates        → {"job_id": ...}   (arranca el trabajo y contesta enseguida)
    GET  /api/candidates/<id>   → estado, paso actual y, al terminar, el resultado

Todo vive en memoria del servidor (un solo proceso: Render corre una instancia). Si el servidor se reinicia
a mitad de una búsqueda, el trabajo desaparece y la página lo informa para reintentar.
"""
from __future__ import annotations

import logging
import secrets
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Callable

from core.ai import AIError

log = logging.getLogger("jobs")
MAX_RUNNING = 3        # búsquedas a la vez: cada una gasta cuota de IA y consulta decenas de tiendas
TTL_S = 1800           # un trabajo terminado se descarta a los 30 minutos
GENERIC_ERROR = "Algo falló en la búsqueda. Probá de nuevo en un momento."


class Busy(Exception):
    """Ya hay MAX_RUNNING búsquedas corriendo."""


@dataclass
class Job:
    id: str
    status: str = "running"                  # running | done | error
    stage: str = ""                          # paso actual (clave)
    detail: str = ""                         # texto corto del paso ("3 de 5 tandas")
    seen: list[str] = field(default_factory=list)   # pasos que ya arrancaron, en orden
    result: dict | None = None
    error: str | None = None
    started: float = field(default_factory=time.time)
    finished: float | None = None
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def progress(self, stage: str, detail: str = "") -> None:
        with self._lock:
            if stage not in self.seen:
                self.seen.append(stage)
            self.stage, self.detail = stage, detail

    def snapshot(self) -> dict:
        with self._lock:
            end = self.finished or time.time()
            out = {"status": self.status, "stage": self.stage, "detail": self.detail, "seen": list(self.seen),
                   "elapsed_s": int(end - self.started)}
            if self.status == "done":
                out["result"] = self.result
            elif self.status == "error":
                out["error"] = self.error
            return out


class JobManager:
    def __init__(self, max_running: int = MAX_RUNNING, ttl_s: int = TTL_S):
        self.max_running, self.ttl_s = max_running, ttl_s
        self.jobs: dict[str, Job] = {}
        self.lock = threading.Lock()
        self.pool = ThreadPoolExecutor(max_workers=max_running, thread_name_prefix="busqueda")

    def start(self, fn: Callable[[Callable[[str, str], None]], dict]) -> Job:
        """Arranca fn(progress) en un hilo. Levanta Busy si ya hay MAX_RUNNING corriendo."""
        with self.lock:
            self._cleanup()
            if sum(1 for j in self.jobs.values() if j.status == "running") >= self.max_running:
                raise Busy("Hay varias búsquedas en curso. Esperá un minuto y probá de nuevo.")
            job = Job(id=secrets.token_urlsafe(12))
            self.jobs[job.id] = job
        self.pool.submit(self._run, job, fn)
        return job

    def get(self, job_id: str) -> Job | None:
        with self.lock:
            return self.jobs.get(job_id)

    def _run(self, job: Job, fn: Callable) -> None:
        try:
            job.result = fn(job.progress)
            job.status = "done"
        except AIError as e:                # errores pensados para la persona (sin cuota, sin tiendas...)
            job.error, job.status = str(e), "error"
        except Exception:                   # noqa: BLE001 — un bug no tiene que tumbar al servidor ni mostrar trazas
            log.exception("la búsqueda falló")
            job.error, job.status = GENERIC_ERROR, "error"
        finally:
            job.finished = time.time()

    def _cleanup(self) -> None:
        now = time.time()
        for jid in [j.id for j in self.jobs.values() if j.finished and now - j.finished > self.ttl_s]:
            del self.jobs[jid]


manager = JobManager()
