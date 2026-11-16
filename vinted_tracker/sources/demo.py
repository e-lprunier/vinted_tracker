"""Générateur de données de DÉMO (fictives) pour tester le dashboard sans scraper.

Les annonces sont inventées mais réalistes, et incluent volontairement des
pièges (faux prix, arnaques, contrefaçons, lots, recherches…) pour vérifier
que le détecteur les écarte bien.
"""
from __future__ import annotations

import base64
import random

from ..config import Config
from ..models import Listing

# Prix médian "Très bon état" fictif par recherche (ordre de grandeur Vinted)
BASE_PRICES = {
    "Nike Dunk Low": 60, "Adidas Samba": 65, "New Balance 550": 55, "Nike Air Force 1": 45,
    "Polo Ralph Lauren": 28, "Pull Ralph Lauren": 40, "Polo Lacoste": 25,
    "Carhartt Detroit Jacket": 120, "Levi's 501": 28, "The North Face Nuptse": 140,
    "Arc'teryx": 170, "Patagonia polaire": 55, "Sézane": 45, "Bracelet Pandora": 35,
    "Lego": 35, "Polaroid": 70,
}
VARIANTS = {
    "Nike Dunk Low": ["Nike Dunk Low Panda", "Nike Dunk Low Retro grey fog", "Nike Dunk Low University Blue",
                      "Dunk Low Nike white black"],
    "Adidas Samba": ["Adidas Samba OG blanche", "Adidas Samba OG noir gomme", "Samba Adidas vert"],
    "New Balance 550": ["New Balance 550 white green", "New Balance 550 blanche", "NB 550 New Balance grise"],
    "Nike Air Force 1": ["Nike Air Force 1 blanche", "Air Force 1 '07 Nike", "Nike AF1 Air Force 1 low"],
    "Polo Ralph Lauren": ["Polo Ralph Lauren custom fit bleu marine", "Polo Ralph Lauren slim rouge",
                          "Polo Ralph Lauren blanc"],
    "Pull Ralph Lauren": ["Pull Ralph Lauren torsadé crème", "Pull Ralph Lauren col rond marine",
                          "Pull Ralph Lauren cable knit vert"],
    "Polo Lacoste": ["Polo Lacoste L.12.12 noir", "Polo Lacoste classic fit blanc", "Polo Lacoste vert"],
    "Carhartt Detroit Jacket": ["Carhartt Detroit Jacket marron", "Veste Carhartt Detroit Jacket noire",
                                "Carhartt WIP Detroit Jacket beige"],
    "Levi's 501": ["Levi's 501 brut", "Jean Levi's 501 original bleu", "Levi's 501 vintage délavé"],
    "The North Face Nuptse": ["The North Face Nuptse 1996 noire", "Doudoune North Face Nuptse 700",
                              "North Face Nuptse retro bleue"],
    "Arc'teryx": ["Veste Arc'teryx Beta LT", "Arcteryx veste Atom", "Veste Arc'teryx Zeta SL"],
    "Patagonia polaire": ["Patagonia polaire Retro-X", "Polaire Patagonia Better Sweater",
                          "Patagonia Synchilla polaire"],
    "Sézane": ["Robe Sézane fleurie", "Pull Sézane mohair", "Blouse Sézane", "Cardigan Sézane"],
    "Bracelet Pandora": ["Bracelet Pandora Moments argent", "Bracelet Pandora avec charms",
                         "Bracelet Pandora rigide"],
    "Lego": ["Lego Technic complet", "Lego Star Wars 75301", "Lego Creator maison", "Lego City complet"],
    "Polaroid": ["Appareil photo Polaroid Now", "Polaroid OneStep 2", "Polaroid 600 vintage fonctionnel"],
}
CONDITIONS = [("Neuf avec étiquette", 0.10), ("Neuf sans étiquette", 0.15), ("Très bon état", 0.45),
              ("Bon état", 0.25), ("Satisfaisant", 0.05)]
SIZES = {"Sneakers": ["38", "39", "40", "41", "42", "43", "44"], "Hauts": ["S", "M", "L", "XL"],
         "Vestes": ["S", "M", "L", "XL"], "Bas": ["W30", "W31", "W32", "W34"], "Femme": ["34", "36", "38", "40"],
         "Bijoux": ["17 cm", "19 cm"], "Objets": [""]}
