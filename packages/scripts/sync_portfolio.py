#!/usr/bin/env python3
"""
sync_portfolio.py — Aggiorna portfolio.json dal CSV transazioni Scalable Capital.

Legge un file CSV esportato da Scalable Capital, calcola le posizioni aperte
(Buy - Sell per ISIN, entry price medio ponderato), e aggiorna portfolio.json.

Uso:
  python3 sync_portfolio.py <scalable_export.csv>
  python3 sync_portfolio.py <scalable_export.csv> --dry-run   # mostra solo le modifiche
"""

import csv
import json
import os
import re
import sys
from collections import OrderedDict
from datetime import datetime
from typing import Optional

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if os.path.basename(SCRIPT_DIR) == "scripts":
    SCRIPT_DIR = os.path.dirname(SCRIPT_DIR)  # go up one level for packages root
PORTFOLIO_FILE = os.path.join(SCRIPT_DIR, 'portfolio.json')

# ── Tipi di asset validi Scalable Capital ──
_VALID_ASSET_TYPES = {"security", "etf", "etn"}

# ── Mappatura ISIN → tipo posizione nota ──
# Se il CSV non specifica l'asset type, usiamo questa per i tipi noti
_KNOWN_ISIN_TYPES = {
    "IE00B4ND3602": "etf",   # iShares Physical Gold ETC
    "IE00B4NCWG09": "etf",   # iShares Physical Silver ETC
    "IE00B5BMR087": "etf",   # iShares Core S&P 500
    "IE00BKM4GZ66": "etf",   # iShares MSCI EM IMI
    "LU2903252349": "etf",   # Xtrackers MSCI AC World
    "IE00BF4RFH31": "etf",   # iShares MSCI World SC
    "IE00B43HR379": "etf",   # iShares S&P 500 Health Care
    "IE000I8KRLL9": "etf",   # iShares MSCI Global Semiconductors
    "US5951121038": "stock",  # Micron
    "US0079031078": "stock",  # AMD
    "US80004C2008": "stock",  # SanDisk
    "US5738741041": "stock",  # Marvell
    "US9581021055": "stock",  # Western Digital
    "US1999081045": "stock",  # Comfort Systems USA
}

# ── Mappatura ISIN → ticker Yahoo Finance nota ──
_KNOWN_ISIN_TICKERS = {
    "US5951121038": "MU",
    "US0079031078": "AMD",
    "US80004C2008": "SNDK",
    "US5738741041": "MRVL",
    "US9581021055": "WDC",
    "US1999081045": "FIX",
    "IE00B4ND3602": "IGLN.L",
    "IE00B4NCWG09": "ISLN.L",
    "IE00B5BMR087": "CSPX.L",
    "IE00BKM4GZ66": "EMIM.L",
    "LU2903252349": "",       # manual_only (Xtrackers)
    "IE00BF4RFH31": "WSML.L",
    "IE00B43HR379": "IUHC.L",
    "IE000I8KRLL9": "SEC0.DE",
}

# ── Mappatura ISIN → nome posizione ──
_KNOWN_ISIN_NAMES = {
    "US5951121038": "Micron Technology",
    "US0079031078": "Advanced Micro Devices",
    "US80004C2008": "SanDisk Corp",
    "US5738741041": "Marvell Technology",
    "US9581021055": "Western Digital",
    "US1999081045": "Comfort Systems USA",
    "IE00B4ND3602": "iShares Physical Gold ETC",
    "IE00B4NCWG09": "iShares Physical Silver ETC (Acc)",
    "IE00B5BMR087": "iShares Core S&P 500 (Acc)",
    "IE00BKM4GZ66": "iShares Core MSCI Emerging Markets IMI (Acc)",
    "LU2903252349": "Scalable MSCI AC World Xtrackers (Acc)",
    "IE00BF4RFH31": "iShares MSCI World Small Cap (Acc)",
    "IE00B43HR379": "iShares S&P 500 Health Care Sector (Acc)",
    "IE000I8KRLL9": "iShares MSCI Global Semiconductors (Acc)",
}

