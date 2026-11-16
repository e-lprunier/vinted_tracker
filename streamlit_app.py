"""Vinted Tracker — dashboard Streamlit.

Lancer en local :   streamlit run streamlit_app.py
Déployer :          share.streamlit.io -> repo e-lprunier/vinted_tracker -> streamlit_app.py
"""
from __future__ import annotations

import html
import os
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from vinted_tracker.config import load_config
from vinted_tracker.pricing import compute_margin, purchase_cost
from vinted_tracker.scoring import STATUS_EXCLUDED, STATUS_OK, STATUS_REVIEW, deal_score
from vinted_tracker.background import ScanManager
from vinted_tracker.storage import load_latest, previous_ids

st.set_page_config(page_title="Vinted Tracker", page_icon="🏷️", layout="wide")

# Secrets Streamlit Cloud -> variables d'environnement (APIFY_TOKEN, ANTHROPIC_API_KEY)
try:
    for _k in ("APIFY_TOKEN", "ANTHROPIC_API_KEY"):
        if _k in st.secrets and not os.environ.get(_k):
            os.environ[_k] = st.secrets[_k]
except Exception:
    pass

# --------------------------------------------------------------------------- #
# Style
# --------------------------------------------------------------------------- #
INK, INK2, MUTED, GRID, AXIS, SURFACE = "#0b0b0b", "#52514e", "#898781", "#e1e0d9", "#c3c2b7", "#fcfcfb"
BLUE, GOOD, CRIT, WARN = "#2a78d6", "#0ca30c", "#d03b3b", "#fab219"
PARIS = ZoneInfo("Europe/Paris")

st.markdown(f"""
<style>
  .block-container {{padding-top: 2.8rem; max-width: 1400px;}}
  h1, h2, h3 {{letter-spacing: -0.02em;}}
  .vt-header {{display:flex; align-items:baseline; gap:14px; margin-bottom:2px;}}
  .vt-title {{font-size:2.1rem; font-weight:800; color:{INK};}}
  .vt-badge {{font-size:.72rem; font-weight:700; padding:3px 9px; border-radius:999px;
             background:#fff4d6; color:#7a5200; border:1px solid #f3d27a;}}
  .vt-live {{font-size:.74rem; font-weight:700; padding:3px 9px; border-radius:999px;
             background:#e7f6e7; color:#006300; border:1px solid #b9e3b9;}}
  .vt-sub {{color:{INK2}; font-size:.92rem; margin-bottom:18px;}}
  .kpi {{background:{SURFACE}; border:1px solid rgba(11,11,11,.08); border-radius:14px; padding:14px 16px;}}
  .kpi .l {{color:{INK2}; font-size:.8rem; font-weight:600; text-transform:uppercase; letter-spacing:.04em;}}
  .kpi .v {{color:{INK}; font-size:1.75rem; font-weight:750; margin-top:2px;}}
  .kpi .h {{color:{MUTED}; font-size:.78rem;}}
  .card {{background:#fff; border:1px solid rgba(11,11,11,.09); border-radius:16px; padding:14px;
          height:100%; box-shadow:0 1px 2px rgba(0,0,0,.04);}}
  .card .top {{display:flex; gap:12px; align-items:flex-start;}}
  .card img {{width:84px; height:84px; object-fit:cover; border-radius:10px; flex-shrink:0; background:#f0efec;}}
  .card .t {{font-weight:700; color:{INK}; font-size:.95rem; line-height:1.25;}}
  .card .s {{color:{MUTED}; font-size:.78rem; margin-top:3px;}}
  .card .row {{display:flex; justify-content:space-between; font-size:.85rem; color:{INK2}; margin-top:6px;}}
  .card .row b {{color:{INK};}}
  .card .margin {{font-size:1.35rem; font-weight:800; color:#006300;}}
  .card .bar {{height:6px; background:#f0efec; border-radius:4px; margin-top:10px; overflow:hidden;}}
  .card .bar > div {{height:100%; background:{BLUE}; border-radius:4px;}}
  .card .warn {{margin-top:8px; font-size:.76rem; color:#7a5200; background:#fff4d6; padding:4px 8px; border-radius:8px;}}
  .card a {{font-size:.82rem; font-weight:600; color:{BLUE}; text-decoration:none;}}
</style>
""", unsafe_allow_html=True)


