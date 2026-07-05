from __future__ import annotations

import csv
import io
import logging
import os
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Optional

import httpx
from finance_core.models import Holding, ScalableTransaction, WatchlistStock
from sqlalchemy import select
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

TWELVE_DATA_BASE = "https://api.twelvedata.com"

_SKIP_STATUSES = {"Cancelled", "Rejected", "Pending"}
_VALID_ASSET_TYPES = {"Security", "ETF", "ETN"}


@dataclass
class ScalableImportResult:
    transactions_imported: int = 0
    holdings_created: int = 0
    holdings_closed: int = 0
    tickers_added: list[str] = field(default_factory=list)
    skipped: list[dict] = field(default_factory=list)


def _parse_eu_number(s: str) -> float:
    s = s.strip()
    if not s or s in ("-", "\u2014", ""):
        return 0.0
    s = s.replace(".", "").replace(",", ".")
    return float(s)


async def _search_isin_twelve_data(description: str, api_key: str) -> Optional[tuple[str, str]]:
    url = f"{TWELVE_DATA_BASE}/symbol_search"
    params = {"symbol": description, "outputsize": 20, "apikey": api_key}
    preferred_mics = {"XETR", "XMUN", "XPAR", "XLON"}
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(url, params=params)
        if resp.status_code == 200:
            items = resp.json().get("data", [])
            for item in items:
                if item.get("mic_code") in preferred_mics:
                    return item["symbol"], item.get("exchange", "")
            if items:
                return items[0]["symbol"], items[0].get("exchange", "")
    except Exception as exc:
        logger.warning(f"[ISIN lookup] Twelve Data search failed for '{description}': {exc}")
    return None


def _sanitize_ticker(description: str, isin: str) -> str:
    words = description.upper().split()
    base = "".join(w[:4] for w in words[:2])[:8]
    suffix = isin[-4:] if isin else "XXXX"
    return f"{base}.{suffix}"


async def _resolve_ticker_for_isin(isin: str, description: str, db: Session) -> str:
    existing = db.execute(
        select(WatchlistStock).where(WatchlistStock.isin == isin)
    ).scalar_one_or_none()
    if existing:
        return existing.ticker

    api_key = os.getenv("TWELVE_DATA_API_KEY", "")
    symbol: Optional[str] = None
    exchange_ticker: Optional[str] = None

    if api_key:
        result = await _search_isin_twelve_data(description, api_key)
        if result:
            symbol, exchange = result
            exchange_ticker = symbol
            logger.info(f"[scalable] ISIN {isin} ({description!r}) \u2192 {symbol}")
            if exchange in {"FSX", "XETR", "XMUN", "TGATE", "BER"}:
                if not symbol.endswith(".DE") and not symbol.endswith(".F"):
                    symbol = f"{symbol}.DE"

    if not symbol:
        symbol = _sanitize_ticker(description, isin)
        if not symbol.endswith(".DE"):
            symbol = f"{symbol}.DE"
        logger.warning(f"[scalable] ISIN {isin} ({description!r}) \u2192 fallback ticker {symbol}")

    stock = db.execute(
        select(WatchlistStock).where(WatchlistStock.ticker == symbol)
    ).scalar_one_or_none()

    if stock is None:
        stock = WatchlistStock(
            ticker=symbol,
            company_name=description,
            isin=isin,
            exchange_ticker=exchange_ticker,
            is_active=True,
        )
        db.add(stock)
        db.commit()
        logger.info(f"[scalable] Added new watchlist stock: {symbol} (ISIN={isin})")
    elif stock.isin is None:
        stock.isin = isin
        if exchange_ticker and not stock.exchange_ticker:
            stock.exchange_ticker = exchange_ticker
        db.commit()

    return stock.ticker


def _create_buy_lot(
    ticker: str, shares: float, price: float, entry_date: date, notes: str, db: Session
) -> Holding:
    lot = Holding(
        ticker=ticker,
        shares=shares,
        entry_price=price,
        entry_date=entry_date,
        notes=notes,
        is_open=True,
    )
    db.add(lot)
    db.commit()
    db.refresh(lot)
    return lot


def _close_sell_lot(
    ticker: str, shares_sold: float, sell_price: float, sell_date: date, db: Session
) -> list[Holding]:
    open_lots = (
        db.execute(
            select(Holding)
            .where(Holding.ticker == ticker, Holding.is_open)
            .order_by(Holding.entry_date)
        )
        .scalars()
        .all()
    )

    if not open_lots:
        logger.warning(f"[scalable] Sell for {ticker} but no open lots found")
        closed = Holding(
            ticker=ticker,
            shares=shares_sold,
            entry_price=sell_price,
            entry_date=sell_date,
            sell_price=sell_price,
            sell_date=sell_date,
            realised_pnl=0.0,
            is_open=False,
            notes="Scalable import - entry price unknown",
        )
        db.add(closed)
        db.commit()
        db.refresh(closed)
        return [closed]

    total_open_shares = sum(lot.shares for lot in open_lots)
    avg_entry = sum(lot.shares * lot.entry_price for lot in open_lots) / total_open_shares

    modified: list[Holding] = []
    remaining_to_sell = shares_sold

    for lot in open_lots:
        if remaining_to_sell <= 0:
            break
        if lot.shares <= remaining_to_sell:
            lot.sell_price = sell_price
            lot.sell_date = sell_date
            lot.realised_pnl = (sell_price - avg_entry) * lot.shares
            lot.is_open = False
            remaining_to_sell -= lot.shares
            modified.append(lot)
        else:
            sold_portion = Holding(
                ticker=ticker,
                shares=remaining_to_sell,
                entry_price=avg_entry,
                entry_date=lot.entry_date,
                sell_price=sell_price,
                sell_date=sell_date,
                realised_pnl=(sell_price - avg_entry) * remaining_to_sell,
                is_open=False,
                notes=f"Partial sell (split from lot {lot.id})",
            )
            db.add(sold_portion)
            lot.shares -= remaining_to_sell
            remaining_to_sell = 0
            db.commit()
            db.refresh(sold_portion)
            modified.append(sold_portion)

    db.commit()
    return modified


