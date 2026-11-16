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


def save_scan(df: pd.DataFrame, source: str, db_path: Path = DB_PATH,
              snapshot_path: Path = SNAPSHOT_PATH) -> str:
    scan_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    df = df.copy()
    df["scan_id"], df["scan_source"] = scan_id, source

    snapshot_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(snapshot_path, index=False)

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


def seen_ids(db_path: Path = DB_PATH) -> set[str]:
    """IDs déjà vus lors des scans précédents (pour marquer les nouveautés)."""
    if not db_path.exists():
        return set()
    try:
        with sqlite3.connect(db_path) as con:
            return {r[0] for r in con.execute("SELECT DISTINCT id FROM analyses")}
    except sqlite3.Error:
        return set()