# ── Mappatura ISIN → currency Yahoo Finance ──
# NB: verificate su yfinance fast_info 2026-07-12 — i ticker .L quotano
# quasi tutti in USD (solo EMIM.L in GBp), NON in GBP.
_KNOWN_ISIN_CURRENCIES = {
    "US5951121038": "USD",
    "US0079031078": "USD",
    "US80004C2008": "USD",
    "US5738741041": "USD",
    "US9581021055": "USD",
    "US1999081045": "USD",
    "IE00B4ND3602": "USD",
    "IE00B4NCWG09": "USD",
    "IE00B5BMR087": "USD",
    "IE00BKM4GZ66": "GBp",
    "LU2903252349": "EUR",
    "IE00BF4RFH31": "USD",
    "IE00B43HR379": "USD",
    "IE000I8KRLL9": "EUR",
}

# ── Mappatura ISIN → listing EUR (Xetra ≈ gettex/Scalable) ──
# Usato da portfolio_manager per prezzi in EUR senza conversione FX.
_KNOWN_ISIN_EUR_TICKERS = {
    "IE00B5BMR087": "SXR8.DE",   # CSPX
    "IE00BKM4GZ66": "IS3N.DE",   # EMIM
    "IE00BF4RFH31": "IUSN.DE",   # WSML
    "IE00B43HR379": "QDVG.DE",   # IUHC
    "IE00B4ND3602": "PPFB.DE",   # IGLN
    "US5951121038": "MTE.DE",    # MU
    "US0079031078": "AMD.DE",    # AMD
    "US9581021055": "WDC.DE",    # WDC
    "LU2903252349": "SCWX.DE",   # Scalable MSCI AC World
}


def parse_eu_number(s: str) -> float:
    """
    Parsa un numero in formato europeo (1.234,56 o 1.311) o inglese (1234.56).

    Scalable Capital exporta SEMPRE in formato europeo:
    - virgola = separatore decimale (sempre presente nei prezzi decimali)
    - punto = separatore migliaia
    - Se c'è virgola -> formato europeo classico: rimuovi punti, virgola→punto
    - Se NON c'è virgola ma c'è un punto -> è un separatore migliaia EU (es. "1.311" = 1311)
      (perché i prezzi hanno sempre la virgola se hanno decimali)
    - Altrimenti -> parse diretto
    """
    s = s.strip()
    if not s or s in ("-", "\u2014", "", "−"):
        return 0.0
    s = s.replace("−", "-")
    if "," in s:
        # Formato europeo classico: "1.234,56" → "1234.56"
        s = s.replace(".", "").replace(",", ".")
    elif "." in s:
        # Punto senza virgola -> è separatore migliaia (es. "1.311" = 1311)
        # I prezzi usano sempre la virgola per i decimali, quindi un punto
        # da solo è necessariamente un separatore migliaia
        s = s.replace(".", "")
    # altrimenti: numero semplice, parse diretto
    try:
        return float(s)
    except ValueError:
        return 0.0


def normalize_row(row: dict) -> dict:
    """Normalizza le chiavi del CSV: lowercase, strip, spazi → underscore."""
    return {k.strip().lower().replace(" ", "_"): v.strip() for k, v in row.items()}


def read_portfolio() -> dict:
    """Legge portfolio.json attuale."""
    if not os.path.exists(PORTFOLIO_FILE):
        return {"meta": {"broker": "Scalable Capital", "currency": "EUR", "updated": ""}, "positions": []}

    with open(PORTFOLIO_FILE) as f:
        return json.load(f)


def resolve_ticker(isin: str, description: str) -> str:
    """Risolve ticker Yahoo Finance per un ISIN.

    Usa prima le mappature note, poi un fallback semplice.
    """
    if isin in _KNOWN_ISIN_TICKERS:
        return _KNOWN_ISIN_TICKERS[isin]

    # Fallback: genera ticker dal nome
    words = description.upper().split()
    ticker = "".join(w[:4] for w in words[:2])[:8] + ".DE"
    return ticker


def is_manual_only(isin: str) -> bool:
    """Restituisce True se la posizione è tracciata manualmente (senza ticker)."""
    return isin in _KNOWN_ISIN_TICKERS and _KNOWN_ISIN_TICKERS[isin] == ""


