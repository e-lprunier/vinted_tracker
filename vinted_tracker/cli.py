"""Ligne de commande — uniquement des annonces Vinted réelles.

    python -m vinted_tracker scan                       # un scan (Vinted direct, Apify en secours)
    python -m vinted_tracker scan --search "Nike Dunk Low" --search "Adidas Samba"
    python -m vinted_tracker watch --every 30 --push    # rescan toutes les 30 min + push GitHub
                                                        # -> l'app Streamlit en ligne se met à jour
"""
from __future__ import annotations

import argparse
import logging
import subprocess
import time
from datetime import datetime

import pandas as pd

from .config import ROOT, load_config
from .pipeline import run_scan
from .storage import PREVIOUS_PATH, SNAPSHOT_PATH, save_scan

log = logging.getLogger("vinted_tracker")


def _print_summary(df: pd.DataFrame, used: str, top: int):
    deals = df[df["is_deal"]]
    print(f"\n[{used}] {len(df)} annonces analysées · {len(deals)} bonnes affaires · "
          f"{(df['status'] == 'exclu').sum()} écartées (faux prix / arnaques / contrefaçons)\n")
    cols = ["search_name", "title", "price", "total_cost", "resale_price", "margin", "roi", "deal_score", "url"]
    with pd.option_context("display.max_colwidth", 40, "display.width", 220):
        print(deals[cols].head(top).to_string(index=False) if len(deals) else "Aucune bonne affaire ce coup-ci.")


def _git_push():
    rels = [str(pth.relative_to(ROOT)) for pth in (SNAPSHOT_PATH, PREVIOUS_PATH) if pth.exists()]
    cmds = [["git", "add", *rels],
            ["git", "commit", "-m", f"scan: {datetime.now():%Y-%m-%d %H:%M}"],
            ["git", "pull", "--rebase", "--autostash"],
            ["git", "push"]]
    for c in cmds:
        r = subprocess.run(c, cwd=ROOT, capture_output=True, text=True)
        if r.returncode != 0 and c[1] != "commit":   # "nothing to commit" n'est pas une erreur
            log.warning("git %s : %s", c[1], (r.stderr or r.stdout).strip()[:200])
            return False
    log.info("Snapshot poussé sur GitHub -> l'app Streamlit va se mettre à jour.")
    return True


def main(argv=None):
    p = argparse.ArgumentParser(prog="vinted_tracker")
    sub = p.add_subparsers(dest="cmd", required=True)
    for name, help_ in (("scan", "Un scan des annonces Vinted"),
                        ("watch", "Scans en boucle (annonces toujours à jour)")):
        s = sub.add_parser(name, help=help_)
        s.add_argument("--source", choices=["auto", "vinted", "apify"], default="auto")
        s.add_argument("--search", action="append", help="Limiter à une recherche (nom dans config.yaml)")
        s.add_argument("--config", default=None)
        s.add_argument("--top", type=int, default=15)
        s.add_argument("--push", action="store_true", help="git commit + push du snapshot après chaque scan")
        if name == "scan":
            s.add_argument("--no-save", action="store_true")
        else:
            s.add_argument("--every", type=int, default=30, help="minutes entre deux scans")
    args = p.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    cfg = load_config(args.config) if args.config else load_config()

    def one_scan(save=True):
        df, used = run_scan(cfg, args.source, args.search)
        _print_summary(df, used, args.top)
        if save:
            scan_id = save_scan(df, used)
            print(f"\nScan {scan_id} enregistré (data/latest_scan.parquet)")
            if args.push:
                _git_push()

    if args.cmd == "scan":
        one_scan(save=not args.no_save)
        return
    while True:   # watch
        try:
            one_scan()
        except Exception as e:
            log.error("Scan échoué : %s", e)
        log.info("Prochain scan dans %d min (Ctrl+C pour arrêter)", args.every)
        time.sleep(args.every * 60)


if __name__ == "__main__":
    main()
