"""Shared market utilities — indicators, data fetching, FX conversion, portfolio loading.

All functions are copies of existing implementations scattered across scripts.
Import from here instead of duplicating locally.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf

# ── Indicators ──────────────────────────────────────────────────────────────


def ema(series: pd.Series, period: int) -> pd.Series:
    """Exponential Moving Average."""
    return series.ewm(span=period, adjust=False).mean()


def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    """Relative Strength Index."""
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(span=period, adjust=False).mean()
    avg_loss = loss.ewm(span=period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


# ── Data fetching ───────────────────────────────────────────────────────────

# market.py vive in packages/core/src/finance_core/ → packages/ è 3 livelli su
_PACKAGES_DIR = Path(__file__).resolve().parents[3]


def fetch_close(ticker: str, period: str = "3mo", **kwargs) -> pd.Series:
    """Fetch adjusted close for a single ticker.

    Handles yfinance MultiIndex columns transparently.
    Additional kwargs forwarded to yf.download.
    """
    raw = yf.download(ticker, period=period, interval="1d", auto_adjust=True,
                      progress=False, **kwargs)
    if raw.empty:
        raise RuntimeError(f"No data for {ticker}")
    if isinstance(raw.columns, pd.MultiIndex):
        close = raw["Close"][ticker]
    else:
        close = raw["Close"]
    return close.dropna()


def fetch_closes(tickers: list[str], period: str = "3mo", **kwargs) -> dict[str, pd.Series]:
    """Batch download close prices for multiple tickers in a single yf.download call.

    Returns {ticker: Series}. Tickers with no data are omitted.
    """
    if not tickers:
        return {}
    raw = yf.download(tickers, period=period, interval="1d", auto_adjust=True,
                      progress=False, **kwargs)
    if raw.empty:
        return {}
    result: dict[str, pd.Series] = {}
    if isinstance(raw.columns, pd.MultiIndex):
        # Multi-ticker download returns MultiIndex columns: (Close, ticker)
        close_df = raw["Close"]
        for t in tickers:
            if t in close_df.columns:
                s = close_df[t].dropna()
                if not s.empty:
                    result[t] = s
    else:
        # Single ticker fallback (shouldn't happen with list input, but defensive)
        close = raw["Close"].dropna()
        if not close.empty:
            result[tickers[0]] = close
    return result


# ── FX conversion ───────────────────────────────────────────────────────────

_FX_CACHE: dict[str, float] = {}
EUR_RATE = 0.92
GBP_TO_EUR = 1.19


def _live_rate(pair: str, fallback: float) -> float:
    """Live FX rate from Yahoo (e.g. 'EURUSD=X'), with cache and static fallback."""
    if pair in _FX_CACHE:
        return _FX_CACHE[pair]
    try:
        rate = float(yf.Ticker(pair).fast_info["last_price"])
        if rate > 0:
            _FX_CACHE[pair] = rate
            return rate
    except Exception:
        pass
    _FX_CACHE[pair] = fallback
    return fallback


def convert_to_eur(price: float, currency: str) -> float:
    """Convert price to EUR. Live FX with static fallback."""
    if currency in ("EUR",):
        return price
    usd_eur = 1.0 / _live_rate("EURUSD=X", 1.0 / EUR_RATE)
    gbp_eur = _live_rate("GBPEUR=X", GBP_TO_EUR)
    if currency == "GBp":
        return (price / 100) * gbp_eur
    if currency == "GBP":
        return price * gbp_eur
    return price * usd_eur


# ── Portfolio loading ───────────────────────────────────────────────────────


def load_portfolio() -> dict:
    """Load packages/portfolio.json."""
    portfolio_path = _PACKAGES_DIR / "portfolio.json"
    if not portfolio_path.exists():
        return {"positions": []}
    with open(portfolio_path) as f:
        return json.load(f)
