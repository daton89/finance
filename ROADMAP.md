# Simplify Roadmap

**Goal:** 1 build system, 1 `uv sync`, 1 `make dev`. Kill JS noise. Move backend into workspace.

---

## Step 1 — Move backend into uv workspace

Move `apps/backend/` → `packages/backend/` so it's part of the workspace. Single `uv sync --all-packages` installs everything.

### 1a. Create `packages/backend/pyproject.toml`

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

[dependency-groups]
dev = [
    "pytest>=8.3",
    "httpx>=0.27",
    "pytest-asyncio>=0.24",
]

[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["."]
```

Note: No `[tool.uv.sources]` — workspace root handles resolution.

### 1b. Copy files

```
cp apps/backend/main.py packages/backend/main.py
cp -r apps/backend/tests packages/backend/tests
```

### 1c. Delete stale venv/lock
```
rm -rf apps/backend/.venv apps/backend/uv.lock
```

### Verify
```
cd packages && uv sync --all-packages
cd packages && uv run --package finance-backend pytest
```
Tests pass.

---

## Step 2 — Update workspace root

Edit `packages/pyproject.toml`:

**Add `finance-backend` to `[tool.uv.sources]`:**
```
finance-backend = { workspace = true }
```

**Add `"backend"` to workspace `members`:**
```
members = [
    "core",
    "market-data",
    "indicators",
    "signal-engine",
    "ai-signals",
    "portfolio",
    "optimizer",
    "backend",
]
```

### Verify
```
rm -rf packages/.venv  # force fresh lock
cd packages && uv sync --all-packages
```
No errors. `uv run --package finance-backend uvicorn main:app --port 8000` starts server.

---

## Step 3 — Simplify Makefile

Replace root `Makefile` with:

```makefile
.PHONY: install dev test lint clean

install:
	cd packages && uv sync --all-packages

dev:
	cd packages && DATABASE_URL=$${DATABASE_URL:-sqlite:///$$(pwd)/finance.db} uv run --package finance-backend uvicorn main:app --reload --port 8000

test:
	cd packages && uv run --package finance-backend pytest

lint:
	cd packages && uv run ruff check

clean:
	cd packages && find . -name '__pycache__' -type d -exec rm -rf {} + 2>/dev/null || true
	cd packages && find . -name '*.egg-info' -type d -exec rm -rf {} + 2>/dev/null || true
```

### Verify
```
make install     # single command, syncs everything
make dev         # starts FastAPI on :8000
make test        # runs backend tests
make lint        # ruff check on all packages
```

---

## Step 4 — Kill JS build system

### 4a. Delete `pnpm-workspace.yaml`
```
rm pnpm-workspace.yaml
```

### 4b. Replace `package.json`
Nothing depends on it. Delete or keep minimal:

```json
{
  "name": "finance",
  "private": true
}
```

### 4c. Remove `node_modules` from `.gitignore`
No JS deps expected. (Keep `node_modules/` line anyway — harmless.)

---

## Step 5 — Delete stub/empty dirs

```
rm -rf apps/desktop apps/mobile apps/workers
rm -rf cli/src
rm -rf archive
```

### Verify
```
ls apps/   # only backend/ remains
ls cli/    # empty or gone
ls archive # gone
```

---

## Step 6 — Delete old `apps/backend/` (now migrated)

```
rm -rf apps/backend
```

### Verify
```
ls apps/   # empty
```

Final tree:
```
packages/
  pyproject.toml   (workspace root — 8 members)
  core/
  market-data/
  indicators/
  signal-engine/
  ai-signals/
  portfolio/
  optimizer/
  backend/
    main.py
    pyproject.toml
    tests/
Makefile
README.md
```

---

## Step 7 — Update README.md

Replace with new structure:

```markdown
# Finance

Monorepo per trading, portafoglio, segnali azionari.

```
packages/
  core/            # ORM models, DB engine, config, calendar
  market-data/     # Market data providers (TradingView, yfinance)
  indicators/      # 32 pure-Python technical indicators
  signal-engine/   # Signal generation + composite scoring
  portfolio/       # Positions, P&L, Scalable Capital CSV import
  ai-signals/      # HTTP adapter for Stock Signal Worker
  optimizer/       # MTD, assortativity, backtest, portfolio opt
  backend/         # FastAPI server (CSV import, market data refresh)
```

## Setup

```bash
make install       # uv sync --all-packages
make dev           # FastAPI on :8000
make test          # run tests
make lint          # ruff check
```
```

---

## Step 8 — Update TODO.md

Remove completed cleanup items. Update to reflect new structure.

---

## Rollback

If anything breaks:

```bash
git checkout -- packages/pyproject.toml Makefile package.json README.md TODO.md
git checkout -- apps/  # restores all apps/
```

Then recreate `apps/backend/.venv` and `apps/backend/uv.lock`:
```bash
cd apps/backend && uv sync
```
