"""Registro de tiendas (config/stores.json): qué vende cada una y en qué plataforma corre."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from functools import lru_cache

from core import settings


@dataclass(frozen=True)
class Store:
    id: str
    name: str
    platform: str
    host: str
    categories: tuple[str, ...]
    notes: str = ""
    cloud_blocked: bool = False


@dataclass(frozen=True)
class Registry:
    categories: dict[str, dict] = field(default_factory=dict)
    stores: tuple[Store, ...] = ()

    def for_categories(self, cats: list[str]) -> list[Store]:
        """Tiendas que venden alguna de esas categorías. En la nube (Render, GitHub Actions) se saltean
        las que bloquean a los servidores: siempre responderían 403."""
        wanted = set(cats or [])
        cloud = running_in_cloud()
        return [s for s in self.stores if wanted & set(s.categories) and not (cloud and s.cloud_blocked)]

    def category_prompt(self) -> str:
        """Lista de categorías para que la IA elija una (con ejemplos)."""
        return "\n".join(f"- {k}: {v['label']} ({v['examples']})" for k, v in self.categories.items())


def running_in_cloud() -> bool:
    return bool(os.getenv("RENDER") or os.getenv("GITHUB_ACTIONS"))


@lru_cache(maxsize=1)
def load() -> Registry:
    data = json.loads((settings.CONFIG_DIR / "stores.json").read_text(encoding="utf-8"))
    stores = tuple(Store(id=s["id"], name=s["name"], platform=s["platform"], host=s["host"],
                         categories=tuple(s["categories"]), notes=s.get("notes", ""),
                         cloud_blocked=bool(s.get("cloud_blocked")))
                   for s in data["stores"])
    return Registry(categories=data["categories"], stores=stores)
