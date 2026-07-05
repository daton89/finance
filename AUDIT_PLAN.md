# Audit Fix Plan

35 items grouped into 6 phases. Each step independently verifiable. Execute in order.

---

## Phase 1 — Foundation (5 steps)

### 1.1 Add `.python-version`

**File:** `/Users/dangeloan/Coding/finance/.python-version`
```
3.12
```

**Verify:** `cat .python-version` prints `3.12`

---

### 1.2 Widen ruff rules

**File:** `packages/pyproject.toml`

Edit `[tool.ruff.lint] select`:
```
select = ["E", "F", "I", "N", "W", "UP", "B", "SIM", "ARG"]
```

**Verify:** `cd packages && uv run ruff check` — passes with no new failures (should warn on existing violations)

---

### 1.3 Add pyright config

**File:** `packages/pyproject.toml`, add after `[tool.ruff]` block:

```toml
[tool.pyright]
include = ["packages"]
typeCheckingMode = "basic"
reportMissingTypeStubs = false
```

Or as `pyproject.toml` at root depending on structure. Since workspace root is `packages/`, add to `packages/pyproject.toml`.

**Verify:** `cd packages && uv run pyright` (after installing pyright: `uv add --dev pyright`). Expect some type errors — baseline them.

---

### 1.4 Clean `.gitignore`

**File:** `.gitignore`

Remove `node_modules/` line (no JS build system). Keep everything else.

**Verify:** `git diff .gitignore` shows only `node_modules/` removed.

---

### 1.5 Add GitHub CI

**File:** `.github/workflows/ci.yml`

```yaml
name: CI
on: [push, pull_request]
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v5
        with:
          version: "latest"
      - run: cd packages && uv sync --all-packages
      - run: cd packages && uv run pytest
      - run: cd packages && uv run ruff check
```

**Verify:** Push would trigger CI. Local: `cd packages && uv run pytest && uv run ruff check`

---

## Phase 2 — Bug Fixes (6 steps)

### 2.1 Fix `check_same_thread` for non-SQLite

**File:** `packages/core/src/finance_core/base.py`

Change `connect_args` to be SQLite-only:
```python
engine = create_engine(DATABASE_URL, connect_args={})

@event.listens_for(engine, "connect")
def _set_sqlite_pragma(dbapi_conn, _connection_record):
    if engine.dialect.name == "sqlite":
        from sqlite3 import Connection as SQLiteConn
        if isinstance(dbapi_conn, SQLiteConn):
            cursor = dbapi_conn.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA busy_timeout=30000")
            cursor.close()
```

**Verify:** `cd packages && uv run pytest` passes. SQLite still works. Postgres/MySQL won't crash on connect.

---

### 2.2 Fix `datetime.utcnow()` in backend

**File:** `packages/backend/main.py`

```python
from datetime import datetime, timezone
# ...
now = datetime.now(timezone.utc)
```

Two occurrences: line 102, line 115.

**Verify:** `make test` passes.

---

### 2.3 Fix `datetime.utcnow()` in signal-engine

**File:** `packages/signal-engine/src/finance_signal_engine/engine.py`

```python
from datetime import datetime, timezone
# ...
triggered_at=datetime.now(timezone.utc),
```

Multiple occurrences: lines 266, 338, 424, 475, 536.

**Verify:** `cd packages && uv run pytest` passes.

---

### 2.4 Fix conftest cleanup — cover all tables

**File:** `packages/conftest.py`

Replace `_clean_tables` fixture to iterate all models:

```python
from finance_core.models import (
    ExternalRating, IndicatorValue, PriceBar, WatchlistStock,
    Holding, Signal, MarketRegime, StrategyDiscoveryRun, Strategy,
    BacktestRun, BacktestTrade, StockAnalysis, AppSetting,
    StockGroup, StockGroupMembership, ScalableTransaction,
)

_ALL_MODELS = [
    ExternalRating, IndicatorValue, PriceBar, WatchlistStock,
    Holding, Signal, MarketRegime, StrategyDiscoveryRun, Strategy,
    BacktestRun, BacktestTrade, StockAnalysis, AppSetting,
    StockGroup, StockGroupMembership, ScalableTransaction,
]

@pytest.fixture(autouse=True)
def _clean_tables():
    yield
    with engine.begin() as conn:
        for model in _ALL_MODELS:
            conn.execute(model.__table__.delete())
```

