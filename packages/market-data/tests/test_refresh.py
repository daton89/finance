from datetime import date

import pytest
from finance_core.base import SessionLocal
from finance_core.models import ExternalRating, IndicatorValue, PriceBar, WatchlistStock
from finance_market_data.refresh import refresh_market_data
from finance_market_data.tradingview import TVQuote


class _FakeClient:
    def __init__(self, quotes: list[TVQuote]):
        self._quotes = quotes

    async def fetch_quotes(self, symbols: list[str]) -> list[TVQuote]:
        return [q for q in self._quotes if q.symbol in symbols]


def _quote(symbol="NASDAQ:AAPL") -> TVQuote:
    return TVQuote(
        symbol=symbol, close=294.21, open=293.44, high=296.59, low=289.195,
        volume=32039492, rsi=50.84, sma20=294.87, sma50=292.67, sma200=270.33,
        ema20=292.9, atr=8.19, adx=24.37, recommend_all=0.6,
    )


@pytest.mark.asyncio
async def test_refresh_skips_tickers_without_tv_symbol():
    db = SessionLocal()
    db.add(WatchlistStock(ticker="AAPL", tv_symbol=None, is_active=True))
    db.commit()

    result = await refresh_market_data(db, client=_FakeClient([]))
    db.close()

    assert result.refreshed == []
    assert result.skipped == [{"ticker": "AAPL", "reason": "no tv_symbol"}]


@pytest.mark.asyncio
async def test_refresh_skips_tickers_not_found_on_tradingview():
    db = SessionLocal()
    db.add(WatchlistStock(ticker="AAPL", tv_symbol="NASDAQ:AAPL", is_active=True))
    db.commit()

    result = await refresh_market_data(db, client=_FakeClient([]))
    db.close()

    assert result.refreshed == []
    assert result.skipped == [
        {"ticker": "AAPL", "reason": "not found on TradingView (NASDAQ:AAPL)"}
    ]


@pytest.mark.asyncio
async def test_refresh_upserts_price_bar_indicator_value_and_external_rating():
    db = SessionLocal()
    db.add(WatchlistStock(ticker="AAPL", tv_symbol="NASDAQ:AAPL", is_active=True))
    db.commit()

    result = await refresh_market_data(db, client=_FakeClient([_quote()]))

    assert result.refreshed == ["AAPL"]
    assert result.skipped == []

    bar = db.query(PriceBar).filter_by(ticker="AAPL", bar_date=date.today()).one()
    assert bar.close == 294.21
    assert bar.exchange == "NASDAQ"

    indicator = db.query(IndicatorValue).filter_by(
        ticker="AAPL", calc_date=date.today(), sma_period=20, rsi_period=14
    ).one()
    assert indicator.rsi_value == 50.84
    assert indicator.sma50_value == 292.67
    assert indicator.adx_value == 24.37
    assert round(indicator.pct_from_sma, 4) == round((294.21 / 294.87 - 1) * 100, 4)

    rating = db.query(ExternalRating).filter_by(ticker="AAPL").one()
    assert rating.recommendation == "BUY"
    assert rating.score == 0.6

    db.close()


@pytest.mark.asyncio
async def test_refresh_called_twice_same_day_updates_not_duplicates():
    db = SessionLocal()
    db.add(WatchlistStock(ticker="AAPL", tv_symbol="NASDAQ:AAPL", is_active=True))
    db.commit()

    await refresh_market_data(db, client=_FakeClient([_quote()]))
    updated_quote = _quote()
    updated_quote.close = 300.0
    await refresh_market_data(db, client=_FakeClient([updated_quote]))

    bars = db.query(PriceBar).filter_by(ticker="AAPL", bar_date=date.today()).all()
    assert len(bars) == 1
    assert bars[0].close == 300.0

    indicators = db.query(IndicatorValue).filter_by(
        ticker="AAPL", calc_date=date.today(), sma_period=20, rsi_period=14
    ).all()
    assert len(indicators) == 1

    ratings = db.query(ExternalRating).filter_by(ticker="AAPL").all()
    assert len(ratings) == 2  # ratings are an append-only history, not upserted

    db.close()
