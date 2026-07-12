#!/usr/bin/env python3
"""
portfolio_manager.py — Portfolio Management Agent.

Tasks:
  - Legge portfolio.json + swing_state.json per la visione completa
  - Calcola allocazione % per ogni posizione
  - Confronta allocazione reale vs target
  - Traccia P&L per posizione e totale
  - Propone ribilanciamenti quando una posizione devia >20% dal target
  - Salva snapshot mensile per confronto P&L
"""

import json
import os
import sys
from datetime import datetime, date, timedelta
from pathlib import Path

import yfinance as yf
import pandas as pd

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if os.path.basename(SCRIPT_DIR) == "scripts":
    SCRIPT_DIR = os.path.dirname(SCRIPT_DIR)

DATA_DIR = os.path.join(SCRIPT_DIR, "data")
os.makedirs(DATA_DIR, exist_ok=True)

CONFIG_DIR = os.path.join(SCRIPT_DIR, "config")

PORTFOLIO_PATH = os.path.join(SCRIPT_DIR, "portfolio.json")
SWING_STATE_PATH = os.path.join(SCRIPT_DIR, "data", "swing_state.json")
PM_STATE_PATH = os.path.join(DATA_DIR, "pm_state.json")  # our snapshots
TARGET_ALLOCATION_PATH = os.path.join(CONFIG_DIR, "target_allocation.json")

EUR_RATE = 0.92  # approximate USD→EUR
GBP_RATE = 1.19  # approximate GBP→EUR (used for GBp too)
GBP_TO_EUR = 1.19

# ── Target allocation ──
# These are the "ideal" splits the user wants
# For the swing trading portion (10k), MU/AMD = 50/50
# For the core portfolio, we monitor but don't rebalance aggressively
TARGET_SWING = {"MU": 0.50, "AMD": 0.50}
REBALANCE_THRESHOLD = 0.20  # 20% deviation from target triggers suggestion


def load_portfolio() -> dict:
    if not os.path.exists(PORTFOLIO_PATH):
        return {"positions": []}
    with open(PORTFOLIO_PATH) as f:
        return json.load(f)


def load_swing_state() -> dict:
    if os.path.exists(SWING_STATE_PATH):
        with open(SWING_STATE_PATH) as f:
            return json.load(f)
    return {"capital": 10000, "cash": 10000, "positions": {}}


def load_target_allocation() -> dict:
    """Load the ETF-only migration target allocation (committed config, not runtime state)."""
    if os.path.exists(TARGET_ALLOCATION_PATH):
        with open(TARGET_ALLOCATION_PATH) as f:
            return json.load(f).get("target", {})
    return {}


def load_pm_state() -> dict:
    if os.path.exists(PM_STATE_PATH):
        with open(PM_STATE_PATH) as f:
            return json.load(f)
    return {"snapshots": [], "peak_value": 0, "peak_date": None}


def save_pm_state(state: dict):
    with open(PM_STATE_PATH, "w") as f:
        json.dump(state, f, indent=2)


def fetch_prices(tickers: list[str]) -> dict:
    """Fetch current prices for a list of tickers. Returns {ticker: price_in_EUR}."""
    if not tickers:
        return {}
    prices = {}
    for t in tickers:
        try:
            ticker = yf.Ticker(t)
            info = ticker.info or {}
            price = info.get("currentPrice") or info.get("regularMarketPrice") or info.get("previousClose")
            if price:
                prices[t] = float(price)
            else:
                # fallback: download 1d
                df = yf.download(t, period="1d", interval="1d", auto_adjust=True)
                if not df.empty:
                    col = df["Close"]
                    if isinstance(col, pd.DataFrame):
                        col = col[t]
                    prices[t] = float(col.iloc[-1])
        except:
            pass
    return prices


