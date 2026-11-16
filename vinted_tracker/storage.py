"""Stockage des scans : SQLite (historique local) + snapshot Parquet (lu par le dashboard).

Le fichier `data/latest_scan.parquet` est celui que lit l'app Streamlit. En
déploiement Streamlit Cloud, il est mis à jour par le workflow GitHub Actions
(ou par un `python -m vinted_tracker scan` local suivi d'un git push).
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from .config import ROOT

DB_PATH = ROOT / "data" / "vinted.db"
SNAPSHOT_PATH = ROOT / "data" / "latest_scan.parquet"
PREVIOUS_PATH = ROOT / "data" / "previous_scan.parquet"
FIRST_SEEN_PATH = ROOT / "data" / "first_seen.parquet"   # id -> 1re apparition (Vinted ne donne plus la date)


def save_scan(df: pd.DataFrame, source: str, db_path: Path = DB_PATH,
              snapshot_path: Path = SNAPSHOT_PATH) -> str:
    scan_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    df = df.copy()
    df["scan_id"], df["scan_source"] = scan_id, source

    snapshot_path.parent.mkdir(parents=True, exist_ok=True)
    if snapshot_path.exists():   # on garde le scan précédent pour repérer les nouvelles annonces
        (snapshot_path.parent / PREVIOUS_PATH.name).write_bytes(snapshot_path.read_bytes())
    if "first_seen" in df:
        df["first_seen"] = df["first_seen"].astype(str)
    df.to_parquet(snapshot_path, index=False)
    update_first_seen(df["id"], path=snapshot_path.parent / FIRST_SEEN_PATH.name)

    with sqlite3.connect(db_path) as con:
        df.to_sql("analyses", con, if_exists="append", index=False)
        con.execute("CREATE TABLE IF NOT EXISTS scans (scan_id TEXT PRIMARY KEY, source TEXT, n INTEGER, "
                    "n_deals INTEGER, n_excluded INTEGER)")
        con.execute("INSERT OR REPLACE INTO scans VALUES (?,?,?,?,?)",
                    (scan_id, source, len(df), int(df["is_deal"].sum()), int((df["status"] == "exclu").sum())))
    return scan_id


def load_latest(snapshot_path: Path = SNAPSHOT_PATH) -> pd.DataFrame | None:
    if snapshot_path.exists():
        return pd.read_parquet(snapshot_path)
    return None


def load_first_seen(path: Path = FIRST_SEEN_PATH) -> dict[str, dict]:
    """id -> {"ts": 1re apparition ISO, "real": âge réel connu ?}.

    Les annonces déjà là au tout premier scan ont un âge inconnu (real=False) : elles
    étaient peut-être en ligne depuis des jours. Seules celles apparues ensuite ont un âge fiable.
    """
    if not path.exists():
        return {}
    try:
        d = pd.read_parquet(path)
        real = d["real"] if "real" in d else pd.Series(False, index=d.index)
        return {str(i): {"ts": str(t), "real": bool(r)} for i, t, r in zip(d["id"], d["first_seen"], real)}
    except Exception:
        return {}


def update_first_seen(ids, path: Path = FIRST_SEEN_PATH, keep_days: int = 30) -> None:
    """Ajoute les annonces jamais vues (horodatées maintenant) et oublie celles de plus de 30 jours."""
    now = pd.Timestamp.now(tz="UTC")
    known = load_first_seen(path)
    has_history = bool(known)
    for i in map(str, ids):
        known.setdefault(i, {"ts": now.isoformat(), "real": has_history})
    d = pd.DataFrame({"id": list(known),
                      "first_seen": pd.to_datetime([v["ts"] for v in known.values()], utc=True, format="ISO8601"),
                      "real": [v["real"] for v in known.values()]})
    d = d[d["first_seen"] >= now - pd.Timedelta(days=keep_days)]
    path.parent.mkdir(parents=True, exist_ok=True)
    d.assign(first_seen=d["first_seen"].map(lambda t: t.isoformat())).to_parquet(path, index=False)


def previous_ids(path: Path = PREVIOUS_PATH) -> set[str]:
    """IDs présents au scan précédent (tout le reste est une nouvelle annonce)."""
    if not path.exists():
        return set()
    try:
        return set(pd.read_parquet(path, columns=["id"])["id"].astype(str))
    except Exception:
        return set()


def seen_ids(db_path: Path = DB_PATH) -> set[str]:
    """IDs déjà vus lors des scans précédents (pour marquer les nouveautés)."""
    if not db_path.exists():
        return set()
    try:
        with sqlite3.connect(db_path) as con:
            return {r[0] for r in con.execute("SELECT DISTINCT id FROM analyses")}
    except sqlite3.Error:
        return set()
