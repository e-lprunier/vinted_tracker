"""Client pour l'API interne (non officielle) de Vinted — version « svc-catalogue » (sept. 2026).

Historique : en septembre 2026, Vinted a retiré `www.vinted.fr/api/v2/catalog/items`
(qui répond 404 pour tout le monde). La recherche vit désormais sur un hôte dédié :

    GET https://api.vinted.fr/svc-catalogue/items
    Authorization: Bearer <cookie access_token_web>     (session anonyme, aucun compte requis)
    X-Anon-Id: <en-tête X-Anon-Id / cookie anon_id>     (envoyé si disponible)
    Locale / Accept-Language: fr-FR                     (sinon réponse en anglais)

La session anonyme s'obtient en visitant d'abord la page d'accueil www (curl_cffi
imite l'empreinte TLS de Chrome pour passer l'anti-bot). Dans la réponse, la marque,
la taille et l'état sont regroupés dans `item_box` (first_line / second_line) et les
URL sont relatives. La description n'est plus accessible en JSON : on la lit dans les
balises OpenGraph de la page de l'annonce.

⚠️ API non publique : elle peut encore changer. L'automatisation n'est pas prévue par
les conditions de Vinted : reste sur des volumes et des délais raisonnables.
"""
from __future__ import annotations

import html as html_lib
import json
import random
import re
import time
import unicodedata
from typing import Iterator

from ..config import Search
from ..models import Listing

try:
    from curl_cffi import requests as cf_requests
except ImportError:  # pragma: no cover
    cf_requests = None

SEARCH_PATH = "/svc-catalogue/items"

CONDITIONS = ["Neuf avec étiquette", "Neuf sans étiquette", "Très bon état", "Bon état", "Satisfaisant"]
_COND_EN = {"new with tags": "Neuf avec étiquette", "new without tags": "Neuf sans étiquette",
            "very good": "Très bon état", "good": "Bon état", "satisfactory": "Satisfaisant"}


class VintedBlocked(RuntimeError):
    """Vinted refuse la connexion (anti-bot, 403/429)."""


class VintedAPIError(RuntimeError):
    """Réponse inattendue de l'API (endpoint changé, format changé…)."""


# --------------------------------------------------------------------------- #
# Parsing tolérant (l'API change souvent de forme)
# --------------------------------------------------------------------------- #
def _strip(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "")
    return "".join(c for c in s if not unicodedata.combining(c)).lower().strip()


def _price(raw) -> float:
    if isinstance(raw, dict):
        raw = raw.get("amount", raw.get("value"))
    if raw is None:
        return 0.0
    txt = re.sub(r"[^\d,.\-]", "", str(raw)).replace(",", ".")
    if txt.count(".") > 1:                      # "1.234.56" -> "1234.56"
        head, _, tail = txt.rpartition(".")
        txt = head.replace(".", "") + "." + tail
    try:
        return float(txt)
    except ValueError:
        return 0.0


def _match_condition(part: str) -> str | None:
    p = _strip(part)
    for c in CONDITIONS:
        if _strip(c) == p:
            return c
    for en, fr in _COND_EN.items():
        if p == en:
            return fr
    return None


def split_second_line(line: str) -> tuple[str, str]:
    """« M · Très bon état » -> ("M", "Très bon état"), dans n'importe quel ordre."""
    size, cond = "", ""
    for part in re.split(r"\s*[·•|]\s*", line or ""):
        if not part:
            continue
        c = _match_condition(part)
        if c and not cond:
            cond = c
        elif not size:
            size = part.strip()
    return size, cond


