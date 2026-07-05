"""Price data loading via yfinance (optional dependency)."""

from __future__ import annotations

import pandas as pd


def load_prices(tickers: list[str], start: str, end: str | None = None) -> pd.DataFrame:
    """Daily adjusted close prices, wide Date x Ticker DataFrame.

    `end=None` means "up to today" (yfinance's default), useful for live use
    where you want the latest available bar rather than a fixed cutoff.
    """
    import yfinance as yf

    raw = yf.download(list(tickers), start=start, end=end, auto_adjust=True, progress=False)
    if isinstance(raw.columns, pd.MultiIndex):
        prices = raw["Close"]
    else:
        prices = raw[["Close"]].rename(columns={"Close": tickers[0]})
    return prices.dropna(how="all").sort_index()
