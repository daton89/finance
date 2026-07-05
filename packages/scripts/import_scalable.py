#!/usr/bin/env python3
"""Import a Scalable Capital CSV export into the portfolio database.

Usage:
    python scripts/import_scalable.py path/to/Scalable_Capital_Report.csv
"""

import asyncio
import sys

from finance_core.base import Base, SessionLocal, engine
from finance_portfolio import parse_scalable_csv


async def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    path = sys.argv[1]

    Base.metadata.create_all(bind=engine)

    with open(path, "rb") as f:
        contents = f.read()

    db = SessionLocal()
    try:
        result = await parse_scalable_csv(contents, db)
        print(f"Imported:   {result.transactions_imported} transactions")
        print(f"Holdings:   {result.holdings_created} created, {result.holdings_closed} closed")
        if result.tickers_added:
            print(f"New tickers: {', '.join(result.tickers_added)}")
        if result.skipped:
            print(f"Skipped:    {len(result.skipped)} rows")
    finally:
        db.close()


if __name__ == "__main__":
    asyncio.run(main())