def eur(x, dec=2):
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return "—"
    s = f"{x:,.{dec}f}".replace(",", " ").replace(".", ",")
    return f"{s} €"


def pct(x):
    return "—" if x is None or (isinstance(x, float) and np.isnan(x)) else f"{x:.0%}"


# --------------------------------------------------------------------------- #
# Données
# --------------------------------------------------------------------------- #
cfg = load_config()


SNAP = Path(__file__).parent / "data" / "latest_scan.parquet"
APP = cfg.raw.get("app", {})
AUTO_EVERY = int(APP.get("auto_refresh_minutes", 30))
STALE_AFTER = int(APP.get("stale_after_minutes", 60))


@st.cache_data(show_spinner=False)
def get_data(mtime: float) -> pd.DataFrame | None:
    """Relit le snapshot quand le fichier change (mtime sert de clé de cache)."""
    return load_latest()


@st.cache_resource
def get_manager() -> ScanManager:
    return ScanManager()


def snapshot_mtime() -> float:
    return SNAP.stat().st_mtime if SNAP.exists() else 0.0


def data_age_minutes(df: pd.DataFrame | None) -> float | None:
    if df is None or df.empty or "scan_id" not in df:
        return None
    try:
        t = datetime.strptime(str(df["scan_id"].iloc[0]), "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    return (datetime.now(timezone.utc) - t).total_seconds() / 60


def fmt_age(m: float | None) -> str:
    if m is None:
        return "—"
    if m < 1:
        return "à l'instant"
    if m < 60:
        return f"il y a {m:.0f} min"
    if m < 48 * 60:
        return f"il y a {m / 60:.0f} h"
    return f"il y a {m / 1440:.0f} j"


def recompute(df: pd.DataFrame, social_pct: float, min_margin: float, min_roi: float) -> pd.DataFrame:
    """Recalcule marge / ROI / score si l'utilisateur change ses hypothèses."""
    d = df.copy()
    r, f = cfg.resale, cfg.fees
    d["resale_net"] = d["resale_price"] * (1 - r["seller_commission_pct"] - social_pct) \
        - r["packaging_cost"] - r["boost_cost"]
    d["margin"] = d["resale_net"] - d["total_cost"]
    d["roi"] = d["margin"] / d["total_cost"]
    ship = d["size_class"].map(f["shipping"]).fillna(f["shipping"]["moyen"])
    d["max_buy_price"] = ((d["resale_net"] - f["buyer_fee_fixed"] - ship - min_margin)
                          / (1 + f["buyer_fee_pct"])).clip(lower=0)
    healthy = d["status"].isin([STATUS_OK, STATUS_REVIEW])
    d["is_deal"] = healthy & (d["margin"] >= min_margin) & (d["roi"] >= min_roi)
    d["deal_score"] = [
        deal_score(0 if pd.isna(x.discount_vs_market) else x.discount_vs_market,
                   0 if pd.isna(x.margin) else x.margin, 0 if pd.isna(x.roi) else x.roi,
                   None if pd.isna(x.decote_neuf) else x.decote_neuf, int(x.liquidity),
                   int(x.favourites or 0), int(x.risk_score)) if h else 0.0
        for x, h in zip(d.itertuples(), healthy)]
    return d


# --------------------------------------------------------------------------- #
# Données réelles + scan automatique en arrière-plan
# --------------------------------------------------------------------------- #
mgr = get_manager()
shown_mtime = snapshot_mtime()
df_raw = get_data(shown_mtime)
age = data_age_minutes(df_raw)
if mgr.should_auto_start(age, AUTO_EVERY):
    mgr.start(cfg, "auto")


@st.fragment(run_every=3)
def scan_status():
    """Barre de progression du scan ; recharge la page quand de nouvelles annonces arrivent."""
    stt = mgr.state
    if stt.running:
        st.session_state["was_running"] = True
        st.progress(stt.fraction, text=f"🔄 Actualisation des annonces Vinted… {stt.message}")
    elif snapshot_mtime() != shown_mtime or st.session_state.pop("was_running", False):
        st.rerun()   # scan terminé (succès ou échec) -> on recharge toute la page
    elif stt.last_error and (df_raw is None or (age or 0) > STALE_AFTER):
        st.warning(f"Dernière actualisation impossible : {stt.last_error}")


# --------------------------------------------------------------------------- #
# Barre latérale
# --------------------------------------------------------------------------- #
with st.sidebar:
    st.markdown("### 🏷️ Vinted Tracker")
    st.caption(f"Annonces actualisées **{fmt_age(age)}**"
               + (f" · rescan auto toutes les {AUTO_EVERY} min" if AUTO_EVERY else ""))
    names = st.multiselect("Recherches à scanner", [s.name for s in cfg.searches], placeholder="Toutes")
    if st.button("🔄 Actualiser maintenant", type="primary", use_container_width=True,
                 disabled=mgr.state.running):
        mgr.start(cfg, "auto", names or None)
        st.rerun()

    st.markdown("---")
    st.markdown("**Hypothèses de marge**")
    social = st.toggle("Je suis micro-entrepreneur (cotisations 12,3 %)", value=False)
    min_margin = st.slider("Marge nette minimale", 0, 50, int(cfg.thresholds["min_margin_eur"]), format="%d €")
    min_roi = st.slider("ROI minimal", 0, 200, int(cfg.thresholds["min_roi"] * 100), step=5, format="%d %%") / 100

# --------------------------------------------------------------------------- #
# Pas encore d'annonces : on l'explique au lieu d'afficher du faux
# --------------------------------------------------------------------------- #
if df_raw is None or df_raw.empty:
    st.markdown('<div class="vt-header"><span class="vt-title">Vinted Tracker</span></div>'
                '<div class="vt-sub">Bonnes affaires Vinted réelles, après frais, livraison et filtrage des faux prix.'
                '</div>', unsafe_allow_html=True)
    scan_status()
    if not mgr.state.running:
        st.info("**Aucune annonce Vinted chargée pour l'instant.** Clique sur « Actualiser maintenant ».")
    with st.expander("Si l'actualisation échoue depuis l'app en ligne", expanded=bool(mgr.state.last_error)):
        st.markdown("""
Vinted bloque souvent les serveurs (Streamlit Cloud, GitHub). Deux solutions :

1. **Depuis ton PC** (gratuit) — les annonces sont scannées chez toi et poussées sur GitHub, l'app se met à jour toute seule :
   ```bash
   python -m vinted_tracker watch --every 30 --push
   ```
2. **Depuis le cloud** — ajoute `APIFY_TOKEN` dans les *Secrets* de l'app Streamlit : l'actualisation passera par Apify si Vinted bloque.
""")
    st.stop()

df_all = recompute(df_raw, 0.123 if social else cfg.resale["social_contributions_pct"], min_margin, min_roi)
prev = previous_ids()
df_all["is_new"] = ~df_all["id"].astype(str).isin(prev) if prev else False

with st.sidebar:
    st.markdown("**Filtres**")
    only_new = st.checkbox("🆕 Nouvelles annonces seulement", value=False, disabled=not prev,
                           help="Annonces apparues depuis le scan précédent.")
    cats = st.multiselect("Catégories", sorted(df_all["category"].unique()), placeholder="Toutes")
    searches = st.multiselect("Recherches", sorted(df_all["search_name"].unique()), placeholder="Toutes")
    budget = st.slider("Prix d'achat max", 5, 400, 400, step=5, format="%d €")
    show_review = st.checkbox("Inclure les annonces « à vérifier »", value=True,
                              help="Prix très bas sur une marque très contrefaite, imitation possible… "
                                   "Rentables sur le papier mais à contrôler avant d'acheter.")
    st.caption(f"Frais d'achat : {eur(cfg.fees['buyer_fee_fixed'])} + "
               f"{cfg.fees['buyer_fee_pct']:.0%} du prix · Port : "
               + " / ".join(f"{k} {eur(v)}" for k, v in cfg.fees["shipping"].items()))

df = df_all.copy()
if only_new:
    df = df[df["is_new"]]
if cats:
    df = df[df["category"].isin(cats)]
if searches:
    df = df[df["search_name"].isin(searches)]
df = df[df["price"] <= budget]
deals = df[df["is_deal"] & (show_review | (df["status"] == STATUS_OK))].sort_values("deal_score", ascending=False)
excluded = df[df["status"] == STATUS_EXCLUDED]

# --------------------------------------------------------------------------- #
# En-tête & KPIs
# --------------------------------------------------------------------------- #
source = str(df_all["scan_source"].iloc[0]) if "scan_source" in df_all else "vinted"
try:
    when = (datetime.strptime(str(df_all["scan_id"].iloc[0]), "%Y%m%dT%H%M%SZ")
            .replace(tzinfo=timezone.utc).astimezone(PARIS).strftime("%d/%m à %H:%M"))
except (ValueError, KeyError):
    when = "—"
stale = age is not None and age > STALE_AFTER
badge = (f'<span class="vt-badge">⏳ annonces {fmt_age(age)}</span>' if stale
         else f'<span class="vt-live">● en direct · {fmt_age(age)}</span>')
st.markdown(f'<div class="vt-header"><span class="vt-title">Vinted Tracker</span>{badge}</div>'
            f'<div class="vt-sub">Annonces Vinted réelles · bonnes affaires après frais Vinted, livraison et filtrage '
            f'des faux prix · scan du {when} via <b>{source}</b></div>', unsafe_allow_html=True)
scan_status()

k = st.columns(5)
kpis = [
    ("Annonces analysées", f"{len(df):,}".replace(",", " "),
     f"{int(df['is_new'].sum())} nouvelles" if prev else f"{df['search_name'].nunique()} recherches"),
    ("Bonnes affaires", f"{len(deals)}", f"marge ≥ {min_margin} € et ROI ≥ {min_roi:.0%}"),
    ("Marge potentielle", eur(deals["margin"].head(10).sum(), 0), "sur les 10 meilleures"),
    ("ROI médian", pct(deals["roi"].median()) if len(deals) else "—", "des bonnes affaires"),
    ("Annonces écartées", f"{len(excluded)}", "faux prix, arnaques, contrefaçons"),
]
for col, (l, v, h) in zip(k, kpis):
    col.markdown(f'<div class="kpi"><div class="l">{l}</div><div class="v">{v}</div><div class="h">{h}</div></div>',
                 unsafe_allow_html=True)
st.write("")

tab_deals, tab_market, tab_excl, tab_calc, tab_method = st.tabs(
    ["🔥 Bonnes affaires", "📈 Marché", "🛡️ Écartées", "🧮 Calculateur", "ℹ️ Méthode"])

# --------------------------------------------------------------------------- #
# Onglet 1 : bonnes affaires
# --------------------------------------------------------------------------- #
with tab_deals:
    if deals.empty:
        st.info("Aucune bonne affaire avec ces critères. Baisse la marge ou le ROI minimal dans la barre latérale.")
    else:
        top = deals.head(6)
        for start in range(0, len(top), 3):
            cols = st.columns(3)
            for col, (_, r) in zip(cols, top.iloc[start:start + 3].iterrows()):
                warn = (f'<div class="warn">⚠️ {html.escape(r["flags_text"])}</div>'
                        if r["status"] == STATUS_REVIEW and r["flags_text"] else "")
                neuf = (f'<div class="row"><span>Décote vs neuf</span><b>{pct(r["decote_neuf"])}</b></div>'
                        if pd.notna(r["decote_neuf"]) else "")
                col.markdown(f"""
<div class="card">
  <div class="top">
    <img src="{html.escape(str(r['photo_url'] or ''))}" alt="">
    <div><div class="t">{'🆕 ' if r.get('is_new') else ''}{html.escape(r['title'])}</div>
      <div class="s">{html.escape(r['search_name'])} · {html.escape(str(r['condition']))}
        {(' · ' + html.escape(str(r['size']))) if r['size'] else ''}</div></div>
  </div>
  <div class="row"><span>Prix affiché</span><b>{eur(r['price'])}</b></div>
  <div class="row"><span>Coût total (frais + port)</span><b>{eur(r['total_cost'])}</b></div>
  <div class="row"><span>Revente estimée</span><b>{eur(r['resale_price'])}</b></div>
  <div class="row"><span>Sous le marché</span><b>{pct(r['discount_vs_market'])}</b></div>
  {neuf}
  <div class="row" style="align-items:baseline"><span>Marge nette · ROI {pct(r['roi'])}</span>
      <span class="margin">+{eur(r['margin'])}</span></div>
  <div class="bar"><div style="width:{r['deal_score']:.0f}%"></div></div>
  <div class="row"><span>Score {r['deal_score']:.0f}/100</span>
      <a href="{html.escape(r['url'])}" target="_blank">Voir l'annonce ↗</a></div>
  {warn}
</div>""", unsafe_allow_html=True)
            st.write("")

        st.markdown("#### Toutes les bonnes affaires")
        table = deals[["photo_url", "is_new", "title", "search_name", "condition", "price", "total_cost", "resale_price",
                       "margin", "roi", "max_buy_price", "discount_vs_market", "decote_neuf", "deal_score",
                       "status", "url"]].copy()
        table["roi"] *= 100
        table["discount_vs_market"] *= 100
        table["decote_neuf"] *= 100
        st.dataframe(
            table, hide_index=True, use_container_width=True, height=460,
            column_config={
                "photo_url": st.column_config.ImageColumn("", width="small"),
                "is_new": st.column_config.CheckboxColumn("🆕", width="small", help="Nouvelle depuis le scan précédent"),
                "title": st.column_config.TextColumn("Annonce", width="medium"),
                "search_name": "Recherche", "condition": "État",
                "price": st.column_config.NumberColumn("Prix", format="%.2f €"),
                "total_cost": st.column_config.NumberColumn("Coût total", format="%.2f €",
                                                            help="Prix + frais Vinted + livraison"),
                "resale_price": st.column_config.NumberColumn("Revente est.", format="%.2f €"),
                "margin": st.column_config.NumberColumn("Marge nette", format="%.2f €"),
                "roi": st.column_config.NumberColumn("ROI", format="%.0f %%"),
                "max_buy_price": st.column_config.NumberColumn(
                    "Prix max", format="%.2f €", help="Prix affiché maximum pour garder ta marge minimale "
                                                     "(la limite des futurs agents de négociation)"),
                "discount_vs_market": st.column_config.NumberColumn("Sous marché", format="%.0f %%"),
                "decote_neuf": st.column_config.NumberColumn("Décote neuf", format="%.0f %%"),
                "deal_score": st.column_config.ProgressColumn("Score", min_value=0, max_value=100, format="%.0f"),
                "status": "Statut",
                "url": st.column_config.LinkColumn("Lien", display_text="Ouvrir ↗"),
            })
        st.download_button("Exporter en CSV", deals.drop(columns=["photo_url"], errors="ignore").to_csv(index=False).encode("utf-8"),
                           "bonnes_affaires.csv", "text/csv")

# --------------------------------------------------------------------------- #
# Onglet 2 : marché
# --------------------------------------------------------------------------- #
with tab_market:
    st.markdown("#### Où se situe chaque annonce par rapport à son marché")
    st.caption("Chaque point est une annonce valide. 100 % = prix médian des annonces comparables "
               "(même recherche, ramené à un état « Très bon état »). Les bonnes affaires sont à gauche.")
    valid = df[df["status"].isin([STATUS_OK, STATUS_REVIEW]) & df["ratio_to_median"].notna()]
    order = (valid.groupby("search_name")["comps_median"].first().sort_values().index.tolist())
    fig = go.Figure()
    for name, sub, color, size in (("Marché", valid[~valid["is_deal"]], AXIS, 7),
                                   ("Bonne affaire", valid[valid["is_deal"]], BLUE, 10)):
        fig.add_trace(go.Scatter(
            x=sub["ratio_to_median"] * 100, y=sub["search_name"], mode="markers", name=name,
            marker=dict(color=color, size=size, line=dict(color=SURFACE, width=2)),
            customdata=np.stack([sub["title"], sub["price"], sub["margin"].fillna(0)], axis=-1),
            hovertemplate="<b>%{customdata[0]}</b><br>%{y}<br>Prix : %{customdata[1]:.0f} €"
                          "<br>%{x:.0f} % de la médiane<br>Marge : %{customdata[2]:.2f} €<extra></extra>"))
    fig.add_vline(x=100, line=dict(color=MUTED, width=1, dash="dot"),
                  annotation_text="médiane", annotation_font_color=MUTED)
    fig.update_layout(
        height=max(380, 34 * len(order) + 120), plot_bgcolor=SURFACE, paper_bgcolor="rgba(0,0,0,0)",
        margin=dict(l=10, r=20, t=30, b=40), font=dict(family="system-ui, -apple-system, Segoe UI, sans-serif",
                                                       color=INK2, size=13),
        legend=dict(orientation="h", y=1.06, x=0, title=None),
        xaxis=dict(title="Prix en % de la médiane du marché", ticksuffix=" %", gridcolor=GRID,
                   zeroline=False, range=[0, max(260, float(valid["ratio_to_median"].quantile(.98) * 100))]),
        yaxis=dict(categoryorder="array", categoryarray=order, gridcolor=GRID, title=None),
        hoverlabel=dict(bgcolor="white", font_color=INK))
    st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})

    st.markdown("#### Synthèse par recherche")
    summary = (df.groupby("search_name").agg(
        catégorie=("category", "first"), annonces=("id", "count"),
        médiane=("comps_median", "first"), p25=("comps_p25", "first"), p75=("comps_p75", "first"),
        prix_neuf=("prix_neuf", "first"), affaires=("is_deal", "sum"),
        écartées=("status", lambda s: (s == STATUS_EXCLUDED).sum()),
    ).reset_index().rename(columns={"search_name": "recherche"}).sort_values("affaires", ascending=False))
    st.dataframe(summary, hide_index=True, use_container_width=True, column_config={
        "médiane": st.column_config.NumberColumn("Médiane (TBE)", format="%.0f €"),
        "p25": st.column_config.NumberColumn("25e centile", format="%.0f €"),
        "p75": st.column_config.NumberColumn("75e centile", format="%.0f €"),
        "prix_neuf": st.column_config.NumberColumn("Prix neuf (réf.)", format="%.0f €"),
    })

