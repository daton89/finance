.PHONY: install build test lint clean dev-backend dev-desktop dev-mobile backup

# ── Python (uv workspace) ──────────────────────────────────────────
install:
	cd packages && uv sync --all-packages
	cd apps/backend && uv sync

build:
	cd packages && uv build

test:
	cd packages && uv run pytest

lint:
	cd packages && uv run ruff check

# ── Development ────────────────────────────────────────────────────
dev: dev-backend  # add dev-desktop dev-mobile etc. as they're built

dev-backend:
	DATABASE_URL=$${DATABASE_URL:-sqlite:///$$(pwd)/packages/finance.db} cd apps/backend && uv run uvicorn main:app --reload --port 8000

dev-desktop:
	cd apps/desktop && npm run dev

dev-mobile:
	cd apps/mobile && npm run dev

# ── Maintenance ────────────────────────────────────────────────────
backup:
	@echo "TODO: dump SQLite -> R2"

clean:
	cd packages && find . -name '__pycache__' -type d -exec rm -rf {} + 2>/dev/null || true
	cd packages && find . -name '*.egg-info' -type d -exec rm -rf {} + 2>/dev/null || true
