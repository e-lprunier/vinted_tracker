"""Deuxième avis par LLM (Claude) sur les annonces ambiguës — optionnel.

Les règles de text_analysis.py attrapent l'essentiel ; le LLM sert aux cas
tordus (formulations inhabituelles, prix écrit en toutes lettres, etc.).
Activé via config.yaml (llm.enabled) + variable ANTHROPIC_API_KEY.
"""
from __future__ import annotations

import json
import os
import re

PROMPT = """Tu analyses une annonce Vinted pour un outil de détection de bonnes affaires.
Réponds UNIQUEMENT avec un objet JSON :
{{"is_fake_price": bool, "real_price": number|null, "is_scam": bool, "is_counterfeit": bool, "reason": "≤ 12 mots"}}

- is_fake_price : le prix affiché n'est pas le vrai prix demandé (ex. 1 € affiché, vrai prix dans le texte).
- is_scam : demande de paiement/contact hors Vinted, article qui n'est pas vendu (boîte vide, photo), etc.
- is_counterfeit : copie/replica avouée ou très probable.

Recherche : {search}
Prix affiché : {price} €
Prix médian du marché pour ce type d'article : {median} €
Titre : {title}
Description : {description}"""


def make_checker(model: str = "claude-haiku-5-5"):
    """Renvoie une fonction row -> verdict (dict), ou None si pas de clé API."""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return None
    import anthropic  # dépendance optionnelle

    client = anthropic.Anthropic()

    def check(row) -> dict | None:
        try:
            msg = client.messages.create(
                model=model, max_tokens=200,
                messages=[{"role": "user", "content": PROMPT.format(
                    search=row["search_name"], price=row["price"],
                    median=round(row.get("comps_median") or 0), title=row["title"],
                    description=(row.get("description") or "")[:1500])}],
            )
            text = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
            m = re.search(r"\{.*\}", text, re.S)
            verdict = json.loads(m.group(0)) if m else None
            if verdict and verdict.get("is_counterfeit"):
                verdict["is_scam"] = True
            return verdict
        except Exception:
            return None

    return check
