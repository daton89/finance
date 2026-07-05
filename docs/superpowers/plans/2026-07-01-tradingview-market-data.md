# TradingView Market Data & Composite Scoring Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build out `packages/market-data` with a hand-rolled TradingView scanner-endpoint adapter that feeds `PriceBar`/`IndicatorValue`/a new `ExternalRating` table, then implement `finance_signal_engine`'s `_composite_score`/`_rating_downgrade` (currently `FIXME` stubs that always return neutral) for real using that data — replacing the never-wired-up `VVImport`/`VVRating` VectorVest models.

**Architecture:** A new `TradingViewClient` (httpx) POSTs a single batched request to TradingView's public (unofficial) scanner endpoint for the whole watchlist, returning current-value technicals + a `Recommend.All` rating per symbol. `refresh_market_data()` upserts that into `PriceBar`/`IndicatorValue`/`ExternalRating`. Signal-engine's two scoring stubs then read those tables. A manual `POST /api/market-data/refresh` endpoint triggers it, guarded against rapid re-calls.

**Tech Stack:** httpx (async), SQLAlchemy 2.0, pytest + pytest-asyncio (`httpx.MockTransport` for HTTP mocking, no live network calls in tests), FastAPI.

## Global Constraints

- No scheduler/cron — manual refresh endpoint only (`POST /api/market-data/refresh`).
- No changes to `finance_optimizer`, to signal-engine's rule structure/Telegram path, or to holding-based stop-loss/trailing-stop logic — only `_composite_score` and `_rating_downgrade` get implemented.
- `WatchlistStock.tv_symbol` is filled in **manually** per ticker (`EXCHANGE:TICKER` form, e.g. `NASDAQ:AAPL`) — no auto-resolution/search step. Tickers with `tv_symbol IS NULL` are skipped during refresh.
- TradingView scanner endpoint contract was verified live during design (not assumed from docs): `POST https://scanner.tradingview.com/global/scan` with body `{"symbols": {"tickers": [...], "query": {"types": []}}, "columns": [...]}` returns `{"totalCount": N, "data": [{"s": "EXCHANGE:TICKER", "d": [<values in column order>]}]}`. The `global` screener accepts mixed-exchange tickers in one request (confirmed with a live US + German ticker mix) — no per-region grouping needed. Unknown/invalid tickers are simply absent from `data` (HTTP 200, not an error). No special headers required.
- This repo has no Alembic — schema changes are plain SQLAlchemy model edits picked up by `Base.metadata.create_all` (adds new tables/columns are NOT altered on existing DBs, but the local `finance.db` has 0 rows in every affected table today, so this is a non-issue; dropped tables are simply left as orphans in any existing local SQLite file, harmless for a personal dev DB).
- This plan is independent of the separately-planned `apps/web` portfolio frontend (`docs/superpowers/plans/2026-07-01-portfolio-webapp.md`) — as of writing that plan has not been executed yet, so this plan is written against `apps/backend/main.py`'s current state (no CORS, no `Depends(get_db)`, manual `SessionLocal()` pattern). If both plans are executed, both touch `apps/backend/main.py` and `apps/backend/pyproject.toml` in non-overlapping ways (different endpoints/imports) — expect a trivial merge, not a conflict.

---

### Task 1: Core model changes — `tv_symbol`, `ExternalRating`, remove VectorVest models

**Files:**
- Modify: `packages/core/src/finance_core/models.py`
- Modify: `packages/core/src/finance_core/__init__.py`
- Create: `packages/conftest.py`
- Create: `packages/core/tests/test_models.py`

**Interfaces:**
- Produces: `finance_core.models.ExternalRating` (`ticker: str, source: str, recommendation: str, score: float | None, fetched_at: datetime`), `WatchlistStock.tv_symbol: str | None` — consumed by Tasks 3 and 5.
- Removes: `finance_core.models.VVImport`, `finance_core.models.VVRating` (confirmed zero consumers anywhere in the repo before this plan was written).

- [ ] **Step 1: Create the shared test-database conftest for the `packages/` workspace**

Create `packages/conftest.py`:

```python
import os
import tempfile

_fd, _path = tempfile.mkstemp(suffix=".db")
os.close(_fd)
os.environ["DATABASE_URL"] = f"sqlite:///{_path}"

import pytest  # noqa: E402
from finance_core.base import Base, engine  # noqa: E402
from finance_core.models import (  # noqa: E402
    ExternalRating,
    IndicatorValue,
    PriceBar,
    WatchlistStock,
)

Base.metadata.create_all(bind=engine)


@pytest.fixture(autouse=True)
def _clean_tables():
    yield
    with engine.begin() as conn:
        conn.execute(ExternalRating.__table__.delete())
        conn.execute(IndicatorValue.__table__.delete())
        conn.execute(PriceBar.__table__.delete())
        conn.execute(WatchlistStock.__table__.delete())
```

This must be set before `finance_core.base` is imported anywhere else in the `packages/` test session (it creates a temp SQLite file and points `DATABASE_URL` at it), and is shared by this task's tests and Tasks 3 and 5's tests, which all run under one `cd packages && uv run pytest` invocation.

- [ ] **Step 2: Write the failing tests**

Create `packages/core/tests/test_models.py`:

```python
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
```

- [ ] **Step 3: Run tests, confirm they fail**

Run: `cd packages && uv run pytest core/tests/test_models.py -v`
Expected: FAIL — `tv_symbol` column doesn't exist yet, `ExternalRating` doesn't exist yet, `VVImport`/`VVRating` still exist.

- [ ] **Step 4: Modify `packages/core/src/finance_core/models.py`**

Replace this block:

```python
class WatchlistStock(Base):
    __tablename__ = "watchlist_stocks"

    id              = Column(Integer, primary_key=True, autoincrement=True)
    ticker          = Column(Text, nullable=False, unique=True)
    company_name    = Column(Text)
    added_at        = Column(DateTime, nullable=False, server_default=func.now())
    is_active       = Column(Boolean, nullable=False, default=True)
    notes           = Column(Text)
    exchange_ticker = Column(Text, nullable=True)
    isin            = Column(Text, nullable=True)

    holdings        = relationship("Holding", back_populates="stock")


class VVImport(Base):
    __tablename__ = "vv_imports"

    id           = Column(Integer, primary_key=True, autoincrement=True)
    imported_at  = Column(DateTime, nullable=False, server_default=func.now())
    filename     = Column(Text)
    total_rows   = Column(Integer)
    valid_rows   = Column(Integer)
    skipped_rows = Column(Integer)
    is_stale     = Column(Boolean, nullable=False, default=False)

    ratings      = relationship("VVRating", back_populates="import_session")


class VVRating(Base):
    __tablename__ = "vv_ratings"

    id             = Column(Integer, primary_key=True, autoincrement=True)
    import_id      = Column(Integer, ForeignKey("vv_imports.id"), nullable=False)
    ticker         = Column(Text, nullable=False)
    recommendation = Column(Text, nullable=False)
    vst_score      = Column(Real)
    rt_score       = Column(Real)
    rv_score       = Column(Real)
    rs_score       = Column(Real)
    grt_score      = Column(Real)
    stop_price     = Column(Real)
    imported_at    = Column(DateTime, nullable=False)

    import_session = relationship("VVImport", back_populates="ratings")

    __table_args__ = (
        CheckConstraint("recommendation IN ('BUY','HOLD','SELL')", name="ck_vv_rec"),
    )
```

with:

```python
class WatchlistStock(Base):
    __tablename__ = "watchlist_stocks"

    id              = Column(Integer, primary_key=True, autoincrement=True)
    ticker          = Column(Text, nullable=False, unique=True)
    company_name    = Column(Text)
    added_at        = Column(DateTime, nullable=False, server_default=func.now())
    is_active       = Column(Boolean, nullable=False, default=True)
    notes           = Column(Text)
    exchange_ticker = Column(Text, nullable=True)
    isin            = Column(Text, nullable=True)
    tv_symbol       = Column(Text, nullable=True)

    holdings        = relationship("Holding", back_populates="stock")


class ExternalRating(Base):
    __tablename__ = "external_ratings"

    id             = Column(Integer, primary_key=True, autoincrement=True)
    ticker         = Column(Text, nullable=False)
    source         = Column(Text, nullable=False, default="tradingview")
    recommendation = Column(Text, nullable=False)
    score          = Column(Real)
    fetched_at     = Column(DateTime, nullable=False, server_default=func.now())

    __table_args__ = (
        CheckConstraint("recommendation IN ('BUY','HOLD','SELL')", name="ck_external_rating_rec"),
    )
```

