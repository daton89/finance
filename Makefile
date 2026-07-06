.PHONY: install dev test lint clean

# ── Python (uv workspace) ──────────────────────────────────────────
install:
	cd packages && uv sync --all-packages

# ── Development ────────────────────────────────────────────────────
dev:
	cd packages && DATABASE_URL=$${DATABASE_URL:-sqlite:///$$(pwd)/packages/finance.db} uv run --package finance-backend uvicorn main:app --reload --port 8000

# ── Test ───────────────────────────────────────────────────────────
test:
	cd packages && uv run pytest

# ── Lint ───────────────────────────────────────────────────────────
lint:
	cd packages && uv run ruff check

# ── Clean ──────────────────────────────────────────────────────────
clean:
	cd packages && find . -name '__pycache__' -type d -exec rm -rf {} + 2>/dev/null || true
	cd packages && find . -name '*.egg-info' -type d -exec rm -rf {} + 2>/dev/null || true