"""Client pour l'API interne (non officielle) de Vinted.

⚠️ À savoir :
- Ce n'est pas une API publique : les endpoints peuvent changer sans préavis.
- Vinted est protégé par un anti-bot (Datadome / Cloudflare). Une requête
  `requests` classique prend un 403 ; on utilise `curl_cffi`, qui imite
  l'empreinte TLS d'un vrai Chrome, et on récupère d'abord les cookies de
  session sur la page d'accueil.
- Ça marche en général depuis une connexion résidentielle (ton PC). Depuis
  un serveur cloud (Streamlit Cloud, GitHub Actions), les IP de datacenter
  sont souvent bloquées : utilise alors la source Apify (sources/apify_source.py).
- Reste raisonnable sur la fréquence (délais aléatoires, peu de pages) :
  l'automatisation n'est pas prévue par les conditions de Vinted.
"""
from __future__ import annotations

import json
import random
import re
import time
from typing import Iterator

from ..config import Search
from ..models import Listing

try:
    from curl_cffi import requests as cf_requests
except ImportError:  # pragma: no cover
    cf_requests = None


class VintedBlocked(RuntimeError):
    pass


def _price(raw) -> float:
    if isinstance(raw, dict):
        raw = raw.get("amount")
    try:
        return float(str(raw).replace(",", "."))
    except (TypeError, ValueError):
        return 0.0


def parse_catalog_item(raw: dict, search: Search, domain: str) -> Listing:
    photo = raw.get("photo") or {}
    user = raw.get("user") or {}
    url = raw.get("url") or f"https://{domain}/items/{raw.get('id')}"
    if url.startswith("/"):
        url = f"https://{domain}{url}"
    return Listing(
        id=str(raw.get("id")),
        title=raw.get("title", ""),
        price=_price(raw.get("price")),
        currency=(raw.get("price") or {}).get("currency_code", "EUR") if isinstance(raw.get("price"), dict)
        else raw.get("currency", "EUR"),
        search_name=search.name,
        category=search.category,
        brand=raw.get("brand_title", "") or "",
        size=raw.get("size_title", "") or "",
        condition=raw.get("status", "") or "",
        url=url,
        photo_url=photo.get("url") or photo.get("full_size_url") or "",
        seller_login=user.get("login", "") or "",
        favourites=int(raw.get("favourite_count") or 0),
        views=int(raw.get("view_count") or 0),
        source="vinted_api",
    )


class VintedClient:
    def __init__(self, domain: str = "www.vinted.fr", min_delay: float = 1.5, max_delay: float = 3.5,
                 impersonate: str = "chrome"):
        if cf_requests is None:
            raise ImportError("pip install curl_cffi")
        self.domain = domain
        self.base = f"https://{domain}"
        self.min_delay, self.max_delay = min_delay, max_delay
        self.session = cf_requests.Session(impersonate=impersonate)
        self._ready = False

    # ------------------------------------------------------------------ #
    def _sleep(self):
        time.sleep(random.uniform(self.min_delay, self.max_delay))

    def _bootstrap(self):
        r = self.session.get(self.base + "/", timeout=30)
        if r.status_code in (403, 429):
            raise VintedBlocked(
                f"Vinted a refusé la connexion ({r.status_code}). Tu es probablement sur une IP de "
                "datacenter ou trop de requêtes ont été faites : réessaie plus tard depuis ta connexion "
                "perso, ou utilise la source Apify.")
        self._ready = True

    def _get_json(self, path: str, params: dict | None = None, retry: bool = True) -> dict:
        if not self._ready:
            self._bootstrap()
        self._sleep()
        r = self.session.get(self.base + path, params=params, timeout=30, headers={
            "Accept": "application/json, text/plain, */*",
            "Referer": self.base + "/catalog",
        })
        if r.status_code == 401 and retry:      # cookie expiré -> on renouvelle
            self._ready = False
            return self._get_json(path, params, retry=False)
        if r.status_code in (403, 429):
            raise VintedBlocked(f"Bloqué par Vinted ({r.status_code}) sur {path}")
        r.raise_for_status()
        return r.json()

    # ------------------------------------------------------------------ #
    def search(self, search: Search, pages: int = 2, per_page: int = 96,
               order: str = "newest_first") -> Iterator[Listing]:
        for page in range(1, pages + 1):
            params = {"search_text": search.query, "page": page, "per_page": per_page, "order": order}
            if search.price_max:
                params["price_to"] = search.price_max
            data = self._get_json("/api/v2/catalog/items", params)
            items = data.get("items", [])
            for raw in items:
                yield parse_catalog_item(raw, search, self.domain)
            if len(items) < per_page:
                break

    def fetch_description(self, listing: Listing) -> str:
        """La recherche catalogue ne renvoie pas la description : on la récupère à part."""
        try:
            data = self._get_json(f"/api/v2/items/{listing.id}")
            item = data.get("item") or {}
            if item.get("description"):
                fb = (item.get("user") or {}).get("feedback_count")
                if fb is not None:
                    listing.seller_feedback_count = int(fb)
                return item["description"]
        except VintedBlocked:
            raise
        except Exception:
            pass
        # Repli : page HTML de l'annonce (bloc JSON-LD ou meta description)
        try:
            self._sleep()
            html = self.session.get(listing.url, timeout=30).text
            for block in re.findall(r'<script type="application/ld\+json"[^>]*>(.*?)</script>', html, re.S):
                try:
                    obj = json.loads(block)
                except json.JSONDecodeError:
                    continue
                if isinstance(obj, dict) and obj.get("description"):
                    return obj["description"]
            m = re.search(r'<meta name="description" content="([^"]*)"', html)
            if m:
                return m.group(1)
        except Exception:
            pass
        return ""