- [ ] **Step 5: Modify `packages/core/src/finance_core/__init__.py`**

Replace its contents with:

```python
from finance_core.base import Base, engine, SessionLocal, get_db, DATABASE_URL
from finance_core.models import (
    WatchlistStock, ExternalRating, PriceBar, IndicatorValue,
    Holding, Signal, MarketRegime, StrategyDiscoveryRun, Strategy,
    BacktestRun, BacktestTrade, StockAnalysis, AppSetting,
    StockGroup, StockGroupMembership, ScalableTransaction,
)
from finance_core.config import EXCHANGE_CONFIG, SETTING_DEFAULTS, HISTORY_DAYS_DEFAULT
from finance_core.calendar import is_trading_day, last_trading_day, trading_days_between, is_market_open, next_market_event
from finance_core.validation import validate_ohlcv

__all__ = [
    "Base", "engine", "SessionLocal", "get_db", "DATABASE_URL",
    "WatchlistStock", "ExternalRating", "PriceBar", "IndicatorValue",
    "Holding", "Signal", "MarketRegime", "StrategyDiscoveryRun", "Strategy",
    "BacktestRun", "BacktestTrade", "StockAnalysis", "AppSetting",
    "StockGroup", "StockGroupMembership", "ScalableTransaction",
    "EXCHANGE_CONFIG", "SETTING_DEFAULTS", "HISTORY_DAYS_DEFAULT",
    "validate_ohlcv",
    "is_trading_day", "last_trading_day", "trading_days_between",
    "is_market_open", "next_market_event",
]
```

- [ ] **Step 6: Run tests, confirm they pass**

Run: `cd packages && uv run pytest core/tests/test_models.py -v`
Expected: 3 passed.

- [ ] **Step 7: Commit**

```bash
git add packages/conftest.py packages/core/src/finance_core/models.py packages/core/src/finance_core/__init__.py packages/core/tests/test_models.py
git commit -m "core: add ExternalRating + tv_symbol, remove unused VectorVest models"
```

---

### Task 2: `TradingViewClient` — scanner endpoint adapter

**Files:**
- Create: `packages/market-data/src/finance_market_data/tradingview.py`
- Create: `packages/market-data/tests/test_tradingview.py`

**Interfaces:**
- Produces: `TVQuote` dataclass (`symbol, close, open, high, low, volume, rsi, sma20, sma50, sma200, ema20, atr, adx, recommend_all`), `TradingViewClient.fetch_quotes(symbols: list[str]) -> list[TVQuote]` — consumed by Task 3.

- [ ] **Step 1: Write the failing tests**

Create `packages/market-data/tests/test_tradingview.py`:

```python
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
```

- [ ] **Step 2: Run tests, confirm they fail**

Run: `cd packages && uv run pytest market-data/tests/test_tradingview.py -v`
Expected: FAIL — `finance_market_data.tradingview` doesn't exist yet.

- [ ] **Step 3: Create `packages/market-data/src/finance_market_data/tradingview.py`**

```python
from __future__ import annotations

from dataclasses import dataclass

import httpx

SCAN_URL = "https://scanner.tradingview.com/global/scan"

COLUMNS = [
    "close", "open", "high", "low", "volume",
    "RSI", "SMA20", "SMA50", "SMA200", "EMA20", "ATR", "ADX", "Recommend.All",
]


@dataclass
class TVQuote:
    symbol: str
    close: float
    open: float
    high: float
    low: float
    volume: int
    rsi: float | None
    sma20: float | None
    sma50: float | None
    sma200: float | None
    ema20: float | None
    atr: float | None
    adx: float | None
    recommend_all: float | None


class TradingViewClient:
    """Thin HTTP adapter for TradingView's public (unofficial) scanner endpoint."""

    def __init__(self, timeout: float = 15.0, transport: httpx.BaseTransport | None = None):
        self.timeout = timeout
        self._transport = transport

    async def fetch_quotes(self, symbols: list[str]) -> list[TVQuote]:
        """
        Batched lookup of current-value technicals + rating for `symbols`
        (each in "EXCHANGE:TICKER" form, e.g. "NASDAQ:AAPL"). One HTTP
        request regardless of how many symbols are passed. Symbols not
        found on TradingView are simply absent from the result.
        """
        if not symbols:
            return []

        payload = {
            "symbols": {"tickers": symbols, "query": {"types": []}},
            "columns": COLUMNS,
        }

        async with httpx.AsyncClient(timeout=self.timeout, transport=self._transport) as client:
            resp = await client.post(SCAN_URL, json=payload)
            resp.raise_for_status()
            body = resp.json()

        quotes = []
        for row in body.get("data", []):
            d = row["d"]
            quotes.append(
                TVQuote(
                    symbol=row["s"],
                    close=d[0],
                    open=d[1],
                    high=d[2],
                    low=d[3],
                    volume=int(d[4]),
                    rsi=d[5],
                    sma20=d[6],
                    sma50=d[7],
                    sma200=d[8],
                    ema20=d[9],
                    atr=d[10],
                    adx=d[11],
                    recommend_all=d[12],
                )
            )
        return quotes
```