async def parse_scalable_csv(contents: bytes, db: Session) -> ScalableImportResult:
    result = ScalableImportResult()

    text_content = contents.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text_content), delimiter=";")

    rows = list(reader)
    if not rows:
        return result

    def _norm(row: dict) -> dict:
        return {k.strip().lower().replace(" ", "_"): v.strip() for k, v in row.items()}

    rows = [_norm(r) for r in rows]

    valid_rows = [
        r
        for r in rows
        if r.get("status", "").lower() == "executed"
        and r.get("assettype", r.get("asset_type", "")).lower() in {"security", "etf", "etn"}
        and r.get("type", "").lower() in {"buy", "sell"}
        and r.get("isin", "").strip()
    ]

    if not valid_rows:
        return result

    incoming_refs = {r["reference"] for r in valid_rows if r.get("reference")}
    existing_txs = (
        db.execute(
            select(ScalableTransaction).where(ScalableTransaction.reference.in_(incoming_refs))
        )
        .scalars()
        .all()
    )

    if existing_txs:
        logger.info(f"[scalable] Re-import: removing {len(existing_txs)} existing transactions")
        for tx in existing_txs:
            if tx.holding_id:
                holding = db.get(Holding, tx.holding_id)
                if holding:
                    db.delete(holding)
            db.delete(tx)
        db.commit()

    valid_rows.sort(key=lambda r: r.get("date_time", r.get("date", "")))

    for row in valid_rows:
        ref = row.get("reference", "").strip()
        isin = row.get("isin", "").strip()
        description = row.get("description", "").strip()
        tx_type = row.get("type", "").strip().capitalize()
        currency = row.get("currency", "EUR").strip()

        try:
            shares = _parse_eu_number(row.get("shares", "0"))
            price = _parse_eu_number(row.get("price", "0"))
            amount = _parse_eu_number(row.get("amount", "0"))
            fee = _parse_eu_number(row.get("fee", "0"))
            tax = _parse_eu_number(row.get("tax", "0"))
        except (ValueError, KeyError) as exc:
            result.skipped.append({"reference": ref, "reason": f"Number parse error: {exc}"})
            continue

        raw_date = row.get("date_time", row.get("date", "")).strip()
        try:
            tx_date = datetime.strptime(raw_date[:10], "%Y-%m-%d").date()
        except ValueError:
            result.skipped.append({"reference": ref, "reason": f"Cannot parse date: {raw_date!r}"})
            continue

        try:
            ticker = await _resolve_ticker_for_isin(isin, description, db)
        except Exception as exc:
            result.skipped.append({"reference": ref, "reason": f"Ticker resolution failed: {exc}"})
            continue

        if ticker not in result.tickers_added:
            result.tickers_added.append(ticker)

        modified_holdings: list[Holding] = []

        if tx_type == "Buy":
            lot = _create_buy_lot(
                ticker=ticker,
                shares=shares,
                price=price,
                entry_date=tx_date,
                notes=f"Scalable Capital import (ref: {ref})",
                db=db,
            )
            modified_holdings = [lot]
            result.holdings_created += 1
        elif tx_type == "Sell":
            modified_holdings = _close_sell_lot(
                ticker=ticker,
                shares_sold=shares,
                sell_price=price,
                sell_date=tx_date,
                db=db,
            )
            result.holdings_closed += 1

        primary_holding_id = modified_holdings[0].id if modified_holdings else None
        tx = ScalableTransaction(
            reference=ref,
            ticker=ticker,
            isin=isin,
            description=description,
            transaction_type=tx_type,
            shares=shares,
            price=price,
            amount=abs(amount),
            fee=abs(fee),
            tax=abs(tax),
            currency=currency,
            transaction_date=tx_date,
            holding_id=primary_holding_id,
        )
        db.add(tx)
        db.commit()
        result.transactions_imported += 1

    result.tickers_added = list(dict.fromkeys(result.tickers_added))
    logger.info(
        f"[scalable] Import complete: {result.transactions_imported} transactions, "
        f"{result.holdings_created} lots created, {result.holdings_closed} lots closed, "
        f"{len(result.tickers_added)} new tickers"
    )
    return result
