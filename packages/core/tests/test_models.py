from finance_core.base import SessionLocal
from finance_core.models import ExternalRating, WatchlistStock


def test_watchlist_stock_has_tv_symbol_column():
    db = SessionLocal()
    db.add(WatchlistStock(ticker="TEST", tv_symbol="NASDAQ:TEST", is_active=True))
    db.commit()

    stock = db.query(WatchlistStock).filter_by(ticker="TEST").one()
    assert stock.tv_symbol == "NASDAQ:TEST"
    db.close()


def test_external_rating_can_be_created_and_queried():
    db = SessionLocal()
    db.add(
        ExternalRating(
            ticker="TEST",
            source="tradingview",
            recommendation="BUY",
            score=0.6,
        )
    )
    db.commit()

    rating = db.query(ExternalRating).filter_by(ticker="TEST").one()
    assert rating.recommendation == "BUY"
    assert rating.score == 0.6
    db.close()


def test_vv_models_removed():
    import finance_core.models as models

    assert not hasattr(models, "VVImport")
    assert not hasattr(models, "VVRating")