- [ ] **Step 4: Run tests, confirm they pass**

Run: `cd packages && uv run pytest market-data/tests/test_tradingview.py -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add packages/market-data/src/finance_market_data/tradingview.py packages/market-data/tests/test_tradingview.py
git commit -m "market-data: add TradingViewClient scanner-endpoint adapter"
```

---

### Task 3: `refresh_market_data` — upsert PriceBar/IndicatorValue/ExternalRating

**Files:**
- Create: `packages/market-data/src/finance_market_data/refresh.py`
- Create: `packages/market-data/tests/test_refresh.py`

**Interfaces:**
- Consumes: `TVQuote`, `TradingViewClient` (Task 2, duck-typed — tests substitute a fake with the same `async fetch_quotes(symbols) -> list[TVQuote]` shape); `WatchlistStock.tv_symbol`, `ExternalRating` (Task 1).
- Produces: `RefreshResult(refreshed: list[str], skipped: list[dict])`, `refresh_market_data(db: Session, client=None) -> RefreshResult` — consumed by Task 6.

- [ ] **Step 1: Write the failing tests**

Create `packages/market-data/tests/test_refresh.py`:

```python
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
```

- [ ] **Step 2: Run tests, confirm they fail**

Run: `cd packages && uv run pytest market-data/tests/test_refresh.py -v`
Expected: FAIL — `finance_market_data.refresh` doesn't exist yet.

- [ ] **Step 3: Create `packages/market-data/src/finance_market_data/refresh.py`**

```python
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


async def refresh_market_data(db: Session, client: TradingViewClient | None = None) -> RefreshResult:
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
```

- [ ] **Step 4: Run tests, confirm they pass**

Run: `cd packages && uv run pytest market-data/tests/test_refresh.py -v`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add packages/market-data/src/finance_market_data/refresh.py packages/market-data/tests/test_refresh.py
git commit -m "market-data: add refresh_market_data upsert pipeline"
```

---

### Task 4: Trim `finance_indicators` to the multi-bar functions only

**Files:**
- Modify: `packages/indicators/src/finance_indicators/indicators.py`
- Modify: `packages/indicators/src/finance_indicators/__init__.py`
- Create: `packages/indicators/tests/test_indicators.py`

**Interfaces:**
- Removes: `calc_sma`, `calc_rsi`, `calc_ema`, `calc_pct_from_sma`, `calc_atr`, `calc_adx`, `calc_all_indicators` (TradingView now supplies these directly — Task 2/3).
- Keeps: `calc_sma_slope`, `classify_regime`, `compute_trend_phase`, `detect_bearish_divergence`, `_build_rsi_series` (multi-bar logic, still consumed by `finance_signal_engine.engine` via `from finance_indicators.indicators import _build_rsi_series, detect_bearish_divergence` — that import is unaffected since it goes straight to the submodule, not through `__init__.py`).

- [ ] **Step 1: Write a smoke test for what's kept**

Create `packages/indicators/tests/test_indicators.py`:

```python
from finance_indicators.indicators import (
    _build_rsi_series,
    classify_regime,
    compute_trend_phase,
    detect_bearish_divergence,
)


def test_classify_regime_still_importable_and_works():
    label, score = classify_regime(sma50=110, sma200=100, adx=30, rsi=65, sma20=115, price=118)
    assert label in {"STRONG_BULL", "BULL", "WEAK_BULL"}
    assert score > 0


