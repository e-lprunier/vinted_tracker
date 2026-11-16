"""Orchestration : collecte -> descriptions -> analyse -> stockage."""
from __future__ import annotations

import logging
from collections import defaultdict
from statistics import median
from typing import Callable

import pandas as pd

from .config import Config
from .llm_check import make_checker
from .models import Listing
from .scoring import analyze

log = logging.getLogger("vinted_tracker")


def _pick_for_details(listings: list[Listing], cfg: Config, top_n: int) -> list[Listing]:
    """Choisit les annonces dont on va lire la description : les moins chères
    vs leur marché (c'est là que se cachent les bonnes affaires ET les faux prix)."""
    by_search: dict[str, list[Listing]] = defaultdict(list)
    for l in listings:
        by_search[l.search_name].append(l)
    picked: list[Listing] = []
    for name, group in by_search.items():
        cf = cfg.condition_factors
        norm = [(l.price / cf.get(l.condition, 1.0), l) for l in group if l.price > 0]
        if not norm:
            continue
        med = median(p for p, _ in norm)
        ranked = sorted(norm, key=lambda t: t[0] / med)
        picked += [l for _, l in ranked[:top_n]]
    return picked


def collect(cfg: Config, source: str = "demo", search_names: list[str] | None = None,
            progress: Callable[[str, float], None] | None = None) -> list[Listing]:
    searches = [s for s in cfg.searches if not search_names or s.name in search_names]
    progress = progress or (lambda msg, frac: log.info(msg))

    if source == "demo":
        from .sources.demo import generate_demo
        out = generate_demo(cfg)
        return [l for l in out if l.search_name in {s.name for s in searches}]

    listings: list[Listing] = []
    if source == "apify":
        from .sources.apify_source import fetch_apify
        ap = cfg.raw.get("apify", {})
        for i, s in enumerate(searches):
            progress(f"Apify : {s.name}", i / len(searches))
            listings += list(fetch_apify(s, ap.get("max_items", 150), cfg.vinted["domain"],
                                         ap.get("actor_id"), ap.get("input_template")))
        return listings

    if source == "vinted":
        from .sources.vinted_api import VintedClient
        v = cfg.vinted
        client = VintedClient(v["domain"], v["min_delay_s"], v["max_delay_s"])
        for i, s in enumerate(searches):
            progress(f"Recherche Vinted : {s.name}", i / (len(searches) + 1))
            listings += list(client.search(s, v["pages_per_search"], v["per_page"], v["order"]))
        # dédoublonnage (une annonce peut sortir dans 2 recherches)
        listings = list({l.id: l for l in listings}.values())
        to_detail = _pick_for_details(listings, cfg, v["fetch_details_for_top"])
        to_detail += [l for l in listings if l.price <= 5 and l not in to_detail]
        for j, l in enumerate(to_detail):
            progress(f"Lecture des descriptions ({j + 1}/{len(to_detail)})",
                     (len(searches) + j / len(to_detail)) / (len(searches) + 1))
            l.description = client.fetch_description(l)
        return listings

    raise ValueError(f"Source inconnue : {source}")


def run_scan(cfg: Config, source: str = "demo", search_names: list[str] | None = None,
             social_pct: float | None = None, progress=None) -> pd.DataFrame:
    listings = collect(cfg, source, search_names, progress)
    checker = make_checker(cfg.llm.get("model", "claude-haiku-5-5")) if cfg.llm.get("enabled") else None
    return analyze(listings, cfg, social_pct=social_pct, llm_checker=checker)