def get_price_single(ticker: str) -> float | None:
    """Get price for a single ticker, with EUR conversion hint."""
    try:
        t = yf.Ticker(ticker)
        info = t.info or {}
        price = info.get("currentPrice") or info.get("regularMarketPrice") or info.get("previousClose")
        if price:
            return float(price)
        df = yf.download(ticker, period="1d", interval="1d", auto_adjust=True)
        if not df.empty:
            col = df["Close"]
            if isinstance(col, pd.DataFrame):
                col = col[ticker]
            return float(col.iloc[-1])
    except:
        pass
    return None


def convert_to_eur(price: float, currency: str) -> float:
    """Convert price to EUR for display."""
    if currency in ("EUR",):
        return price
    if currency == "GBp":
        # GBp → GBP (÷100) → EUR
        return (price / 100) * GBP_TO_EUR
    if currency == "GBP":
        return price * GBP_TO_EUR
    # USD → EUR
    return price * EUR_RATE


# ── Portfolio Analysis ──

def analyze_portfolio() -> dict:
    """Full portfolio analysis."""
    portfolio = load_portfolio()
    swing = load_swing_state()
    pm = load_pm_state()

    positions = portfolio.get("positions", [])
    tickers = [p.get("ticker") for p in positions if p.get("ticker")]

    prices = fetch_prices(tickers)

    result = {
        "total_value": 0.0,
        "total_cost": 0.0,
        "total_pnl_abs": 0.0,
        "total_pnl_pct": 0.0,
        "positions": [],
        "swing": {
            "cash": swing.get("cash", 0),
            "capital": swing.get("capital", 10000),
            "positions": {},
            "active_pnl": 0.0,
            "active_pnl_pct": 0.0,
        },
        "allocation": {},
        "rebalance_suggestions": [],
        "alerts": [],
    }

    total_value = 0.0
    total_cost = 0.0

    # Process each portfolio position
    for pos in positions:
        name = pos.get("name", "?")
        ticker = pos.get("ticker", "")
        shares = pos.get("shares", 0)
        avg_entry = pos.get("avg_entry", 0)
        pos_type = pos.get("type", "stock")
        currency = pos.get("yf_currency", "USD")

        cost = shares * avg_entry  # in original currency
        cost_eur = convert_to_eur(cost, currency)

        current_price = None
        if ticker:
            current_price = prices.get(ticker) or get_price_single(ticker)

        if current_price:
            market_value = shares * current_price
            market_value_eur = convert_to_eur(market_value, currency)
        else:
            # Fallback: use cost for manual-only positions
            market_value_eur = cost_eur
            current_price = avg_entry

        pnl_abs = market_value_eur - cost_eur
        pnl_pct = ((pnl_abs / cost_eur) * 100) if cost_eur else 0

        total_value += market_value_eur
        total_cost += cost_eur

        pos_entry = {
            "name": name,
            "ticker": ticker,
            "type": pos_type,
            "shares": shares,
            "avg_entry": avg_entry,
            "current_price": current_price,
            "cost_eur": round(cost_eur, 2),
            "value_eur": round(market_value_eur, 2),
            "pnl_abs": round(pnl_abs, 2),
            "pnl_pct": round(pnl_pct, 2),
            "weight_pct": 0.0,  # calculated after total
        }
        result["positions"].append(pos_entry)

    # Calculate allocation % and identify over/under-weight
    for entry in result["positions"]:
        if total_value > 0:
            entry["weight_pct"] = round((entry["value_eur"] / total_value) * 100, 1)

    # Asset type allocation
    type_alloc = {}
    for entry in result["positions"]:
        t = entry["type"]
        type_alloc[t] = type_alloc.get(t, 0) + entry["weight_pct"]
    result["allocation"]["by_type"] = {k: round(v, 1) for k, v in sorted(type_alloc.items(), key=lambda x: -x[1])}

    # Sector allocation (stocks only)
    sector_alloc = {}
    for entry in result["positions"]:
        if entry["type"] == "stock":
            # Simple heuristic: semiconductors vs other
            ticker = entry["ticker"]
            if ticker in ("MU", "AMD", "MRVL", "WDC"):
                sector = "semiconductors"
            else:
                sector = "other_stocks"
            sector_alloc[sector] = sector_alloc.get(sector, 0) + entry["weight_pct"]
    if sector_alloc:
        result["allocation"]["by_sector"] = {k: round(v, 1) for k, v in sorted(sector_alloc.items(), key=lambda x: -x[1])}

    # ── Swing portfolio analysis ──
    swing_positions = swing.get("positions", {})
    swing_active_value = 0.0
    swing_active_cost = 0.0

    for ticker, pos_data in swing_positions.items():
        shares = pos_data.get("shares", 0)
        entry_price = pos_data.get("entry_price", 0)
        current_price = prices.get(ticker) or get_price_single(ticker)

        cost = shares * entry_price
        value = shares * (current_price or entry_price)
        pnl_abs = value - cost
        pnl_pct = ((pnl_abs / cost) * 100) if cost else 0

        swing_active_value += value
        swing_active_cost += cost

        result["swing"]["positions"][ticker] = {
            "shares": shares,
            "entry_price": entry_price,
            "current_price": current_price,
            "cost": round(cost, 2),
            "value": round(value, 2),
            "pnl_abs": round(pnl_abs, 2),
            "pnl_pct": round(pnl_pct, 2),
        }

    if swing_active_cost > 0:
        result["swing"]["active_pnl"] = round(swing_active_value - swing_active_cost, 2)
        result["swing"]["active_pnl_pct"] = round(((swing_active_value / swing_active_cost) - 1) * 100, 2)

    # ── Rebalancing suggestions ──
    if result["swing"]["positions"]:
        swing_total = swing.get("cash", 0) + swing_active_value
        for ticker, alloc_pct in TARGET_SWING.items():
            if ticker in result["swing"]["positions"]:
                actual = result["swing"]["positions"][ticker]
                target_value = swing_total * alloc_pct
                actual_value = actual["value"]
                deviation = ((actual_value - target_value) / target_value) * 100 if target_value else 0

                if abs(deviation) > REBALANCE_THRESHOLD * 100:
                    direction = "vendi" if deviation > 0 else "compra"
                    amount = round(abs(actual_value - target_value), 2)
                    result["rebalance_suggestions"].append(
                        f"{ticker}: {direction} ~{amount:.0f}€ (devia {deviation:+.0f}%, target {alloc_pct*100:.0f}%)"
                    )

    # ── Performance alerts ──
    for entry in result["positions"]:
        if entry["pnl_pct"] < -10:
            result["alerts"].append(f"🔴 {entry['ticker'] or entry['name']}: -{abs(entry['pnl_pct']):.1f}%")
        elif entry["pnl_pct"] > 20:
            result["alerts"].append(f"🟢 {entry['ticker'] or entry['name']}: +{entry['pnl_pct']:.1f}% — prendere profitto?")

    # Totals
    result["total_value"] = round(total_value, 2)
    result["total_cost"] = round(total_cost, 2)
    result["total_pnl_abs"] = round(total_value - total_cost, 2)
    result["total_pnl_pct"] = round(((total_value / total_cost) - 1) * 100, 2) if total_cost else 0

    # ── Snapshot for monthly P&L ──
    today = date.today()
    today_str = today.isoformat()
    current_month = today.strftime("%Y-%m")

    # Update peak
    if total_value > pm.get("peak_value", 0):
        pm["peak_value"] = total_value
        pm["peak_date"] = today_str

    # Save snapshot if new month
    existing_months = [s["month"] for s in pm.get("snapshots", [])]
    if current_month not in existing_months:
        pm["snapshots"].append({"month": current_month, "value": round(total_value, 2), "date": today_str})
        pm["snapshots"] = pm["snapshots"][-24:]  # keep last 24 months
        save_pm_state(pm)

    # Monthly P&L from snapshots
    result["snapshots"] = pm.get("snapshots", [])
    if len(result["snapshots"]) >= 2:
        prev = result["snapshots"][-2]
        curr = result["snapshots"][-1]
        monthly_chg = curr["value"] - prev["value"]
        monthly_pct = (monthly_chg / prev["value"]) * 100
        result["monthly_pnl_abs"] = round(monthly_chg, 2)
        result["monthly_pnl_pct"] = round(monthly_pct, 2)
        result["monthly_from"] = prev["month"]
        result["monthly_to"] = curr["month"]
    else:
        result["monthly_pnl_abs"] = 0
        result["monthly_pnl_pct"] = 0

    # Drawdown from peak
    if pm["peak_value"] > 0 and total_value < pm["peak_value"]:
        result["drawdown"] = round(((total_value - pm["peak_value"]) / pm["peak_value"]) * 100, 2)
        result["drawdown_date"] = pm["peak_date"]
    else:
        result["drawdown"] = 0.0
        result["drawdown_date"] = today_str

    return result


