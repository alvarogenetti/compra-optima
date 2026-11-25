"""Configuración leída del entorno (.env)."""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

CONFIG_DIR = ROOT / "config"


AI_KEYS = ("GEMINI_API_KEY", "GROQ_API_KEY", "CEREBRAS_API_KEY", "OPENROUTER_API_KEY")


def ai_provider() -> str:
    """'ia' si hay alguna clave; si no, 'demo' (salvo AI_PROVIDER=demo explícito)."""
    p = (os.getenv("AI_PROVIDER") or "").strip().lower()
    if p == "demo":
        return "demo"
    return "ia" if any(os.getenv(k) for k in AI_KEYS) else "demo"


def ai_chain() -> list[str]:
    """Orden en que se prueban las IA (sólo cuentan las que tienen clave)."""
    raw = os.getenv("AI_CHAIN") or "gemini,groq,cerebras,openrouter"
    return [x.strip().lower() for x in raw.split(",") if x.strip()]


def ai_names() -> list[str]:
    keys = {"gemini": "GEMINI_API_KEY", "groq": "GROQ_API_KEY", "cerebras": "CEREBRAS_API_KEY",
            "openrouter": "OPENROUTER_API_KEY"}
    return [n for n in ai_chain() if os.getenv(keys.get(n, "-"))]


def gemini_key() -> str:
    return (os.getenv("GEMINI_API_KEY") or "").strip()


def gemini_model() -> str:
    return (os.getenv("GEMINI_MODEL") or "gemini-3.1-flash-lite,gemini-3.5-flash-lite,gemini-flash-lite-latest,gemini-3-flash-preview,gemini-3.5-flash,gemini-flash-latest").strip()


def access_code() -> str:
    """Si está definido, la web pide este código antes de usar la IA (para la versión publicada:
    así nadie de afuera gasta el cupo gratis de Gemini). Vacío = sin código (uso local)."""
    return (os.getenv("ACCESS_CODE") or "").strip()


# Límites por persona (IP) y por hora, para la versión publicada
LIMIT_INTERVIEW_PER_HOUR = int(os.getenv("LIMIT_INTERVIEW_PER_HOUR") or 80)
LIMIT_SEARCH_PER_HOUR = int(os.getenv("LIMIT_SEARCH_PER_HOUR") or 12)

MIN_QUESTIONS = 3         # aunque el primer mensaje sea muy detallado, se repregunta lo que falte
MAX_QUESTIONS = 10        # tope de la entrevista: suficiente para cubrir los factores clave sin cansar
SEARCH_TIMEOUT_S = 20      # por tienda
MAX_LISTINGS_TO_EVALUATE = 150
