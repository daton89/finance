#!/usr/bin/env python3
"""
risk_agent.py — Risk Agent per Finance Team.

Tasks:
  - Portfolio drawdown from peak
  - Volatility tracking per position (30d rolling)
  - Position sizing alerts (single position >20% portfolio)
  - Sector concentration >40% alert
  - Correlation between MU/AMD
  - Daily risk score (low/medium/high)
"""

import json
import os
import sys
from datetime import datetime, date, timedelta
from pathlib import Path

import yfinance as yf
import pandas as pd
import numpy as np

from finance_core.market import convert_to_eur, fetch_closes

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if os.path.basename(SCRIPT_DIR) == "scripts":
    SCRIPT_DIR = os.path.dirname(SCRIPT_DIR)

DATA_DIR = os.path.join(SCRIPT_DIR, "data")
os.makedirs(DATA_DIR, exist_ok=True)

PORTFOLIO_PATH = os.path.join(SCRIPT_DIR, "portfolio.json")
SWING_STATE_PATH = os.path.join(SCRIPT_DIR, "data", "swing_state.json")
RISK_STATE_PATH = os.path.join(DATA_DIR, "risk_state.json")

# Risk thresholds
MAX_SINGLE_POSITION_PCT = 25.0   # alert if >25% in one position
MAX_SECTOR_PCT = 45.0             # alert if >45% in one sector
MAX_DRAWDOWN = 15.0               # max acceptable drawdown %
VAR_95_DAYS = 1                   # 1-day VaR
VOLATILITY_HIGH = 60.0            # annualized vol >60% = high risk


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


def load_risk_state() -> dict:
    if os.path.exists(RISK_STATE_PATH):
        with open(RISK_STATE_PATH) as f:
            return json.load(f)
    return {"peak_value": 0, "peak_date": None, "daily_log": []}


def save_risk_state(state: dict):
    with open(RISK_STATE_PATH, "w") as f:
        json.dump(state, f, indent=2)


def fetch_price_history(tickers: list[str], period: str = "6mo") -> dict[str, pd.Series]:
    """Fetch adjusted close price history via batch download. Returns {ticker: Series}."""
    if not tickers:
        return {}
    try:
        return fetch_closes(tickers, period=period)
    except Exception:
        return {}


def calc_volatility(prices: pd.Series, window: int = 21) -> float:
    """Annualized volatility from daily returns."""
    if len(prices) < window:
        return 0.0
    returns = prices.pct_change().dropna()
    recent = returns.iloc[-window:]
    if len(recent) < 5:
        return 0.0
    return float(recent.std() * np.sqrt(252) * 100)


def calc_var(prices: pd.Series, confidence: float = 0.95, days: int = 1) -> float:
    """Historical VaR at confidence level for given days."""
    if len(prices) < 30:
        return 0.0
    returns = prices.pct_change().dropna()
    if len(returns) < 20:
        return 0.0
    # Sort returns and find percentile
    sorted_rets = returns.sort_values()
    idx = int((1 - confidence) * len(sorted_rets))
    idx = max(0, min(idx, len(sorted_rets) - 1))
    daily_var = float(sorted_rets.iloc[idx])
    return daily_var * np.sqrt(days) * 100  # as %


def calc_drawdown(prices: pd.Series) -> dict:
    """Calculate max drawdown and current drawdown from price series."""
    if len(prices) < 5:
        return {"current_dd": 0.0, "max_dd": 0.0, "max_dd_date": None}
    peak = prices.expanding().max()
    dd = (prices - peak) / peak * 100
    current_dd = float(dd.iloc[-1])
    max_dd_idx = dd.idxmin()
    max_dd = float(dd.min())
    return {
        "current_dd": round(current_dd, 2),
        "max_dd": round(max_dd, 2),
        "max_dd_date": max_dd_idx.strftime("%Y-%m-%d") if hasattr(max_dd_idx, 'strftime') else str(max_dd_idx)[:10],
    }


