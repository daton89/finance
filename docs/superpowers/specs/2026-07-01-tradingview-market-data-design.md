# TradingView-backed Market Data & Composite Scoring — Design

**Goal:** Build out `packages/market-data` (currently an empty stub) with a hand-rolled TradingView adapter that supplies price bars, technical indicators, and a BUY/HOLD/SELL-style rating for the watchlist — replacing VectorVest as the intended data source for `finance_signal_engine`'s composite scoring, and implementing that scoring for the first time (it exists today only as `FIXME` stubs).

## Context / Findings

Investigation before this design was written turned up two things that reframe the original ask ("replace VectorVest with a TradingView MCP"):

1. **No TradingView MCP is usable here.** The MCP registry has zero TradingView connectors. Community options exist on GitHub (`mikeh-22/tradingview-mcp`, `bidouilles/mcp-tradingview-server`, `fiale-plus/tradingview-mcp-server`) but MCP is shaped for interactive LLM tool-calling, not an unattended cron job — routing a scheduled data refresh through MCP's protocol would add ceremony without benefit. Decision: skip MCP, use a direct HTTP adapter (same pattern as the existing `finance_ai_signals/client.py`).
2. **VectorVest was never actually wired up in this repo.** `VVImport`/`VVRating` (`packages/core/src/finance_core/models.py`) exist only as bare SQLAlchemy models ported from TradeForge — no CSV-parsing logic, no upload endpoint, zero consumers. `finance_signal_engine`'s `_composite_score` and `_rating_downgrade` (`packages/signal-engine/src/finance_signal_engine/engine.py:57-73`) are `FIXME` stubs that always return neutral/`False`. So this isn't a risky migration of working logic — it's a first implementation, with TradingView chosen as the data source instead of VectorVest.

## Non-Goals

- No scheduler/cron infrastructure (manual refresh endpoint only, for v1).
- No changes to `finance_optimizer` (backtest/MTD/assortativity) — unrelated to per-ticker signals.
- No changes to signal-engine's rule structure, Telegram notification path, or holding-based stop-loss/trailing-stop logic — only the two scoring stubs get implemented.
- No automatic TradingView symbol resolution (search-and-cache) — watchlist is small enough for manual entry.
- Not evaluating paid data vendors (Twelve Data, Alpha Vantage, etc.) as an alternative in this pass — TradingView was the explicit starting point and the adapter approach was chosen over that alternative.

## Architecture

### New: `packages/market-data/src/finance_market_data/tradingview.py`

```python
@dataclass
class TVQuote:
    ticker: str
    close: float
    open: float
    high: float
    low: float
    volume: int
    rsi: float | None
    sma20: float | None
    sma50: float | None
    sma200: float | None
    ema: float | None
    atr: float | None
    adx: float | None
    recommend_all: float | None  # TradingView's raw -1..1 technical rating


class TradingViewClient:
    """Thin HTTP adapter for TradingView's public scanner endpoint."""

    def __init__(self, timeout: float = 15.0): ...

    async def fetch_quotes(self, symbols: list[str]) -> list[TVQuote]:
        """
        POST to https://scanner.tradingview.com/{market}/scan with all symbols
        batched into one request. Raises httpx.HTTPStatusError on non-2xx.
        """
```

