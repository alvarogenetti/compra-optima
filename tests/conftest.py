"""Aislamiento común de los tests: cada uno arranca con un registro de uso de IA y una base en memoria
nuevos. Sin esto el registro (global, de core/usage.py) acumula consultas de un test al siguiente y los
tests escribirían en la base local data/compra.db."""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from core import db, usage


@pytest.fixture(autouse=True)
def aislado(monkeypatch):
    db.set_engine(create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}))
    monkeypatch.setattr(usage, "ledger", usage.Ledger())
    from core import candidates
    monkeypatch.setattr(candidates, "_eval_cache", {})      # la caché de evaluaciones también es global
    yield
    db.set_engine(None)