def parse_scalable_csv(filepath: str) -> list[dict]:
    """Legge il CSV di Scalable Capital e restituisce le transazioni valide."""
    with open(filepath, "rb") as f:
        raw = f.read()

    # Detect encoding: prova utf-8-sig prima, poi utf-8, poi latin-1
    text = raw.decode("utf-8-sig")
    reader = csv.DictReader(text.splitlines(), delimiter=";")
    rows = [normalize_row(r) for r in reader]

    if not rows:
        print("❌ CSV vuoto o illeggibile.")
        sys.exit(1)

    # Filtra solo transazioni valide
    valid = []
    discarded = 0
    unknown_asset_types = set()
    for r in rows:
        status = r.get("status", "").lower()
        asset_type = r.get("assettype", r.get("asset_type", "")).lower()
        tx_type = r.get("type", "").lower()
        isin = r.get("isin", "").strip()

        if status != "executed":
            discarded += 1
            continue
        if asset_type not in _VALID_ASSET_TYPES:
            discarded += 1
            if asset_type:
                unknown_asset_types.add(asset_type)
            continue
        if tx_type not in ("buy", "sell"):
            discarded += 1
            continue
        if not isin:
            discarded += 1
            continue

        valid.append(r)

    if discarded:
        warn = f"⚠️ {discarded} righe scartate"
        if unknown_asset_types:
            warn += f" (assetType non riconosciuti: {', '.join(sorted(unknown_asset_types))})"
        print(warn)

    return valid


def compute_fifo_cost_basis(transactions: list[dict]) -> list[dict]:
    """Aggrega le transazioni Buy/Sell per ISIN e calcola le posizioni aperte.

    Usa FIFO (First In, First Out) per il costo fiscale delle azioni residue:
    - Le vendite consumano i lotti più vecchi prima
    - Il prezzo di carico = media ponderata dei lotti rimanenti
    """
    # Ordina cronologicamente (FIFO richiede oldest-first)
    sorted_tx = sorted(
        transactions,
        key=lambda t: (t.get("date_time", t.get("date", "")), t.get("time", "")),
    )
    # Per ISIN: accumula buys come lotti [(shares, price, date)]
    positions = OrderedDict()  # isin -> dict with lots

    for t in sorted_tx:
        isin = t["isin"]
        tx_type = t["type"].lower()
        description = t.get("description", "").strip()

        try:
            shares = parse_eu_number(t.get("shares", "0"))
            price = parse_eu_number(t.get("price", "0"))
        except (ValueError, KeyError):
            continue

        raw_date = t.get("date_time", t.get("date", "")).strip()
        tx_date = raw_date[:10] if raw_date else ""

        if isin not in positions:
            positions[isin] = {
                "isin": isin,
                "description": description,
                "lots": [],  # [(shares, price, date)]
                "last_date": tx_date,
            }

        if tx_date > positions[isin]["last_date"]:
            positions[isin]["last_date"] = tx_date

        if tx_type == "buy":
            positions[isin]["lots"].append([shares, price, tx_date])
        elif tx_type == "sell":
            remaining_to_sell = shares
            while remaining_to_sell > 0.001 and positions[isin]["lots"]:
                oldest_lot = positions[isin]["lots"][0]
                if oldest_lot[0] <= remaining_to_sell:
                    # Consume this entire lot
                    remaining_to_sell -= oldest_lot[0]
                    positions[isin]["lots"].pop(0)
                else:
                    # Partially consume this lot
                    oldest_lot[0] -= remaining_to_sell
                    remaining_to_sell = 0

    # Calcola posizioni finali
    result = []
    for isin, data in positions.items():
        remaining_shares = sum(lot[0] for lot in data["lots"])
        if remaining_shares <= 0.001:
            continue

        total_cost = sum(lot[0] * lot[1] for lot in data["lots"])
        avg_entry = round(total_cost / remaining_shares, 2) if remaining_shares > 0 else 0.0
        remaining_shares = round(remaining_shares, 4)

        ticker = resolve_ticker(isin, data["description"])
        name = _KNOWN_ISIN_NAMES.get(isin, data["description"])
        currency = _KNOWN_ISIN_CURRENCIES.get(isin, "EUR")
        pos_type = _KNOWN_ISIN_TYPES.get(isin, "stock")
        manual = is_manual_only(isin)

        pos = {
            "name": name,
            "isin": isin,
            "ticker": ticker if not manual else "",
            "shares": remaining_shares,
            "avg_entry": avg_entry,
            # I prezzi di esecuzione Scalable/gettex sono sempre in EUR
            "entry_currency": "EUR",
            "type": pos_type,
        }

        eur_ticker = _KNOWN_ISIN_EUR_TICKERS.get(isin)
        if eur_ticker:
            pos["eur_ticker"] = eur_ticker

        if not ticker:
            pos["yf_currency"] = currency
            pos["manual_only"] = True
        else:
            pos["yf_currency"] = currency

        result.append(pos)

    return result


