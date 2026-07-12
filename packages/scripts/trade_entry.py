#!/usr/bin/env python3
"""
trade_entry.py — Registra un trade eseguito su Scalable in portfolio.json.

Aggiornamento PROVVISORIO: effetto immediato su report/digest. La fonte
autoritativa resta l'export CSV Scalable (import_scalable.py), che
ricostruisce le posizioni dalla storia completa e riassorbe ogni drift.

Uso:
    uv run python scripts/trade_entry.py buy  TICKER SHARES PREZZO_EUR
    uv run python scripts/trade_entry.py sell TICKER SHARES PREZZO_EUR

TICKER: ticker della posizione in portfolio.json (es. WDC, MU, CSPX.L)
        oppure parte del nome (es. "sandisk").
PREZZO_EUR: prezzo di esecuzione gettex in EUR.
"""

import json
import os
import sys
from datetime import datetime

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PACKAGES_DIR = os.path.dirname(SCRIPT_DIR)
PORTFOLIO_FILE = os.path.join(PACKAGES_DIR, "portfolio.json")


def find_position(positions: list[dict], key: str) -> dict | None:
    key_low = key.lower()
    # match esatto sul ticker
    for p in positions:
        if p.get("ticker", "").lower() == key_low:
            return p
    # match sul ticker senza suffisso (.L, .DE)
    for p in positions:
        base = p.get("ticker", "").split(".")[0].lower()
        if base and base == key_low:
            return p
    # match parziale sul nome
    matches = [p for p in positions if key_low in p.get("name", "").lower()]
    return matches[0] if len(matches) == 1 else None


def main():
    if len(sys.argv) != 5 or sys.argv[1] not in ("buy", "sell"):
        print(__doc__)
        sys.exit(1)

    action, key = sys.argv[1], sys.argv[2]
    try:
        shares = float(sys.argv[3].replace(",", "."))
        price = float(sys.argv[4].replace(",", "."))
    except ValueError:
        print(f"❌ Quantità/prezzo non validi: {sys.argv[3]} {sys.argv[4]}")
        sys.exit(1)
    if shares <= 0 or price <= 0:
        print("❌ Quantità e prezzo devono essere positivi.")
        sys.exit(1)

    with open(PORTFOLIO_FILE) as f:
        data = json.load(f)
    positions = data.get("positions", [])

    pos = find_position(positions, key)

    if action == "sell":
        if pos is None:
            print(f"❌ Posizione '{key}' non trovata in portfolio.json.")
            sys.exit(1)
        held = pos.get("shares", 0)
        if shares > held + 1e-6:
            print(f"❌ Vendi {shares} ma possiedi {held} di {pos['name']}.")
            sys.exit(1)
        pos["shares"] = round(held - shares, 4)
        realized = (price - pos.get("avg_entry", 0)) * shares
        if pos["shares"] <= 1e-4:
            positions.remove(pos)
            print(f"✅ {pos['name']}: posizione CHIUSA ({shares} @ {price}€).")
        else:
            print(f"✅ {pos['name']}: -{shares} @ {price}€ → restano {pos['shares']}.")
        print(f"   P&L realizzato (vs carico medio): {realized:+,.0f}€")
    else:  # buy
        if pos is None:
            print(f"❌ Posizione '{key}' non trovata. I nuovi strumenti vanno "
                  f"aggiunti via import_scalable.py (serve l'ISIN dal CSV).")
            sys.exit(1)
        held = pos.get("shares", 0)
        old_cost = held * pos.get("avg_entry", 0)
        pos["shares"] = round(held + shares, 4)
        pos["avg_entry"] = round((old_cost + shares * price) / pos["shares"], 2)
        pos["entry_currency"] = "EUR"
        print(f"✅ {pos['name']}: +{shares} @ {price}€ → {pos['shares']} "
              f"(carico medio {pos['avg_entry']}€).")

    data.setdefault("meta", {})["updated"] = datetime.now().strftime("%Y-%m-%d")
    data["meta"]["provisional"] = True  # in attesa di conferma da import CSV

    with open(PORTFOLIO_FILE, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")

    print("ℹ️  Aggiornamento provvisorio — riconcilia col prossimo export CSV "
          "(import_scalable.py).")


if __name__ == "__main__":
    main()
