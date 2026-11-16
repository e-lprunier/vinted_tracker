"""Analyse du texte des annonces : faux prix, arnaques, contrefaçons, lots...

Le principe : on ne fait PAS confiance au prix affiché tant que le texte ne
l'a pas confirmé. Chaque règle produit un `Flag` (code, libellé, poids,
extrait de texte). Les flags `hard=True` excluent l'annonce d'office ; les
autres alimentent un score de risque (voir scoring.py).

Toutes les regex travaillent sur un texte en minuscules SANS accents.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

from .models import Flag, Listing


# --------------------------------------------------------------------------- #
# Normalisation
# --------------------------------------------------------------------------- #
def normalize(text: str) -> str:
    """minuscules + suppression des accents + espaces compactés."""
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = text.lower().replace("’", "'").replace(" ", " ")
    return re.sub(r"\s+", " ", text).strip()


def _snippet(text: str, start: int, end: int, pad: int = 35) -> str:
    s, e = max(0, start - pad), min(len(text), end + pad)
    return ("…" if s > 0 else "") + text[s:e].strip() + ("…" if e < len(text) else "")


# --------------------------------------------------------------------------- #
# 1. Extraction des montants dans la description
# --------------------------------------------------------------------------- #
_NUM = r"(\d{1,5}(?:[.,]\d{1,2})?)"
# "100€", "100 €", "100 euros", "100e", "100 balles", "€100"
_AMOUNT_RE = re.compile(
    rf"(?<![\w.,]){_NUM}\s*(?:€|euros?\b|eur\b|balles\b|e\b(?!\s*(?:main|etage|edition|generation|gen\b|choix|age|siecle|partie|place|taille)))"
    rf"|€\s*{_NUM}"
)
# "prix : 80", "prix ferme 80", "prix reel 120"
_PRICE_WORD_RE = re.compile(
    rf"\bprix\s*(?:reel|final|ferme|de vente|demande|souhaite)?\s*[:=]?\s*{_NUM}\b(?!\s*(?:%|cm|kg|g\b|ml|ans|mois|x\b))"
)

_CTX_PATTERNS = {
    # le montant correspond au prix NEUF / d'achat initial (utile pour la décote)
    "retail": re.compile(
        r"achet|paye|prix neuf|prix d.achat|valeur|vaut|boutique|magasin|retail|prix public"
        r"|au lieu de|sur le site|coute|neuf a|neuve a|prix initial|prix de base|origine|prix d.origine"),
    # le montant concerne la livraison
    "shipping": re.compile(r"\bport\b|livraison|envoi|frais|expedition|colis|mondial relay"),
    # le montant est un prix de VENTE annoncé dans le texte
    "sell": re.compile(
        r"prix|vends|\bvend\b|cede|laisse|negociable|\bnego|ferme|\breel|\bvrai|final|minimum"
        r"|pas moins|le tout|offre|a partir|je veux|demande|je le mets|mis a"),
}


@dataclass
class Amount:
    value: float
    context: str     # retail | shipping | sell | unknown
    snippet: str


def _classify_context(text: str, start: int, end: int) -> str:
    """Le mot-clé le plus PROCHE du montant (avant lui, ou juste après) l'emporte."""
    before = text[max(0, start - 45):start]
    after = text[end:end + 12]
    best, best_dist = "unknown", 10**9
    for ctx, pat in _CTX_PATTERNS.items():
        for m in pat.finditer(before):
            dist = len(before) - m.end()
            if dist < best_dist:
                best, best_dist = ctx, dist
        m = pat.search(after)
        if m and m.start() + 3 < best_dist:   # léger handicap pour "après"
            best, best_dist = ctx, m.start() + 3
    return best


def extract_amounts(text_norm: str) -> list[Amount]:
    found: dict[int, Amount] = {}
    for regex in (_AMOUNT_RE, _PRICE_WORD_RE):
        for m in regex.finditer(text_norm):
            raw = next(g for g in m.groups() if g)
            try:
                value = float(raw.replace(",", "."))
            except ValueError:
                continue
            if value <= 0 or value > 20000:
                continue
            # clé = position du NOMBRE (évite de compter 2 fois "prix 90€")
            gi = next(i for i, g in enumerate(m.groups(), 1) if g)
            key = m.start(gi)
            if key in found:
                continue
            found[key] = Amount(
                value=value,
                context=_classify_context(text_norm, m.start(gi), m.end()),
                snippet=_snippet(text_norm, m.start(), m.end()),
            )
    return [found[k] for k in sorted(found)]


# --------------------------------------------------------------------------- #
# 2. Règles de détection
# --------------------------------------------------------------------------- #
_PLACEHOLDER_RE = re.compile(
    r"prix (?:en|dans la|dans) (?:description|desc)|voir (?:la )?desc\w* pour le prix|\bvrai prix"
    r"|prix reel|prix symbolique|prix fictif|prix d.appel|prix sur demande|faites? (?:moi )?(?:une |vos )?offres?"
    r"|(?:me )?contacte[rz]? (?:moi )?pour (?:le|un) prix|\bmp pour (?:le )?prix|prix a debattre")