# --------------------------------------------------------------------------- #
# Onglet 3 : annonces écartées
# --------------------------------------------------------------------------- #
with tab_excl:
    st.markdown("#### Pourquoi ces annonces ont été écartées")
    st.caption("Elles ne sont jamais proposées comme bonnes affaires et ne servent pas au calcul des prix de marché "
               "(sinon un faux prix à 1 € tirerait la médiane vers le bas).")
    import json
    reasons = {}
    for fj in excluded["flags_json"]:
        for f in json.loads(fj):
            if f["hard"] or f["weight"] >= 30:
                lab = f["label"].split(":")[0].split("(")[0].strip()
                reasons[lab] = reasons.get(lab, 0) + 1
    if reasons:
        rs = pd.Series(reasons).sort_values()
        bar = go.Figure(go.Bar(x=rs.values, y=rs.index, orientation="h", marker=dict(color=BLUE),
                               text=rs.values, textposition="outside", cliponaxis=False,
                               hovertemplate="%{y} : %{x} annonce(s)<extra></extra>"))
        bar.update_traces(marker_line_width=0)
        bar.update_layout(height=60 + 34 * len(rs), plot_bgcolor=SURFACE, paper_bgcolor="rgba(0,0,0,0)",
                          margin=dict(l=10, r=40, t=10, b=10), bargap=0.35,
                          font=dict(family="system-ui, -apple-system, Segoe UI, sans-serif", color=INK2, size=13),
                          xaxis=dict(visible=False), yaxis=dict(title=None))
        st.plotly_chart(bar, use_container_width=True, config={"displayModeBar": False})
    ex = excluded[["title", "search_name", "price", "real_price_mentioned", "risk_score", "flags_text",
                   "description", "url"]]
    st.dataframe(ex, hide_index=True, use_container_width=True, height=420, column_config={
        "title": "Annonce", "search_name": "Recherche",
        "price": st.column_config.NumberColumn("Prix affiché", format="%.2f €"),
        "real_price_mentioned": st.column_config.NumberColumn("Vrai prix (texte)", format="%.0f €"),
        "risk_score": st.column_config.ProgressColumn("Risque", min_value=0, max_value=100, format="%d"),
        "flags_text": st.column_config.TextColumn("Raisons", width="large"),
        "description": st.column_config.TextColumn("Description", width="large"),
        "url": st.column_config.LinkColumn("Lien", display_text="Ouvrir ↗"),
    })
    review = df[df["status"] == STATUS_REVIEW]
    if len(review):
        st.markdown("#### À vérifier avant d'acheter")
        st.dataframe(review[["title", "search_name", "price", "margin", "flags_text", "url"]], hide_index=True,
                     use_container_width=True, column_config={
                         "price": st.column_config.NumberColumn("Prix", format="%.2f €"),
                         "margin": st.column_config.NumberColumn("Marge", format="%.2f €"),
                         "flags_text": st.column_config.TextColumn("Signaux", width="large"),
                         "url": st.column_config.LinkColumn("Lien", display_text="Ouvrir ↗")})

