"""Chargement de la configuration (config.yaml) et des prix de référence."""
from __future__ import annotations

import csv
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = ROOT / "config.yaml"
DEFAULT_REFERENCE = ROOT / "data" / "reference_prices.csv"
DEFAULT_DB = ROOT / "data" / "vinted.db"


@dataclass
class Search:
    name: str
    query: str
    category: str
    size_class: str = "moyen"
    liquidity: int = 2
    counterfeit_risk: bool = False
    price_max: float | None = None


@dataclass
class Config:
    raw: dict[str, Any]
    searches: list[Search] = field(default_factory=list)
    reference_prices: dict[str, float] = field(default_factory=dict)

    # Accès pratiques -------------------------------------------------------
    @property
    def fees(self) -> dict[str, Any]:
        return self.raw["fees"]

    @property
    def resale(self) -> dict[str, Any]:
        return self.raw["resale"]

    @property
    def thresholds(self) -> dict[str, Any]:
        return self.raw["thresholds"]

    @property
    def condition_factors(self) -> dict[str, float]:
        return self.raw["condition_factors"]

    @property
    def vinted(self) -> dict[str, Any]:
        return self.raw["vinted"]

    @property
    def llm(self) -> dict[str, Any]:
        return self.raw.get("llm", {"enabled": False})

    def search_by_name(self, name: str) -> Search | None:
        return next((s for s in self.searches if s.name == name), None)


def load_reference_prices(path: Path = DEFAULT_REFERENCE) -> dict[str, float]:
    if not path.exists():
        return {}
    with path.open(encoding="utf-8") as f:
        return {
            row["search_name"]: float(row["prix_neuf"])
            for row in csv.DictReader(f)
            if row.get("prix_neuf")
        }


def load_config(path: Path | str = DEFAULT_CONFIG,
                reference_path: Path | str = DEFAULT_REFERENCE) -> Config:
    with Path(path).open(encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    searches = [Search(**s) for s in raw.get("searches", [])]
    return Config(raw=raw, searches=searches,
                  reference_prices=load_reference_prices(Path(reference_path)))
