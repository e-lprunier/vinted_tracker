"""Source alternative : un « actor » Apify qui scrape Vinted pour toi.

Utile quand l'API Vinted te bloque (IP de serveur, GitHub Actions, Streamlit
Cloud). Payant à l'usage (quelques centimes à ~2 $ les 1 000 annonces selon
l'actor). Chaque actor a son propre format d'entrée : configure `actor_id`
et `input_template` dans config.yaml (section apify) — `{query}`, `{max_items}`,
`{domain}` et `{price_max}` sont remplacés automatiquement. Le parsing de la
sortie est volontairement tolérant aux différents noms de champs.
"""
from __future__ import annotations

import json
import os
from typing import Any, Iterator

from ..config import Search
from ..models import Listing

DEFAULT_ACTOR = "fetch_cat/vinted-search-scraper"
DEFAULT_INPUT = {"searchText": "{query}", "maxItems": "{max_items}", "domain": "{domain}"}


def _get(d: dict, *keys, default=None):
    for k in keys:
        cur: Any = d
        for part in k.split("."):
            cur = cur.get(part) if isinstance(cur, dict) else None
            if cur is None:
                break
        if cur not in (None, ""):
            return cur
    return default


def _num(v) -> float:
    if isinstance(v, dict):
        v = v.get("amount")
    try:
        return float(str(v).replace(",", ".").replace("€", "").strip())
    except (TypeError, ValueError):
        return 0.0


def parse_generic(raw: dict, search: Search) -> Listing:
    photos = _get(raw, "photos", "images", default=[])
    first_photo = photos[0] if isinstance(photos, list) and photos else None
    if isinstance(first_photo, dict):
        first_photo = first_photo.get("url")
    return Listing(
        id=str(_get(raw, "id", "itemId", "url", default="")),
        title=_get(raw, "title", "name", default=""),
        price=_num(_get(raw, "price", "price.amount", "priceAmount", default=0)),
        search_name=search.name,
        category=search.category,
        description=_get(raw, "description", default="") or "",
        brand=_get(raw, "brand_title", "brand", "brandTitle", default="") or "",
        size=_get(raw, "size_title", "size", default="") or "",
        condition=_get(raw, "status", "condition", default="") or "",
        url=_get(raw, "url", "link", default="") or "",
        photo_url=_get(raw, "photo.url", "image", "photo_url", "photoUrl", default=first_photo or "") or "",
        seller_login=_get(raw, "user.login", "seller.login", "sellerName", default="") or "",
        seller_feedback_count=_get(raw, "user.feedback_count", "seller.feedbackCount", default=None),
        favourites=int(_num(_get(raw, "favourite_count", "favourites", "likes", default=0))),
        views=int(_num(_get(raw, "view_count", "views", default=0))),
        source="apify",
    )


def fetch_apify(search: Search, max_items: int, domain: str, actor_id: str | None = None,
                input_template: dict | None = None, token: str | None = None) -> Iterator[Listing]:
    from apify_client import ApifyClient  # import paresseux : dépendance optionnelle

    token = token or os.environ.get("APIFY_TOKEN")
    if not token:
        raise RuntimeError("APIFY_TOKEN manquant (variable d'environnement ou secret Streamlit).")
    template = json.dumps(input_template or DEFAULT_INPUT)
    filled = (template.replace("{query}", search.query)
              .replace('"{max_items}"', str(max_items))
              .replace("{domain}", domain)
              .replace('"{price_max}"', str(search.price_max or 0)))
    client = ApifyClient(token)
    run = client.actor(actor_id or DEFAULT_ACTOR).call(run_input=json.loads(filled))
    for raw in client.dataset(run["defaultDatasetId"]).iterate_items():
        yield parse_generic(raw, search)
