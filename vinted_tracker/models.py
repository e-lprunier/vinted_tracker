"""Modèle de données commun à toutes les sources (API Vinted, Apify, démo)."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone


@dataclass
class Listing:
    id: str
    title: str
    price: float
    search_name: str
    category: str
    description: str = ""
    currency: str = "EUR"
    brand: str = ""
    size: str = ""
    condition: str = ""
    url: str = ""
    photo_url: str = ""
    seller_login: str = ""
    seller_feedback_count: int | None = None
    seller_rating: float | None = None
    favourites: int = 0
    views: int = 0
    source: str = "vinted"
    fetched_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds"))

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Flag:
    """Un signal de risque détecté dans une annonce."""
    code: str          # identifiant stable, ex. "fake_price"
    label: str         # libellé lisible en français
    weight: int        # contribution au score de risque (0-100)
    evidence: str = "" # extrait du texte qui a déclenché le signal
    hard: bool = False # True = exclusion directe, quel que soit le score

    def to_dict(self) -> dict:
        return asdict(self)
