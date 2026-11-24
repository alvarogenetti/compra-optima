"""Registro de tiendas (config/stores.json): qué vende cada una y en qué plataforma corre."""
from __future__ import annotations

import json
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


@dataclass(frozen=True)
class Registry:
    categories: dict[str, dict] = field(default_factory=dict)
    stores: tuple[Store, ...] = ()

    def for_categories(self, cats: list[str]) -> list[Store]:
        wanted = set(cats or [])
        return [s for s in self.stores if wanted & set(s.categories)]

    def category_prompt(self) -> str:
        """Lista de categorías para que la IA elija una (con ejemplos)."""
        return "\n".join(f"- {k}: {v['label']} ({v['examples']})" for k, v in self.categories.items())


@lru_cache(maxsize=1)
def load() -> Registry:
    data = json.loads((settings.CONFIG_DIR / "stores.json").read_text(encoding="utf-8"))
    stores = tuple(Store(id=s["id"], name=s["name"], platform=s["platform"], host=s["host"],
                         categories=tuple(s["categories"]), notes=s.get("notes", ""))
                   for s in data["stores"])
    return Registry(categories=data["categories"], stores=stores)