# ── ETF Transition Analysis ──

def analyze_transition() -> dict:
    """Confronta l'allocazione attuale con il target ETF-only e propone vendite
    con abbinamento fiscale gain/loss (minusvalenze non compensano redditi di
    capitale degli ETF, quindi si abbinano vendite in gain e in loss nello
    stesso anno fiscale)."""
    analysis = analyze_portfolio()
    target = load_target_allocation()
    total_value = analysis["total_value"]

    rows = []
    seen_keys = set()
    for p in analysis["positions"]:
        key = p["ticker"] or p["name"]
        seen_keys.add(key)
        current_pct = p["weight_pct"]
        target_pct = target.get(key, 0.0) * 100
        current_eur = p["value_eur"]
        target_eur = total_value * (target_pct / 100) if total_value else 0.0
        rows.append({
            "key": key,
            "name": p["name"],
            "type": p["type"],
            "current_pct": current_pct,
            "target_pct": target_pct,
            "delta_eur": round(target_eur - current_eur, 2),
            "pnl_pct": p["pnl_pct"],
            "pnl_abs": p["pnl_abs"],
        })

    # CASH target with no matching position (not held explicitly)
    if "CASH" in target and "CASH" not in seen_keys:
        target_pct = target["CASH"] * 100
        target_eur = total_value * (target_pct / 100) if total_value else 0.0
        rows.append({
            "key": "CASH",
            "name": "Cassa",
            "type": "cash",
            "current_pct": 0.0,
            "target_pct": target_pct,
            "delta_eur": round(target_eur, 2),
            "pnl_pct": 0.0,
            "pnl_abs": 0.0,
        })

    # % of portfolio currently already in target ETFs (target > 0, excluding cash)
    etf_target_current_pct = sum(r["current_pct"] for r in rows if r["key"] in target and r["key"] != "CASH")
    etf_target_goal_pct = sum(v * 100 for k, v in target.items() if k != "CASH")

    # Stocks to sell (target 0) with unrealized P&L, for tax-loss pairing
    to_sell = [r for r in rows if r["type"] == "stock" and target.get(r["key"], 0.0) == 0.0]
    gains = sorted([r for r in to_sell if r["pnl_abs"] > 0], key=lambda r: -r["pnl_abs"])
    losses = sorted([r for r in to_sell if r["pnl_abs"] < 0], key=lambda r: r["pnl_abs"])

    pairs = []
    unpaired = []
    gi, li = 0, 0
    while gi < len(gains) and li < len(losses):
        pairs.append((gains[gi], losses[li]))
        gi += 1
        li += 1
    unpaired.extend(gains[gi:])
    unpaired.extend(losses[li:])

    return {
        "rows": rows,
        "etf_target_current_pct": round(etf_target_current_pct, 1),
        "etf_target_goal_pct": round(etf_target_goal_pct, 1),
        "to_sell": to_sell,
        "pairs": pairs,
        "unpaired": unpaired,
        "total_value": total_value,
    }


