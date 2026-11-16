"""Analyse complète d'un lot d'annonces : comparables, risques, marge, score.

Entrée : une liste de `Listing`. Sortie : un DataFrame avec une ligne par
annonce et toutes les métriques utiles au dashboard.
"""
from __future__ import annotations

import json
import math

import numpy as np
import pandas as pd

from .config import Config
from .models import Flag, Listing
from .pricing import compute_margin, purchase_cost
from .text_analysis import analyze_listing

STATUS_OK, STATUS_REVIEW, STATUS_EXCLUDED, STATUS_NA = "ok", "à vérifier", "exclu", "non comparable"


def _brand_tokens(search_name: str) -> list[str]:
    generic = {"low", "jacket", "polaire", "bracelet", "polo", "pull", "appareil", "veste", "the"}
    return [t for t in search_name.replace("'", " ").split() if t.lower() not in generic and len(t) > 2]


def _clip01(x: float) -> float:
    return 0.0 if x is None or math.isnan(x) else max(0.0, min(1.0, x))


def deal_score(discount: float, margin: float, roi: float, decote_neuf: float | None,
               liquidity: int, favourites: int, risk: int) -> float:
    """Score 0-100. Pondérations volontairement simples et lisibles."""
    s = 0.0
    s += 35 * _clip01(discount / 0.6)            # décote vs marché (60 % sous la médiane = max)
    s += 25 * _clip01(margin / 30)               # 30 € de marge nette = max
    s += 15 * _clip01(roi / 1.0)                 # 100 % de ROI = max
    s += 10 * _clip01((decote_neuf or 0) / 0.8)  # 80 % sous le prix neuf = max
    s += 10 * _clip01((liquidity - 1) / 2) * 0.6 + 10 * _clip01(favourites / 20) * 0.4
    s += 5                                       # bonus de base pour les annonces saines
    s -= 0.4 * risk
    return round(max(0.0, min(100.0, s)), 1)