NORMAL_DESC = [
    "Très peu porté, aucun défaut. Envoi rapide et soigné.",
    "Porté quelques fois, très bon état général. N'hésitez pas pour plus de photos.",
    "Taille normalement. Lavé et prêt à porter. Envoi sous 48h.",
    "Bon état, petites traces d'usure normales visibles sur photos.",
    "Jamais porté, ne me va pas. Possibilité de lot avec mes autres articles.",
    "100% authentique, pas de fake. Ticket de caisse disponible.",
    "Je fais du tri dans mon dressing, prix négociable raisonnablement.",
    "Article en très bon état, fumeur non, animaux non.",
]
DEAL_DESC = [
    "Achetée 120€ en boutique il y a 6 mois, portée 2 fois. Je déménage, je vends vite.",
    "Vends car erreur de taille, très bon état. Pressée de vendre.",
    "Je vide mon armoire avant de partir à l'étranger, tout doit partir.",
    "Cadeau qui ne me plaît pas, jamais porté.",
]
# (code, price_ratio ou prix fixe, titre_suffix, description)
TRAPS = [
    ("fake_price", 1, "", "Prix réel 90€, ne pas acheter au prix affiché, faites-moi une offre en message."),
    ("fake_price2", 1, "", "Je la laisse à 85€ ferme. Prix à 1€ pour la visibilité."),
    ("fake_price3", 0.25, "", "Valeur 140€, je la laisse à 120€, pas moins."),
    ("off_platform", 0.45, "", "Pour plus de photos contactez-moi sur WhatsApp au 06 12 34 56 78."),
    ("paypal", 0.5, "", "Paiement PayPal entre amis possible, moins cher qu'ici."),
    ("replica", 0.3, "", "Qualité 1:1 top, rep impossible à différencier."),
    ("box_only", 0.2, "", "Boîte vide uniquement, en très bon état, idéale pour collection."),
    ("search", 0.6, "", "Je recherche une paire en taille 42, faites-moi vos propositions."),
    ("lot", 1.8, "Lot de 3 ", "Lot de 3 articles, vendus ensemble uniquement."),
    ("too_good", 0.12, "", "Neuve jamais portée, envoi rapide."),
    ("style", 0.35, "", "Article style {brand}, même qualité, très bon état."),
]


def _placeholder_svg(text: str, color: str) -> str:
    initials = "".join(w[0] for w in text.split()[:2]).upper()
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" width="120" height="120">'
           f'<rect width="120" height="120" rx="14" fill="{color}"/>'
           f'<text x="60" y="72" font-family="Arial" font-size="40" font-weight="700" '
           f'text-anchor="middle" fill="#ffffff">{initials}</text></svg>')
    return "data:image/svg+xml;base64," + base64.b64encode(svg.encode()).decode()


_COLORS = ["#2a78d6", "#eb6834", "#1baf7a", "#6250d6", "#e34948", "#008300", "#e87ba4", "#eda100"]


def _nice_price(x: float) -> float:
    if x < 10:
        return round(max(x, 1))
    return float(int(round(x)))


def generate_demo(cfg: Config, per_search: int = 55, seed: int = 42) -> list[Listing]:
    rng = random.Random(seed)
    listings: list[Listing] = []
    counter = 4_000_000_000
    for si, search in enumerate(cfg.searches):
        base = BASE_PRICES.get(search.name, 40)
        color = _COLORS[si % len(_COLORS)]
        brand = next(w for w in search.name.split() if w.lower() not in {"bracelet", "polo", "pull"})
        sizes = SIZES.get(search.category, [""])
        traps = rng.sample(TRAPS, k=4)
        n_deals = rng.randint(2, 4)
        for k in range(per_search + len(traps) + n_deals):
            counter += rng.randint(1, 9999)
            cond = rng.choices([c for c, _ in CONDITIONS], [w for _, w in CONDITIONS])[0]
            factor = cfg.condition_factors[cond]
            title = rng.choice(VARIANTS.get(search.name, [search.name]))
            desc = rng.choice(NORMAL_DESC)
            fb = rng.choice([0, 1, 3, 8, 15, 27, 42, 64, 120, 300])
            if k < len(traps):                                  # pièges
                code, ratio, prefix, desc = traps[k]
                desc = desc.replace("{brand}", brand)
                price = 1.0 if (code.startswith("fake_price") and ratio == 1) else _nice_price(base * ratio)
                title = prefix + title
                if code == "too_good":
                    fb = 0
            elif k < len(traps) + n_deals:                      # vraies bonnes affaires
                price = _nice_price(base * factor * rng.uniform(0.35, 0.58))
                desc = rng.choice(DEAL_DESC)
            else:                                               # marché normal
                price = _nice_price(base * factor * rng.lognormvariate(0, 0.25))
            favs = max(0, int(rng.gauss(8, 6)) + (10 if price < base * 0.6 else 0))
            listings.append(Listing(
                id=str(counter), title=title, price=float(price), search_name=search.name,
                category=search.category, description=desc, brand=brand, size=rng.choice(sizes),
                condition=cond, url=f"https://www.vinted.fr/items/{counter}",
                photo_url=_placeholder_svg(search.name, color),
                seller_login=f"user{rng.randint(1000, 99999)}", seller_feedback_count=fb,
                seller_rating=round(rng.uniform(4.2, 5.0), 1) if fb else None,
                favourites=favs, views=favs * rng.randint(6, 15) + rng.randint(5, 40), source="demo",
            ))
    return listings