# --------------------------------------------------------------------------- #
# Onglet 4 : calculateur
# --------------------------------------------------------------------------- #
with tab_calc:
    st.markdown("#### Calculer la marge d'un achat-revente")
    c1, c2 = st.columns([1, 1.3])
    with c1:
        p = st.number_input("Prix affiché de l'annonce", 0.0, 5000.0, 25.0, 1.0, format="%.2f")
        size = st.radio("Taille du colis", list(cfg.fees["shipping"].keys()), index=1, horizontal=True)
        resale = st.number_input("Prix auquel tu revends", 0.0, 5000.0, 55.0, 1.0, format="%.2f")
        soc = st.toggle("Cotisations micro-entrepreneur (12,3 %)", value=social, key="calc_soc")
        target = st.number_input("Marge visée", 0.0, 500.0, float(min_margin), 1.0, format="%.2f")
    cost = purchase_cost(p, size, cfg)
    m = compute_margin(p, resale, size, cfg, social_pct=0.123 if soc else 0.0, target_margin=target)
    with c2:
        rows = [
            ("Prix de l'article", -cost.price), ("Frais Vinted (0,70 € + 5 %)", -cost.buyer_fee),
            ("Livraison", -cost.shipping), ("= Coût total d'achat", -cost.total_cost),
            ("Prix de revente", resale),
            ("Cotisations sociales", -(resale * (0.123 if soc else 0))),
            ("Emballage & boost", -(cfg.resale["packaging_cost"] + cfg.resale["boost_cost"])),
            ("= Revente nette", m.resale_net), ("MARGE NETTE", m.margin),
        ]
        st.dataframe(pd.DataFrame(rows, columns=["Poste", "Montant"]), hide_index=True, use_container_width=True,
                     column_config={"Montant": st.column_config.NumberColumn(format="%.2f €")})
        a, b, c = st.columns(3)
        a.metric("Marge nette", eur(m.margin))
        b.metric("ROI", pct(m.roi))
        c.metric("Prix max à payer", eur(m.max_buy_price), help=f"Pour garder {eur(target)} de marge")

