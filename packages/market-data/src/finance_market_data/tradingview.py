from __future__ import annotations

from dataclasses import dataclass

import httpx

SCAN_URL = "https://scanner.tradingview.com/global/scan"

COLUMNS = [
    "close", "open", "high", "low", "volume",
    "RSI", "SMA20", "SMA50", "SMA200", "EMA20", "ATR", "ADX", "Recommend.All",
]


@dataclass
class TVQuote:
    symbol: str
    close: float
    open: float
    high: float
    low: float
    volume: int
    rsi: float | None
    sma20: float | None
    sma50: float | None
    sma200: float | None
    ema20: float | None
    atr: float | None
    adx: float | None
    recommend_all: float | None


class TradingViewClient:
    """Thin HTTP adapter for TradingView's public (unofficial) scanner endpoint."""

    def __init__(self, timeout: float = 15.0, transport: httpx.BaseTransport | None = None):
        self.timeout = timeout
        self._transport = transport

    async def fetch_quotes(self, symbols: list[str]) -> list[TVQuote]:
        """
        Batched lookup of current-value technicals + rating for `symbols`
        (each in "EXCHANGE:TICKER" form, e.g. "NASDAQ:AAPL"). One HTTP
        request regardless of how many symbols are passed. Symbols not
        found on TradingView are simply absent from the result.
        """
        if not symbols:
            return []

        payload = {
            "symbols": {"tickers": symbols, "query": {"types": []}},
            "columns": COLUMNS,
        }

        async with httpx.AsyncClient(timeout=self.timeout, transport=self._transport) as client:
            resp = await client.post(SCAN_URL, json=payload)
            resp.raise_for_status()
            body = resp.json()

        quotes = []
        for row in body.get("data", []):
            d = row["d"]
            if any(d[i] is None for i in range(5)):
                # Missing close/open/high/low/volume (thinly-traded, pre-market,
                # delisted-but-still-queryable symbol, etc.) — PriceBar columns
                # are non-nullable, so treat this the same as "not found".
                continue
            quotes.append(
                TVQuote(
                    symbol=row["s"],
                    close=d[0],
                    open=d[1],
                    high=d[2],
                    low=d[3],
                    volume=int(d[4]),
                    rsi=d[5],
                    sma20=d[6],
                    sma50=d[7],
                    sma200=d[8],
                    ema20=d[9],
                    atr=d[10],
                    adx=d[11],
                    recommend_all=d[12],
                )
            )
        return quotes