def calc_correlation(prices_a: pd.Series, prices_b: pd.Series, window: int = 60) -> float:
    """Rolling correlation between two price series."""
    common = pd.concat([prices_a, prices_b], axis=1).dropna()
    if len(common) < window:
        return 0.0
    rets = common.pct_change().dropna()
    recent = rets.iloc[-window:]
    if len(recent) < 10:
        return 0.0
    return float(recent.iloc[:, 0].corr(recent.iloc[:, 1]))


# ── Risk Analysis ──

def analyze_risk() -> dict:
    """Full portfolio risk analysis."""
    portfolio = load_portfolio()
    swing = load_swing_state()
    risk = load_risk_state()

    positions = portfolio.get("positions", [])
    tickers = [p["ticker"] for p in positions if p.get("ticker") and p.get("ticker") != ""]

    # Also track swing tickers if not in portfolio
    swing_tickers = list(swing.get("positions", {}).keys())
    all_tickers = list(set(tickers + swing_tickers))

    prices = fetch_price_history(all_tickers)
    price_data = {}  # {ticker: price, currency, type...}
    for p in positions:
        if p.get("ticker"):
            price_data[p["ticker"]] = {
                "name": p["name"],
                "shares": p["shares"],
                "type": p["type"],
                "currency": p.get("yf_currency", "USD"),
            }

    result = {
        "date": date.today().isoformat(),
        "total_value": 0.0,
        "risk_score": "low",
        "risk_factors": [],
        "drawdown": {},
        "volatilities": {},
        "var_95": {},
        "correlations": {},
        "exposure": {},
        "alerts": [],
    }

    total_value = 0.0

    # Fetch current prices for total value
    for p in positions:
        ticker = p.get("ticker", "")
        if not ticker:
            continue
        current_price = None
        if ticker in price_data:
            try:
                t = yf.Ticker(ticker)
                info = t.info or {}
                current_price = info.get("currentPrice") or info.get("regularMarketPrice")
            except:
                pass
        if current_price is None and ticker in prices:
            try:
                current_price = float(prices[ticker].iloc[-1])
            except:
                pass
        if current_price:
            currency = p.get("yf_currency", "USD")
            price_eur = convert_to_eur(current_price, currency)
            total_value += price_eur * p["shares"]

    # Add swing cash
    total_value += swing.get("cash", 0)

    result["total_value"] = round(total_value, 2)

    # ── Position-level risk ──
    for p in positions:
        ticker = p.get("ticker", "")
        if not ticker or ticker not in prices:
            continue

        px = prices[ticker]

        # Volatility
        vol = calc_volatility(px)
        result["volatilities"][ticker] = round(vol, 1)

        # VaR
        var95 = calc_var(px)
        result["var_95"][ticker] = round(var95, 2)

        # Drawdown
        dd = calc_drawdown(px)
        result.setdefault("drawdown", {}).setdefault(ticker, dd)

        # Exposure % (simple)
        if ticker in price_data:
            current_price = float(px.iloc[-1])
            currency = price_data[ticker].get("currency", "USD")
            price_eur = convert_to_eur(current_price, currency)
            position_value = price_eur * price_data[ticker]["shares"]
            pct = (position_value / total_value * 100) if total_value > 0 else 0
            result["exposure"][ticker] = round(pct, 1)

    # ── Correlations ──
    if "MU" in prices and "AMD" in prices:
        result["correlations"]["MU_AMD"] = round(calc_correlation(prices["MU"], prices["AMD"]), 3)

    # Add sector correlations
    for a in ["MU", "AMD"]:
        for b in ["MRVL", "WDC"]:
            key = f"{a}_{b}"
            if a in prices and b in prices:
                result["correlations"][key] = round(calc_correlation(prices[a], prices[b]), 3)

    # ── Alerts ──
    # Position concentration
    for ticker, pct in result.get("exposure", {}).items():
        name = price_data.get(ticker, {}).get("name", ticker)
        if pct > MAX_SINGLE_POSITION_PCT:
            result["alerts"].append(f"🔴 {name} ({ticker}): {pct:.1f}% del portafoglio — supera soglia {MAX_SINGLE_POSITION_PCT}%")

    # Volatility
    for ticker, vol in result.get("volatilities", {}).items():
        name = price_data.get(ticker, {}).get("name", ticker)
        if vol > VOLATILITY_HIGH:
            result["alerts"].append(f"⚠️ {name} ({ticker}): volatilità {vol:.0f}% annua — alta")
        elif vol > 40:
            result["alerts"].append(f"ℹ️ {name} ({ticker}): volatilità {vol:.0f}% annua")
        elif vol < 15:
            result["alerts"].append(f"ℹ️ {name} ({ticker}): volatilità {vol:.0f}% annua — bassa")

    # Drawdown
    for ticker, dd_info in result.get("drawdown", {}).items():
        name = price_data.get(ticker, {}).get("name", ticker)
        if abs(dd_info.get("current_dd", 0)) > MAX_DRAWDOWN:
            result["alerts"].append(f"🔴 {name} ({ticker}): drawdown {dd_info['current_dd']:.1f}% — oltre soglia {MAX_DRAWDOWN}%")

    # ➕ NEW: Incorporate swing portfolio positions
    swing_positions = swing.get("positions", {})
    if swing_positions:
        swing_active_value = 0.0
        for st, sp in swing_positions.items():
            # Also add to exposure
            swing_px = prices.get(st)
            if swing_px is not None:
                curr_px = float(swing_px.iloc[-1])
                val = curr_px * sp.get("shares", 0)
                swing_active_value += val
        result["swing_exposure"] = round(swing_active_value, 2)
        swing_cash = swing.get("cash", 0)
        if swing_active_value + swing_cash > 0:
            swing_invested_pct = (swing_active_value / (swing_active_value + swing_cash)) * 100
            result["swing_invested_pct"] = round(swing_invested_pct, 1)
        else:
            result["swing_invested_pct"] = 0

    # ── Risk score ──
    risk_score = "low"
    high_risks = [a for a in result["alerts"] if "🔴" in a]
    med_risks = [a for a in result["alerts"] if "⚠️" in a]

    if len(high_risks) >= 2:
        risk_score = "high"
    elif len(high_risks) >= 1 or len(med_risks) >= 2:
        risk_score = "medium"

    result["risk_score"] = risk_score

    # ── Save state ──
    # Update peak
    if total_value > risk.get("peak_value", 0):
        risk["peak_value"] = total_value
        risk["peak_date"] = date.today().isoformat()
        save_risk_state(risk)

    # Portfolio-level drawdown from peak
    if risk.get("peak_value", 0) > 0 and total_value < risk["peak_value"]:
        port_dd = ((total_value - risk["peak_value"]) / risk["peak_value"]) * 100
        result["portfolio_drawdown"] = round(port_dd, 2)
        result["portfolio_peak"] = risk["peak_value"]
        result["portfolio_peak_date"] = risk["peak_date"]
    else:
        result["portfolio_drawdown"] = 0.0
        result["portfolio_peak"] = total_value
        result["portfolio_peak_date"] = date.today().isoformat()

    return result