def report_transition():
    """Report di avanzamento migrazione verso portafoglio ETF-only."""
    t = analyze_transition()
    lines = ["🔀 TRANSIZIONE ETF-ONLY", ""]

    lines.append(
        f"  Progresso: {t['etf_target_current_pct']:.0f}% → target {t['etf_target_goal_pct']:.0f}% (esclusa cash)"
    )
    lines.append("")

    lines.append("  Posizione                  attuale  target    Δ€")
    for r in sorted(t["rows"], key=lambda r: -r["target_pct"]):
        if r["current_pct"] == 0.0 and r["target_pct"] == 0.0:
            continue
        arrow = "🟢" if r["delta_eur"] >= 0 else "🔴"
        lines.append(
            f"  {arrow} {r['name'][:22]:22s} {r['current_pct']:>5.1f}%  {r['target_pct']:>5.1f}%  {r['delta_eur']:>+8,.0f}"
        )

    lines.append("")

    if t["to_sell"]:
        lines.append("  💸 Vendite stock (target 0%), abbinamento fiscale gain/loss:")
        if t["pairs"]:
            for g, l in t["pairs"]:
                lines.append(
                    f"     {g['name'][:16]:16s} +{g['pnl_abs']:,.0f}€  ↔  {l['name'][:16]:16s} {l['pnl_abs']:,.0f}€"
                )
        if t["unpaired"]:
            for u in t["unpaired"]:
                tag = "gain non abbinato" if u["pnl_abs"] > 0 else "loss non abbinata"
                lines.append(f"     ⚠️  {u['name'][:22]:22s} {u['pnl_abs']:+,.0f}€ — {tag}")
        lines.append("     (suggerimento, verifica col commercialista)")
        lines.append("")

    if t["to_sell"]:
        first = t["to_sell"][0]
        lines.append(f"  ➡️  Prossimo step: vendi {first['name']} e reinvesti in CSPX.L/EMIM.L")
    else:
        lines.append("  ➡️  Prossimo step: nessuno stock da vendere, monitora ribilanciamento ETF")

    return "\n".join(lines)