def test_removed_functions_are_gone():
    import finance_indicators.indicators as indicators

    assert not hasattr(indicators, "calc_sma")
    assert not hasattr(indicators, "calc_rsi")
    assert not hasattr(indicators, "calc_ema")
    assert not hasattr(indicators, "calc_atr")
    assert not hasattr(indicators, "calc_adx")
    assert not hasattr(indicators, "calc_pct_from_sma")
    assert not hasattr(indicators, "calc_all_indicators")
```

- [ ] **Step 2: Run test, confirm the second half fails**

Run: `cd packages && uv run pytest indicators/tests/test_indicators.py -v`
Expected: `test_classify_regime_still_importable_and_works` PASSES, `test_removed_functions_are_gone` FAILS (functions still present).

- [ ] **Step 3: Replace `packages/indicators/src/finance_indicators/indicators.py`**

```python
"""
Technical indicator calculations.
All functions operate on plain Python lists and have no database dependency.
"""

from __future__ import annotations


def calc_sma_slope(sma_values: list[float | None], lookback: int = 5) -> float | None:
    """
    Rate of change of SMA over `lookback` bars.
    Returns percentage change: (current - lookback_ago) / lookback_ago * 100.
    Positive = uptrend, negative = downtrend.
    """
    valid = [v for v in sma_values[-lookback:] if v is not None]
    if len(valid) < 2:
        return None
    if valid[0] == 0:
        return None
    return (valid[-1] - valid[0]) / valid[0] * 100.0


def classify_regime(
    sma50: float | None,
    sma200: float | None,
    adx: float | None,
    rsi: float | None = None,
    sma20: float | None = None,
    price: float | None = None,
) -> tuple[str, float]:
    """
    Multi-factor regime classification using a weighted scoring system.

    Factors (weights sum to 1.0):
      - SMA50 vs SMA200 crossover      (0.35) — primary trend direction
      - SMA20 vs SMA50 alignment        (0.20) — short-term trend alignment
      - Price vs SMA200                 (0.15) — long-term price position
      - Price vs SMA50                  (0.10) — medium-term price position
      - RSI momentum zone               (0.20) — momentum confirmation

    ADX dampening: low ADX (<20) compresses the score toward zero,
    reflecting range-bound / non-trending conditions.

    Returns: (regime_label, direction_score)
      - regime_label: STRONG_BULL | BULL | WEAK_BULL | SIDEWAYS |
                      WEAK_BEAR | BEAR | STRONG_BEAR | UNKNOWN
      - direction_score: float in [-1.0, 1.0], negative = bearish
    """
    if sma50 is None or sma200 is None:
        return "UNKNOWN", 0.0

    score = 0.0

    score += 0.35 if sma50 > sma200 else -0.35

    if sma20 is not None:
        score += 0.20 if sma20 > sma50 else -0.20

    if price is not None:
        score += 0.15 if price > sma200 else -0.15
        score += 0.10 if price > sma50 else -0.10

    if rsi is not None:
        if rsi >= 60:
            score += 0.20
        elif rsi >= 50:
            score += 0.10
        elif rsi >= 40:
            score -= 0.10
        else:
            score -= 0.20

    if adx is not None:
        if adx < 15:
            score *= 0.25
        elif adx < 20:
            score *= 0.55

    score = max(-1.0, min(1.0, round(score, 3)))

    if adx is not None and adx < 15:
        label = "SIDEWAYS"
    elif score >= 0.60:
        label = "STRONG_BULL"
    elif score >= 0.30:
        label = "BULL"
    elif score >= 0.08:
        label = "WEAK_BULL"
    elif score >= -0.08:
        label = "SIDEWAYS"
    elif score >= -0.30:
        label = "WEAK_BEAR"
    elif score >= -0.60:
        label = "BEAR"
    else:
        label = "STRONG_BEAR"

    return label, score


def compute_trend_phase(
    regime: str,
    rsi: float | None,
    adx: float | None,
    direction_score: float = 0.0,
) -> str:
    """
    Infer the current trend phase based on regime label, RSI, and ADX.

    Phases:
      early_bull    — bullish regime just forming, momentum building
      mature_bull   — established uptrend with strong ADX
      topping       — bull regime but RSI overbought (potential reversal)
      ranging       — no clear trend direction
      early_bear    — bearish regime just forming, momentum turning down
      mature_bear   — established downtrend with strong ADX
      bottoming     — bear regime but RSI oversold (potential reversal)
    """
    is_bull = regime in ("STRONG_BULL", "BULL", "WEAK_BULL")
    is_bear = regime in ("STRONG_BEAR", "BEAR", "WEAK_BEAR")
    strong_trend = adx is not None and adx > 28
    rsi_overbought = rsi is not None and rsi >= 70
    rsi_oversold = rsi is not None and rsi <= 30

    if is_bull:
        if rsi_overbought:
            return "topping"
        if strong_trend:
            return "mature_bull"
        return "early_bull"
    if is_bear:
        if rsi_oversold:
            return "bottoming"
        if strong_trend:
            return "mature_bear"
        return "early_bear"
    return "ranging"