**Verify:** `cd packages && uv run pytest` — existing tests still pass. No cross-test data leakage.

---

### 2.5 Fix backend conftest same way

**File:** `packages/backend/tests/conftest.py` (after backend moves to `packages/backend/`)

Apply same `_ALL_MODELS` approach.

**Verify:** `cd packages && uv run --package finance-backend pytest` passes.

---

### 2.6 Drop `test_vv_models_removed`

**File:** `packages/core/tests/test_models.py`

Delete the `test_vv_models_removed` function. It was a migration check — permanent noise now.

**Verify:** `cd packages && uv run pytest` — no test removal errors.

---

## Phase 3 — Security (2 steps)

### 3.1 Remove secrets from `SETTING_DEFAULTS`

**File:** `packages/core/src/finance_core/config.py`

Remove these keys from `SETTING_DEFAULTS`:
- `telegram_bot_token`
- `telegram_chat_id`

Update `_fire_telegram` in `engine.py` to read from env vars instead:

```python
import os

def _fire_telegram(...):
    if settings.get("telegram_notifications_enabled") != "true":
        return
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN") or settings.get("telegram_bot_token")
    chat_id = os.getenv("TELEGRAM_CHAT_ID") or settings.get("telegram_chat_id")
    if not bot_token or not chat_id:
        return
    # ... rest of function
```

This is backward-compatible: env var takes precedence, but old DB value still works.

**Verify:** `cd packages && uv run pytest` passes. Old configs still work if they exist in DB. New installs don't store secrets.

---

### 3.2 Remove dead VV config keys

**File:** `packages/core/src/finance_core/config.py`

Remove from `SETTING_DEFAULTS`:
- `vv_source_id`
- `vv_sync_enabled`

**Verify:** `cd packages && uv run pytest` passes.

---

## Phase 4 — Deprecation Cleanup (2 steps)

### 4.1 Replace `pytz` with `zoneinfo`

**File:** `packages/core/src/finance_core/calendar.py`

```python
from zoneinfo import ZoneInfo
# Remove: import pytz
# Replace: _ET = pytz.timezone("US/Eastern")
_ET = ZoneInfo("US/Eastern")
```

Replace all `.localize()` calls with `.replace(tzinfo=...)`:
- `now = _ET.localize(now)` → `now = now.replace(tzinfo=_ET)` (only if `now.tzinfo is None`)
- `_ET.localize(datetime(...))` → `datetime(..., tzinfo=_ET)`

**File:** `packages/core/pyproject.toml` — remove `pytz` from dependencies.

**Verify:** `cd packages && uv sync` succeeds. `cd packages && uv run pytest` passes. Calendar functions still work.

---

### 4.2 Fix `volume < 0` → `volume < 0 or volume == 0`

Actually, looking at the code again: the bug is that `validate_ohlcv` rejects `volume == 0`, but zero-volume bars are valid (no trades that day). Fix:

**File:** `packages/core/src/finance_core/validation.py`

Change `if volume < 0: return False` to `if volume < 0: return False`. This is already correct — the issue is that `_parse_eu_number` might return 0.0 for missing values, which is fine. Re-read the audit...

The audit says "volume < 0 check — volume can be 0 (no trades). Bug: 0 volume bars get `validate_ohlcv` returning True but volume=0 is valid". Actually the function currently returns True for volume=0 because `volume < 0` is False. So this is NOT a bug. The audit was wrong. Remove this step.

Actually wait, let me re-read. The validation checks: `if volume < 0: return False`. If volume is 0, this check doesn't trigger, so it passes. That's correct behavior. The audit note was misleading. Skip this step.

---

## Phase 5 — Code Quality (10 steps)

### 5.1 Add named constants for regime scoring

**File:** `packages/indicators/src/finance_indicators/indicators.py`