# ── CLI ──

def report_full():
    analysis = analyze_portfolio()
    today_info = date.today().strftime("%A %d %B %Y")
    lines = [f"📊 PORTFOLIO MANAGER — {today_info}", ""]
    sep = "─" * 42

    # Summary
    lines.append(sep)
    lines.append(f"  💰 Total Value:  {analysis['total_value']:,.2f} €")
    lines.append(f"     Total Cost:   {analysis['total_cost']:,.2f} €")
    lines.append(f"     P&L:          {analysis['total_pnl_abs']:+,.2f} €  ({analysis['total_pnl_pct']:+.2f}%)")

    if analysis["drawdown"] != 0:
        lines.append(f"     Drawdown:     {analysis['drawdown']:.1f}% (da {analysis['drawdown_date']})")

    if analysis["monthly_pnl_abs"] != 0:
        lines.append(f"  📅 Monthly P&L:  {analysis['monthly_pnl_abs']:+,.2f} €  ({analysis['monthly_pnl_pct']:+.2f}%) | {analysis['monthly_to']}")

    lines.append("")

    # Asset allocation
    lines.append(f"  📊 Asset Allocation:")
    for atype, pct in analysis["allocation"].get("by_type", {}).items():
        lines.append(f"     {atype}: {pct}%")

    if analysis["allocation"].get("by_sector"):
        lines.append(f"  🏭 Sector (stocks):")
        for sec, pct in analysis["allocation"]["by_sector"].items():
            lines.append(f"     {sec}: {pct}%")

    lines.append("")

    # Positions
    lines.append(f"  📋 Positions:")
    for p in analysis["positions"]:
        ticker_disp = f"({p['ticker']}) " if p['ticker'] else ""
        pnl_sym = "🟢" if p["pnl_pct"] >= 0 else "🔴"
        lines.append(f"     {pnl_sym} {ticker_disp}{p['name'][:25]:25s}  {p['value_eur']:>9,.2f}€  {p['weight_pct']:>4.1f}%  P&L: {p['pnl_pct']:+.1f}%")

    lines.append("")

    # Swing portfolio
    swing = analysis["swing"]
    lines.append(f"  🎯 Swing Portfolio (10k target):")
    lines.append(f"     Cassa: {swing['cash']:,.2f}€  |  In posizioni: {sum(v['value'] for v in swing['positions'].values()):,.2f}€" if swing['positions'] else f"     Cassa: {swing['cash']:,.2f}€  |  Nessuna posizione attiva")

    for ticker, pos_data in swing["positions"].items():
        pnl_sym = "🟢" if pos_data["pnl_pct"] >= 0 else "🔴"
        lines.append(f"     {pnl_sym} {ticker}: {pos_data['shares']} az. @ {pos_data['entry_price']:.2f} → {pos_data['current_price']:.2f}  |  {pos_data['pnl_abs']:+,.2f}€ ({pos_data['pnl_pct']:+.2f}%)")

    swing_total_pnl = swing["active_pnl"]
    if swing_total_pnl:
        lines.append(f"     Swing P&L totale: {swing_total_pnl:+,.2f}€")
    lines.append("")

    # Rebalancing suggestions
    if analysis["rebalance_suggestions"]:
        lines.append(f"  🔄 Rebalancing:")
        for s in analysis["rebalance_suggestions"]:
            lines.append(f"     {s}")
        lines.append("")

    # Alerts
    if analysis["alerts"]:
        lines.append(f"  ⚠️  Alerts:")
        for a in analysis["alerts"]:
            lines.append(f"     {a}")
        lines.append("")

    # Targets
    lines.append(sep)
    lines.append(f"  🎯 Target: +10% mese sul fondo swing (10k→11k)")
    lines.append(f"  📈 Peak portafoglio:  {analysis.get('_peak', analysis['total_value']):,.2f}€")

    return "\n".join(lines)


