"""Calcul des frais, du coût total d'achat, du prix de revente net et de la marge.

Rappel du modèle Vinted (particuliers, France) :
- l'ACHETEUR paie : prix + « Frais Vinted » (0,70 € + 5 % du prix) + livraison ;
- le VENDEUR ne paie pas de commission : il touche le prix affiché.
Donc quand tu achètes pour revendre :
    coût total   = prix + 0,70 + 5 % × prix + port
    revente nette = prix de revente × (1 − cotisations) − emballage − boost
    marge        = revente nette − coût total
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

from .config import Config


@dataclass
class CostBreakdown:
    price: float
    buyer_fee: float
    shipping: float
    total_cost: float

    def to_dict(self):
        return asdict(self)


@dataclass
class MarginResult:
    total_cost: float
    resale_price: float          # prix auquel on remet en vente (estimé)
    resale_net: float            # ce qu'on touche réellement
    margin: float
    roi: float                   # marge / coût total
    max_buy_price: float         # prix d'achat max pour garder la marge cible

    def to_dict(self):
        return asdict(self)


def buyer_fee(price: float, cfg: Config) -> float:
    f = cfg.fees
    return round(f["buyer_fee_fixed"] + f["buyer_fee_pct"] * price, 2)


def shipping_cost(size_class: str, cfg: Config) -> float:
    ship = cfg.fees["shipping"]
    return float(ship.get(size_class, ship["moyen"]))


def purchase_cost(price: float, size_class: str, cfg: Config) -> CostBreakdown:
    fee = buyer_fee(price, cfg)
    ship = shipping_cost(size_class, cfg)
    return CostBreakdown(price=price, buyer_fee=fee, shipping=ship,
                         total_cost=round(price + fee + ship, 2))


def resale_net(resale_price: float, cfg: Config, social_pct: float | None = None) -> float:
    r = cfg.resale
    social = r["social_contributions_pct"] if social_pct is None else social_pct
    net = resale_price * (1 - r["seller_commission_pct"] - social) - r["packaging_cost"] - r["boost_cost"]
    return round(net, 2)


def max_buy_price(net_resale: float, size_class: str, target_margin: float, cfg: Config) -> float:
    """Prix affiché maximum à payer pour garder `target_margin` € de marge.

    net − (p + fixed + pct·p + ship) = target  ⇒  p = (net − fixed − ship − target) / (1 + pct)
    C'est la limite qu'utiliseront les agents de négociation (partie 2).
    """
    f = cfg.fees
    p = (net_resale - f["buyer_fee_fixed"] - shipping_cost(size_class, cfg) - target_margin) / (1 + f["buyer_fee_pct"])
    return round(max(p, 0.0), 2)


def compute_margin(price: float, resale_price: float, size_class: str, cfg: Config,
                   social_pct: float | None = None, target_margin: float | None = None) -> MarginResult:
    cost = purchase_cost(price, size_class, cfg)
    net = resale_net(resale_price, cfg, social_pct)
    margin = round(net - cost.total_cost, 2)
    target = cfg.thresholds["min_margin_eur"] if target_margin is None else target_margin
    return MarginResult(
        total_cost=cost.total_cost,
        resale_price=round(resale_price, 2),
        resale_net=net,
        margin=margin,
        roi=round(margin / cost.total_cost, 4) if cost.total_cost else 0.0,
        max_buy_price=max_buy_price(net, size_class, target, cfg),
    )