_NOT_FOR_SALE_RE = re.compile(
    r"ne (?:pas|surtout pas) (?:acheter|commander|payer)|n.achetez pas|pas a vendre|\bdeja vendu|\bdeja reserve"
    r"|\breservee? (?:pour|a)\b|\bje (?:re)?cherche\b|\brecherche (?:une?|des|le|la|ce|cette|taille)\b"
    r"|echange uniquement|uniquement (?:en |de l.)?echange|que de l.echange|seulement (?:en )?echange"
    r"|\bindisponible|\bplus dispo")

_OFF_PLATFORM_RE = re.compile(
    r"whats ?app|telegram|\binsta(?:gram)?\b|\bsnap(?:chat)?\b|paypal|virement|\blydia\b|\bpaylib\b|\bwero\b"
    r"|western union|hors vinted|en dehors de vinted|sans passer par vinted|par (?:e-?)?mail|ecri[st]-? ?moi sur"
    r"|ajoute[sz]?-? ?moi|contacte[sz]?-? ?moi sur|\bwa\.me")
_PHONE_RE = re.compile(r"(?:\+33\s?|\b0)[67](?:[\s.-]?\d{2}){4}\b")
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[a-z]{2,}")
_URL_RE = re.compile(r"https?://|www\.|bit\.ly|\.(?:com|fr|net|shop)/")

_COUNTERFEIT_STRONG_RE = re.compile(
    r"\breplica|\breplique|\breps?\b|\bfake\b|\b1[:/]1\b|contrefacon|\bcopie\b|imitation|\bdupe\b"
    r"|non authentique|pas authentique|pas (?:un )?original|non original|\baaa\+?\b|qualite (?:miroir|1:1|top)")
_NEGATION_BEFORE = re.compile(r"(?:pas de|pas un|pas une|aucun|aucune|no|non|zero|0)\s*$")

_NOT_THE_ITEM_RE = re.compile(
    r"boite (?:vide|seule|uniquement)|(?:juste|seulement|que) (?:la|le) (?:boite|photo|dust ?bag|pochette|facture|notice)"
    r"|\bbox only\b|empty box|photo (?:uniquement|seulement)|etiquettes? seules?|dust ?bag seul|pochette (?:vide|seule)"
    r"|facture seule|lacets seuls|semelles? seules?|notice seule|pour pieces|\bhors service\b|\bhs\b"
    r"|ne fonctionne (?:pas|plus)|en panne|carte cadeau")

_LOT_TITLE_RE = re.compile(r"\blots?\b|\bx\s?[2-9]\b|\b[2-9]\d?\s?(?:pieces|articles|paires|bijoux)\b|\bbundle\b")
_LOT_DESC_RE = re.compile(r"\blot de \d+|vendus? ensemble|\ble tout pour\b|\bles \d+ pour\b")
_KIDS_RE = re.compile(r"\benfants?\b|\bbebe\b|\bkids?\b|\bjunior\b|\bgs\b|\btd\b|\b(?:2|4|6|8|10|12|14) ans\b")
_DEFECT_RE = re.compile(
    r"\btaches?\b|\btrous?\b|dechir|abime|\busee?s?\b|defauts?\b|decoll|\bcasse|\bmanque|incomplet|jauni|bouloch|craquel")
_HAND_ONLY_RE = re.compile(r"main propre (?:uniquement|seulement)|uniquement (?:en )?main propre|pas d.envoi")

_GENERIC_TOKENS = {"veste", "appareil", "low", "the"}


@dataclass
class TextAnalysis:
    flags: list[Flag] = field(default_factory=list)
    amounts: list[Amount] = field(default_factory=list)
    retail_price_mentioned: float | None = None   # "acheté 150€" -> 150
    real_price_mentioned: float | None = None     # "prix 100€" alors qu'affiché 1€ -> 100
    is_lot: bool = False
    is_off_topic: bool = False

    @property
    def hard_excluded(self) -> bool:
        return any(f.hard for f in self.flags)

    def add(self, code, label, weight, evidence="", hard=False):
        if not any(f.code == code for f in self.flags):
            self.flags.append(Flag(code, label, weight, evidence, hard))


def _first(regex: re.Pattern, text: str) -> re.Match | None:
    return regex.search(text)


