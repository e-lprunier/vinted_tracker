"""Tests du détecteur de faux prix / arnaques et du calcul de marge."""
import pytest

from vinted_tracker.config import load_config
from vinted_tracker.models import Listing
from vinted_tracker.pricing import compute_margin, max_buy_price, purchase_cost, resale_net
from vinted_tracker.scoring import analyze
from vinted_tracker.sources.demo import generate_demo
from vinted_tracker.text_analysis import analyze_listing

CFG = load_config()


def L(desc, price=30.0, title="Nike Dunk Low Panda"):
    return Listing(id="1", title=title, price=price, search_name="Nike Dunk Low",
                   category="Sneakers", description=desc, brand="Nike")


def codes(desc, price=30.0, title="Nike Dunk Low Panda"):
    return {f.code for f in analyze_listing(L(desc, price, title), "nike dunk low", ["Nike", "Dunk"]).flags}


# --- Faux prix -------------------------------------------------------------
@pytest.mark.parametrize("desc,price", [
    ("Prix réel 90€, ne pas acheter", 1),
    ("Je la laisse à 85€ ferme. Prix à 1€ pour la visibilité.", 1),
    ("Valeur 140€, je la laisse à 120€, pas moins.", 10),
    ("prix : 100 euros, négociable", 2),
    ("100 balles et elle est à toi", 1),
])
def test_fake_price_detected(desc, price):
    assert "fake_price" in codes(desc, price)


@pytest.mark.parametrize("desc", [
    "Achetée 120€ en boutique il y a 6 mois, portée 2 fois.",   # prix neuf = info utile, pas un faux prix
    "Payée 150 euros, jamais portée.",
    "Envoi 4€ en Mondial Relay.",
    "Très bon état, 2e main mais propre.",
])
def test_legit_amounts_not_flagged(desc):
    assert "fake_price" not in codes(desc, 30)


def test_retail_price_extracted():
    r = analyze_listing(L("Achetée 120€ en boutique"), "nike dunk low")
    assert r.retail_price_mentioned == 120


# --- Arnaques & annonces non valables -------------------------------------
@pytest.mark.parametrize("desc,code", [
    ("Contactez-moi sur WhatsApp", "off_platform"),
    ("Paiement PayPal entre amis possible", "off_platform"),
    ("appelez le 06 12 34 56 78", "off_platform"),
    ("écrivez à vendeur.top@gmail.com", "off_platform"),
    ("Qualité 1:1, rep top", "counterfeit"),
    ("Boîte vide uniquement", "not_the_item"),
    ("Je recherche une paire en 42", "not_for_sale"),
    ("Réservée pour Julie", "not_for_sale"),
    ("Ne pas acheter, faites une offre", "not_for_sale"),
])
def test_scams_detected(desc, code):
    assert code in codes(desc)


@pytest.mark.parametrize("desc", [
    "100% authentique, pas de fake. Ticket dispo.",
    "Aucune copie, achetée chez Foot Locker.",
    "Modèle très recherché, aucun défaut.",
    "Possibilité de lot avec mes autres articles.",
])
def test_no_false_positive(desc):
    c = codes(desc)
    assert not c & {"counterfeit", "not_for_sale", "lot", "defects", "off_platform"}


def test_lot_and_off_topic():
    assert "lot" in codes("Vendus ensemble", title="Lot de 3 paires Nike Dunk")
    assert "off_topic" in codes("Très bon état", title="Chaussettes Adidas")
    assert "kids" in codes("Très bon état", title="Nike Dunk Low enfant 33")


# --- Frais & marge ---------------------------------------------------------
def test_purchase_cost():
    c = purchase_cost(20, "petit", CFG)
    assert c.buyer_fee == pytest.approx(0.70 + 1.0)
    assert c.total_cost == pytest.approx(20 + 1.70 + CFG.fees["shipping"]["petit"])


def test_margin_and_max_buy_price_consistent():
    m = compute_margin(25, 60, "moyen", CFG, target_margin=10)
    assert m.margin == pytest.approx(m.resale_net - m.total_cost)
    # acheter exactement au prix max doit laisser exactement la marge cible
    at_max = compute_margin(m.max_buy_price, 60, "moyen", CFG)
    assert at_max.margin == pytest.approx(10, abs=0.02)


def test_social_contributions_reduce_net():
    assert resale_net(100, CFG, social_pct=0.123) < resale_net(100, CFG, social_pct=0)


# --- Pipeline complet sur la démo -----------------------------------------
def test_demo_pipeline_excludes_traps():
    df = analyze(generate_demo(CFG), CFG)
    deals = df[df["is_deal"]]
    assert len(deals) > 10
    # aucune bonne affaire ne doit contenir un piège
    bad = deals["description"].str.contains("WhatsApp|PayPal|1:1|Boîte vide|recherche|Prix réel|laisse à",
                                            case=False)
    assert not bad.any()
    # les faux prix à 1 € sont exclus
    assert (df.loc[df["price"] <= 1, "status"] == "exclu").all()
