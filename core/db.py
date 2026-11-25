"""Base de datos: seguimientos, quién los recibe y el historial de precios.

- Publicado: Postgres en Neon (DATABASE_URL, la misma en Render y en GitHub Actions).
- En tu PC sin DATABASE_URL: SQLite en data/compra.db.

Tablas:
  watchers  personas (un chat de Telegram cada una)
  follows   "seguir precio": un modelo, quién lo sigue y el precio de cuando empezó
  prices    mejor precio por tienda en cada escaneo, por modelo (compartido entre todos los que lo siguen)
"""
from __future__ import annotations

import os
from datetime import datetime, timezone

from sqlalchemy import (JSON, Boolean, Column, DateTime, Float, ForeignKey, Integer, MetaData, String,
                        Table, Text, create_engine)
from sqlalchemy.engine import Engine

from core import settings

meta = MetaData()

watchers = Table(
    "watchers", meta,
    Column("id", Integer, primary_key=True),
    Column("chat_id", String(32), unique=True, nullable=False),
    Column("name", String(120)),
    Column("created_at", DateTime(timezone=True), nullable=False),
)

follows = Table(
    "follows", meta,
    Column("id", Integer, primary_key=True),
    Column("token", String(40), unique=True, nullable=False),   # el del link de Telegram
    Column("watcher_id", Integer, ForeignKey("watchers.id"), index=True),  # vacío hasta que toca Iniciar
    Column("model", String(120), nullable=False),
    Column("model_key", String(120), nullable=False, index=True),
    Column("category", String(40), nullable=False),
    Column("product", String(120)),                 # "Mouse ergonómico vertical" (del perfil)
    Column("start_price", Float, nullable=False),   # mejor precio cuando empezó a seguirlo
    Column("start_store", String(80)),
    Column("start_url", Text),
    Column("active", Boolean, nullable=False, default=True),
    Column("last_alert_price", Float),
    Column("last_alert_at", DateTime(timezone=True)),
    Column("created_at", DateTime(timezone=True), nullable=False),
)

prices = Table(
    "prices", meta,
    Column("id", Integer, primary_key=True),
    Column("model_key", String(120), nullable=False, index=True),
    Column("ts", DateTime(timezone=True), nullable=False, index=True),
    Column("store", String(40), nullable=False),
    Column("store_name", String(80)),
    Column("price", Float, nullable=False),
    Column("list_price", Float),
    Column("title", Text),
    Column("url", Text),
    Column("extra", JSON),
)


comparaya_slugs = Table(
    "comparaya_slugs", meta,   # dirección de la ficha de ComparaYa de cada modelo (o vacía si no existe)
    Column("model_key", String(120), primary_key=True),
    Column("slug", String(200)),
    Column("checked_at", DateTime(timezone=True), nullable=False),
)


ai_usage = Table(
    "ai_usage", meta,   # una fila por consulta a la IA: tokens reales y estado (core/usage.py)
    Column("id", Integer, primary_key=True),
    Column("ts", DateTime(timezone=True), nullable=False, index=True),
    Column("provider", String(20), nullable=False),
    Column("model", String(80), nullable=False),
    Column("task", String(20), nullable=False),
    Column("prompt", Integer, nullable=False, default=0),
    Column("output", Integer, nullable=False, default=0),
    Column("thoughts", Integer, default=0),
    Column("status", String(12), nullable=False),
    Column("ms", Integer, default=0),
)

model_specs = Table(
    "model_specs", meta,   # ficha del fabricante por modelo (core/specs.py), con fuentes
    Column("model_key", String(120), primary_key=True),
    Column("data", JSON),
    Column("checked_at", DateTime(timezone=True), nullable=False),
)


def now() -> datetime:
    return datetime.now(timezone.utc)


def database_url() -> str:
    url = (os.getenv("DATABASE_URL") or "").strip()
    if not url:
        (settings.ROOT / "data").mkdir(exist_ok=True)
        return f"sqlite:///{settings.ROOT / 'data' / 'compra.db'}"
    # Neon da "postgresql://..." (o "postgres://"); SQLAlchemy necesita saber el driver
    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix):]
    return url


def is_configured() -> bool:
    """¿Hay base para guardar seguimientos? En la versión publicada tiene que ser Postgres."""
    return bool(os.getenv("DATABASE_URL")) or not os.getenv("RENDER")


_engine: Engine | None = None


def engine() -> Engine:
    global _engine
    if _engine is None:
        url = database_url()
        kw = {"pool_pre_ping": True} if url.startswith("postgresql") else {}
        _engine = create_engine(url, **kw)
        meta.create_all(_engine)
    return _engine


def set_engine(e: Engine | None) -> None:
    """Para tests: una base en memoria."""
    global _engine
    _engine = e
    if e is not None:
        meta.create_all(e)
