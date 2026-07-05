from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from finance_core.models import ExternalRating, IndicatorValue, PriceBar, WatchlistStock
from sqlalchemy import select
from sqlalchemy.orm import Session

from finance_market_data.tradingview import TradingViewClient


@dataclass
class RefreshResult:
    refreshed: list[str] = field(default_factory=list)
    skipped: list[dict] = field(default_factory=list)


def _recommendation_from_score(score: float) -> str:
    if score >= 0.5:
        return "BUY"
    if score <= -0.5:
        return "SELL"
    return "HOLD"


async def refresh_market_data(
    db: Session, client: TradingViewClient | None = None
) -> RefreshResult:
    """
    Refresh PriceBar/IndicatorValue/ExternalRating for every active
    WatchlistStock with a non-null tv_symbol, via one batched TradingView
    call. Tickers with no tv_symbol, or not found on TradingView, are
    reported in RefreshResult.skipped instead of raising.
    """
    client = client or TradingViewClient()
    result = RefreshResult()

    stocks = db.execute(select(WatchlistStock).where(WatchlistStock.is_active)).scalars().all()

    for s in stocks:
        if not s.tv_symbol:
            result.skipped.append({"ticker": s.ticker, "reason": "no tv_symbol"})

    mapped = [s for s in stocks if s.tv_symbol]
    if not mapped:
        return result

    symbol_to_ticker = {s.tv_symbol: s.ticker for s in mapped}
    quotes = await client.fetch_quotes(list(symbol_to_ticker.keys()))

    found_symbols = {q.symbol for q in quotes}
    for tv_symbol, ticker in symbol_to_ticker.items():
        if tv_symbol not in found_symbols:
            result.skipped.append(
                {"ticker": ticker, "reason": f"not found on TradingView ({tv_symbol})"}
            )

    today = date.today()
    for q in quotes:
        ticker = symbol_to_ticker[q.symbol]
        exchange = q.symbol.split(":")[0]

        bar = db.execute(
            select(PriceBar).where(
                PriceBar.ticker == ticker,
                PriceBar.bar_date == today,
                PriceBar.exchange == exchange,
            )
        ).scalar_one_or_none()
        if bar is None:
            bar = PriceBar(ticker=ticker, bar_date=today, exchange=exchange)
            db.add(bar)
        bar.open = q.open
        bar.high = q.high
        bar.low = q.low
        bar.close = q.close
        bar.volume = q.volume

        indicator = db.execute(
            select(IndicatorValue).where(
                IndicatorValue.ticker == ticker,
                IndicatorValue.calc_date == today,
                IndicatorValue.sma_period == 20,
                IndicatorValue.rsi_period == 14,
            )
        ).scalar_one_or_none()
        if indicator is None:
            indicator = IndicatorValue(ticker=ticker, calc_date=today, sma_period=20, rsi_period=14)
            db.add(indicator)
        indicator.sma_value = q.sma20
        indicator.sma50_value = q.sma50
        indicator.sma200_value = q.sma200
        indicator.rsi_value = q.rsi
        indicator.pct_from_sma = ((q.close / q.sma20 - 1) * 100) if q.sma20 else None
        indicator.ema_value = q.ema20
        indicator.atr_value = q.atr
        indicator.adx_value = q.adx

        if q.recommend_all is not None:
            db.add(
                ExternalRating(
                    ticker=ticker,
                    source="tradingview",
                    recommendation=_recommendation_from_score(q.recommend_all),
                    score=q.recommend_all,
                )
            )

        result.refreshed.append(ticker)

    db.commit()
    return result
