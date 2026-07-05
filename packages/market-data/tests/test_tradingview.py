import json

import httpx
import pytest
from finance_market_data.tradingview import TradingViewClient


def _handler_with_data(request: httpx.Request) -> httpx.Response:
    body = json.loads(request.content)
    assert body["symbols"]["tickers"] == ["NASDAQ:AAPL"]
    assert body["columns"] == [
        "close", "open", "high", "low", "volume",
        "RSI", "SMA20", "SMA50", "SMA200", "EMA20", "ATR", "ADX", "Recommend.All",
    ]
    return httpx.Response(
        200,
        json={
            "totalCount": 1,
            "data": [
                {
                    "s": "NASDAQ:AAPL",
                    "d": [294.21, 293.44, 296.59, 289.195, 32039492,
                          50.84, 294.87, 292.67, 270.33, 292.9, 8.19, 24.37, 0.3787],
                }
            ],
        },
    )


@pytest.mark.asyncio
async def test_fetch_quotes_parses_response():
    client = TradingViewClient(transport=httpx.MockTransport(_handler_with_data))

    quotes = await client.fetch_quotes(["NASDAQ:AAPL"])

    assert len(quotes) == 1
    q = quotes[0]
    assert q.symbol == "NASDAQ:AAPL"
    assert q.close == 294.21
    assert q.volume == 32039492
    assert q.rsi == 50.84
    assert q.sma20 == 294.87
    assert q.adx == 24.37
    assert q.recommend_all == 0.3787


@pytest.mark.asyncio
async def test_fetch_quotes_returns_empty_for_unknown_symbol():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"totalCount": 0, "data": []})

    client = TradingViewClient(transport=httpx.MockTransport(handler))

    quotes = await client.fetch_quotes(["NASDAQ:ZZZZNOTREAL"])

    assert quotes == []


@pytest.mark.asyncio
async def test_fetch_quotes_with_no_symbols_makes_no_request():
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("should not be called")

    client = TradingViewClient(transport=httpx.MockTransport(handler))

    quotes = await client.fetch_quotes([])

    assert quotes == []


@pytest.mark.asyncio
async def test_fetch_quotes_skips_row_with_null_volume():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "totalCount": 2,
                "data": [
                    {
                        "s": "NASDAQ:THIN",
                        "d": [10.0, 9.5, 10.5, 9.0, None,
                              50.0, 10.1, 10.2, 10.3, 10.0, 0.5, 20.0, 0.0],
                    },
                    {
                        "s": "NASDAQ:AAPL",
                        "d": [294.21, 293.44, 296.59, 289.195, 32039492,
                              50.84, 294.87, 292.67, 270.33, 292.9, 8.19, 24.37, 0.3787],
                    },
                ],
            },
        )

    client = TradingViewClient(transport=httpx.MockTransport(handler))

    quotes = await client.fetch_quotes(["NASDAQ:THIN", "NASDAQ:AAPL"])

    assert len(quotes) == 1
    assert quotes[0].symbol == "NASDAQ:AAPL"