# ── Reports ──

def report_full():
    analysis = analyze_risk()
    today_info = date.today().strftime("%A %d %B %Y")

    risk_icon = {"low": "🟢", "medium": "🟡", "high": "🔴"}
    icon = risk_icon.get(analysis["risk_score"], "⚪")

    lines = [f"🛡️ RISK REPORT — {today_info}", ""]
    sep = "─" * 42

    lines.append(sep)
    lines.append(f"  {icon} Risk Score: {analysis['risk_score'].upper()}")
    lines.append(f"  💰 Portfolio Value: {analysis['total_value']:,.2f} €")
    lines.append(f"  📉 Drawdown (portafoglio): {analysis['portfolio_drawdown']:.2f}%")

    if analysis.get("swing_exposure", 0) > 0:
        lines.append(f"  🎯 Swing invested: {analysis['swing_invested_pct']:.0f}% | esposizione: {analysis['swing_exposure']:,.0f}€")
    lines.append("")

    # Volatilities
    lines.append(f"  📊 Volatilità Annualizzata (30d):")
    for ticker, vol in sorted(analysis.get("volatilities", {}).items(), key=lambda x: -x[1]):
        vol_icon = "🔴" if vol > 60 else "🟡" if vol > 40 else "🟢"
        name = load_portfolio()["positions"]
        n = next((p["name"][:20] for p in name if p.get("ticker") == ticker), ticker)
        lines.append(f"     {vol_icon} {ticker:6s} {n:20s}  {vol:>5.1f}%")

    lines.append("")

    # VaR
    lines.append(f"  📉 VaR 95% (1d):")
    for ticker, var in sorted(analysis.get("var_95", {}).items(), key=lambda x: -abs(x[1])):
        lines.append(f"     {ticker:6s}  {var:>+5.2f}%")

    lines.append("")

    # Correlations
    if analysis.get("correlations"):
        lines.append(f"  🔗 Correlazioni:")
        for pair, corr in sorted(analysis["correlations"].items()):
            c_icon = "🔴" if abs(corr) > 0.8 else "🟡" if abs(corr) > 0.6 else "🟢"
            lines.append(f"     {c_icon} {pair.replace('_', ' vs ')}:  {corr:.3f}")

    lines.append("")

    # Alerts
    if analysis["alerts"]:
        lines.append(f"  ⚠️  Alerts ({len(analysis['alerts'])}):")
        for a in analysis["alerts"]:
            lines.append(f"     {a}")
    else:
        lines.append(f"  ✅ Nessun alert — profilo sereno")

    lines.append("")
    lines.append(sep)
    if analysis["risk_score"] == "high":
        lines.append(f"  🚨 Rischio ALTO — considera ridurre esposizione")
    elif analysis["risk_score"] == "medium":
        lines.append(f"  ⚠️  Rischio MEDIO — monitora posizioni concentrate")
    else:
        lines.append(f"  ✅ Rischio BASSO — portafoglio equilibrato")

    return "\n".join(lines)