def detect_bearish_divergence(closes: list[float], rsis: list[float], window: int) -> bool:
    """
    Detect bearish price/RSI divergence over a rolling `window` of bars.

    A divergence is flagged when:
      - max(close) in the second half > max(close) in the first half  (price HH)
      - max(RSI)   in the second half < max(RSI)   in the first half  (RSI LH)

    The window is split into two equal halves; for odd windows the middle bar
    is included in both halves.

    Returns False if insufficient data.
    """
    if len(closes) < window or len(rsis) < window:
        return False

    recent_closes = closes[-window:]
    recent_rsis = rsis[-window:]

    mid = window // 2

    first_closes = recent_closes[: mid + (window % 2)]
    second_closes = recent_closes[mid:]
    first_rsis = recent_rsis[: mid + (window % 2)]
    second_rsis = recent_rsis[mid:]

    price_hh = max(second_closes) > max(first_closes)
    rsi_lh = max(second_rsis) < max(first_rsis)

    return price_hh and rsi_lh


def _build_rsi_series(closes: list[float], period: int) -> list[float]:
    """
    Build the full RSI time series for a list of closes.
    Used internally for divergence detection.
    """
    if len(closes) < period + 1:
        return []

    rsi_values: list[float] = []
    deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]

    gains = [max(d, 0.0) for d in deltas[:period]]
    losses = [abs(min(d, 0.0)) for d in deltas[:period]]
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period

    def _rsi_from_avgs(ag: float, al: float) -> float:
        if al == 0:
            return 100.0
        return 100.0 - (100.0 / (1.0 + ag / al))

    rsi_values.append(_rsi_from_avgs(avg_gain, avg_loss))

    for delta in deltas[period:]:
        gain = max(delta, 0.0)
        loss = abs(min(delta, 0.0))
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period
        rsi_values.append(_rsi_from_avgs(avg_gain, avg_loss))

    return rsi_values
```

- [ ] **Step 4: Replace `packages/indicators/src/finance_indicators/__init__.py`**

```python
from finance_indicators.indicators import (
    _build_rsi_series,
    calc_sma_slope,
    classify_regime,
    compute_trend_phase,
    detect_bearish_divergence,
)

__all__ = [
    "calc_sma_slope",
    "classify_regime",
    "compute_trend_phase",
    "detect_bearish_divergence",
    "_build_rsi_series",
]
```

- [ ] **Step 5: Run test, confirm it passes**

Run: `cd packages && uv run pytest indicators/tests/test_indicators.py -v`
Expected: 2 passed.

- [ ] **Step 6: Confirm `finance_signal_engine` still imports cleanly**

Run: `cd packages && uv run python -c "import finance_signal_engine.engine"`
Expected: no output, exit 0 (its import goes straight to `finance_indicators.indicators`, unaffected by the `__init__.py` trim).

- [ ] **Step 7: Commit**

```bash
git add packages/indicators/src/finance_indicators/indicators.py packages/indicators/src/finance_indicators/__init__.py packages/indicators/tests/test_indicators.py
git commit -m "indicators: drop atomic calcs now sourced from TradingView, keep multi-bar logic"
```

---

### Task 5: Implement `_composite_score` and `_rating_downgrade`

**Files:**
- Modify: `packages/signal-engine/src/finance_signal_engine/engine.py`
- Create: `packages/signal-engine/tests/test_scoring.py`

**Interfaces:**
- Consumes: `finance_core.models.ExternalRating`, `finance_core.models.IndicatorValue` (Task 1); `_latest_indicator(ticker, db, sma_period, rsi_period)` (already exists in this file).
- Produces: `_composite_score(ticker, db, settings) -> {"is_buy": bool, "is_fresh": bool, "score": int | None}`, `_rating_downgrade(ticker, db) -> bool` — both already consumed by `evaluate_watchlist_signals` (only `["is_buy"]`) and `evaluate_sell_signals` respectively; no changes needed to those callers.

- [ ] **Step 1: Write the failing tests**

Create `packages/signal-engine/tests/test_scoring.py`:

```python
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
```

- [ ] **Step 2: Run tests, confirm they fail**

Run: `cd packages && uv run pytest signal-engine/tests/test_scoring.py -v`
Expected: FAIL — current stubs always return `{"is_buy": False, "is_fresh": False, "score": None}` / `False`, so the "buy" and "downgrade" tests fail their assertions.

- [ ] **Step 3: Implement the two functions**

In `packages/signal-engine/src/finance_signal_engine/engine.py`, change the top import block from:

```python
from finance_core.models import (
    AppSetting,
    Holding,
    IndicatorValue,
    PriceBar,
    Signal,
    StockAnalysis,
    WatchlistStock,
)
```

to:

```python
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
```

Replace:

```python
def _composite_score(ticker: str, db: Session, settings: dict) -> dict:
    """
    FIXME: Option B — multi-indicator technical composite score.
    Current stub: returns neutral (no signal).
    Replace with weighted combination of RSI + SMA proximity + volume + ADX.
    See TODO.md §VectorVest for design spec.
    """
    return {"is_buy": False, "is_fresh": False, "score": None}