def main():
    dry_run = "--dry-run" in sys.argv
    prune = "--prune" in sys.argv
    args = [a for a in sys.argv[1:] if a not in ("--dry-run", "--prune")]

    if not args:
        print("Uso: python3 sync_portfolio.py <scalable_export.csv> [--dry-run] [--prune]")
        print("  --dry-run: mostra le modifiche senza salvare")
        print("  --prune: rimuovi posizioni non presenti nel CSV (default: preserva)")
        sys.exit(1)

    csv_path = args[0]
    if not os.path.exists(csv_path):
        print(f"❌ File non trovato: {csv_path}")
        sys.exit(1)

    print(f"📂 Leggo: {csv_path}")
    transactions = parse_scalable_csv(csv_path)
    print(f"   {len(transactions)} transazioni valide trovate")

    if not transactions:
        print("❌ Nessuna transazione valida nel CSV.")
        sys.exit(1)

    positions = compute_fifo_cost_basis(transactions)
    print(f"   {len(positions)} posizioni aperte calcolate")

    # Leggi portfolio.json attuale
    portfolio = read_portfolio()
    old_positions = {p["isin"]: p for p in portfolio.get("positions", [])}

    # Confronta
    changes = []
    new_positions = []
    for pos in positions:
        isin = pos["isin"]
        old = old_positions.get(isin)

        if old:
            # Preserva metadati custom non ricostruibili dal CSV
            if old.get("eur_ticker") and not pos.get("eur_ticker"):
                pos["eur_ticker"] = old["eur_ticker"]

            old_shares = old.get("shares", 0)
            old_entry = old.get("avg_entry", 0)
            new_shares = pos["shares"]
            new_entry = pos["avg_entry"]

            if abs(old_shares - new_shares) > 0.001 or abs(old_entry - new_entry) > 0.01:
                changes.append(f"   📝 {pos['name']} ({isin}): {old_shares}@{old_entry}€ → {new_shares}@{new_entry}€")
        else:
            changes.append(f"   🆕 {pos['name']} ({isin}): {pos['shares']}@{pos['avg_entry']}€")

        new_positions.append(pos)

    # Posizioni rimosse (erano in old ma non in new) — preserva a meno di --prune
    new_isins = {p["isin"] for p in new_positions}
    preserved = 0
    for isin, old in old_positions.items():
        if isin not in new_isins:
            if not prune:
                new_positions.append(old)
                changes.append(f"   ⚠️ {old['name']} ({isin}): non presente nel CSV — mantenuta (usa --prune per rimuoverla)")
                preserved += 1
            else:
                changes.append(f"   ❌ {old['name']} ({isin}): posizione chiusa/rimossa")

    if not changes:
        print("✅ Nessuna modifica — posizioni identiche.")
        if preserved:
            print(f"   ({preserved} posizioni preservate)")
        return

    print("\n📋 Modifiche rilevate:")
    for c in changes:
        print(c)
    if preserved:
        print(f"\n🔒 {preserved} posizioni preservate (non nel CSV)")

    if dry_run:
        print("\n🔍 Dry-run — nessun file modificato.")
        return

    # Genera nuovo portfolio.json
    updated = {
        "meta": {
            "broker": "Scalable Capital",
            "currency": "EUR",
            "updated": datetime.now().strftime("%Y-%m-%d"),
        },
        "positions": new_positions,
    }

    with open(PORTFOLIO_FILE, "w") as f:
        json.dump(updated, f, indent=2, ensure_ascii=False)
        f.write("\n")

    print(f"\n✅ portfolio.json aggiornato ({len(new_positions)} posizioni).")


if __name__ == "__main__":
    main()