Add at top:
```python
_REGIME_WEIGHTS = {
    "sma50_vs_sma200": 0.35,
    "sma20_vs_sma50": 0.20,
    "price_vs_sma200": 0.15,
    "price_vs_sma50": 0.10,
    "rsi_momentum": 0.20,
}
_LOW_ADX = 15
_MID_ADX = 20
_STRONG_TREND_ADX = 28
_RSI_OVERBOUGHT = 70
_RSI_OVERSOLD = 30
```

Use constants in `classify_regime`, `compute_trend_phase`.

**Verify:** `cd packages && uv run pytest` passes. No behavior change — verify with existing test `test_classify_regime_still_importable_and_works`.

---

### 5.2 Remove dead `services.notifications` import

**File:** `packages/signal-engine/src/finance_signal_engine/engine.py` line 41-55

Replace `_fire_telegram` with a version that doesn't try to import:

```python
def _fire_telegram(signal_type: str, ticker: str, conditions: list[str], settings: dict) -> None:
    if settings.get("telegram_notifications_enabled") != "true":
        return
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not bot_token or not chat_id:
        return
    try:
        import httpx
        msg = f"[{signal_type}] {ticker}: {'; '.join(conditions)}"
        httpx.post(
            f"https://api.telegram.org/bot{bot_token}/sendMessage",
            json={"chat_id": chat_id, "text": msg},
            timeout=10,
        )
    except Exception:
        logger.warning(f"Telegram notification failed for {ticker}: {exc}", exc_info=True)
    # Actually we should log properly:
    except Exception as exc:
        logger.warning(f"Telegram notification failed for {ticker}: {exc}")
```

**Verify:** `cd packages && uv run pytest` passes.

---

### 5.3 Remove dead `services.regime_detector` import

**File:** `packages/signal-engine/src/finance_signal_engine/engine.py` lines 168-176

Replace the regime filter block. For now, simplify to not use external module. Since regime detection was never working (the import always failed), make it a no-op or use `MarketRegime` model directly:

```python
regime_is_bear = False
if settings.get("regime_filter_enabled", "false") == "true":
    try:
        exchange = settings.get("exchange", "NASDAQ")
        latest = db.execute(
            select(MarketRegime)
            .where(MarketRegime.ticker == exchange)
            .order_by(MarketRegime.calc_date.desc())
            .limit(1)
        ).scalar_one_or_none()
        regime_is_bear = latest is not None and latest.regime == "BEAR"
    except Exception as exc:
        logger.warning(f"Regime check failed: {exc}")
```

**Verify:** `cd packages && uv run pytest` passes.

---

### 5.4 Delete `evaluate_buy_signals` delegator

**File:** `packages/signal-engine/src/finance_signal_engine/engine.py`

Remove the `evaluate_buy_signals` function entirely. Update `evaluate_all_signals` to call `evaluate_watchlist_signals` directly.

Also remove from `__init__.py` exports and `__all__`.

**Verify:** `cd packages && uv run pytest` passes. `from finance_signal_engine import evaluate_buy_signals` should fail.

---

### 5.5 Remove unused `settings` param from `AISignalService.analyze_ticker`

**File:** `packages/ai-signals/src/finance_ai_signals/service.py`

Simply remove the `settings` parameter. Update any callers (search for `analyze_ticker(` in the codebase — likely none call it yet).

**Verify:** `cd packages && uv run pytest` passes. No callers break.

---

### 5.6 Update stale market-data description

**File:** `packages/market-data/pyproject.toml`

Change description:
```
description = "Market data providers (TradingView, yfinance)"
```

**Verify:** `grep description packages/market-data/pyproject.toml` shows correct text.

---

### 5.7 Make `httpx` optional in portfolio

**File:** `packages/portfolio/pyproject.toml`

Move `httpx` from `dependencies` to `[project.optional-dependencies]`:
```toml
[project.optional-dependencies]
twelve_data = ["httpx>=0.28"]
```

**File:** `packages/portfolio/src/finance_portfolio/scalable_import.py`

Wrap `import httpx` in a try/except at the top of `_search_isin_twelve_data`:

```python
async def _search_isin_twelve_data(description: str, api_key: str) -> Optional[tuple[str, str]]:
    try:
        import httpx
    except ImportError:
        logger.warning("httpx not installed — ISIN lookup unavailable")
        return None
    # ... rest of function
```