def analyze(listings: list[Listing], cfg: Config, social_pct: float | None = None,
            llm_checker=None) -> pd.DataFrame:
    if not listings:
        return pd.DataFrame()

    th = cfg.thresholds
    cond = cfg.condition_factors
    rows = []

    # 1) analyse texte, annonce par annonce ------------------------------------
    for lst in listings:
        search = cfg.search_by_name(lst.search_name)
        ta = analyze_listing(lst, search.query if search else "", _brand_tokens(lst.search_name))
        factor = cond.get(lst.condition, 1.0)
        rows.append({
            **lst.to_dict(),
            "_flags": list(ta.flags),
            "_hard": ta.hard_excluded,
            "is_lot": ta.is_lot,
            "is_off_topic": ta.is_off_topic,
            "retail_price_mentioned": ta.retail_price_mentioned,
            "real_price_mentioned": ta.real_price_mentioned,
            "cond_factor": factor,
            "price_norm": lst.price / factor,
            "size_class": search.size_class if search else "moyen",
            "liquidity": search.liquidity if search else 2,
            "counterfeit_risk": search.counterfeit_risk if search else False,
        })
    df = pd.DataFrame(rows)
    for col in ("retail_price_mentioned", "real_price_mentioned", "seller_feedback_count", "seller_rating"):
        df[col] = pd.to_numeric(df[col], errors="coerce")

    # 2) comparables par recherche (on exclut faux prix, lots, hors-sujet) ----
    clean = df[~df["_hard"] & ~df["is_lot"] & ~df["is_off_topic"] & (df["price"] > 2)]
    q = cfg.resale["resale_quantile"]
    stats = clean.groupby("search_name")["price_norm"].agg(
        comps_n="count",
        comps_median="median",
        comps_q=lambda s: s.quantile(q),
        comps_p25=lambda s: s.quantile(0.25),
        comps_p75=lambda s: s.quantile(0.75),
        comps_mad=lambda s: (s - s.median()).abs().median(),
    ).reset_index()
    df = df.merge(stats, on="search_name", how="left")
    df["comps_n"] = df["comps_n"].fillna(0).astype(int)

    sorted_comps = {k: np.sort(g.values) for k, g in clean.groupby("search_name")["price_norm"]}

    def pct_rank(row):
        arr = sorted_comps.get(row["search_name"])
        if arr is None or len(arr) == 0:
            return np.nan
        return float(np.searchsorted(arr, row["price_norm"], side="right")) / len(arr)

    df["price_percentile"] = df.apply(pct_rank, axis=1)
    df["ratio_to_median"] = df["price_norm"] / df["comps_median"]
    df["discount_vs_market"] = 1 - df["ratio_to_median"]
    mad = df["comps_mad"].replace(0, np.nan) * 1.4826
    df["robust_z"] = (df["price_norm"] - df["comps_median"]) / mad

    # 3) signaux statistiques + score de risque --------------------------------
    statuses, risks, flags_json, flag_labels = [], [], [], []
    for i, r in df.iterrows():
        flags: list[Flag] = list(r["_flags"])
        ratio = r["ratio_to_median"]
        median = r["comps_median"]
        enough = r["comps_n"] >= th["min_comps"]
        if enough and r["price"] <= 2 and median >= 15:
            flags.append(Flag("placeholder_low", f"Prix symbolique ({r['price']:.0f} €) vs marché {median:.0f} €",
                              60, hard=True))
        elif enough and not math.isnan(ratio):
            limit = th["luxury_too_good_ratio"] if r["counterfeit_risk"] else th["too_good_ratio"]
            if ratio < limit:
                label = ("Trop beau pour être vrai — risque de contrefaçon" if r["counterfeit_risk"]
                         else "Trop beau pour être vrai")
                flags.append(Flag("too_good", f"{label} ({ratio:.0%} de la médiane)", 45))
        fb = r.get("seller_feedback_count")
        if pd.notna(fb) and int(fb) == 0 \
                and not math.isnan(ratio) and ratio < 0.5:
            flags.append(Flag("new_seller", "Vendeur sans évaluation + prix très bas", 15))

        risk = min(100, sum(f.weight for f in flags))
        hard = any(f.hard for f in flags)
        if hard or risk >= th["risk_exclude"]:
            status = STATUS_EXCLUDED
        elif r["is_lot"] or r["is_off_topic"] or not enough:
            status = STATUS_NA
        elif risk >= th["risk_review"]:
            status = STATUS_REVIEW
        else:
            status = STATUS_OK
        statuses.append(status)
        risks.append(risk)
        df.at[i, "_flags"] = flags
        flags_json.append(json.dumps([f.to_dict() for f in flags], ensure_ascii=False))
        flag_labels.append(" · ".join(f.label for f in flags if f.weight > 0 or f.hard) or "")

    df["status"], df["risk_score"] = statuses, risks
    df["flags_json"], df["flags_text"] = flags_json, flag_labels

    # 4) vérification LLM optionnelle (sur les cas limites) ---------------------
    if llm_checker is not None:
        mask = df["status"] == STATUS_REVIEW if cfg.llm.get("only_for_review", True) \
            else df["status"].isin([STATUS_OK, STATUS_REVIEW])
        for i in df[mask].index:
            verdict = llm_checker(df.loc[i])
            if verdict and (verdict.get("is_fake_price") or verdict.get("is_scam")):
                df.at[i, "status"] = STATUS_EXCLUDED
                df.at[i, "risk_score"] = 100
                df.at[i, "flags_text"] = (df.at[i, "flags_text"] + " · " if df.at[i, "flags_text"] else "") \
                    + f"IA : {verdict.get('reason', 'suspect')}"

    # 5) prix neuf, revente, marge, score ---------------------------------------
    ref = cfg.reference_prices
    out = []
    for _, r in df.iterrows():
        prix_neuf = ref.get(r["search_name"])
        source_neuf = "référence" if prix_neuf else None
        rp = r["retail_price_mentioned"]
        if rp and not (isinstance(rp, float) and math.isnan(rp)) and r["comps_median"] == r["comps_median"]:
            if r["comps_median"] * 0.8 <= rp <= r["comps_median"] * 15:
                prix_neuf, source_neuf = float(rp), "annonce"
        if r["comps_n"] > 0 and r["comps_q"] == r["comps_q"]:
            resale_price = r["comps_q"] * r["cond_factor"] * cfg.resale["asking_to_sold_ratio"]
            m = compute_margin(r["price"], resale_price, r["size_class"], cfg, social_pct)
            total_cost, resale, net, margin, roi, max_buy = (
                m.total_cost, m.resale_price, m.resale_net, m.margin, m.roi, m.max_buy_price)
        else:
            total_cost = purchase_cost(r["price"], r["size_class"], cfg).total_cost
            resale = net = margin = roi = max_buy = np.nan
        decote = (1 - total_cost / prix_neuf) if prix_neuf else np.nan
        disc = r["discount_vs_market"]
        score = deal_score(0 if disc != disc else disc, 0 if margin != margin else margin,
                           0 if roi != roi else roi, None if decote != decote else decote,
                           r["liquidity"], int(r["favourites"] or 0), r["risk_score"]) \
            if r["status"] in (STATUS_OK, STATUS_REVIEW) else 0.0
        is_deal = bool(r["status"] in (STATUS_OK, STATUS_REVIEW) and margin == margin
                       and margin >= th["min_margin_eur"] and roi >= th["min_roi"])
        out.append(dict(prix_neuf=prix_neuf, prix_neuf_source=source_neuf, total_cost=total_cost,
                        resale_price=resale, resale_net=net, margin=margin, roi=roi,
                        max_buy_price=max_buy, decote_neuf=decote, deal_score=score, is_deal=is_deal))
    df = pd.concat([df.reset_index(drop=True), pd.DataFrame(out)], axis=1)
    return df.drop(columns=["_flags", "_hard"]).sort_values("deal_score", ascending=False).reset_index(drop=True)