def report_allocation():
    """Quick allocation report."""
    analysis = analyze_portfolio()
    lines = ["📊 Allocation", ""]
    for p in analysis["positions"]:
        sym = "🟢" if p["pnl_pct"] >= 0 else "🔴"
        lines.append(f"  {sym} {p['name'][:25]:25s}  {p['weight_pct']:>5.1f}%  |  P&L: {p['pnl_pct']:+.1f}%  |  {p['value_eur']:>8,.0f}€")
    lines.append("")
    lines.append(f"  💰 Totale: {analysis['total_value']:,.0f}€  |  P&L: {analysis['total_pnl_abs']:+,.0f}€")
    return "\n".join(lines)


def report_rebalance():
    """Just rebalancing suggestions."""
    analysis = analyze_portfolio()
    if not analysis["rebalance_suggestions"]:
        return "✅ Allocazione bilanciata — nessun ribilanciamento necessario."
    lines = ["🔄 Rebalancing Request", ""]
    for s in analysis["rebalance_suggestions"]:
        lines.append(f"  {s}")
    lines.append("")
    lines.append(f"  Esegui l'operazione su Scalable Capital poi registra con:")
    lines.append(f"    uv run python scripts/swing_signals.py enter <TICKER> <PREZZO> <AZIONI>")
    return "\n".join(lines)


def report_monthly():
    """Monthly P&L tracking."""
    analysis = analyze_portfolio()
    lines = ["📅 Monthly P&L", ""]
    for snap in analysis.get("snapshots", []):
        lines.append(f"  {snap['month']}:  {snap['value']:>10,.2f}€")
    if analysis["monthly_pnl_abs"]:
        lines.append("")
        lines.append(f"  {analysis['monthly_to']}:  {analysis['monthly_pnl_abs']:+,.2f}€ ({analysis['monthly_pnl_pct']:+.2f}%)")
    lines.append("")
    lines.append(f"  Portfolio value: {analysis['total_value']:,.2f}€")
    return "\n".join(lines)


def main():
    if len(sys.argv) > 1:
        cmd = sys.argv[1]
        if cmd in ("alloc", "allocation"):
            print(report_allocation())
        elif cmd in ("rebal", "rebalance"):
            print(report_rebalance())
        elif cmd in ("monthly", "month"):
            print(report_monthly())
        elif cmd in ("transition", "etf"):
            print(report_transition())
        else:
            print(f"Unknown command: {cmd}")
            print("Comandi: alloc | rebalance | monthly | transition")
        return
    print(report_full())


if __name__ == "__main__":
    main()
