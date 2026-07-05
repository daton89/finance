# Portfolio Web App Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `apps/web` — a single responsive Vite+React+TS webapp for portfolio management (holdings, P&L, Scalable Capital CSV import) styled to match the dark/neon-green terminal look of `~/Coding/stock-signal`, backed by new JSON endpoints on the existing FastAPI backend.

**Architecture:** `apps/backend/main.py` gains `/api/positions/{summary,open,closed}` (GET) and `/api/import` (POST) JSON endpoints alongside its existing HTML upload page, with CORS opened for the Vite dev origin. `apps/web` is a from-scratch Vite React-TS app using TanStack Query to call those endpoints, Tailwind (v3) themed to stock-signal's palette, no router (single page, in-memory tabs), no auth.

**Tech Stack:** FastAPI + Pydantic v2 (backend, unchanged deps otherwise) · Vite + React 18 + TypeScript + Tailwind CSS v3 + `@tanstack/react-query` v5 (frontend) · pnpm workspace · pytest + `TestClient` for backend tests.

## Global Constraints

- No native shell (no Electron/Tauri) — `apps/web` is a plain webapp opened in a browser.
- V1 scope is portfolio only: holdings, P&L, CSV import. No signal-engine/optimizer/ai-signals/backtest endpoints in this plan.
- No auth anywhere — local single-user tool.
- CORS on FastAPI: `allow_origins=["http://localhost:5173"]` (Vite's default dev port). No Vite proxy, no same-origin static serving.
- JS package manager is **pnpm** everywhere in this repo, not npm/yarn.
- Tailwind must be **v3** (`tailwindcss@^3`), not v4 — this plan's config files (`tailwind.config.js` + `postcss.config.js` + `@tailwind` directives) are v3-shaped.
- No shadcn CLI generator — hand-author Tailwind-styled components directly in `src/components/`, in the spirit of shadcn (own the code, no runtime UI-library dependency) without the interactive codegen step.
- Exact palette (copied from `~/Coding/stock-signal/src/components/StockCard.jsx` and `index.html`): background `#040804`, card surface `#0a0f0a`, accent green `#00ff88`, text `#e8f5e8`, muted `#4a6a4a`, negative/red `#ff4466`, border `#1a2a1a`, border-accent `#2a4a2a`, 8px card radius, monospace everywhere (system font stack, no webfont).
- `packages/market-data` is an empty stub and `price_bars` has 0 rows — `current_price`/`market_value`/`unrealised_pnl` will render as `null`/`—` until a price feed exists. That's out of scope here; don't add price fetching.
- **Environment gotcha:** the `node`/`pnpm` on this machine's default `PATH` resolve to an ancient Node v10.24.1 via a stale shim. Before any frontend command, run `source ~/.nvm/nvm.sh && nvm use 22` in that shell — this puts a working Node v22.17.0 + pnpm on `PATH`. Every frontend step below assumes this was done first in that terminal.

---

## Backend Tasks

### Task 1: Test infra, CORS, and `Depends(get_db)` refactor

**Files:**
- Modify: `apps/backend/pyproject.toml`
- Modify: `apps/backend/main.py`
- Create: `apps/backend/tests/conftest.py`
- Create: `apps/backend/tests/test_main.py`

**Interfaces:**
- Produces: `app` (FastAPI instance) importable as `from main import app`, now with CORS middleware and every route using `Depends(get_db)` instead of manual `SessionLocal()`.
- Consumes: `finance_core.base.get_db` (existing generator dependency, already exported from `finance_core.base.__init__`).

- [ ] **Step 1: Add pytest/httpx dev deps to `apps/backend/pyproject.toml`**

Replace the file's contents with:

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
]

[tool.uv.sources]
finance-portfolio = { path = "../../packages/portfolio" }
finance-core = { path = "../../packages/core" }

[dependency-groups]
dev = [
    "pytest>=8.3",
    "httpx>=0.27",
]

[tool.pytest.ini_options]
testpaths = ["tests"]
```

- [ ] **Step 2: Sync deps**

Run: `cd apps/backend && uv sync`
Expected: exit 0, `pytest` and `httpx` installed into `apps/backend/.venv`.

- [ ] **Step 3: Write the test infra + a failing CORS test**

Create `apps/backend/tests/conftest.py`:

```python
import os
import tempfile

_fd, _path = tempfile.mkstemp(suffix=".db")
os.close(_fd)
os.environ["DATABASE_URL"] = f"sqlite:///{_path}"

import pytest  # noqa: E402
from finance_core.base import engine  # noqa: E402
from finance_core.models import Holding, ScalableTransaction, WatchlistStock  # noqa: E402


@pytest.fixture(autouse=True)
def _clean_tables():
    yield
    with engine.begin() as conn:
        conn.execute(Holding.__table__.delete())
        conn.execute(ScalableTransaction.__table__.delete())
        conn.execute(WatchlistStock.__table__.delete())
```

Create `apps/backend/tests/test_main.py`:

```python
from fastapi.testclient import TestClient

from main import app

client = TestClient(app)


def test_cors_allows_local_frontend_origin():
    resp = client.get("/", headers={"Origin": "http://localhost:5173"})
    assert resp.headers.get("access-control-allow-origin") == "http://localhost:5173"


def test_index_still_serves_upload_form():
    resp = client.get("/")
    assert resp.status_code == 200
    assert "Scalable Capital CSV Import" in resp.text
```

- [ ] **Step 4: Run tests, confirm the CORS test fails**

Run: `cd apps/backend && uv run pytest tests/test_main.py -v`
Expected: `test_index_still_serves_upload_form` PASSES, `test_cors_allows_local_frontend_origin` FAILS with `assert None == 'http://localhost:5173'`.

- [ ] **Step 5: Add CORS middleware and refactor to `Depends(get_db)`**

Replace `apps/backend/main.py` in full with:

```python
from fastapi import Depends, FastAPI, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from finance_core.base import Base, engine, get_db
from finance_portfolio import parse_scalable_csv

