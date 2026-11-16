"""Orchestration : collecte -> descriptions -> analyse -> stockage.

Uniquement des annonces Vinted RÉELLES. Sources :
- "vinted" : API Vinted en direct (curl_cffi). Marche depuis une connexion perso.
- "apify"  : via Apify (APIFY_TOKEN). Marche depuis n'importe quel serveur.
- "auto"   : essaie Vinted en direct, bascule sur Apify si Vinted bloque.
"""
from __future__ import annotations

import logging
import os
from collections import defaultdict
from statistics import median
from typing import Callable

import pandas as pd

from .config import Config
from .llm_check import make_checker
from .models import Listing
from .scoring import analyze

log = logging.getLogger("vinted_tracker")


class ScanError(RuntimeError):
    pass


def _pick_for_details(listings: list[Listing], cfg: Config, top_n: int) -> list[Listing]:
    """Annonces dont on lit la description : les moins chères vs leur marché
    (c'est là que se cachent les bonnes affaires ET les faux prix)."""
    by_search: dict[str, list[Listing]] = defaultdict(list)
    for l in listings:
        by_search[l.search_name].append(l)
    picked: list[Listing] = []
    cf = cfg.condition_factors
    for group in by_search.values():
        norm = [(l.price / cf.get(l.condition, 1.0), l) for l in group if l.price > 0]
        if not norm:
            continue
        med = median(p for p, _ in norm)
        picked += [l for _, l in sorted(norm, key=lambda t: t[0] / med)[:top_n]]
    return picked


def _collect_vinted(cfg: Config, searches, progress) -> list[Listing]:
    from .sources.vinted_api import VintedClient
    v = cfg.vinted
    client = VintedClient(v["domain"], v["min_delay_s"], v["max_delay_s"])
    listings: list[Listing] = []
    total = len(searches) + 1
    for i, s in enumerate(searches):
        progress(f"Vinted : {s.name}", i / total)
        listings += list(client.search(s, v["pages_per_search"], v["per_page"], v["order"]))
    listings = list({l.id: l for l in listings}.values())  # dédoublonnage
    to_detail = _pick_for_details(listings, cfg, v["fetch_details_for_top"])
    ids = {l.id for l in to_detail}
    to_detail += [l for l in listings if l.price <= 5 and l.id not in ids]
    for j, l in enumerate(to_detail):
        progress(f"Lecture des descriptions ({j + 1}/{len(to_detail)})",
                 (len(searches) + j / max(len(to_detail), 1)) / total)
        l.description = client.fetch_description(l)
    return listings


def _collect_apify(cfg: Config, searches, progress) -> list[Listing]:
    from .sources.apify_source import fetch_apify
    ap = cfg.raw.get("apify", {})
    listings: list[Listing] = []
    for i, s in enumerate(searches):
        progress(f"Apify : {s.name}", i / len(searches))
        listings += list(fetch_apify(s, ap.get("max_items", 150), cfg.vinted["domain"],
                                     ap.get("actor_id"), ap.get("input_template")))
    return list({l.id: l for l in listings}.values())


def collect(cfg: Config, source: str = "auto", search_names: list[str] | None = None,
            progress: Callable[[str, float], None] | None = None) -> tuple[list[Listing], str]:
    """Renvoie (annonces, source réellement utilisée)."""
    searches = [s for s in cfg.searches if not search_names or s.name in search_names]
    progress = progress or (lambda msg, frac: log.info(msg))

    if source == "vinted":
        return _collect_vinted(cfg, searches, progress), "vinted"
    if source == "apify":
        return _collect_apify(cfg, searches, progress), "apify"
    if source == "auto":
        try:
            return _collect_vinted(cfg, searches, progress), "vinted"
        except Exception as e:  # blocage anti-bot, API modifiée, réseau…
            from .sources.vinted_api import VintedBlocked
            if os.environ.get("APIFY_TOKEN"):
                log.warning("Vinted indisponible (%s) -> bascule sur Apify", e)
                progress("Vinted indisponible depuis ce serveur, bascule sur Apify…", 0.0)
                return _collect_apify(cfg, searches, progress), "apify"
            if isinstance(e, VintedBlocked):
                msg = (f"Vinted bloque ce serveur ({e}). Lance le scan depuis ton PC "
                       "(python -m vinted_tracker watch --push) ou ajoute un APIFY_TOKEN dans les Secrets.")
            else:
                msg = (f"L'API Vinted a répondu de façon inattendue ({e}). Vinted a peut-être encore modifié "
                       "son API : le client (vinted_tracker/sources/vinted_api.py) est à mettre à jour.")
            raise ScanError(msg) from e
    raise ValueError(f"Source inconnue : {source}")


def run_scan(cfg: Config, source: str = "auto", search_names: list[str] | None = None,
             social_pct: float | None = None, progress=None) -> tuple[pd.DataFrame, str]:
    listings, used = collect(cfg, source, search_names, progress)
    if not listings:
        raise ScanError("Aucune annonce récupérée : vérifie la connexion ou les recherches de config.yaml.")
    checker = make_checker(cfg.llm.get("model", "claude-haiku-5-5")) if cfg.llm.get("enabled") else None
    return analyze(listings, cfg, social_pct=social_pct, llm_checker=checker), used
