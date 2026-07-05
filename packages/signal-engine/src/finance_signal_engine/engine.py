"""
Signal engine — evaluates buy and sell conditions and manages signal lifecycle.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import date, datetime

from finance_core.models import (
    AppSetting,
    ExternalRating,
    Holding,
    IndicatorValue,
    PriceBar,
    Signal,
    StockAnalysis,
    WatchlistStock,
)
from finance_indicators.indicators import _build_rsi_series, detect_bearish_divergence
from sqlalchemy import select
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

WATCHLIST_SIGNAL_TYPES = ["NEWS_CATALYST", "BUY_ZONE", "ACCUMULATE", "WATCH", "OVERBOUGHT"]


def _load_settings(db: Session) -> dict:
    rows = db.execute(select(AppSetting)).scalars().all()
    return {r.key: r.value for r in rows}


def _fire_telegram(signal_type: str, ticker: str, conditions: list[str], settings: dict) -> None:
    """Fire-and-forget Telegram notification for a newly created signal."""
    if settings.get("telegram_notifications_enabled") != "true":
        return
    try:
        from services.notifications import format_signal_message, send_telegram

        msg = format_signal_message(signal_type, ticker, conditions)
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(send_telegram(msg, settings))
        except RuntimeError:
            import threading

            threading.Thread(
                target=lambda: asyncio.run(send_telegram(msg, settings)),
                daemon=True,
            ).start()
    except Exception:
        pass


def _latest_external_rating(ticker: str, db: Session) -> ExternalRating | None:
    return db.execute(
        select(ExternalRating)
        .where(ExternalRating.ticker == ticker)
        .order_by(ExternalRating.fetched_at.desc())
        .limit(1)
    ).scalar_one_or_none()


def _composite_score(ticker: str, db: Session, settings: dict) -> dict:
    """
    Weighted composite score: TradingView rating (40) + RSI neutral zone (20)
    + ADX trend strength (20) + SMA proximity (20), out of 100. is_buy
    requires a BUY rating plus at least one supporting technical factor.
    Neutral (no signal) when indicator or rating data is missing.
    """
    sma_period = int(settings.get("sma_period", 20))
    rsi_period = int(settings.get("rsi_period", 14))

    indicator = _latest_indicator(ticker, db, sma_period, rsi_period)
    rating = _latest_external_rating(ticker, db)

    if indicator is None or rating is None:
        return {"is_buy": False, "is_fresh": False, "score": None}

    score = 0
    if rating.recommendation == "BUY":
        score += 40
    elif rating.recommendation == "SELL":
        score -= 40

    if indicator.rsi_value is not None and 40 <= indicator.rsi_value <= 60:
        score += 20
    if indicator.adx_value is not None and indicator.adx_value > 20:
        score += 20
    if indicator.pct_from_sma is not None and abs(indicator.pct_from_sma) <= 10:
        score += 20

    is_fresh = rating.fetched_at.date() == date.today()

    is_buy = score >= 60 and rating.recommendation == "BUY"

    return {"is_buy": is_buy, "is_fresh": is_fresh, "score": score}


def _rating_downgrade(ticker: str, db: Session) -> bool:
    """True on a BUY -> HOLD/SELL flip between the two most recent ratings."""
    ratings = (
        db.execute(
            select(ExternalRating)
            .where(ExternalRating.ticker == ticker)
            .order_by(ExternalRating.fetched_at.desc())
            .limit(2)
        )
        .scalars()
        .all()
    )

    if len(ratings) < 2:
        return False

    latest, previous = ratings[0], ratings[1]
    return previous.recommendation == "BUY" and latest.recommendation in ("HOLD", "SELL")


def _latest_ai_analysis(ticker: str, db: Session) -> StockAnalysis | None:
    """Fetch most recent completed AI analysis for a ticker."""
    return db.execute(
        select(StockAnalysis)
        .where(StockAnalysis.ticker == ticker)
        .where(StockAnalysis.status == "completed")
        .order_by(StockAnalysis.analysis_date.desc())
        .limit(1)
    ).scalar_one_or_none()


def evaluate_watchlist_signals(ticker: str, db: Session, settings: dict) -> None:
    """
    Evaluate watchlist signals for a non-held stock. Determines exactly one
    signal winner (NEWS_CATALYST > BUY_ZONE > ACCUMULATE > WATCH > OVERBOUGHT)
    and resolves any stale watchlist signals for that ticker.

    Signal logic:
    - NEWS_CATALYST : composite=buy + AI outlook=bullish + score>=7 + analysis<=2d old
    - BUY_ZONE      : composite=buy + RSI 40-60 + |pct_from_sma| <= threshold + news != bearish
    - ACCUMULATE    : composite=buy + RSI < 35 + pct_from_sma <= 5% + news != bearish
    - WATCH         : composite=buy + RSI < 50 + -15% <= pct_from_sma <= -5%
    - OVERBOUGHT    : RSI > 70 OR pct_from_sma > 15%
    """
    sma_period = int(settings.get("sma_period", 20))
    rsi_period = int(settings.get("rsi_period", 14))
    threshold = float(settings.get("buy_proximity_pct", 2.0))

    indicator = _latest_indicator(ticker, db, sma_period, rsi_period)
    if indicator is None:
        _resolve_all_watchlist_signals(ticker, db)
        return

    composite = _composite_score(ticker, db, settings)
    rsi = indicator.rsi_value
    pct = indicator.pct_from_sma

    composite_is_buy = composite["is_buy"]

    ai = _latest_ai_analysis(ticker, db)
    ai_fresh = ai is not None and (date.today() - ai.analysis_date).days <= 2
    news_bullish = ai_fresh and ai.outlook == "bullish" and (ai.overall_score or 0) >= 7
    news_bearish = ai is not None and ai.outlook == "bearish"

    regime_is_bear = False
    if settings.get("regime_filter_enabled", "false") == "true":
        try:
            from services.regime_detector import get_market_regime

            exchange = settings.get("exchange", "NASDAQ")
            regime_data = get_market_regime(exchange, db)
            regime_is_bear = regime_data["regime"] == "BEAR"
        except Exception:
            pass

    winner: str | None = None
    conditions: list[str] = []

    if composite_is_buy and news_bullish and not regime_is_bear:
        winner = "NEWS_CATALYST"
        age = (date.today() - ai.analysis_date).days
        conditions = [
            "Composite=buy",
            f"AI outlook: bullish (score {ai.overall_score:.1f}/10)",
            f"Analysis {age}d ago",
        ]
    elif (
        composite_is_buy
        and rsi is not None
        and 40 <= rsi <= 60
        and pct is not None
        and abs(pct) <= threshold
        and not news_bearish
        and not regime_is_bear
    ):
        winner = "BUY_ZONE"
        conditions = [
            "Composite=buy",
            f"Within {threshold}% of SMA{sma_period} ({pct:+.2f}%)",
            f"RSI={rsi:.1f} (neutral zone)",
        ]
    elif (
        composite_is_buy
        and rsi is not None
        and rsi < 35
        and pct is not None
        and pct <= 5
        and not news_bearish
    ):
        winner = "ACCUMULATE"
        conditions = [
            "Composite=buy",
            f"RSI={rsi:.1f} (oversold — quality dip)",
            f"SMA distance: {pct:+.2f}%",
        ]
    elif composite_is_buy and rsi is not None and rsi < 50 and pct is not None and -15 <= pct <= -5:
        winner = "WATCH"
        conditions = [
            "Composite=buy",
            f"Approaching SMA ({pct:+.2f}%)",
            f"RSI={rsi:.1f}",
        ]
    elif (rsi is not None and rsi > 70) or (pct is not None and pct > 15):
        winner = "OVERBOUGHT"
        parts = []
        if rsi is not None and rsi > 70:
            parts.append(f"RSI={rsi:.1f} (overbought)")
        if pct is not None and pct > 15:
            parts.append(f"+{pct:.1f}% above SMA{sma_period}")
        if news_bullish:
            parts.append("AI outlook bullish — momentum may continue")
        conditions = parts

    _reconcile_watchlist_signal(ticker, winner, conditions, db, settings)


def _resolve_all_watchlist_signals(ticker: str, db: Session) -> None:
    for stype in WATCHLIST_SIGNAL_TYPES:
        existing = _active_signal(ticker, stype, None, db)
        if existing is not None:
            existing.status = "resolved"
    db.commit()


def _reconcile_watchlist_signal(
    ticker: str,
    winner: str | None,
    conditions: list[str],
    db: Session,
    settings: dict,
) -> None:
    for stype in WATCHLIST_SIGNAL_TYPES:
        existing = _active_signal(ticker, stype, None, db)
        if existing is not None and stype != winner:
            existing.status = "resolved"

    if winner is not None:
        if _active_signal(ticker, winner, None, db) is None:
            signal = Signal(
                ticker=ticker,
                holding_id=None,
                signal_type=winner,
                conditions=json.dumps(conditions),
                triggered_at=datetime.utcnow(),
                status="active",
                is_read=False,
            )
            db.add(signal)
            db.commit()
            logger.info(f"[{ticker}] {winner} signal created.")
            _fire_telegram(winner, ticker, conditions, settings)
        else:
            db.commit()
    else:
        db.commit()


def evaluate_buy_signals(ticker: str, db: Session, settings: dict) -> None:
    evaluate_watchlist_signals(ticker, db, settings)


def evaluate_sell_signals(holding: Holding, db: Session, settings: dict) -> None:
    """
    Evaluate SELL ALERT conditions for an open holding lot.
    Creates or updates a SELL_ALERT signal as appropriate.
    """
    ticker = holding.ticker
    sma_period = int(settings.get("sma_period", 20))
    rsi_period = int(settings.get("rsi_period", 14))
    rsi_threshold = float(settings.get("rsi_sell_threshold", 35))

    indicator = _latest_indicator(ticker, db, sma_period, rsi_period)

    fired: list[str] = []

    if indicator is not None:
        if (
            indicator.sma50_value is not None
            and indicator.sma200_value is not None
            and indicator.pct_from_sma is not None
            and indicator.pct_from_sma < 0
            and indicator.sma50_value < indicator.sma200_value
        ):
            fired.append(f"Price < SMA{sma_period} & SMA50 < SMA200")
        elif (
            indicator.sma_value is not None
            and indicator.pct_from_sma is not None
            and indicator.pct_from_sma < 0
        ):
            fired.append(f"Price < SMA{sma_period}")

        if indicator.rsi_value is not None and indicator.rsi_value < rsi_threshold:
            fired.append(f"RSI < {int(rsi_threshold)}")

        if _check_divergence(ticker, rsi_period, db):
            fired.append("RSI Divergence")

    if _rating_downgrade(ticker, db):
        fired.append("Rating downgraded (composite worsened)")

    existing = _active_signal(ticker, "SELL_ALERT", holding.id, db)

    if fired:
        if existing is None:
            signal = Signal(
                ticker=ticker,
                holding_id=holding.id,
                signal_type="SELL_ALERT",
                conditions=json.dumps(fired),
                triggered_at=datetime.utcnow(),
                status="active",
                is_read=False,
            )
            db.add(signal)
            db.commit()
            logger.info(f"[{ticker}] SELL_ALERT created: {fired}")
            _fire_telegram("SELL_ALERT", ticker, fired, settings)
        else:
            existing_conditions = set(json.loads(existing.conditions))
            new_conditions = existing_conditions | set(fired)
            if new_conditions != existing_conditions:
                existing.conditions = json.dumps(list(new_conditions))
                db.commit()
    else:
        if existing is not None:
            existing.status = "resolved"
            existing.resolved_at = datetime.utcnow()
            db.commit()
            logger.info(f"[{ticker}] SELL_ALERT resolved.")


def evaluate_early_trend_break_signals(holding: Holding, db: Session, settings: dict) -> None:
    """
    Evaluate EARLY_TREND_BREAK conditions for an open holding.
    Fires when ALL three are true simultaneously:
      1. Price < EMA{sma_period}
      2. EMA slope negative (EMA today < EMA 3 bars ago)
      3. RSI < 50
    """
    ticker = holding.ticker
    sma_period = int(settings.get("sma_period", 20))
    rsi_period = int(settings.get("rsi_period", 14))

    indicators = (
        db.execute(
            select(IndicatorValue)
            .where(
                IndicatorValue.ticker == ticker,
                IndicatorValue.sma_period == sma_period,
                IndicatorValue.rsi_period == rsi_period,
            )
            .order_by(IndicatorValue.calc_date.desc())
            .limit(4)
        )
        .scalars()
        .all()
    )

    existing = _active_signal(ticker, "EARLY_TREND_BREAK", holding.id, db)

    if len(indicators) < 4:
        if existing is not None:
            existing.status = "resolved"
            existing.resolved_at = datetime.utcnow()
            db.commit()
        return

    latest = indicators[0]
    three_bars_ago = indicators[3]

    latest_bar = db.execute(
        select(PriceBar)
        .where(PriceBar.ticker == ticker)
        .order_by(PriceBar.bar_date.desc())
        .limit(1)
    ).scalar_one_or_none()

    if latest_bar is None or latest.ema_value is None or three_bars_ago.ema_value is None:
        if existing is not None:
            existing.status = "resolved"
            existing.resolved_at = datetime.utcnow()
            db.commit()
        return

    fired: list[str] = []

    if latest_bar.close < latest.ema_value:
        fired.append(f"Price < EMA{sma_period}")

    if latest.ema_value < three_bars_ago.ema_value:
        fired.append(f"EMA{sma_period} slope negative")

    if latest.rsi_value is not None and latest.rsi_value < 50:
        fired.append("RSI < 50")

    if len(fired) == 3:
        if existing is None:
            signal = Signal(
                ticker=ticker,
                holding_id=holding.id,
                signal_type="EARLY_TREND_BREAK",
                conditions=json.dumps(fired),
                triggered_at=datetime.utcnow(),
                status="active",
                is_read=False,
            )
            db.add(signal)
            db.commit()
            logger.info(f"[{ticker}] EARLY_TREND_BREAK signal created: {fired}")
            _fire_telegram("EARLY_TREND_BREAK", ticker, fired, settings)
    else:
        if existing is not None:
            existing.status = "resolved"
            existing.resolved_at = datetime.utcnow()
            db.commit()
            logger.info(f"[{ticker}] EARLY_TREND_BREAK signal resolved.")


def evaluate_stop_loss_signals(holding: Holding, db: Session, settings: dict) -> None:
    """
    Evaluate STOP_LOSS condition: triggers when price drops below stop_loss_pct from entry.
    Non-dismissible signal — once hit, must be handled.
    """
    ticker = holding.ticker
    stop_loss_pct = float(settings.get("stop_loss_pct", -5.0))

    latest_bar = db.execute(
        select(PriceBar)
        .where(PriceBar.ticker == ticker)
        .order_by(PriceBar.bar_date.desc())
        .limit(1)
    ).scalar_one_or_none()

    if latest_bar is None:
        return

    loss_pct = (latest_bar.close - holding.entry_price) / holding.entry_price * 100.0

    existing = _active_signal(ticker, "STOP_LOSS", holding.id, db)

    if loss_pct <= stop_loss_pct:
        if existing is None:
            signal = Signal(
                ticker=ticker,
                holding_id=holding.id,
                signal_type="STOP_LOSS",
                conditions=json.dumps(
                    [
                        f"Loss: {loss_pct:.2f}% (threshold: {stop_loss_pct}%)",
                        f"Entry: ${holding.entry_price:.2f}, Current: ${latest_bar.close:.2f}",
                    ]
                ),
                triggered_at=datetime.utcnow(),
                status="active",
                is_read=False,
            )
            db.add(signal)
            db.commit()
            logger.info(f"[{ticker}] STOP_LOSS signal created at {loss_pct:.2f}% loss.")
            _fire_telegram("STOP_LOSS", ticker, json.loads(signal.conditions), settings)
    else:
        if existing is not None:
            existing.status = "resolved"
            existing.resolved_at = datetime.utcnow()
            db.commit()
            logger.info(f"[{ticker}] STOP_LOSS signal resolved (recovered above threshold).")


def evaluate_trailing_stop_signals(holding: Holding, db: Session, settings: dict) -> None:
    """
    Evaluate TRAILING_STOP condition: tracks peak price, triggers when price drops X% from peak.
    Updates peak_price on every evaluation.
    """
    ticker = holding.ticker
    trailing_stop_pct = float(settings.get("trailing_stop_pct", -15.0))

    latest_bar = db.execute(
        select(PriceBar)
        .where(PriceBar.ticker == ticker)
        .order_by(PriceBar.bar_date.desc())
        .limit(1)
    ).scalar_one_or_none()

    if latest_bar is None:
        return

    current_price = latest_bar.close
    peak = holding.peak_price if holding.peak_price is not None else holding.entry_price

    if current_price > peak:
        holding.peak_price = current_price
        db.commit()
        peak = current_price

    drawdown_pct = (current_price - peak) / peak * 100.0

    existing = _active_signal(ticker, "TRAILING_STOP", holding.id, db)

    if drawdown_pct <= trailing_stop_pct:
        if existing is None:
            signal = Signal(
                ticker=ticker,
                holding_id=holding.id,
                signal_type="TRAILING_STOP",
                conditions=json.dumps(
                    [
                        (
                            f"Drawdown from peak: {drawdown_pct:.2f}%"
                            f" (threshold: {trailing_stop_pct}%)"
                        ),
                        f"Peak: ${peak:.2f}, Current: ${current_price:.2f}",
                    ]
                ),
                triggered_at=datetime.utcnow(),
                status="active",
                is_read=False,
            )
            db.add(signal)
            db.commit()
            logger.info(f"[{ticker}] TRAILING_STOP signal created at {drawdown_pct:.2f}% drawdown.")
            _fire_telegram("TRAILING_STOP", ticker, json.loads(signal.conditions), settings)
    else:
        if existing is not None:
            existing.status = "resolved"
            existing.resolved_at = datetime.utcnow()
            db.commit()
            logger.info(f"[{ticker}] TRAILING_STOP signal resolved (price recovered).")


def evaluate_all_signals(db: Session) -> None:
    """Run buy and sell signal evaluation for all active stocks and holdings."""
    settings = _load_settings(db)

    tickers = (
        db.execute(select(WatchlistStock.ticker).where(WatchlistStock.is_active)).scalars().all()
    )

    for ticker in tickers:
        try:
            evaluate_buy_signals(ticker, db, settings)
        except Exception as exc:
            logger.error(f"Buy signal eval failed for {ticker}: {exc}")
            db.rollback()

    open_holdings = db.execute(select(Holding).where(Holding.is_open)).scalars().all()

    for holding in open_holdings:
        try:
            evaluate_stop_loss_signals(holding, db, settings)
            evaluate_trailing_stop_signals(holding, db, settings)
            evaluate_sell_signals(holding, db, settings)
            evaluate_early_trend_break_signals(holding, db, settings)
        except Exception as exc:
            logger.error(f"Sell signal eval failed for holding {holding.id}: {exc}")
            db.rollback()


def _latest_indicator(
    ticker: str, db: Session, sma_period: int, rsi_period: int
) -> IndicatorValue | None:
    return db.execute(
        select(IndicatorValue)
        .where(
            IndicatorValue.ticker == ticker,
            IndicatorValue.sma_period == sma_period,
            IndicatorValue.rsi_period == rsi_period,
        )
        .order_by(IndicatorValue.calc_date.desc())
        .limit(1)
    ).scalar_one_or_none()


def _active_signal(
    ticker: str, signal_type: str, holding_id: int | None, db: Session
) -> Signal | None:
    query = select(Signal).where(
        Signal.ticker == ticker,
        Signal.signal_type == signal_type,
        Signal.status == "active",
    )
    if holding_id is not None:
        query = query.where(Signal.holding_id == holding_id)
    else:
        query = query.where(Signal.holding_id.is_(None))
    return db.execute(query).scalar_one_or_none()


def _check_divergence(ticker: str, rsi_period: int, db: Session) -> bool:
    """Fetch price bars and compute bearish divergence."""
    needed = rsi_period * 3
    bars = (
        db.execute(
            select(PriceBar)
            .where(PriceBar.ticker == ticker)
            .order_by(PriceBar.bar_date.desc())
            .limit(needed)
        )
        .scalars()
        .all()
    )

    if len(bars) < rsi_period + 1:
        return False

    closes = [b.close for b in reversed(bars)]
    rsi_series = _build_rsi_series(closes, rsi_period)

    if len(rsi_series) < rsi_period:
        return False

    return detect_bearish_divergence(closes[-(rsi_period):], rsi_series[-(rsi_period):], rsi_period)