def _rating_downgrade(ticker: str, db: Session) -> bool:
    """
    FIXME: VV rating flip replacement for SELL_ALERT.
    Original: previous=BUY -> latest=HOLD/SELL.
    Stub returns False until composite scoring is implemented.
    """
    return False
```

with:

```python
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

    return {"is_buy": score >= 60, "is_fresh": is_fresh, "score": score}


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
```

- [ ] **Step 4: Run tests, confirm they pass**

Run: `cd packages && uv run pytest signal-engine/tests/test_scoring.py -v`
Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add packages/signal-engine/src/finance_signal_engine/engine.py packages/signal-engine/tests/test_scoring.py
git commit -m "signal-engine: implement composite scoring and rating-downgrade from ExternalRating"
```

---

### Task 6: `POST /api/market-data/refresh` endpoint

**Files:**
- Modify: `apps/backend/pyproject.toml`
- Modify: `apps/backend/main.py`
- Create: `apps/backend/tests/conftest.py`
- Create: `apps/backend/tests/test_market_data_refresh.py`

**Interfaces:**
- Consumes: `finance_market_data.refresh.refresh_market_data(db) -> RefreshResult` (Task 3).

- [ ] **Step 1: Add `finance-market-data` + test deps to `apps/backend/pyproject.toml`**

Replace its contents with:

```toml
[project]
name = "finance-backend"
version = "0.1.0"
description = "Finance backend API"
requires-python = ">=3.12"
dependencies = [
    "fastapi>=0.115",
    "uvicorn[standard]>=0.34",
    "jinja2>=3.1",
    "python-multipart>=0.0.18",
    "finance-portfolio",
    "finance-market-data",
]

[tool.uv.sources]
finance-portfolio = { path = "../../packages/portfolio" }
finance-market-data = { path = "../../packages/market-data" }
finance-core = { path = "../../packages/core" }

[dependency-groups]
dev = [
    "pytest>=8.3",
    "httpx>=0.27",
    "pytest-asyncio>=0.24",
]

[tool.pytest.ini_options]
testpaths = ["tests"]
```

- [ ] **Step 2: Sync deps**

Run: `cd apps/backend && uv sync`
Expected: exit 0.

- [ ] **Step 3: Write the test infra + failing test**

Create `apps/backend/tests/conftest.py`:

```python
import os
import tempfile

_fd, _path = tempfile.mkstemp(suffix=".db")
os.close(_fd)
os.environ["DATABASE_URL"] = f"sqlite:///{_path}"

import pytest  # noqa: E402
from finance_core.base import engine  # noqa: E402
from finance_core.models import AppSetting  # noqa: E402


@pytest.fixture(autouse=True)
def _clean_tables():
    yield
    with engine.begin() as conn:
        conn.execute(AppSetting.__table__.delete())
```

Create `apps/backend/tests/test_market_data_refresh.py`:

