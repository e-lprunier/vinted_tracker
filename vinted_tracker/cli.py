"""Ligne de commande.

    python -m vinted_tracker scan --source demo          # données fictives
    python -m vinted_tracker scan --source vinted        # API Vinted (depuis ton PC)
    python -m vinted_tracker scan --source apify         # via Apify (APIFY_TOKEN)
    python -m vinted_tracker scan --source vinted --search "Nike Dunk Low" --search "Adidas Samba"
"""
from __future__ import annotations

import argparse
import logging

from .config import load_config
from .pipeline import run_scan
from .storage import save_scan


def main(argv=None):
    p = argparse.ArgumentParser(prog="vinted_tracker")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("scan", help="Scanner les annonces et détecter les bonnes affaires")
    s.add_argument("--source", choices=["demo", "vinted", "apify"], default="demo")
    s.add_argument("--search", action="append", help="Limiter à une recherche (nom dans config.yaml)")
    s.add_argument("--config", default=None)
    s.add_argument("--no-save", action="store_true")
    s.add_argument("--top", type=int, default=15)
    args = p.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    cfg = load_config(args.config) if args.config else load_config()
    df = run_scan(cfg, args.source, args.search)

    deals = df[df["is_deal"]]
    print(f"\n{len(df)} annonces analysées · {len(deals)} bonnes affaires · "
          f"{(df['status'] == 'exclu').sum()} exclues (faux prix / arnaques / contrefaçons)\n")
    cols = ["search_name", "title", "price", "total_cost", "resale_price", "margin", "roi", "deal_score", "url"]
    with __import__("pandas").option_context("display.max_colwidth", 40, "display.width", 200):
        print(deals[cols].head(args.top).to_string(index=False))
    if not args.no_save:
        scan_id = save_scan(df, args.source)
        print(f"\nScan {scan_id} enregistré (data/latest_scan.parquet + data/vinted.db)")


if __name__ == "__main__":
    main()
