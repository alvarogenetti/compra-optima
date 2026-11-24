"""Configuración leída del entorno (.env)."""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

CONFIG_DIR = ROOT / "config"


def ai_provider() -> str:
    """'gemini' si hay clave; si no, 'demo' (salvo que se pida otra cosa explícitamente)."""
    p = (os.getenv("AI_PROVIDER") or "").strip().lower()
    if p:
        return p
    return "gemini" if os.getenv("GEMINI_API_KEY") else "demo"


def gemini_key() -> str:
    return (os.getenv("GEMINI_API_KEY") or "").strip()


def gemini_model() -> str:
    return (os.getenv("GEMINI_MODEL") or "gemini-3-flash-preview,gemini-flash-lite-latest,gemini-flash-latest").strip()


def access_code() -> str:
    """Si está definido, la web pide este código antes de usar la IA (para la versión publicada:
    así nadie de afuera gasta el cupo gratis de Gemini). Vacío = sin código (uso local)."""
    return (os.getenv("ACCESS_CODE") or "").strip()


# Límites por persona (IP) y por hora, para la versión publicada
LIMIT_INTERVIEW_PER_HOUR = int(os.getenv("LIMIT_INTERVIEW_PER_HOUR") or 80)
LIMIT_SEARCH_PER_HOUR = int(os.getenv("LIMIT_SEARCH_PER_HOUR") or 12)

MAX_QUESTIONS = 8         # la entrevista no pasa de esto: con más, la gente abandona
SEARCH_TIMEOUT_S = 20      # por tienda
MAX_LISTINGS_TO_EVALUATE = 120
