"""Servidor web: la página y tres endpoints.

  GET  /api/status       proveedor de IA, tiendas y categorías
  POST /api/interview    un turno de la entrevista  {conversation: [...], finish: bool}
  POST /api/candidates   busca y evalúa candidatos  {profile: {...}}
"""
from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from core import candidates, interview, settings, stores
from core.ai import AIError

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
STATIC = Path(__file__).parent / "static"

app = FastAPI(title="Compra Óptima")
app.mount("/static", StaticFiles(directory=STATIC), name="static")


class Message(BaseModel):
    role: str
    content: str = Field(max_length=4000)


class InterviewIn(BaseModel):
    conversation: list[Message] = Field(min_length=1, max_length=60)
    finish: bool = False


class CandidatesIn(BaseModel):
    profile: dict


@app.exception_handler(AIError)
async def ai_error(_: Request, exc: AIError):
    return JSONResponse(status_code=400, content={"error": str(exc)})


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/api/status")
def status():
    reg = stores.load()
    provider = settings.ai_provider()
    return {"provider": provider,
            "model": settings.gemini_model() if provider == "gemini" else None,
            "stores": len(reg.stores),
            "categories": {k: v["label"] for k, v in reg.categories.items()}}


@app.post("/api/interview")
def do_interview(body: InterviewIn):
    return interview.step([m.model_dump() for m in body.conversation], finish=body.finish)


@app.post("/api/candidates")
def do_candidates(body: CandidatesIn):
    return candidates.find(interview.normalize_profile(body.profile))


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8010)