```python
from fastapi.testclient import TestClient

import main
from finance_market_data.refresh import RefreshResult
from main import app

client = TestClient(app)


def test_refresh_endpoint_runs_and_guards_against_rapid_retrigger(monkeypatch):
    async def fake_refresh(db):
        return RefreshResult(refreshed=["AAPL"], skipped=[])

    monkeypatch.setattr(main, "refresh_market_data", fake_refresh)

    resp1 = client.post("/api/market-data/refresh")
    assert resp1.status_code == 200
    assert resp1.json() == {"refreshed": ["AAPL"], "skipped": []}

    resp2 = client.post("/api/market-data/refresh")
    assert resp2.status_code == 429
```

- [ ] **Step 4: Run test, confirm it fails**

Run: `cd apps/backend && uv run pytest tests/test_market_data_refresh.py -v`
Expected: FAIL with 404 (`/api/market-data/refresh` doesn't exist yet).

- [ ] **Step 5: Add the endpoint to `apps/backend/main.py`**

Change the top import block from:

```python
from fastapi import FastAPI, UploadFile
from fastapi.responses import HTMLResponse

from finance_core.base import Base, engine, SessionLocal
from finance_portfolio import parse_scalable_csv
```

to:

```python
from datetime import datetime

from fastapi import FastAPI, HTTPException, UploadFile
from fastapi.responses import HTMLResponse

from finance_core.base import Base, engine, SessionLocal
from finance_core.models import AppSetting
from finance_market_data.refresh import refresh_market_data
from finance_portfolio import parse_scalable_csv
```

Append at the end of the file:

```python
_REFRESH_SETTING_KEY = "market_data_last_refreshed_at"
_REFRESH_MIN_INTERVAL_SECONDS = 60


@app.post("/api/market-data/refresh")
async def api_refresh_market_data():
    db = SessionLocal()
    try:
        setting = db.get(AppSetting, _REFRESH_SETTING_KEY)
        now = datetime.utcnow()

        if setting is not None:
            last = datetime.fromisoformat(setting.value)
            if (now - last).total_seconds() < _REFRESH_MIN_INTERVAL_SECONDS:
                raise HTTPException(
                    status_code=429,
                    detail=f"Refresh already ran within the last {_REFRESH_MIN_INTERVAL_SECONDS}s",
                )

        result = await refresh_market_data(db)

        if setting is None:
            db.add(AppSetting(key=_REFRESH_SETTING_KEY, value=now.isoformat()))
        else:
            setting.value = now.isoformat()
        db.commit()

        return {"refreshed": result.refreshed, "skipped": result.skipped}
    finally:
        db.close()
```

- [ ] **Step 6: Run test, confirm it passes**

Run: `cd apps/backend && uv run pytest tests/test_market_data_refresh.py -v`
Expected: 1 passed.

- [ ] **Step 7: Commit**

```bash
git add apps/backend/pyproject.toml apps/backend/main.py apps/backend/tests
git commit -m "backend: add POST /api/market-data/refresh with 60s retrigger guard"
```

---

### Task 7: Wrap-up — TODO.md and full regression check

**Files:**
- Modify: `TODO.md`

- [ ] **Step 1: Update `TODO.md`**

In the "Da fare" section, replace any remaining VectorVest-related line (if present) and add a note. Since the current `TODO.md` "Fatto" list doesn't mention VectorVest/signal-engine at all, add this line to the "Fatto ✅" section, after the `packages/portfolio` line:

```markdown
- [x] TradingView-backed market data (`packages/market-data`) + composite scoring in `packages/signal-engine` — replaces unused VectorVest models
```

- [ ] **Step 2: Run the full `packages/` test suite**

Run: `cd packages && uv run pytest -v`
Expected: 3 (core) + 3 (tradingview) + 4 (refresh) + 2 (indicators) + 6 (scoring) = 18 passed.

- [ ] **Step 3: Run the full `apps/backend` test suite**

Run: `cd apps/backend && uv run pytest -v`
Expected: 1 passed (only this plan's test exists so far, since `apps/web`'s backend plan hasn't been executed).

- [ ] **Step 4: Lint check**

Run: `cd packages && uv run ruff check`
Expected: no errors (confirms the indicators/core trims didn't leave unused imports).

- [ ] **Step 5: Commit**

```bash
git add TODO.md
git commit -m "docs: mark TradingView market-data + composite scoring done in TODO.md"
```
