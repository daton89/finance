from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

from finance_core.models import Holding, PriceBar
from sqlalchemy import func as sa_func
from sqlalchemy import select
from sqlalchemy.orm import Session


@dataclass
class Position:
    ticker: str
    shares: float
    entry_price: float
    entry_date: date
    current_price: float | None = None
    market_value: float | None = None
    cost_basis: float | None = None
    unrealised_pnl: float | None = None
    unrealised_pnl_pct: float | None = None
    days_held: int | None = None
    notes: str | None = None


def open_positions(db: Session) -> list[Position]:
    rows = (
        db.execute(
            select(Holding).where(Holding.is_open).order_by(Holding.ticker, Holding.entry_date)
        )
        .scalars()
        .all()
    )

    tickers = {h.ticker for h in rows}
    latest = latest_price_map(db, tickers)

    results: list[Position] = []
    for h in rows:
        curr = latest.get(h.ticker)
        cost = h.shares * h.entry_price
        mv = curr * h.shares if curr else None
        results.append(
            Position(
                ticker=h.ticker,
                shares=h.shares,
                entry_price=h.entry_price,
                entry_date=h.entry_date,
                current_price=curr,
                market_value=mv,
                cost_basis=cost,
                unrealised_pnl=(mv - cost) if (mv is not None and cost) else None,
                unrealised_pnl_pct=((mv / cost) - 1) * 100
                if (mv is not None and cost and cost > 0)
                else None,
                days_held=(date.today() - h.entry_date).days,
                notes=h.notes,
            )
        )
    return results


def closed_positions(db: Session) -> list[dict[str, Any]]:
    rows = (
        db.execute(
            select(Holding)
            .where(Holding.is_open.is_(False))
            .order_by(Holding.sell_date.desc().nullslast(), Holding.ticker)
        )
        .scalars()
        .all()
    )

    return [
        {
            "ticker": h.ticker,
            "shares": h.shares,
            "entry_price": h.entry_price,
            "entry_date": h.entry_date,
            "sell_price": h.sell_price,
            "sell_date": h.sell_date,
            "realised_pnl": h.realised_pnl,
            "days_held": (h.sell_date - h.entry_date).days if h.sell_date else None,
            "notes": h.notes,
        }
        for h in rows
    ]


def all_positions(db: Session) -> list[dict[str, Any]]:
    rows = db.execute(select(Holding).order_by(Holding.ticker)).scalars().all()
    return [
        {
            "id": h.id,
            "ticker": h.ticker,
            "shares": h.shares,
            "entry_price": h.entry_price,
            "entry_date": h.entry_date,
            "is_open": h.is_open,
            "sell_price": h.sell_price,
            "sell_date": h.sell_date,
            "realised_pnl": h.realised_pnl,
            "notes": h.notes,
        }
        for h in rows
    ]


def position_summary(db: Session) -> dict[str, Any]:
    opens = open_positions(db)
    closed = db.execute(
        select(sa_func.count(), sa_func.coalesce(sa_func.sum(Holding.realised_pnl), 0)).where(
            Holding.is_open.is_(False)
        )
    ).one()

    total_cost = sum(p.cost_basis or 0 for p in opens)
    total_mv = sum(p.market_value or 0 for p in opens)
    total_unrealised = sum(p.unrealised_pnl or 0 for p in opens)

    return {
        "open_positions": len(opens),
        "closed_positions": closed[0],
        "total_cost_basis": total_cost,
        "total_market_value": total_mv,
        "total_unrealised_pnl": total_unrealised,
        "total_realised_pnl": float(closed[1] or 0),
    }


def latest_price_map(db: Session, tickers: set[str]) -> dict[str, float]:
    if not tickers:
        return {}
    subq = (
        select(PriceBar.ticker, sa_func.max(PriceBar.bar_date).label("max_date"))
        .where(PriceBar.ticker.in_(tickers))
        .group_by(PriceBar.ticker)
        .subquery()
    )
    rows = db.execute(
        select(PriceBar.ticker, PriceBar.close).join(
            subq, (PriceBar.ticker == subq.c.ticker) & (PriceBar.bar_date == subq.c.max_date)
        )
    ).all()
    return {r.ticker: float(r.close) for r in rows}
