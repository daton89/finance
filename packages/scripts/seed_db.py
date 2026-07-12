"""One-time migration script: reads portfolio.json and seeds the finance SQLite DB
with WatchlistStock + Holding rows.

Usage (from workspace root):
    cd /Users/daton/coding/finance/packages
    uv run python scripts/seed_db.py
"""

import json
from datetime import date
from pathlib import Path

from finance_core.base import Base, engine, SessionLocal
from finance_core.models import WatchlistStock, Holding

# Path to portfolio.json (alongside this script, at packages root)
PORTFOLIO_JSON = Path(__file__).resolve().parent.parent / "portfolio.json"


def _make_ticker(pos: dict) -> str:
    """Determine ticker for a position. Use `_manual_{last4_isin}` when ticker is empty."""
    ticker = (pos.get("ticker") or "").strip()
    if not ticker:
        isin = pos["isin"]
        return f"_manual_{isin[-4:]}"
    return ticker


def seed():
    print(f"Reading {PORTFOLIO_JSON} ...")
    with open(PORTFOLIO_JSON) as f:
        data = json.load(f)

    positions = data["positions"]
    print(f"Found {len(positions)} positions in portfolio.json\n")

    # Ensure tables exist
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()

    stocks_created = 0
    holdings_created = 0
    holdings_updated = 0
    holdings_deduped = 0
    stocks_skipped = 0

    today = date.today()

    try:
        for pos in positions:
            isin = pos["isin"]
            ticker = _make_ticker(pos)
            company_name = pos["name"]
            yf_currency = pos.get("yf_currency", "")
            is_manual = ticker.startswith("_manual_")

            # Idempotency: skip if WatchlistStock already exists for this ISIN
            existing_stock = (
                db.query(WatchlistStock)
                .filter(WatchlistStock.isin == isin)
                .first()
            )
            if existing_stock:
                print(f"  ⏭  SKIP (already exists): {company_name} ({isin}) → ticker={existing_stock.ticker}")
                stocks_skipped += 1
                stock = existing_stock
            else:
                stock = WatchlistStock(
                    ticker=ticker,
                    company_name=company_name,
                    isin=isin,
                    tv_symbol=ticker if not is_manual else None,
                    is_active=True,
                )
                db.add(stock)
                db.flush()  # flush so we get the id / make ticker visible for FK
                stocks_created += 1
                print(f"  ✅ Created stock: {ticker} — {company_name}")

            # Idempotent Holding: update existing open Holding or create new.
            # Eventuali righe aperte extra per lo stesso ticker (duplicati di
            # run precedenti) vengono chiuse — deve restarne UNA sola aperta.
            open_holdings = (
                db.query(Holding)
                .filter(Holding.ticker == stock.ticker, Holding.is_open == True)
                .order_by(Holding.id)
                .all()
            )
            if open_holdings:
                keep = open_holdings[0]
                keep.entry_price = pos["avg_entry"]
                keep.shares = pos["shares"]
                keep.entry_date = today
                for dup in open_holdings[1:]:
                    dup.is_open = False
                    holdings_deduped += 1
                holdings_updated += 1
                extra = f" ({len(open_holdings) - 1} duplicati chiusi)" if len(open_holdings) > 1 else ""
                print(f"     → Updated holding: {stock.ticker} @ {pos['avg_entry']} x {pos['shares']}{extra}")
            else:
                holding = Holding(
                    ticker=stock.ticker,
                    entry_price=pos["avg_entry"],
                    entry_date=today,
                    shares=pos["shares"],
                    is_open=True,
                    notes=(
                        f"Migrated from portfolio.json | "
                        f"currency={yf_currency} | "
                        f"isin={isin}"
                    ),
                )
                db.add(holding)
                holdings_created += 1
                print(f"     → Created holding: {stock.ticker} @ {pos['avg_entry']} x {pos['shares']}")

        db.commit()
        print(f"\n{'='*60}")
        print(f"Summary:")
        print(f"  Stocks created (new):   {stocks_created}")
        print(f"  Stocks skipped (dup):   {stocks_skipped}")
        print(f"  Holdings created:       {holdings_created}")
        print(f"  Holdings updated:       {holdings_updated}")
        print(f"  Holdings deduped:       {holdings_deduped}")
        print(f"{'='*60}")

    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    seed()