app = FastAPI(title="Finance Backend")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

Base.metadata.create_all(bind=engine)

_HTML_TOP = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Finance - CSV Import</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;background:#f5f5f7;color:#1d1d1f;padding:2rem}
.card{max-width:640px;margin:2rem auto;background:#fff;border-radius:16px;padding:2rem;box-shadow:0 1px 3px rgba(0,0,0,.08)}
h1{font-size:1.5rem;font-weight:600;margin-bottom:.25rem}
.sub{color:#6e6e73;font-size:.9rem;margin-bottom:1.5rem}
input[type=file]{width:100%;padding:.5rem;border:1px dashed #c7c7cc;border-radius:8px;background:#fafafa;cursor:pointer}
button{margin-top:1rem;padding:.6rem 1.5rem;background:#0071e3;color:#fff;border:none;border-radius:8px;font-size:.9rem;font-weight:500;cursor:pointer}
button:hover{background:#0077ed}
.result{margin-top:1.5rem;padding:1rem;border-radius:8px}
.ok{background:#e8f5e9;border:1px solid #a5d6a7}
.err{background:#fbe9e7;border:1px solid #ef9a9a}
.result h2{font-size:1rem;margin-bottom:.5rem}
.result ul{list-style:none;font-size:.9rem}
.result li{margin-bottom:.25rem}
</style>
</head>
<body>
<div class="card">
<h1>Scalable Capital CSV Import</h1>
<p class="sub">Upload your Scalable Capital transaction report</p>
<form action="/import" method="post" enctype="multipart/form-data">
<input type="file" name="file" accept=".csv,.tsv" required style="margin-bottom:.5rem">
<button type="submit">Upload &amp; Import</button>
</form>
"""

_HTML_BOTTOM = """\
</div>
</body>
</html>"""


def _page(result=None, error=None) -> str:
    if error:
        body = '<div class="result err"><h2>Error</h2><p>{}</p></div>'.format(error)
    elif result:
        parts = ['<div class="result ok"><h2>Import complete</h2><ul>']
        parts.append(f"<li>Transactions imported: {result.transactions_imported}</li>")
        parts.append(f"<li>Holdings created: {result.holdings_created}, closed: {result.holdings_closed}</li>")
        if result.tickers_added:
            parts.append(f"<li>New tickers: {', '.join(result.tickers_added)}</li>")
        if result.skipped:
            parts.append(f"<li>Skipped rows: {len(result.skipped)}</li>")
        parts.append("</ul></div>")
        body = "".join(parts)
    else:
        body = ""
    return _HTML_TOP + body + _HTML_BOTTOM


@app.get("/", response_class=HTMLResponse)
async def index():
    return _page()


@app.post("/import", response_class=HTMLResponse)
async def import_csv(file: UploadFile | None = None, db: Session = Depends(get_db)):
    if file is None or not file.filename:
        return _page(error="No file selected")
    contents = await file.read()
    try:
        result = await parse_scalable_csv(contents, db)
        return _page(result=result)
    except Exception as e:
        return _page(error=str(e))
```

- [ ] **Step 6: Run tests, confirm both pass**

Run: `cd apps/backend && uv run pytest tests/test_main.py -v`
Expected: 2 passed.

- [ ] **Step 7: Commit**

```bash
git add apps/backend/pyproject.toml apps/backend/main.py apps/backend/tests
git commit -m "backend: add CORS, switch to Depends(get_db), add test infra"
```

---

### Task 2: `GET /api/positions/summary`

**Files:**
- Create: `apps/backend/schemas.py`
- Modify: `apps/backend/main.py`
- Create: `apps/backend/tests/test_positions_summary.py`

**Interfaces:**
- Consumes: `finance_portfolio.position_summary(db: Session) -> dict` (existing, returns keys `open_positions, closed_positions, total_cost_basis, total_market_value, total_unrealised_pnl, total_realised_pnl`).
- Produces: `schemas.PositionSummaryOut` (reused by no one else, but establishes the schema file other tasks append to).

- [ ] **Step 1: Write the failing test**

Create `apps/backend/tests/test_positions_summary.py`:

```python
from fastapi.testclient import TestClient

from main import app

client = TestClient(app)


def test_summary_zero_state_when_no_holdings():
    resp = client.get("/api/positions/summary")
    assert resp.status_code == 200
    assert resp.json() == {
        "open_positions": 0,
        "closed_positions": 0,
        "total_cost_basis": 0.0,
        "total_market_value": 0.0,
        "total_unrealised_pnl": 0.0,
        "total_realised_pnl": 0.0,
    }
```

- [ ] **Step 2: Run test, confirm it fails**

Run: `cd apps/backend && uv run pytest tests/test_positions_summary.py -v`
Expected: FAIL with 404 (`/api/positions/summary` doesn't exist yet).

- [ ] **Step 3: Create `apps/backend/schemas.py`**

```python
from pydantic import BaseModel


class PositionSummaryOut(BaseModel):
    open_positions: int
    closed_positions: int
    total_cost_basis: float
    total_market_value: float
    total_unrealised_pnl: float
    total_realised_pnl: float
```

- [ ] **Step 4: Add the endpoint to `apps/backend/main.py`**

Change the import lines at the top from:

```python
from finance_core.base import Base, engine, get_db
from finance_portfolio import parse_scalable_csv
```

to:

```python
from finance_core.base import Base, engine, get_db
from finance_portfolio import parse_scalable_csv, position_summary
from schemas import PositionSummaryOut
```

Append this endpoint at the end of the file (after `import_csv`):

```python
@app.get("/api/positions/summary", response_model=PositionSummaryOut)
def api_position_summary(db: Session = Depends(get_db)):
    return position_summary(db)
```

- [ ] **Step 5: Run test, confirm it passes**

Run: `cd apps/backend && uv run pytest tests/test_positions_summary.py -v`
Expected: 1 passed.

- [ ] **Step 6: Commit**

```bash
git add apps/backend/schemas.py apps/backend/main.py apps/backend/tests/test_positions_summary.py
git commit -m "backend: add GET /api/positions/summary"
```

---

### Task 3: `GET /api/positions/open`

**Files:**
- Modify: `apps/backend/schemas.py`
- Modify: `apps/backend/main.py`
- Create: `apps/backend/tests/test_positions_open.py`

**Interfaces:**
- Consumes: `finance_portfolio.open_positions(db) -> list[Position]` where `Position` is the dataclass in `finance_portfolio.holdings` with fields `ticker, shares, entry_price, entry_date, current_price, market_value, cost_basis, unrealised_pnl, unrealised_pnl_pct, days_held, notes`.
- Produces: `schemas.OpenPositionOut`.

- [ ] **Step 1: Write the failing test**

Create `apps/backend/tests/test_positions_open.py`:

```python
from datetime import date

from fastapi.testclient import TestClient

from finance_core.base import SessionLocal
from finance_core.models import Holding, WatchlistStock
from main import app

client = TestClient(app)


def _seed_open_holding():
    db = SessionLocal()
    db.add(WatchlistStock(ticker="TEST", company_name="Test Co", is_active=True))
    db.add(
        Holding(
            ticker="TEST",
            shares=10,
            entry_price=100.0,
            entry_date=date(2026, 1, 1),
            is_open=True,
        )
    )
    db.commit()
    db.close()


def test_open_positions_returns_seeded_holding():
    _seed_open_holding()

    resp = client.get("/api/positions/open")

    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["ticker"] == "TEST"
    assert body[0]["shares"] == 10
    assert body[0]["cost_basis"] == 1000.0
    assert body[0]["current_price"] is None
```

- [ ] **Step 2: Run test, confirm it fails**

Run: `cd apps/backend && uv run pytest tests/test_positions_open.py -v`
Expected: FAIL with 404.

- [ ] **Step 3: Add `OpenPositionOut` to `apps/backend/schemas.py`**

Replace the file's contents with:

```python
from datetime import date

from pydantic import BaseModel, ConfigDict


class PositionSummaryOut(BaseModel):
    open_positions: int
    closed_positions: int
    total_cost_basis: float
    total_market_value: float
    total_unrealised_pnl: float
    total_realised_pnl: float


class OpenPositionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

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
```

- [ ] **Step 4: Add the endpoint to `apps/backend/main.py`**

Change the import line:

```python
from finance_portfolio import parse_scalable_csv, position_summary
from schemas import PositionSummaryOut
```

to:

```python
from finance_portfolio import open_positions, parse_scalable_csv, position_summary
from schemas import OpenPositionOut, PositionSummaryOut
```

Append at the end of the file:

```python
@app.get("/api/positions/open", response_model=list[OpenPositionOut])
def api_open_positions(db: Session = Depends(get_db)):
    return open_positions(db)
```

- [ ] **Step 5: Run test, confirm it passes**

Run: `cd apps/backend && uv run pytest tests/test_positions_open.py -v`
Expected: 1 passed.

- [ ] **Step 6: Commit**

```bash
git add apps/backend/schemas.py apps/backend/main.py apps/backend/tests/test_positions_open.py
git commit -m "backend: add GET /api/positions/open"
```

---

### Task 4: `GET /api/positions/closed`

**Files:**
- Modify: `apps/backend/schemas.py`
- Modify: `apps/backend/main.py`
- Create: `apps/backend/tests/test_positions_closed.py`

**Interfaces:**
- Consumes: `finance_portfolio.closed_positions(db) -> list[dict]` with keys `ticker, shares, entry_price, entry_date, sell_price, sell_date, realised_pnl, days_held, notes`.
- Produces: `schemas.ClosedPositionOut`.

- [ ] **Step 1: Write the failing test**

Create `apps/backend/tests/test_positions_closed.py`:

```python
from datetime import date

from fastapi.testclient import TestClient

from finance_core.base import SessionLocal
from finance_core.models import Holding, WatchlistStock
from main import app

client = TestClient(app)


def _seed_closed_holding():
    db = SessionLocal()
    db.add(WatchlistStock(ticker="TEST", company_name="Test Co", is_active=True))
    db.add(
        Holding(
            ticker="TEST",
            shares=5,
            entry_price=50.0,
            entry_date=date(2026, 1, 1),
            is_open=False,
            sell_price=60.0,
            sell_date=date(2026, 2, 1),
            realised_pnl=50.0,
        )
    )
    db.commit()
    db.close()


def test_closed_positions_returns_seeded_holding():
    _seed_closed_holding()

    resp = client.get("/api/positions/closed")

    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["ticker"] == "TEST"
    assert body[0]["realised_pnl"] == 50.0
    assert body[0]["sell_price"] == 60.0
```

- [ ] **Step 2: Run test, confirm it fails**

Run: `cd apps/backend && uv run pytest tests/test_positions_closed.py -v`
Expected: FAIL with 404.

- [ ] **Step 3: Add `ClosedPositionOut` to `apps/backend/schemas.py`**

Append to the end of `apps/backend/schemas.py`:

```python


class ClosedPositionOut(BaseModel):
    ticker: str
    shares: float
    entry_price: float
    entry_date: date
    sell_price: float | None = None
    sell_date: date | None = None
    realised_pnl: float | None = None
    days_held: int | None = None
    notes: str | None = None
```

- [ ] **Step 4: Add the endpoint to `apps/backend/main.py`**

Change the import lines:

```python
from finance_portfolio import open_positions, parse_scalable_csv, position_summary
from schemas import OpenPositionOut, PositionSummaryOut
```

to:

```python
from finance_portfolio import closed_positions, open_positions, parse_scalable_csv, position_summary
from schemas import ClosedPositionOut, OpenPositionOut, PositionSummaryOut
```

Append at the end of the file:

```python
@app.get("/api/positions/closed", response_model=list[ClosedPositionOut])
def api_closed_positions(db: Session = Depends(get_db)):
    return closed_positions(db)
```

- [ ] **Step 5: Run test, confirm it passes**

Run: `cd apps/backend && uv run pytest tests/test_positions_closed.py -v`
Expected: 1 passed.

- [ ] **Step 6: Commit**

```bash
git add apps/backend/schemas.py apps/backend/main.py apps/backend/tests/test_positions_closed.py
git commit -m "backend: add GET /api/positions/closed"
```

---

### Task 5: `POST /api/import` (JSON)

**Files:**
- Modify: `apps/backend/schemas.py`
- Modify: `apps/backend/main.py`
- Create: `apps/backend/tests/test_import_api.py`

**Interfaces:**
- Consumes: `finance_portfolio.parse_scalable_csv(contents: bytes, db: Session) -> ScalableImportResult` (existing, fields `transactions_imported, holdings_created, holdings_closed, tickers_added: list[str], skipped: list[dict]`).
- Produces: `schemas.ImportResultOut`.

- [ ] **Step 1: Write the failing test**

Create `apps/backend/tests/test_import_api.py`:

```python
from fastapi.testclient import TestClient

from main import app

client = TestClient(app)

_CSV = (
    "Status;Type;ISIN;Reference;Description;Shares;Price;Amount;Fee;Tax;Currency;Date;Asset Type\n"
    "Executed;Buy;US0000000000;REF001;Test Co;10;100;1000;0;0;EUR;2026-01-15;Security\n"
)


def test_import_endpoint_parses_valid_csv(monkeypatch):
    monkeypatch.delenv("TWELVE_DATA_API_KEY", raising=False)

    resp = client.post(
        "/api/import",
        files={"file": ("scalable.csv", _CSV.encode("utf-8"), "text/csv")},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["transactions_imported"] == 1
    assert body["holdings_created"] == 1
    assert len(body["tickers_added"]) == 1
```

- [ ] **Step 2: Run test, confirm it fails**

Run: `cd apps/backend && uv run pytest tests/test_import_api.py -v`
Expected: FAIL with 404.

- [ ] **Step 3: Add `ImportResultOut` to `apps/backend/schemas.py`**

Append to the end of `apps/backend/schemas.py`:

```python


class ImportResultOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    transactions_imported: int
    holdings_created: int
    holdings_closed: int
    tickers_added: list[str]
    skipped: list[dict]
```

- [ ] **Step 4: Add the endpoint to `apps/backend/main.py`**

Change the top import line:

```python
from fastapi import Depends, FastAPI, UploadFile
```

to:

```python
from fastapi import Depends, FastAPI, HTTPException, UploadFile
```

Change:

```python
from finance_portfolio import closed_positions, open_positions, parse_scalable_csv, position_summary
from schemas import ClosedPositionOut, OpenPositionOut, PositionSummaryOut
```

to:

```python
from finance_portfolio import closed_positions, open_positions, parse_scalable_csv, position_summary
from schemas import ClosedPositionOut, ImportResultOut, OpenPositionOut, PositionSummaryOut
```

Append at the end of the file:

```python
@app.post("/api/import", response_model=ImportResultOut)
async def api_import_csv(file: UploadFile, db: Session = Depends(get_db)):
    contents = await file.read()
    try:
        return await parse_scalable_csv(contents, db)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
```

- [ ] **Step 5: Run test, confirm it passes**

Run: `cd apps/backend && uv run pytest tests/test_import_api.py -v`
Expected: 1 passed.

- [ ] **Step 6: Run the full backend test suite**

Run: `cd apps/backend && uv run pytest -v`
Expected: 6 passed (2 from `test_main.py`, 1 each from `test_positions_summary.py`, `test_positions_open.py`, `test_positions_closed.py`, `test_import_api.py`).

- [ ] **Step 7: Commit**

```bash
git add apps/backend/schemas.py apps/backend/main.py apps/backend/tests/test_import_api.py
git commit -m "backend: add POST /api/import JSON endpoint"
```

---

## Frontend Tasks

### Task 6: Repo-wide JS workspace cleanup

**Files:**
- Delete: `apps/desktop/` (empty dir)
- Delete: `apps/mobile/` (empty dir)
- Modify: `pnpm-workspace.yaml`
- Modify: `package.json`
- Modify: `Makefile`

**Interfaces:** none (config only).

- [ ] **Step 1: Remove the stale empty app directories**

Run: `rmdir apps/desktop apps/mobile`
Expected: exit 0 (both dirs are empty — verified earlier, no files inside).

- [ ] **Step 2: Update `pnpm-workspace.yaml`**

Replace its contents with:

```yaml
packages:
  - "apps/web"
  - "apps/workers"
```

- [ ] **Step 3: Update `package.json`**

Replace its contents with:

```json
{
  "name": "finance",
  "private": true,
  "scripts": {
    "dev:web": "cd apps/web && pnpm dev",
    "build:web": "cd apps/web && pnpm build"
  }
}
```

- [ ] **Step 4: Update `Makefile`**

Replace its contents with:

```makefile
.PHONY: install build test lint clean dev-backend dev-web backup

# ── Python (uv workspace) ──────────────────────────────────────────
install:
	cd packages && uv sync --all-packages
	cd apps/backend && uv sync

build:
	cd packages && uv build

test:
	cd packages && uv run pytest
	cd apps/backend && uv run pytest

lint:
	cd packages && uv run ruff check

# ── Development ────────────────────────────────────────────────────
dev: dev-backend  # run dev-web in a separate terminal

dev-backend:
	DATABASE_URL=$${DATABASE_URL:-sqlite:///$$(pwd)/packages/finance.db} cd apps/backend && uv run uvicorn main:app --reload --port 8000

dev-web:
	cd apps/web && pnpm dev

# ── Maintenance ────────────────────────────────────────────────────
backup:
	@echo "TODO: dump SQLite -> R2"

clean:
	cd packages && find . -name '__pycache__' -type d -exec rm -rf {} + 2>/dev/null || true
	cd packages && find . -name '*.egg-info' -type d -exec rm -rf {} + 2>/dev/null || true
```

- [ ] **Step 5: Verify**

Run: `git status --porcelain apps/desktop apps/mobile pnpm-workspace.yaml package.json Makefile`
Expected: `apps/desktop` and `apps/mobile` no longer listed as untracked (gone); the four config files show as modified/untracked.

- [ ] **Step 6: Commit**

```bash
git add apps pnpm-workspace.yaml package.json Makefile
git commit -m "chore: drop desktop/mobile split, wire pnpm + dev-web/test-backend targets"
```

---

### Task 7: Scaffold `apps/web` (Vite + React + TS + themed Tailwind)

**Files:**
- Create: `apps/web/` (full Vite scaffold)
- Create: `apps/web/tailwind.config.js`
- Create: `apps/web/postcss.config.js`
- Modify: `apps/web/src/index.css`
- Modify: `apps/web/src/App.tsx`

**Interfaces:**
- Produces: a buildable Vite app with Tailwind classes `bg-bg`, `bg-surface`, `bg-surfaceAlt`, `text-accent`, `text-accentDim`, `text-text`, `text-muted`, `text-mutedDim`, `text-negative`, `border-border`, `border-borderAccent` available repo-wide for later tasks.

- [ ] **Step 1: Scaffold the Vite project**

Run: `cd /Users/dangeloan/Coding/finance && source ~/.nvm/nvm.sh && nvm use 22 && pnpm create vite@latest apps/web -- --template react-ts`
Expected: creates `apps/web/` with `package.json`, `vite.config.ts`, `tsconfig*.json`, `index.html`, `src/{main.tsx,App.tsx,App.css,index.css,assets/}`, `public/vite.svg`.

- [ ] **Step 2: Install deps**

Run: `cd apps/web && pnpm install`
Expected: exit 0, `node_modules/` and `pnpm-lock.yaml` created.

- [ ] **Step 3: Add Tailwind v3 + TanStack Query**

Run: `cd apps/web && pnpm add -D "tailwindcss@^3" postcss autoprefixer && pnpm add @tanstack/react-query`
Expected: exit 0, both added to `apps/web/package.json`.

- [ ] **Step 4: Create `apps/web/tailwind.config.js`**

```js
/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        bg: "#040804",
        surface: "#0a0f0a",
        surfaceAlt: "#060f09",
        accent: "#00ff88",
        accentDim: "#00cc66",
        text: "#e8f5e8",
        muted: "#4a6a4a",
        mutedDim: "#2a5a2a",
        border: "#1a2a1a",
        borderAccent: "#2a4a2a",
        negative: "#ff4466",
      },
      fontFamily: {
        mono: ["ui-monospace", "SFMono-Regular", "Menlo", "Consolas", "monospace"],
      },
      borderRadius: {
        lg: "8px",
      },
    },
  },
  plugins: [],
};
```

- [ ] **Step 5: Create `apps/web/postcss.config.js`**

```js
export default {
  plugins: {
    tailwindcss: {},
    autoprefixer: {},
  },
};
```

- [ ] **Step 6: Replace `apps/web/src/index.css`**

```css
@tailwind base;
@tailwind components;
@tailwind utilities;

body {
  margin: 0;
  background: #040804;
  color: #e8f5e8;
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
}
```

- [ ] **Step 7: Replace `apps/web/src/App.tsx` with a minimal placeholder**

```tsx
function App() {
  return (
    <div className="min-h-screen bg-bg p-8 font-mono text-text">
      <h1 className="text-lg font-bold tracking-widest text-accent">◈ FINANCE</h1>
    </div>
  );
}

export default App;
```

- [ ] **Step 8: Verify it builds**

Run: `cd apps/web && pnpm build`
Expected: exit 0, `dist/` produced, no TypeScript errors.

- [ ] **Step 9: Commit**

```bash
git add apps/web
git commit -m "web: scaffold Vite+React+TS app with themed Tailwind"
```

---

### Task 8: API client + TanStack Query wiring

**Files:**
- Create: `apps/web/src/types.ts`
- Create: `apps/web/src/api/client.ts`
- Create: `apps/web/src/api/queries.ts`
- Modify: `apps/web/src/main.tsx`

**Interfaces:**
- Produces: `useSummary()`, `useOpenPositions()`, `useClosedPositions()`, `useImportCsv()` hooks (TanStack Query) — consumed by Tasks 9–12.
- Consumes: backend endpoints from Tasks 2–5 (`GET /api/positions/summary|open|closed`, `POST /api/import`).

- [ ] **Step 1: Create `apps/web/src/types.ts`**

```ts
export interface OpenPosition {
  ticker: string;
  shares: number;
  entry_price: number;
  entry_date: string;
  current_price: number | null;
  market_value: number | null;
  cost_basis: number | null;
  unrealised_pnl: number | null;
  unrealised_pnl_pct: number | null;
  days_held: number | null;
  notes: string | null;
}

export interface ClosedPosition {
  ticker: string;
  shares: number;
  entry_price: number;
  entry_date: string;
  sell_price: number | null;
  sell_date: string | null;
  realised_pnl: number | null;
  days_held: number | null;
  notes: string | null;
}

export interface PositionSummary {
  open_positions: number;
  closed_positions: number;
  total_cost_basis: number;
  total_market_value: number;
  total_unrealised_pnl: number;
  total_realised_pnl: number;
}

export interface ImportResult {
  transactions_imported: number;
  holdings_created: number;
  holdings_closed: number;
  tickers_added: string[];
  skipped: Record<string, unknown>[];
}
```

- [ ] **Step 2: Create `apps/web/src/api/client.ts`**

```ts
import type { ClosedPosition, ImportResult, OpenPosition, PositionSummary } from "../types";

const API_BASE = "http://localhost:8000";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, init);
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`${res.status} ${res.statusText}: ${text}`);
  }
  return res.json() as Promise<T>;
}

export function fetchSummary() {
  return request<PositionSummary>("/api/positions/summary");
}

export function fetchOpenPositions() {
  return request<OpenPosition[]>("/api/positions/open");
}

export function fetchClosedPositions() {
  return request<ClosedPosition[]>("/api/positions/closed");
}

export function importCsv(file: File) {
  const form = new FormData();
  form.append("file", file);
  return request<ImportResult>("/api/import", { method: "POST", body: form });
}
```

- [ ] **Step 3: Create `apps/web/src/api/queries.ts`**

```ts
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { fetchClosedPositions, fetchOpenPositions, fetchSummary, importCsv } from "./client";

export function useSummary() {
  return useQuery({ queryKey: ["summary"], queryFn: fetchSummary });
}

export function useOpenPositions() {
  return useQuery({ queryKey: ["positions", "open"], queryFn: fetchOpenPositions });
}

export function useClosedPositions() {
  return useQuery({ queryKey: ["positions", "closed"], queryFn: fetchClosedPositions });
}

export function useImportCsv() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: importCsv,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["summary"] });
      qc.invalidateQueries({ queryKey: ["positions"] });
    },
  });
}
```

- [ ] **Step 4: Wire `QueryClientProvider` into `apps/web/src/main.tsx`**

Replace its contents with:

```tsx
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App.tsx";
import "./index.css";

const queryClient = new QueryClient();

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <App />
    </QueryClientProvider>
  </StrictMode>,
);
```

- [ ] **Step 5: Verify it builds**

Run: `cd apps/web && pnpm build`
Expected: exit 0, no TypeScript errors.

- [ ] **Step 6: Commit**

```bash
git add apps/web/src/types.ts apps/web/src/api apps/web/src/main.tsx
git commit -m "web: add API client and TanStack Query hooks"
```

---

### Task 9: `PnlValue` + `SummaryCards`

**Files:**
- Create: `apps/web/src/components/PnlValue.tsx`
- Create: `apps/web/src/components/SummaryCards.tsx`

**Interfaces:**
- Produces: `PnlValue` (reused by Tasks 10–11), `SummaryCards` (consumed by Task 13's App shell).
- Consumes: `useSummary()` from Task 8.

- [ ] **Step 1: Create `apps/web/src/components/PnlValue.tsx`**

```tsx
interface PnlValueProps {
  value: number | null;
  className?: string;
}

export function PnlValue({ value, className = "" }: PnlValueProps) {
  if (value === null) {
    return <span className={`text-muted ${className}`}>—</span>;
  }
  const isPositive = value >= 0;
  return (
    <span className={`${isPositive ? "text-accent" : "text-negative"} ${className}`}>
      {isPositive ? "▲" : "▼"} {Math.abs(value).toFixed(2)}
    </span>
  );
}
```

- [ ] **Step 2: Create `apps/web/src/components/SummaryCards.tsx`**

```tsx
import { useSummary } from "../api/queries";
import { PnlValue } from "./PnlValue";

export function SummaryCards() {
  const { data, isLoading, isError } = useSummary();

  if (isLoading) return <div className="font-mono text-muted">loading summary...</div>;
  if (isError || !data) return <div className="font-mono text-negative">failed to load summary</div>;

  const cards = [
    { label: "OPEN POSITIONS", value: data.open_positions },
    { label: "CLOSED POSITIONS", value: data.closed_positions },
    { label: "COST BASIS", value: `$${data.total_cost_basis.toFixed(2)}` },
    { label: "MARKET VALUE", value: `$${data.total_market_value.toFixed(2)}` },
  ];

  return (
    <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
      {cards.map((c) => (
        <div key={c.label} className="rounded-lg border border-border bg-surface p-4">
          <div className="text-[10px] tracking-widest text-muted">{c.label}</div>
          <div className="mt-1 text-xl font-bold text-text">{c.value}</div>
        </div>
      ))}
      <div className="rounded-lg border border-border bg-surface p-4">
        <div className="text-[10px] tracking-widest text-muted">UNREALISED P&amp;L</div>
        <PnlValue value={data.total_unrealised_pnl} className="mt-1 text-xl font-bold" />
      </div>
      <div className="rounded-lg border border-border bg-surface p-4">
        <div className="text-[10px] tracking-widest text-muted">REALISED P&amp;L</div>
        <PnlValue value={data.total_realised_pnl} className="mt-1 text-xl font-bold" />
      </div>
    </div>
  );
}
```

- [ ] **Step 3: Verify it builds**

Run: `cd apps/web && pnpm build`
Expected: exit 0.

- [ ] **Step 4: Commit**

```bash
git add apps/web/src/components/PnlValue.tsx apps/web/src/components/SummaryCards.tsx
git commit -m "web: add PnlValue and SummaryCards components"
```

---

### Task 10: `OpenPositionsTable`

**Files:**
- Create: `apps/web/src/components/OpenPositionsTable.tsx`

**Interfaces:**
- Consumes: `useOpenPositions()` (Task 8), `PnlValue` (Task 9).
- Produces: `OpenPositionsTable` (consumed by Task 13).

- [ ] **Step 1: Create `apps/web/src/components/OpenPositionsTable.tsx`**

```tsx
import { useOpenPositions } from "../api/queries";
import { PnlValue } from "./PnlValue";

export function OpenPositionsTable() {
  const { data, isLoading, isError } = useOpenPositions();

  if (isLoading) return <div className="font-mono text-muted">loading open positions...</div>;
  if (isError || !data) return <div className="font-mono text-negative">failed to load open positions</div>;
  if (data.length === 0) return <div className="font-mono text-muted">no open positions</div>;

  return (
    <table className="w-full border-collapse text-left text-sm">
      <thead>
        <tr className="border-b border-border text-[10px] tracking-widest text-muted">
          <th className="py-2">TICKER</th>
          <th className="py-2">SHARES</th>
          <th className="py-2">ENTRY</th>
          <th className="py-2">CURRENT</th>
          <th className="py-2">MKT VALUE</th>
          <th className="py-2">P&amp;L</th>
          <th className="py-2">DAYS</th>
        </tr>
      </thead>
      <tbody>
        {data.map((p) => (
          <tr key={`${p.ticker}-${p.entry_date}`} className="border-b border-border">
            <td className="py-2 font-bold text-text">{p.ticker}</td>
            <td className="py-2 text-text">{p.shares}</td>
            <td className="py-2 text-text">${p.entry_price.toFixed(2)}</td>
            <td className="py-2 text-text">
              {p.current_price !== null ? `$${p.current_price.toFixed(2)}` : "—"}
            </td>
            <td className="py-2 text-text">
              {p.market_value !== null ? `$${p.market_value.toFixed(2)}` : "—"}
            </td>
            <td className="py-2">
              <PnlValue value={p.unrealised_pnl} />
            </td>
            <td className="py-2 text-muted">{p.days_held ?? "—"}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
```

- [ ] **Step 2: Verify it builds**

Run: `cd apps/web && pnpm build`
Expected: exit 0.

- [ ] **Step 3: Commit**

```bash
git add apps/web/src/components/OpenPositionsTable.tsx
git commit -m "web: add OpenPositionsTable component"
```

---

### Task 11: `ClosedPositionsTable`

**Files:**
- Create: `apps/web/src/components/ClosedPositionsTable.tsx`

**Interfaces:**
- Consumes: `useClosedPositions()` (Task 8), `PnlValue` (Task 9).
- Produces: `ClosedPositionsTable` (consumed by Task 13).

- [ ] **Step 1: Create `apps/web/src/components/ClosedPositionsTable.tsx`**

```tsx
import { useClosedPositions } from "../api/queries";
import { PnlValue } from "./PnlValue";

export function ClosedPositionsTable() {
  const { data, isLoading, isError } = useClosedPositions();

  if (isLoading) return <div className="font-mono text-muted">loading closed positions...</div>;
  if (isError || !data) return <div className="font-mono text-negative">failed to load closed positions</div>;
  if (data.length === 0) return <div className="font-mono text-muted">no closed positions</div>;

  return (
    <table className="w-full border-collapse text-left text-sm">
      <thead>
        <tr className="border-b border-border text-[10px] tracking-widest text-muted">
          <th className="py-2">TICKER</th>
          <th className="py-2">SHARES</th>
          <th className="py-2">ENTRY</th>
          <th className="py-2">SELL</th>
          <th className="py-2">REALISED P&amp;L</th>
          <th className="py-2">DAYS</th>
        </tr>
      </thead>
      <tbody>
        {data.map((p, i) => (
          <tr key={`${p.ticker}-${p.entry_date}-${i}`} className="border-b border-border">
            <td className="py-2 font-bold text-text">{p.ticker}</td>
            <td className="py-2 text-text">{p.shares}</td>
            <td className="py-2 text-text">${p.entry_price.toFixed(2)}</td>
            <td className="py-2 text-text">
              {p.sell_price !== null ? `$${p.sell_price.toFixed(2)}` : "—"}
            </td>
            <td className="py-2">
              <PnlValue value={p.realised_pnl} />
            </td>
            <td className="py-2 text-muted">{p.days_held ?? "—"}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
```

- [ ] **Step 2: Verify it builds**

Run: `cd apps/web && pnpm build`
Expected: exit 0.

- [ ] **Step 3: Commit**

```bash
git add apps/web/src/components/ClosedPositionsTable.tsx
git commit -m "web: add ClosedPositionsTable component"
```

---

### Task 12: `ImportPanel`

**Files:**
- Create: `apps/web/src/components/ImportPanel.tsx`

**Interfaces:**
- Consumes: `useImportCsv()` (Task 8).
- Produces: `ImportPanel` (consumed by Task 13).

- [ ] **Step 1: Create `apps/web/src/components/ImportPanel.tsx`**

```tsx
import { useRef, useState, type FormEvent } from "react";
import { useImportCsv } from "../api/queries";

export function ImportPanel() {
  const fileInput = useRef<HTMLInputElement>(null);
  const [selectedName, setSelectedName] = useState<string | null>(null);
  const mutation = useImportCsv();

  function handleSubmit(e: FormEvent) {
    e.preventDefault();
    const file = fileInput.current?.files?.[0];
    if (!file) return;
    mutation.mutate(file);
  }

  return (
    <div className="max-w-md rounded-lg border border-border bg-surface p-6">
      <h2 className="mb-1 text-sm font-bold tracking-widest text-text">SCALABLE CAPITAL CSV IMPORT</h2>
      <p className="mb-4 text-xs text-muted">Upload your Scalable Capital transaction report</p>
      <form onSubmit={handleSubmit}>
        <input
          ref={fileInput}
          type="file"
          accept=".csv,.tsv"
          onChange={(e) => setSelectedName(e.target.files?.[0]?.name ?? null)}
          className="w-full rounded border border-dashed border-border bg-bg p-2 text-xs text-muted"
        />
        <button
          type="submit"
          disabled={!selectedName || mutation.isPending}
          className="mt-3 rounded border border-borderAccent bg-accent/10 px-4 py-2 text-xs font-bold tracking-wide text-accent disabled:opacity-40"
        >
          {mutation.isPending ? "importing..." : "▶ upload & import"}
        </button>
      </form>

      {mutation.isSuccess && (
        <div className="mt-4 rounded border border-borderAccent bg-bg p-3 text-xs text-text">
          <div>transactions imported: {mutation.data.transactions_imported}</div>
          <div>
            holdings created: {mutation.data.holdings_created}, closed: {mutation.data.holdings_closed}
          </div>
          {mutation.data.tickers_added.length > 0 && (
            <div>new tickers: {mutation.data.tickers_added.join(", ")}</div>
          )}
        </div>
      )}
      {mutation.isError && (
        <div className="mt-4 rounded border border-negative bg-bg p-3 text-xs text-negative">
          {(mutation.error as Error).message}
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 2: Verify it builds**

Run: `cd apps/web && pnpm build`
Expected: exit 0.

- [ ] **Step 3: Commit**

```bash
git add apps/web/src/components/ImportPanel.tsx
git commit -m "web: add ImportPanel component"
```

---

### Task 13: App shell — tabs integrating all four views

**Files:**
- Modify: `apps/web/src/App.tsx`

**Interfaces:**
- Consumes: `SummaryCards` (Task 9), `OpenPositionsTable` (Task 10), `ClosedPositionsTable` (Task 11), `ImportPanel` (Task 12).

- [ ] **Step 1: Replace `apps/web/src/App.tsx`**

```tsx
import { useState } from "react";
import { ClosedPositionsTable } from "./components/ClosedPositionsTable";
import { ImportPanel } from "./components/ImportPanel";
import { OpenPositionsTable } from "./components/OpenPositionsTable";
import { SummaryCards } from "./components/SummaryCards";

const TABS = [
  { id: "dashboard", label: "Dashboard" },
  { id: "open", label: "Open Positions" },
  { id: "closed", label: "Closed Positions" },
  { id: "import", label: "Import" },
] as const;

type TabId = (typeof TABS)[number]["id"];

function App() {
  const [tab, setTab] = useState<TabId>("dashboard");

  return (
    <div className="min-h-screen bg-bg p-8 font-mono text-text">
      <h1 className="mb-6 text-lg font-bold tracking-widest text-accent">◈ FINANCE</h1>

      <div className="mb-6 flex gap-2 border-b border-border">
        {TABS.map((t) => (
          <button
            key={t.id}
            onClick={() => setTab(t.id)}
            className={`px-3 py-2 text-xs font-bold tracking-wide ${
              tab === t.id ? "border-b-2 border-accent text-accent" : "text-muted"
            }`}
          >
            {t.label.toUpperCase()}
          </button>
        ))}
      </div>

      {tab === "dashboard" && <SummaryCards />}
      {tab === "open" && <OpenPositionsTable />}
      {tab === "closed" && <ClosedPositionsTable />}
      {tab === "import" && <ImportPanel />}
    </div>
  );
}

export default App;
```

- [ ] **Step 2: Verify it builds**

Run: `cd apps/web && pnpm build`
Expected: exit 0.

- [ ] **Step 3: End-to-end manual verification**

In one terminal: `make dev-backend`
In another terminal: `source ~/.nvm/nvm.sh && nvm use 22 && cd apps/web && pnpm dev`

Open `http://localhost:5173` in a browser. Verify:
- Dark background (`#040804`), monospace green `◈ FINANCE` header.
- Four tabs render: Dashboard, Open Positions, Closed Positions, Import.
- Dashboard shows 6 cards, all zero/blank (empty DB).
- Open/Closed Positions tabs show "no open/closed positions".
- Import tab shows the upload form; selecting and uploading a real Scalable Capital CSV shows a success panel with transaction/holding counts, and switching to Dashboard/Open Positions afterward reflects the imported data (TanStack Query cache invalidation from Task 8).

- [ ] **Step 4: Commit**

```bash
git add apps/web/src/App.tsx
git commit -m "web: wire tab navigation across dashboard, positions, and import views"
```

---

### Task 14: Wrap-up — TODO.md and final regression check

**Files:**
- Modify: `TODO.md`

- [ ] **Step 1: Update the Apps section of `TODO.md`**

Replace:

```markdown
### Apps
- [ ] `apps/desktop/` — Vite + React + TypeScript (TradeForge frontend)
- [ ] `apps/mobile/` — Stock Signal PWA
- [ ] `apps/workers/` — Cloudflare Workers (API proxy, CDN)
```

with:

```markdown
### Apps
- [x] `apps/web` — Vite + React + TS + Tailwind, portfolio dashboard (holdings, P&L, Scalable CSV import)
- [ ] `apps/workers/` — Cloudflare Workers (API proxy, CDN)
```

- [ ] **Step 2: Run the full backend test suite**

Run: `cd apps/backend && uv run pytest -v`
Expected: 6 passed.

- [ ] **Step 3: Run the full frontend build**

Run: `cd apps/web && pnpm build`
Expected: exit 0.

- [ ] **Step 4: Commit**

```bash
git add TODO.md
git commit -m "docs: mark apps/web done in TODO.md"
```