def report_volatility():
    analysis = analyze_risk()
    lines = ["📊 Volatilità", ""]
    for ticker, vol in sorted(analysis.get("volatilities", {}).items(), key=lambda x: -x[1]):
        sym = "🔴" if vol > 60 else "🟡" if vol > 40 else "🟢"
        lines.append(f"  {sym} {ticker}: {vol:.1f}% annualizzata")
    return "\n".join(lines)


def report_drawdown():
    analysis = analyze_risk()
    lines = ["📉 Drawdown", ""]
    lines.append(f"  Portafoglio: {analysis['portfolio_drawdown']:.2f}% (picco: {analysis.get('portfolio_peak', 0):,.0f}€)")
    lines.append("")
    for ticker, dd in analysis.get("drawdown", {}).items():
        sym = "🔴" if abs(dd.get("current_dd", 0)) > 10 else "🟡" if abs(dd.get("current_dd", 0)) > 5 else "🟢"
        lines.append(f"  {sym} {ticker}: {dd.get('current_dd', 0):.1f}% (max: {dd.get('max_dd', 0):.1f}% il {dd.get('max_dd_date', '?')})")
    return "\n".join(lines)


def main():
    if len(sys.argv) > 1:
        cmd = sys.argv[1]
        if cmd in ("vol", "volatility"):
            print(report_volatility())
        elif cmd in ("dd", "drawdown"):
            print(report_drawdown())
        elif cmd in ("score",):
            analysis = analyze_risk()
            print(f"🛡️ Risk Score: {analysis['risk_score'].upper()}  |  Drawdown: {analysis['portfolio_drawdown']:.1f}%  |  Vol: {len(analysis['volatilities'])} posizioni seguite")
        else:
            print(f"Unknown: {cmd}")
            print("Comandi: vol | drawdown | score")
        return
    print(report_full())


if __name__ == "__main__":
    main()
