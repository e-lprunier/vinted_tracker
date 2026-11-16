"""Comparables fins : même modèle, même couleur, même taille — avec repli automatique.

Pour chaque annonce, on cherche les annonces « identiques ou très proches » dans la
même recherche, du plus précis au plus large, et on garde le premier niveau qui
contient assez d'annonces pour être fiable :

    1. modèle + couleur + taille
    2. modèle + couleur
    3. modèle + taille
    4. modèle
    5. couleur
    6. recherche entière (ex. toutes les « Nike Dunk Low »)

Le « modèle » est déduit des mots du titre qui reviennent dans plusieurs annonces de
la même recherche (ex. « panda », « beta », « lt », « 75301 »), une fois retirés la
marque, les couleurs, les tailles, l'état et les mots génériques. L'annonce elle-même
n'est jamais dans ses propres comparables.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

import numpy as np
import pandas as pd

from .text_analysis import normalize

# --------------------------------------------------------------------------- #
# Couleurs (FR + EN) -> couleur canonique
# --------------------------------------------------------------------------- #
_COLOR_WORDS = {
    "noir": ["noir", "noire", "noirs", "noires", "black"],
    "blanc": ["blanc", "blanche", "blancs", "blanches", "white"],
    "gris": ["gris", "grise", "grises", "grey", "gray", "anthracite", "argent", "silver"],
    "bleu": ["bleu", "bleue", "bleus", "bleues", "blue", "marine", "navy", "ciel", "turquoise"],
    "rouge": ["rouge", "rouges", "red", "bordeaux", "burgundy"],
    "vert": ["vert", "verte", "verts", "vertes", "green", "sapin"],
    "kaki": ["kaki", "khaki", "olive"],
    "beige": ["beige", "creme", "cream", "ecru", "sable", "sand", "ivoire", "off-white", "offwhite"],
    "marron": ["marron", "brown", "chocolat", "camel", "cognac", "caramel", "tan"],
    "rose": ["rose", "roses", "pink"],
    "jaune": ["jaune", "jaunes", "yellow", "moutarde"],
    "orange": ["orange"],
    "violet": ["violet", "violette", "purple", "lilas", "lavande", "mauve"],
    "or": ["or", "dore", "doree", "gold"],
    "multicolore": ["multicolore", "multicolor", "multi"],
}
COLOR_OF = {w: canon for canon, words in _COLOR_WORDS.items() for w in words}

_STOP = set("""
de du des la le les un une et a au aux en pour avec sans sur par taille t tbe neuf neuve neufs etiquette
etiquettes jamais porte portee tres bon bonne etat excellent parfait comme homme femme mixte enfant
original originale authentique vrai vintage paire chaussure chaussures basket baskets sneakers sneaker
veste vestes pull pulls polo polos jean jeans pantalon robe blouse cardigan sweat hoodie doudoune manteau
tshirt t-shirt shirt chemise bracelet collier bague bijou appareil photo jacket coat size uk us eu fr
retro modele model edition collection nouveau nouvelle lot cm mm og wip nb af1 07 complet complete
fonctionnel fonctionne neuve marque
""".split())

_SIZE_TOKEN = re.compile(r"^(?:x{0,3}s|m|l|x{1,3}l|\d{1,2}xl|w\d{2}|l\d{2}|\d{2}(?:[.,]5)?|\d{2}/\d{2})$")


def _tokens(text: str) -> list[str]:
    t = re.sub(r"['’`]", "", normalize(text))
    return [w for w in re.split(r"[^a-z0-9]+", t) if w]


def extract_colors(title: str) -> tuple[str, ...]:
    """Couleurs canoniques citées dans le titre (2 max, triées) — ex. ('blanc', 'vert')."""
    found: list[str] = []
    for w in _tokens(title):
        c = COLOR_OF.get(w)
        if c and c not in found:
            found.append(c)
    return tuple(sorted(found[:2]))


def size_bucket(size: str, category: str) -> str:
    """Regroupe les tailles voisines (une 42 et une 43 se revendent pareil)."""
    s = normalize(size or "").replace(" ", "")
    if not s:
        return ""
    m = re.match(r"^w?(\d{2})", s)
    if category == "Sneakers" and m:
        n = int(m.group(1))
        return "≤38" if n <= 38 else "39-41" if n <= 41 else "42-44" if n <= 44 else "45+"
    if s.startswith("w") and m:                                  # jeans W30/L32
        n = int(m.group(1))
        return "W≤29" if n <= 29 else "W30-32" if n <= 32 else "W33+"
    letters = re.match(r"^(x{0,3}s|m|l|x{1,3}l|\dxl)", s)
    if letters:
        v = letters.group(1)
        return "XS-S" if v.endswith("s") else "M-L" if v in ("m", "l") else "XL+"
    if m:                                                        # tailles FR 34-46
        n = int(m.group(1))
        if 30 <= n <= 50:
            return "≤36" if n <= 36 else "38-40" if n <= 40 else "42+"
    return s


def candidate_model_tokens(title: str, brand: str, query: str) -> set[str]:
    drop = set(_tokens(query)) | set(_tokens(brand)) | _STOP | set(COLOR_OF)
    out = set()
    for w in _tokens(title):
        if w in drop or _SIZE_TOKEN.match(w) or len(w) < 2:
            continue
        out.add(w)
    return out


# --------------------------------------------------------------------------- #
# Sélection des comparables
# --------------------------------------------------------------------------- #
@dataclass
class CompStats:
    n: int = 0
    level: str = ""
    median: float = np.nan
    q: float = np.nan
    p25: float = np.nan
    p75: float = np.nan
    mad: float = np.nan
    percentile: float = np.nan
    fav_median: float = np.nan


LEVELS = [
    ("modèle + couleur + taille", True, True, True),
    ("modèle + couleur", True, True, False),
    ("modèle + taille", True, False, True),
    ("modèle", True, False, False),
    ("couleur", False, True, False),
]


def add_attributes(df: pd.DataFrame, queries: dict[str, str]) -> pd.DataFrame:
    """Ajoute colonnes color_key, size_bucket, model_tokens (modèle = mots fréquents de la recherche)."""
    df = df.copy()
    df["color_key"] = df["title"].map(lambda t: "/".join(extract_colors(t)))
    df["size_bucket"] = [size_bucket(s, c) for s, c in zip(df["size"], df["category"])]
    cand = [candidate_model_tokens(t, b, queries.get(n, ""))
            for t, b, n in zip(df["title"], df["brand"], df["search_name"])]
    df["_cand"] = cand
    models = []
    for name, idx in df.groupby("search_name").groups.items():
        sub = df.loc[idx, "_cand"]
        n = len(sub)
        freq: dict[str, int] = {}
        for toks in sub:
            for t in toks:
                freq[t] = freq.get(t, 0) + 1
        # un mot "de modèle" revient dans >= 3 annonces mais pas dans plus de 60 % d'entre elles
        vocab = {t for t, k in freq.items() if k >= 3 and k <= max(3, 0.6 * n)}
        for i in idx:
            models.append((i, frozenset(df.at[i, "_cand"] & vocab)))
    model_map = dict(models)
    df["model_tokens"] = [model_map.get(i, frozenset()) for i in df.index]
    df["model_key"] = df["model_tokens"].map(lambda s: " ".join(sorted(s)))
    return df.drop(columns=["_cand"])


def compute_comps(df: pd.DataFrame, eligible: pd.Series, quantile: float,
                  min_similar: int, min_search: int) -> pd.DataFrame:
    """Statistiques de comparables pour chaque annonce (leave-one-out)."""
    out = {}
    for name, idx in df.groupby("search_name").groups.items():
        sub = df.loc[idx]
        pool = sub[eligible.loc[idx]]
        p_price = pool["price_norm"].to_numpy()
        p_models = pool["model_tokens"].tolist()
        p_color = pool["color_key"].to_numpy()
        p_size = pool["size_bucket"].to_numpy()
        p_fav = pool["favourites"].to_numpy(dtype=float)
        p_ids = pool.index.to_numpy()
        for i, r in sub.iterrows():
            not_self = p_ids != i
            chosen, level = None, ""
            for label, use_m, use_c, use_s in LEVELS:
                if use_m and not r["model_tokens"]:
                    continue
                if use_c and not r["color_key"]:
                    continue
                if use_s and not r["size_bucket"]:
                    continue
                mask = not_self.copy()
                if use_m:
                    mask &= np.array([r["model_tokens"] <= m for m in p_models], dtype=bool)
                if use_c:
                    mask &= p_color == r["color_key"]
                if use_s:
                    mask &= p_size == r["size_bucket"]
                if mask.sum() >= min_similar:
                    chosen, level = mask, label
                    break
            if chosen is None and not_self.sum() >= min_search:
                chosen, level = not_self, "recherche entière"
            if chosen is None:
                out[i] = CompStats(n=int(not_self.sum()))
                continue
            prices = p_price[chosen]
            med = float(np.median(prices))
            out[i] = CompStats(
                n=int(chosen.sum()), level=level, median=med,
                q=float(np.quantile(prices, quantile)),
                p25=float(np.quantile(prices, 0.25)), p75=float(np.quantile(prices, 0.75)),
                mad=float(np.median(np.abs(prices - med))),
                percentile=float((prices <= r["price_norm"]).mean()),
                fav_median=float(np.median(p_fav[chosen])) if len(p_fav) else np.nan,
            )
    stats = pd.DataFrame({i: vars(s) for i, s in out.items()}).T
    stats.columns = ["comps_" + c if c not in ("percentile",) else "price_percentile" for c in stats.columns]
    stats = stats.rename(columns={"comps_fav_median": "comps_fav_median"})
    for c in stats.columns:
        if c != "comps_level":
            stats[c] = pd.to_numeric(stats[c], errors="coerce")
    stats["comps_n"] = stats["comps_n"].fillna(0).astype(int)
    return stats
