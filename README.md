# Finance

Personal investment management monorepo. Import transactions, track positions & P&L, compute technical indicators, generate trading signals, refresh market data, optimize portfolio allocation.

## Pipeline

```
Market Data ──► Indicators ──► Signals ──► Portfolio ──► Optimizer ──► API
(TradingView)   (SMA/RSI/     (composite    (positions,   (MTD +        (FastAPI)
 yfinance)       MACD/BB)      scoring)      P&L, CSV      Markowitz)
                                             import)
```

## Structure

```
packages/
  core/            # SQLAlchemy ORM models, DB engine, config, trading calendar
  market-data/     # Market data providers (TradingView, yfinance)
  indicators/      # 32 pure-Python technical indicators
  signal-engine/   # Signal generation + composite scoring
  portfolio/       # Positions, P&L, Scalable Capital CSV import
  ai-signals/      # HTTP adapter for external AI signal Worker
  optimizer/       # Portfolio optimization (MTD, assortativity, Markowitz)
  backend/         # FastAPI server (CSV import, market data refresh)
```

## Setup

```bash
make install       # uv sync --all-packages
make dev           # FastAPI on :8000
make test          # run tests
make lint          # ruff check
```

## Status

Active development. Backend functional. Frontends (web/CLI) planned.
