"""Servidor web: la página y la API.

  GET  /api/status            proveedor de IA, tiendas, categorías y si se puede seguir precios
  POST /api/interview         un turno de la entrevista  {conversation: [...], finish: bool}
  POST /api/candidates        busca y evalúa candidatos  {profile: {...}}
  POST /api/follow            "Seguir precio": crea el seguimiento y devuelve el link de Telegram
  POST /api/telegram/<clave>  webhook del bot (lo llama Telegram)
  POST /api/feedback          "¿Te sirvió?"
"""
from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from core import (candidates, comparaya, db, feedback, follow, guard, interview, search, settings, stores,
                  telegram)
from core.ai import AIError

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("app")
STATIC = Path(__file__).parent / "static"


def follow_enabled() -> bool:
    return telegram.enabled() and db.is_configured()


@asynccontextmanager
async def lifespan(_: FastAPI):
    # En Render: registrar el webhook del bot en la dirección pública (RENDER_EXTERNAL_URL)
    base = os.getenv("RENDER_EXTERNAL_URL") or os.getenv("PUBLIC_URL")
    if base and telegram.enabled():
        try:
            await run_in_threadpool(telegram.set_webhook, base)
        except Exception as e:  # noqa: BLE001 — sin webhook la web igual anda
            log.warning("No se pudo registrar el webhook de Telegram: %s", e)
    yield


app = FastAPI(title="Compra Óptima", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC), name="static")


class Message(BaseModel):
    role: str
    content: str = Field(max_length=4000)


class InterviewIn(BaseModel):
    conversation: list[Message] = Field(min_length=1, max_length=60)
    finish: bool = False


class CandidatesIn(BaseModel):
    profile: dict


class FeedbackIn(BaseModel):
    useful: bool | None = None
    comment: str = Field(default="", max_length=1500)
    product: str = Field(default="", max_length=120)
    recommended: str = Field(default="", max_length=120)
    matching: int | None = None
    queries: list[str] = Field(default_factory=list, max_length=12)


@app.exception_handler(AIError)
async def ai_error(_: Request, exc: AIError):
    return JSONResponse(status_code=400, content={"error": str(exc)})


@app.exception_handler(guard.Denied)
async def denied(_: Request, exc: guard.Denied):
    return JSONResponse(status_code=exc.status, content={"error": str(exc), "code_required": exc.status == 401})


def protect(request: Request, bucket: str, limit: int) -> None:
    """Código de acceso (si está configurado) y límite por hora por persona."""
    guard.check_code(request.headers.get("x-access-code"))
    if settings.access_code():   # los límites sólo aplican a la versión publicada
        ip = guard.client_ip(request.headers, request.client.host if request.client else None)
        guard.limiter.hit(ip, bucket, limit)


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/api/status")
def status():
    reg = stores.load()
    provider = settings.ai_provider()
    return {"provider": provider,
            "model": settings.gemini_model().split(",")[0] if provider == "gemini" else None,
            "stores": len(reg.stores),
            "code_required": bool(settings.access_code()),
            "follow_enabled": follow_enabled(),
            "categories": {k: v["label"] for k, v in reg.categories.items()}}


@app.post("/api/check-code")
def check_code(request: Request):
    guard.check_code(request.headers.get("x-access-code"))
    return {"ok": True}


@app.post("/api/interview")
def do_interview(body: InterviewIn, request: Request):
    protect(request, "interview", settings.LIMIT_INTERVIEW_PER_HOUR)
    return interview.step([m.model_dump() for m in body.conversation], finish=body.finish)


@app.post("/api/candidates")
def do_candidates(body: CandidatesIn, request: Request):
    protect(request, "search", settings.LIMIT_SEARCH_PER_HOUR)
    return candidates.find(interview.normalize_profile(body.profile))


class Offer(BaseModel):
    price: float = Field(gt=0)
    store_name: str = Field(default="", max_length=80)
    url: str = Field(default="", max_length=1000)


class FollowIn(BaseModel):
    model: str = Field(min_length=2, max_length=120)
    category: str = Field(max_length=40)
    product: str = Field(default="", max_length=120)
    best: Offer


@app.post("/api/follow")
def do_follow(body: FollowIn, request: Request):
    protect(request, "follow", 30)
    if not follow_enabled():
        raise AIError("Seguir precios todavía no está activado en esta versión.")
    if body.category not in stores.load().categories:
        raise AIError("Categoría desconocida.")
    tok = follow.create(body.model, body.category, body.product, body.best.model_dump())
    return {"link": telegram.deep_link(tok)}


class MlIn(BaseModel):
    model: str = Field(min_length=2, max_length=120)
    product: str = Field(default="", max_length=120)
    title: str = Field(default="", max_length=300)


@app.post("/api/ml")
def do_ml(body: MlIn, request: Request):
    """"¿Y en Mercado Libre?": precio de ML (y de tiendas que no leemos) vía la ficha de ComparaYa."""
    protect(request, "ml", 40)
    out = comparaya.find(body.model, body.product, body.title)
    out["search_url"] = search.mercadolibre_url(body.model)
    return out


@app.post("/api/telegram/{secret}")
async def telegram_webhook(secret: str, request: Request):
    import hmac
    if not telegram.enabled() or not hmac.compare_digest(secret, telegram.webhook_secret()):
        return JSONResponse(status_code=404, content={"error": "no"})
    update = await request.json()
    message = update.get("message") or {}
    reply = await run_in_threadpool(follow.handle, message)
    if reply:
        await run_in_threadpool(telegram.send, message["chat"]["id"], reply)
    return {"ok": True}


@app.post("/api/feedback")
def do_feedback(body: FeedbackIn, request: Request):
    protect(request, "feedback", 10)
    data = body.model_dump()
    data["queries"] = [q[:80] for q in data["queries"]]
    return {"ok": True, "sent": feedback.send(data)}


if __name__ == "__main__":
    import os

    import uvicorn

    # En Render (u otro hosting) llega PORT y hay que escuchar en todas las interfaces
    port = os.getenv("PORT")
    uvicorn.run(app, host="0.0.0.0" if port else "127.0.0.1", port=int(port or 8010))