def parse_catalog_item(raw: dict, search: Search, domain: str) -> Listing:
    base = f"https://{domain}"
    box = raw.get("item_box") or {}
    title = raw.get("title") or ""

    brand = raw.get("brand_title") or ""
    first = (box.get("first_line") or "").strip()
    if not brand and first and _strip(first) != _strip(title):
        brand = first
    size, cond = split_second_line(box.get("second_line") or "")
    size = raw.get("size_title") or size
    status = raw.get("status")
    if isinstance(status, dict):
        status = status.get("title")
    cond = (_match_condition(str(status)) if status else None) or cond

    url = raw.get("url") or raw.get("path") or f"/items/{raw.get('id')}"
    if url.startswith("/"):
        url = base + url

    photo = raw.get("photo") or (raw.get("photos") or [None])[0] or {}
    if isinstance(photo, str):
        photo_url = photo
    else:
        thumbs = photo.get("thumbnails") or []
        photo_url = photo.get("url") or photo.get("full_size_url") or (thumbs[0].get("url") if thumbs else "")

    price_raw = raw.get("price")
    currency = price_raw.get("currency_code", "EUR") if isinstance(price_raw, dict) else raw.get("currency", "EUR")
    user = raw.get("user") or {}
    return Listing(
        id=str(raw.get("id")),
        title=title,
        price=_price(price_raw if price_raw is not None else raw.get("price_numeric")),
        currency=currency or "EUR",
        search_name=search.name,
        category=search.category,
        brand=brand,
        size=size or "",
        condition=cond or "",
        url=url,
        photo_url=photo_url or "",
        seller_login=user.get("login", "") if isinstance(user, dict) else "",
        seller_id=str(user.get("id") or "") if isinstance(user, dict) else "",
        favourites=int(raw.get("favourite_count") or raw.get("favourites_count") or 0),
        views=int(raw.get("view_count") or 0),
        source="vinted_api",
    )


def extract_description(page_html: str) -> str:
    """Description depuis la page de l'annonce : JSON-LD, puis OpenGraph, puis meta description."""
    for block in re.findall(r'<script[^>]+application/ld\+json[^>]*>(.*?)</script>', page_html, re.S):
        try:
            obj = json.loads(block)
        except json.JSONDecodeError:
            continue
        for o in (obj if isinstance(obj, list) else [obj]):
            if isinstance(o, dict) and o.get("description"):
                return html_lib.unescape(o["description"])
    for pat in (r'<meta[^>]+property="og:description"[^>]+content="([^"]*)"',
                r'<meta[^>]+content="([^"]*)"[^>]+property="og:description"',
                r'<meta[^>]+name="description"[^>]+content="([^"]*)"'):
        m = re.search(pat, page_html)
        if m:
            return html_lib.unescape(m.group(1))
    return ""


def extract_seller_id(page_html: str) -> str:
    """Identifiant du vendeur depuis la page de l'annonce (lien /member/<id>-<login>)."""
    m = re.search(r'"user_id"\s*:\s*(\d+)', page_html) or re.search(r'/member/(\d+)', page_html)
    return m.group(1) if m else ""


def parse_seller(user: dict) -> dict:
    """Profil Vinted -> note /5, nombre d'avis, articles vendus.

    `feedback_reputation` est entre 0 et 1 (×5 = étoiles affichées sur le profil).
    """
    rep = user.get("feedback_reputation")
    count = user.get("feedback_count")
    if count is None:
        parts = [user.get(k) for k in ("positive_feedback_count", "neutral_feedback_count", "negative_feedback_count")]
        count = sum(int(x) for x in parts if x is not None) if any(x is not None for x in parts) else None
    rating = round(float(rep) * 5, 2) if rep is not None else None
    if rating is not None and count == 0:
        rating = None                      # pas d'avis = pas de note (Vinted renvoie 0)
    sold = user.get("given_item_count", user.get("sold_items_count"))
    return {"rating": rating, "count": int(count) if count is not None else None,
            "sold": int(sold) if sold is not None else None, "login": user.get("login", "")}


