#!/usr/bin/env python3
"""
transition_checker.py — Silent watchdog for stock divestment plan.

Monitors positions earmarked for divestment (target 0%) against:
  - RSI(14) > 60 → strength sell signal
  - Backstop date (ticker-specific or default) → forced liquidation deadline

Rules (ADR-0002):
  RSI(14) > 60  → 🔔 VENDI TRANCHE {ticker} — RSI {value:.0f} > 60
  Backstop ≤14d → ⏰ BACKSTOP {ticker} — {n} giorni al {date}
  Backstop pass  → 🚨 BACKSTOP SCADUTO {ticker} — vendi ora
  Fetch error    → ⚠️ {ticker}: dati non disponibili

Silent by default (empty stdout = nothing to report). Use --verbose to show all status.
"""

import json
import os
import sys
from datetime import datetime, date, timedelta
from typing import Optional, Union

from finance_core.market import rsi, fetch_closes

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if os.path.basename(SCRIPT_DIR) == "scripts":
    SCRIPT_DIR = os.path.dirname(SCRIPT_DIR)

PORTFOLIO_FILE = os.path.join(SCRIPT_DIR, "portfolio.json")
TARGET_FILE = os.path.join(SCRIPT_DIR, "config", "target_allocation.json")


def load_portfolio() -> list[dict]:
    """Load positions from portfolio.json."""
    with open(PORTFOLIO_FILE) as f:
        data = json.load(f)
    return data.get("positions", [])


def load_targets_and_backstops() -> tuple[dict, dict]:
    """Load target allocations and backstop dates."""
    with open(TARGET_FILE) as f:
        data = json.load(f)
    targets = data.get("target", {})
    backstops = data.get("backstops", {})
    return targets, backstops


def get_in_scope_tickers(positions: list[dict], targets: dict) -> list[dict]:
    """
    Filter positions that are earmarked for divestment:
    - shares > 0
    - type == 'stock'
    - not present in targets OR target == 0%
    Returns list of dicts with: ticker, shares, position_data
    """
    in_scope = []
    for pos in positions:
        if pos.get("shares", 0) <= 0 or pos.get("type") != "stock":
            continue
        ticker = pos.get("ticker", "").strip()
        if not ticker:
            continue
        target_pct = targets.get(ticker, 0.0)  # implicit 0% if not in target
        if target_pct == 0:
            in_scope.append({
                "ticker": ticker,
                "shares": pos["shares"],
                "data": pos,
            })
    return in_scope


def get_backstop_date(ticker: str, backstops: dict) -> date:
    """Get backstop date for a ticker, defaulting to 'default' key."""
    if ticker in backstops:
        return datetime.strptime(backstops[ticker], "%Y-%m-%d").date()
    return datetime.strptime(backstops.get("default", "2026-10-31"), "%Y-%m-%d").date()


def check_ticker(ticker: str, backstops: dict, close: "pd.Series | None",
                  verbose: bool = False) -> list[str]:
    """
    Check a single ticker and return output lines (may be empty).
    close: pre-fetched price series (from batch download), or None if unavailable.
    """
    lines = []

    # Compute RSI from pre-fetched data
    if close is None or len(close) < 2:
        lines.append(f"⚠️ {ticker}: dati non disponibili")
        return lines

    rsi_val = float(rsi(close, 14).iloc[-1])

    # Backstop date
    backstop = get_backstop_date(ticker, backstops)
    today = date.today()
    days_to_backstop = (backstop - today).days

    # Verbose mode: always show status
    if verbose:
        if days_to_backstop < 0:
            lines.append(f"📊 {ticker} — RSI {rsi_val:.0f}, BACKSTOP SCADUTO {days_to_backstop * -1} giorni fa ({backstop})")
        else:
            lines.append(f"📊 {ticker} — RSI {rsi_val:.0f}, backstop tra {days_to_backstop} giorni ({backstop})")

    # Check conditions (silent mode: only output if triggered)
    if days_to_backstop < 0:
        lines.append(f"🚨 BACKSTOP SCADUTO {ticker} — vendi ora")
    elif 0 <= days_to_backstop <= 14:
        lines.append(f"⏰ BACKSTOP {ticker} — {days_to_backstop} giorni al {backstop.strftime('%Y-%m-%d')}: vendi il residuo a prescindere")
    elif rsi_val > 60:
        lines.append(f"🔔 VENDI TRANCHE {ticker} — RSI {rsi_val:.0f} > 60 (vendita sulla forza, ADR-0002)")

    return lines


def main():
    verbose = "--verbose" in sys.argv

    # Load data
    positions = load_portfolio()
    targets, backstops = load_targets_and_backstops()
    in_scope = get_in_scope_tickers(positions, targets)

    if not in_scope:
        if verbose:
            print("✅ Nessuna posizione stock in dismissione")
        return

    # Batch download all in-scope tickers at once
    tickers = [item["ticker"] for item in in_scope]
    all_closes = fetch_closes(tickers, period="3mo")

    # Check each ticker with pre-fetched data
    all_lines = []
    for item in in_scope:
        close = all_closes.get(item["ticker"])
        lines = check_ticker(item["ticker"], backstops, close, verbose)
        all_lines.extend(lines)

    # Output
    for line in all_lines:
        print(line)


if __name__ == "__main__":
    main()
