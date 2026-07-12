#!/usr/bin/env python3
"""Import a Scalable Capital CSV export into the portfolio database.

Pipeline:
  1. sync_portfolio.py — FIFO cost-basis CSV → portfolio.json
  2. seed_db.py — portfolio.json → SQLite DB (WatchlistStock + Holding)

Usage:
    python scripts/import_scalable.py path/to/Scalable_Capital_Report.csv
"""

import os
import subprocess
import sys

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    csv_path = os.path.abspath(sys.argv[1])
    if not os.path.exists(csv_path):
        print(f"❌ File non trovato: {csv_path}")
        sys.exit(1)

    # Step 1: sync_portfolio.py (CSV → portfolio.json, FIFO)
    sync_script = os.path.join(SCRIPTS_DIR, "sync_portfolio.py")
    print("=" * 60)
    print("Step 1: sync_portfolio.py (FIFO cost basis)")
    print("=" * 60)
    r1 = subprocess.run([sys.executable, sync_script, csv_path])
    if r1.returncode != 0:
        print("❌ sync_portfolio.py failed")
        sys.exit(r1.returncode)

    # Step 2: seed_db.py (portfolio.json → DB)
    seed_script = os.path.join(SCRIPTS_DIR, "seed_db.py")
    print("\n" + "=" * 60)
    print("Step 2: seed_db.py (portfolio.json → SQLite DB)")
    print("=" * 60)
    r2 = subprocess.run([sys.executable, seed_script])
    if r2.returncode != 0:
        print("❌ seed_db.py failed")
        sys.exit(r2.returncode)

    print("\n✅ Import completato. Run `uv run python scripts/finance_report.py quick` per verificare.")


if __name__ == "__main__":
    main()