# --------------------------------------------------------------------------- #
# Client
# --------------------------------------------------------------------------- #
class VintedClient:
    def __init__(self, domain: str = "www.vinted.fr", min_delay: float = 1.5, max_delay: float = 3.5,
                 impersonate: str = "chrome", locale: str = "fr-FR"):
        if cf_requests is None:
            raise ImportError("pip install curl_cffi")
        self.domain = domain if domain.startswith("www.") else "www." + domain
        self.www = f"https://{self.domain}"
        self.api = f"https://api.{self.domain[4:]}"
        self.locale = locale
        self.min_delay, self.max_delay = min_delay, max_delay
        self.session = cf_requests.Session(impersonate=impersonate)
        self.token: str | None = None
        self.anon_id: str | None = None

    def _sleep(self):
        time.sleep(random.uniform(self.min_delay, self.max_delay))

    def _bootstrap(self):
        """Session anonyme : page d'accueil -> cookie access_token_web + identifiant anonyme."""
        r = self.session.get(self.www + "/", timeout=30, headers={"Accept-Language": f"{self.locale},fr;q=0.9"})
        if r.status_code in (403, 429):
            raise VintedBlocked(f"page d'accueil refusée ({r.status_code}) — IP probablement filtrée par l'anti-bot")
        tokens = [c.value for c in self.session.cookies.jar if c.name == "access_token_web" and c.value]
        self.token = tokens[-1] if tokens else None
        self.anon_id = (r.headers.get("x-anon-id")
                        or next((c.value for c in self.session.cookies.jar if c.name == "anon_id" and c.value), None))
        if not self.token:
            # certaines versions ne posent le cookie que sur /catalog
            r2 = self.session.get(self.www + "/catalog", timeout=30)
            tokens = [c.value for c in self.session.cookies.jar if c.name == "access_token_web" and c.value]
            self.token = tokens[-1] if tokens else None
            self.anon_id = self.anon_id or r2.headers.get("x-anon-id")

    def _api_headers(self) -> dict:
        h = {"Accept": "application/json, text/plain, */*",
             "Accept-Language": f"{self.locale},fr;q=0.9",
             "Locale": self.locale,
             "Origin": self.www,
             "Referer": self.www + "/catalog"}
        if self.token:
            h["Authorization"] = f"Bearer {self.token}"
        if self.anon_id:
            h["X-Anon-Id"] = self.anon_id
        return h

    def _get_api(self, path: str, params: dict, attempt: int = 0) -> dict:
        if self.token is None and attempt == 0:
            self._bootstrap()
        self._sleep()
        params = {k: v for k, v in params.items() if v not in (None, "")}   # 400 sur paramètres vides
        r = self.session.get(self.api + path, params=params, timeout=30, headers=self._api_headers())
        if r.status_code in (401, 403) and attempt < 2:    # token expiré -> nouvelle session
            self.session.cookies.clear()
            self._bootstrap()
            return self._get_api(path, params, attempt + 1)
        if r.status_code == 400 and "order" in params:      # paramètre refusé -> on le retire
            params.pop("order")
            return self._get_api(path, params, attempt + 1)
        if r.status_code in (403, 429):
            raise VintedBlocked(f"{r.status_code} sur {path} (anti-bot / trop de requêtes)")
        if r.status_code != 200:
            raise VintedAPIError(f"HTTP {r.status_code} sur {self.api}{path} — {r.text[:160]!r}")
        try:
            return r.json()
        except Exception as e:
            raise VintedAPIError(f"réponse non-JSON sur {path} : {r.text[:160]!r}") from e

    def search(self, search: Search, pages: int = 1, per_page: int = 96,
               order: str = "newest_first") -> Iterator[Listing]:
        seen: set[str] = set()
        for page in range(1, pages + 1):
            data = self._get_api(SEARCH_PATH, {
                "search_text": search.query, "page": page, "per_page": per_page,
                "order": order, "price_to": search.price_max or None,
            })
            items = data.get("items")
            if items is None:
                items = (data.get("data") or {}).get("items", []) if isinstance(data.get("data"), dict) else []
            new = 0
            for raw in items:
                if not isinstance(raw, dict) or str(raw.get("id")) in seen:
                    continue
                seen.add(str(raw.get("id")))
                new += 1
                yield parse_catalog_item(raw, search, self.domain)
            if new == 0 or len(items) < per_page:
                break

    def fetch_seller(self, user_id: str) -> dict | None:
        """Note et nombre d'avis du vendeur (endpoint profil, resté sur l'hôte www)."""
        if not user_id:
            return None
        try:
            self._sleep()
            r = self.session.get(f"{self.www}/api/v2/users/{user_id}", timeout=30, headers={
                **self._api_headers(), "Referer": f"{self.www}/member/{user_id}"})
            if r.status_code in (403, 429):
                raise VintedBlocked(f"{r.status_code} sur un profil vendeur")
            if r.status_code != 200:
                return None
            user = r.json().get("user") or {}
            return parse_seller(user) if user else None
        except VintedBlocked:
            raise
        except Exception:
            return None

    def fetch_description(self, listing: Listing) -> str:
        """La recherche ne renvoie pas la description : on la lit sur la page de l'annonce."""
        try:
            self._sleep()
            r = self.session.get(listing.url, timeout=30, headers={"Accept-Language": f"{self.locale},fr;q=0.9"})
            if r.status_code in (403, 429):
                raise VintedBlocked(f"{r.status_code} sur la page d'une annonce")
            if r.status_code != 200:
                return ""
            if not listing.seller_id:
                listing.seller_id = extract_seller_id(r.text)
            return extract_description(r.text)
        except VintedBlocked:
            raise
        except Exception:
            return ""