One request per refresh call regardless of watchlist size (TradingView's scanner endpoint natively accepts a batched symbol list) — this keeps request volume low by construction, which matters given the endpoint is unofficial/scraped rather than a published API.

`TVQuote` → `IndicatorValue` field mapping (fixed periods, matching `IndicatorValue`'s existing schema shape):

| `TVQuote` field | `IndicatorValue` column | Period |
|---|---|---|
| `sma20` | `sma_value` | `sma_period=20` |
| `sma50` | `sma50_value` | — (dedicated column) |
| `sma200` | `sma200_value` | — (dedicated column) |
| `rsi` | `rsi_value` | `rsi_period=14` |
| `ema` | `ema_value` | — |
| `atr` | `atr_value` | — |
| `adx` | `adx_value` | — |
| `close`, `sma20` | `pct_from_sma` | computed as `(close/sma20 - 1) * 100` |

### New: `packages/market-data/src/finance_market_data/refresh.py`

```python
@dataclass
class RefreshResult:
    refreshed: list[str]
    skipped: list[dict]  # {"ticker": ..., "reason": "no tv_symbol"}


def refresh_market_data(db: Session) -> RefreshResult:
    """
    For every active WatchlistStock with a non-null tv_symbol:
      - call TradingViewClient.fetch_quotes (one batched call)
      - upsert today's PriceBar
      - upsert today's IndicatorValue (rsi/sma/ema/atr/adx columns already exist)
      - insert a new ExternalRating row (recommendation derived from recommend_all)
    Tickers with tv_symbol IS NULL are skipped and reported in RefreshResult.skipped.
    """
```

### New endpoint: `apps/backend/main.py`

```python
@app.post("/api/market-data/refresh")
def api_refresh_market_data(db: Session = Depends(get_db)):
    """Manual trigger. Rejects with 429 if called again within 60s of the last run."""
```

A simple last-refreshed-at guard prevents accidental rapid-fire calls against the unofficial endpoint: the refresh timestamp is stored as an `AppSetting` row keyed `market_data_last_refreshed_at` (reusing the existing settings table — no new table needed).

## Data Model Changes

`packages/core/src/finance_core/models.py`:

- **Add** `WatchlistStock.tv_symbol: str | None` — manually-entered TradingView symbol in `EXCHANGE:TICKER` form (e.g. `NASDAQ:AAPL`, `XETR:SAP`). Null means "not yet mapped, skip in refresh."
- **Add** `ExternalRating` model:
  ```python
  class ExternalRating(Base):
      __tablename__ = "external_ratings"

      id             = Column(Integer, primary_key=True, autoincrement=True)
      ticker         = Column(Text, nullable=False)
      source         = Column(Text, nullable=False, default="tradingview")
      recommendation = Column(Text, nullable=False)  # BUY / HOLD / SELL
      score          = Column(Real)  # raw recommend_all, -1..1
      fetched_at     = Column(DateTime, nullable=False, server_default=func.now())
  ```
- **Remove** `VVImport` and `VVRating` models entirely (dead code, zero consumers, confirmed above).

`recommend_all` → `recommendation` mapping (TradingView's own convention):
- `>= 0.5` → BUY (Strong Buy/Buy)
- `<= -0.5` → SELL (Strong Sell/Sell)
- otherwise → HOLD (Neutral)

## Signal-Engine Changes

`packages/signal-engine/src/finance_signal_engine/engine.py`:

- **`_composite_score(ticker, db, settings)`** — implemented for real per its own existing docstring intent ("weighted combination of RSI + SMA proximity + volume + ADX"): reads the latest `IndicatorValue` row (`rsi_value`, `pct_from_sma`, `adx_value`) and latest `ExternalRating` for the ticker, combines into `{"is_buy": bool, "is_fresh": bool, "score": float}`.
- **`_rating_downgrade(ticker, db)`** — implemented per its own docstring intent: compares the two most recent `ExternalRating.recommendation` values for the ticker, returns `True` on a BUY → HOLD/SELL flip.
- No other function in this file changes. Signal types, Telegram notification path (`_fire_telegram`), and holding-based stop-loss/trailing-stop evaluators are untouched.

## `finance_indicators` Changes

- **Drop**: `calc_sma`, `calc_rsi`, `calc_ema`, `calc_atr`, `calc_adx`, `calc_pct_from_sma`, `calc_all_indicators` — TradingView now supplies these as direct current-value fields, no local computation needed.
- **Keep**: `calc_sma_slope`, `classify_regime`, `compute_trend_phase`, `detect_bearish_divergence`, `_build_rsi_series` — these are multi-bar derived logic specific to this project's strategy, operating on local `PriceBar` history that `refresh_market_data` continues to populate. TradingView's scanner gives snapshots, not historical series, so this logic must stay local.

## Testing

- `finance_market_data`: unit tests for `TradingViewClient.fetch_quotes` against a mocked httpx transport (no live network calls in tests), covering the batched-request shape and response parsing into `TVQuote`.
- `finance_market_data`: unit tests for `refresh_market_data` against an in-memory/temp SQLite DB — covers upsert behavior for `PriceBar`/`IndicatorValue`/`ExternalRating`, and the `tv_symbol IS NULL` skip path.
- `finance_signal_engine`: unit tests for `_composite_score` and `_rating_downgrade` against seeded `IndicatorValue`/`ExternalRating` rows, replacing their current always-neutral behavior.
- `apps/backend`: test for `POST /api/market-data/refresh` including the 60s re-trigger guard (429 on immediate repeat call).

## Risks (accepted, not mitigated further here)

- TradingView's scanner endpoint is unofficial/reverse-engineered — it can change or block without notice. No SLA. This risk was surfaced and knowingly accepted in favor of the "direct HTTP adapter" approach over official paid vendors or MCP indirection.
- Scraping TradingView's endpoint may be against their Terms of Service. This is a personal, low-volume (single batched request per manual refresh), non-commercial use case, but the risk exists and is the user's to accept.