**Verify:** `cd packages && uv sync` works. `cd packages && uv run pytest` passes.

---

### 5.8 Add `_LOG_FLOOR` fix

**File:** `packages/optimizer/src/finance_optimizer/mtd.py`

Change:
```python
_LOG_FLOOR = 1e-12
```
To:
```python
import numpy as np
_LOG_FLOOR = np.finfo(float).eps  # ~2.2e-16
```

**Verify:** `cd packages && uv run pytest` passes.

---

### 5.9 Fix `validate_ohlcv` — allow zero volume

**File:** `packages/core/src/finance_core/validation.py`

Change `if volume < 0: return False` to keep as-is (it already allows volume=0). Minimum check should be `volume < 0` (no negative volume) and `high >= low` instead of `high < low`.

Actually re-reading the issue: the current check is `if high < low: return False`. This should be `if high < low: return False` which is correct. But it should also check `if high < 0 or low < 0` BEFORE the high/low cross check to avoid confusing error messages. Let me just fix the order:

```python
def validate_ohlcv(bar: dict) -> bool:
    try:
        high = float(bar.get("high", 0))
        low = float(bar.get("low", 0))
        open_ = float(bar.get("open", 0))
        close = float(bar.get("close", 0))
        volume = int(bar.get("volume", 0))
        if high < 0 or low < 0 or open_ < 0 or close < 0 or volume < 0:
            return False
        if high < low:
            return False
        return True
    except (ValueError, TypeError, KeyError):
        return False
```

Combined the negativity checks, removed redundant volume >= 0 check after the combined check.

**Verify:** `cd packages && uv run pytest` passes.

---

### 5.10 Remove `node_modules/` from `.gitignore` and clean stale references

**File:** `.gitignore`

Remove `node_modules/` line. Already done in 1.4.

**Also clean:** `packages/pyproject.toml` — remove any references to non-existent ruff rules if they show up after widening.

---

## Phase 6 — Test Improvements (2 steps)

### 6.1 Add test for `refresh.py` duplicate-upsert behavior

Already exists as `test_refresh_called_twice_same_day_updates_not_duplicates`. Confirm it passes.

**File:** `packages/market-data/tests/test_refresh.py`

No change needed — test already covers this.

---

### 6.2 Simplify test helpers in signal-engine tests

**File:** `packages/signal-engine/tests/test_scoring.py`

Refactor `_seed_indicator` and `_seed_rating` to accept db session instead of creating their own:

```python
def _seed_indicator(db, rsi=50.0, pct=1.0, adx=25.0):
    db.add(IndicatorValue(...))
    db.commit()

def _seed_rating(db, recommendation="BUY", score=0.6, fetched_at=None):
    db.add(ExternalRating(...))
    db.commit()
```

Update all test functions to create session once and pass to helpers.

**Verify:** `cd packages && uv run pytest packages/signal-engine/` — all tests pass, no DB connection churn.

---

## Running Order

```
Phase 1: Foundation
  1.1 .python-version
  1.2 Ruff rules
  1.3 Pyright config
  1.4 Clean .gitignore
  1.5 GitHub CI

Phase 2: Bug Fixes
  2.1 check_same_thread
  2.2 backend utcnow
  2.3 engine utcnow
  2.4 conftest cleanup
  2.5 backend conftest
  2.6 delete dead test

Phase 3: Security
  3.1 secrets → env vars
  3.2 dead VV config

Phase 4: Deprecations
  4.1 pytz → zoneinfo
  (4.2 skipped — not a real bug)

Phase 5: Code Quality
  5.1 regime constants
  5.2 telegram dead import
  5.3 regime dead import
  5.4 delete delegator
  5.5 unused settings param
  5.6 stale description
  5.7 httpx optional
  5.8 _LOG_FLOOR
  5.9 validate_ohlcv
  5.10 gitignore cleanup

Phase 6: Test Quality
  6.1 verify duplicate test
  6.2 simplify test helpers
```

## Verify All

After all phases complete:

```bash
cd packages && uv sync --all-packages
cd packages && uv run pytest
cd packages && uv run ruff check
```

Total: ~25 individual edits across ~15 files. Each has a verify step.