# --------------------------------------------------------------------------- #
# Onglet 5 : méthode
# --------------------------------------------------------------------------- #
with tab_method:
    st.markdown(f"""
#### Comment une annonce devient une « bonne affaire »

1. **Collecte** des annonces pour chaque recherche de la watchlist (`config.yaml`).
2. **Analyse du texte** (titre + description) — exclusion directe si :
   - **faux prix** : un prix de vente plus élevé est écrit dans le texte (« prix réel 90 € », « je la laisse à 85 € »), ou le prix affiché est symbolique (≤ 2 €) ;
   - **arnaque** : WhatsApp, PayPal, virement, téléphone, e-mail, lien externe… ;
   - **contrefaçon avouée** : replica, 1:1, copie, « pas authentique »… (« pas de fake » n'est *pas* compté) ;
   - **pas l'article** : boîte vide, photo, pour pièces ; **pas une vente** : réservé, recherche, échange uniquement.
   Les montants « acheté 120 € en boutique » sont reconnus comme **prix neuf**, pas comme faux prix.
3. **Prix de marché** : médiane des annonces comparables *saines* (hors faux prix, lots, tailles enfant, hors-sujet), ramenées à l'état « Très bon état ».
4. **Signaux statistiques** : prix < {cfg.thresholds['too_good_ratio']:.0%} de la médiane (< {cfg.thresholds['luxury_too_good_ratio']:.0%} pour les marques très contrefaites) → « trop beau pour être vrai ».
5. **Marge** = revente estimée (médiane × état × {cfg.resale['asking_to_sold_ratio']:.0%}, car les prix affichés ne sont pas les prix vendus) − emballage − cotisations − (prix + 0,70 € + 5 % + port).
6. **Score** /100 : décote vs marché, marge, ROI, décote vs neuf, liquidité de la catégorie, favoris, moins le risque.

**Limites** : les prix de marché sont des prix *demandés* ; le prix neuf vient d'un fichier de référence indicatif
(`data/reference_prices.csv`, à compléter) ; une annonce « ok » n'est pas une garantie d'authenticité —
regarde toujours les photos et le profil du vendeur.
""")
