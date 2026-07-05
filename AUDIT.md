# Codebase Audit

## BUGS (wrong behavior)

| File | Issue | Impact |
|------|-------|--------|
| `signal-engine/engine.py:41-55` | `_fire_telegram` catches `Exception: pass` — silently swallows missing `services.notifications` module | Dead code, no user-visible effect |
| `signal-engine/engine.py:168-176` | `from services.regime_detector import ...` — module doesn't exist. Caught by `except Exception: pass` | Regime filter always disabled |
| `backend/main.py:104` | `datetime.utcnow()` — deprecated py3.12. Not tz-aware. Rate limit compars UTC naive vs naive, OK technically but should fix | Low now, breaks with tz-aware switch |
| `core/validation.py` | `volume < 0` check — volume can be 0 (no trades). Bug: 0 volume bars get `validate_ohlcv` returning True but volume=0 is valid | Low — 0-volume edge case |
| `core/base.py:8-9` | `check_same_thread=False` passed to all engines — Postgres/MySQL will error. Only valid for SQLite | Blocks non-SQLite usage |

## CODE SMELLS (works but risky)

### core/
- `Real = Float` alias — `Float` loses precision. Monetary values should use `Numeric(10,2)` (
  `Holding.entry_price`, `Holding.sell_price`, `Holding.realised_pnl`).
- `PriceBar.volume` is `Integer` — some exchanges exceed 32-bit. should be `BigInteger`.
- `pytz` in `calendar.py` — deprecated py3.12+. stdlib `zoneinfo` replaces it.
- `get_db()` yields generator — FastAPI pattern in library package. Couples core to FastAPI.
- `SETTING_DEFAULTS` has `telegram_bot_token`, `telegram_chat_id` as plain text. Secrets in DB.
- `SETTING_DEFAULTS` has `vv_source_id`, `vv_sync_enabled` — dead VectorVest config.
- `EXCHANGE_CONFIG` references `polygon`, `twelve_data` providers — neither implemented.
- NYSE holidays hardcoded 2024-2026. Expires every year. Needs manual updates.

### market-data/
- `SCAN_URL` is unofficial TradingView scanner endpoint. No fallback if TV changes API.
- `refresh.py` mixes async function with sync SQLAlchemy. Blocks event loop.
- Indicator periods hardcoded (sma_period=20, rsi_period=14). Not configurable per-ticker.

### indicators/
- `classify_regime` magic numbers (weights 0.35, 0.20, 0.15, 0.10, 0.20) — no named constants.
- `compute_trend_phase` thresholds (ADX > 28, RSI >= 70/30) hardcoded.

### signal-engine/
- `_active_signal()` returns `scalar_one_or_none()` — no DB constraint prevents duplicate active signals for same (ticker, type, holding).
- `evaluate_buy_signals` is a 1-line delegator to `evaluate_watchlist_signals`. Dead indirection.
- `evaluate_all_signals` calls `db.rollback()` per-ticker — without explicit transaction, this is no-op.

### portfolio/
- `_sanitize_ticker()` generates synthetic tickers (e.g. `ADID.DE697`) from description + ISIN suffix. No user confirmation needed — could create garbage tickers.
- Re-import deletes holdings then re-creates. If re-import fails mid-way, data is lost.
- `closed_positions`, `all_positions` return plain `dict` — fragile. Should be typed.

### ai-signals/
- `AISignalService.analyze_ticker()` accepts `settings` param but never uses it.
- No retry on Worker HTTP errors.

### optimizer/
- `_LOG_FLOOR = 1e-12` — numerical edge case risk. Should use `np.finfo(float).eps`.
- SLSQP failure emits warning but returns potentially invalid lambdas.
- `assortative_max_quadratic_utility` sign logic (`1.0 if modality in MINIMIZE_MODALITIES else -1.0`) opaque.

### backend/
- `Base.metadata.create_all(bind=engine)` at module level — runs on import. Makes test isolation harder (already worked around in conftest).
- `/import` endpoint is HTML-only. No JSON API for programmatic import.
- `datetime.utcnow()` used — deprecated py3.12+.

## TEST ISSUES

| Test | Problem |
|------|---------|
| `core/tests/test_models.py` | `test_vv_models_removed` — checks removed models. This test was written during migration and is now permanent noise. Delete. |
| `signal-engine/tests/test_scoring.py` | Helper fns (`_seed_indicator`, `_seed_rating`) create own `SessionLocal`, close it, then test opens another. Works but convoluted. |
| `backend/tests/test_market_data_refresh.py` | `import main` at module level — fragile. Requires `pythonpath = ["."]`. Works but import side effects (create_all) run. |
| All test files except `test_refresh.py` | No tests for duplicate-upsert behavior. |
| `packages/conftest.py` | Cleanup only deletes 4 tables — misses `Holding`, `Signal`, `ScalableTransaction`, `StockGroup`, `StockAnalysis`. Test leakage across files. |

## PYPROJECT.TOML ISSUES

| File | Issue |
|------|-------|
| Workspace root | Ruff only checks E/F/I/N/W — missing `UP` (pyupgrade), `B` (bugbear), `SIM` (simplify), `ARG` (unused args) |
| `market-data/` | Description says "Polygon, yfinance, Finnhub" — only TradingView implemented. Stale. |
| `optimizer/` | `finance-core` not in optional deps but uses it via... actually it doesn't import core. Clean. |
| `portfolio/` | `httpx` is hard dependency for optional Twelve Data ISIN lookup. Should be optional. |
| `core/` | `pytz` dep should be replaced with stdlib `zoneinfo` (py3.12+) |

## PROJECT CONFIG ISSUES

| Item | Issue |
|------|-------|
| No `.python-version` | Python version not pinned for tools like pyenv |
| No CI/CD config | No `.github/workflows/` |
| No type checking | No mypy/pyright in tool config or Makefile |
| `.gitignore` has `node_modules/` | Dead entry after removing JS build system |
| `uv.lock` in packages/ | Managed by uv but no instructions for production deploy |

## SUMMARY

- **0 crash bugs** found. Everything functional.
- **2 silent dead-code paths** in signal-engine (telegram, regime filter) — no user impact, just noise.
- **~30 code smells** — mostly hardcoded values, deprecated APIs, precision issues, coupling.
- **Test gaps**: 5/8 packages have no tests. Integration coverage thin. Conftest leaks data between tests.