def analyze_listing(listing: Listing, search_query: str = "",
                    brand_tokens: list[str] | None = None) -> TextAnalysis:
    """Analyse titre + description d'une annonce et renvoie les signaux détectés."""
    title = normalize(listing.title)
    desc = normalize(listing.description)
    full = f"{title} . {desc}"
    price = listing.price
    res = TextAnalysis()

    # --- Montants & faux prix ---------------------------------------------
    res.amounts = extract_amounts(full)
    retail = [a.value for a in res.amounts if a.context == "retail"]
    if retail:
        res.retail_price_mentioned = max(retail)

    candidates = [a for a in res.amounts
                  if a.context in ("sell", "unknown") and abs(a.value - price) > 0.5]
    threshold = max(2 * price, price + 10)
    big = [a for a in candidates if a.value >= threshold]
    if big:
        top = max(big, key=lambda a: a.value)
        res.real_price_mentioned = top.value
        if top.context == "sell":
            res.add("fake_price", f"Faux prix : {price:.0f} € affiché, {top.value:.0f} € dans le texte",
                    70, top.snippet, hard=True)
        elif price <= 5:
            res.add("fake_price", f"Prix d'appel : {price:.0f} € affiché, {top.value:.0f} € mentionné",
                    60, top.snippet, hard=True)
        else:
            res.add("price_mismatch", f"Montant incohérent dans le texte ({top.value:.0f} €)",
                    25, top.snippet)

    m = _first(_PLACEHOLDER_RE, full)
    if m:
        hard = price <= 5
        res.add("placeholder_price", "Prix non réel (« prix en description », « faites une offre »…)",
                60 if hard else 30, _snippet(full, m.start(), m.end()), hard=hard)

    # --- Annonce qui n'est pas une vraie vente --------------------------------
    m = _first(_NOT_FOR_SALE_RE, full)
    if m:
        res.add("not_for_sale", "Pas une vente (réservé, recherche, échange, « ne pas acheter »)",
                80, _snippet(full, m.start(), m.end()), hard=True)

    # --- Arnaque : sortir de la plateforme ------------------------------------
    for regex, what in ((_OFF_PLATFORM_RE, "paiement/contact hors Vinted"),
                        (_PHONE_RE, "numéro de téléphone"),
                        (_EMAIL_RE, "adresse e-mail"),
                        (_URL_RE, "lien externe")):
        m = _first(regex, full)
        if m:
            res.add("off_platform", f"Arnaque probable : {what}", 85,
                    _snippet(full, m.start(), m.end()), hard=True)
            break

    # --- Contrefaçon ----------------------------------------------------------
    for m in _COUNTERFEIT_STRONG_RE.finditer(full):
        before = full[max(0, m.start() - 14):m.start()]
        term = m.group(0)
        negatable = term.startswith(("fake", "copie", "replica", "replique", "rep", "contrefacon", "imitation", "dupe"))
        if negatable and _NEGATION_BEFORE.search(before):
            continue  # "pas de fake", "aucune copie" = affirmation d'authenticité
        res.add("counterfeit", "Contrefaçon déclarée (replica, copie, 1:1…)", 75,
                _snippet(full, m.start(), m.end()), hard=True)
        break

    if brand_tokens:
        brands = "|".join(re.escape(normalize(b)) for b in brand_tokens if len(b) > 2)
        if brands:
            weak = re.search(rf"\b(?:facon|type|style|inspire[e]?s?(?: de| par)?|esprit|similaire a)\s+(?:{brands})\b", full)
            if weak:
                res.add("counterfeit_weak", "Possible imitation (« style / façon / type … »)", 35,
                        _snippet(full, weak.start(), weak.end()))

    # --- Pas l'article lui-même ----------------------------------------------
    m = _first(_NOT_THE_ITEM_RE, full)
    if m:
        res.add("not_the_item", "Pas l'article complet (boîte seule, photo, pour pièces…)", 70,
                _snippet(full, m.start(), m.end()), hard=True)

    # --- Lot, enfant, hors-sujet : faussent la comparaison de prix ------------
    m = _first(_LOT_TITLE_RE, title) or _first(_LOT_DESC_RE, desc)
    if m:
        res.is_lot = True
        res.add("lot", "Lot : prix unitaire non comparable", 0, m.group(0))

    if _first(_KIDS_RE, title):
        res.is_off_topic = True
        res.add("kids", "Taille enfant : marché différent", 0, title)

    if search_query:
        q_tokens = [t for t in re.sub(r"['\-.]", "", normalize(search_query)).split()
                    if len(t) >= 3 and t not in _GENERIC_TOKENS]
        haystack = re.sub(r"['\-.]", "", normalize(f"{listing.title} {listing.brand}"))
        if q_tokens:
            hit = sum(1 for t in q_tokens if t in haystack) / len(q_tokens)
            if hit < 0.66:   # au moins ~2/3 des mots-clés de la recherche
                res.is_off_topic = True
                res.add("off_topic", "Ne correspond pas à la recherche", 0, listing.title)

    # --- Signaux faibles -------------------------------------------------------
    m = next((d for d in _DEFECT_RE.finditer(full)
              if not re.search(r"(?:aucun|aucune|sans|pas de|zero|ni)\s*$", full[max(0, d.start() - 10):d.start()])), None)
    if m:
        res.add("defects", "Défauts mentionnés", 10, _snippet(full, m.start(), m.end()))
    m = _first(_HAND_ONLY_RE, full)
    if m:
        res.add("hand_only", "Main propre uniquement (pas de protection acheteur)", 15,
                _snippet(full, m.start(), m.end()))
    if len(desc) < 15:
        res.add("empty_description", "Description vide ou quasi vide", 5)

    return res
