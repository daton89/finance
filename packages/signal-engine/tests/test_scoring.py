from datetime import date, datetime, timedelta

from finance_core.base import SessionLocal
from finance_core.models import ExternalRating, IndicatorValue
from finance_signal_engine.engine import _composite_score, _rating_downgrade


def _seed_indicator(rsi=50.0, pct=1.0, adx=25.0):
    db = SessionLocal()
    db.add(
        IndicatorValue(
            ticker="TEST",
            calc_date=date.today(),
            sma_period=20,
            rsi_period=14,
            rsi_value=rsi,
            pct_from_sma=pct,
            adx_value=adx,
        )
    )
    db.commit()
    db.close()


def _seed_rating(recommendation="BUY", score=0.6, fetched_at=None):
    db = SessionLocal()
    db.add(
        ExternalRating(
            ticker="TEST",
            source="tradingview",
            recommendation=recommendation,
            score=score,
            fetched_at=fetched_at or datetime.utcnow(),
        )
    )
    db.commit()
    db.close()


def test_composite_score_neutral_when_no_data():
    db = SessionLocal()
    result = _composite_score("TEST", db, {})
    db.close()
    assert result == {"is_buy": False, "is_fresh": False, "score": None}


def test_composite_score_buy_when_rating_buy_rsi_neutral_and_trending():
    _seed_indicator(rsi=50.0, pct=1.0, adx=25.0)
    _seed_rating(recommendation="BUY", score=0.6)

    db = SessionLocal()
    result = _composite_score("TEST", db, {})
    db.close()

    assert result["is_buy"] is True
    assert result["is_fresh"] is True
    assert result["score"] == 100


def test_composite_score_not_buy_when_rating_is_sell():
    _seed_indicator(rsi=50.0, pct=1.0, adx=25.0)
    _seed_rating(recommendation="SELL", score=-0.6)

    db = SessionLocal()
    result = _composite_score("TEST", db, {})
    db.close()

    assert result["is_buy"] is False


def test_composite_score_not_buy_when_rating_is_hold_even_if_score_reaches_60():
    _seed_indicator(rsi=50.0, pct=1.0, adx=25.0)
    _seed_rating(recommendation="HOLD", score=0.0)

    db = SessionLocal()
    result = _composite_score("TEST", db, {})
    db.close()

    assert result["score"] == 60
    assert result["is_buy"] is False


def test_rating_downgrade_true_on_buy_to_hold_flip():
    _seed_rating(recommendation="BUY", fetched_at=datetime.utcnow() - timedelta(days=1))
    _seed_rating(recommendation="HOLD", fetched_at=datetime.utcnow())

    db = SessionLocal()
    assert _rating_downgrade("TEST", db) is True
    db.close()


def test_rating_downgrade_false_when_still_buy():
    _seed_rating(recommendation="BUY", fetched_at=datetime.utcnow() - timedelta(days=1))
    _seed_rating(recommendation="BUY", fetched_at=datetime.utcnow())

    db = SessionLocal()
    assert _rating_downgrade("TEST", db) is False
    db.close()


def test_rating_downgrade_false_with_only_one_rating():
    _seed_rating(recommendation="BUY")

    db = SessionLocal()
    assert _rating_downgrade("TEST", db) is False
    db.close()
