# Finance Monorepo — Agent Context

## Repo
`~/coding/finance` — personal investment management monorepo.

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
apps/
  backend/         # OLD location — needs migration to packages/backend/
```

## Backlog source of truth
`ROADMAP.md` — currently has "Simplify Roadmap" with 8 steps:
1. Move backend into uv workspace (`apps/backend/` → `packages/backend/`)
2. Update workspace root pyproject.toml
3. Simplify Makefile
4. Kill JS build system (pnpm, package.json cleanup)
5. Delete stub/empty dirs
6. Delete old `apps/backend/`
7. Update README.md
8. Update TODO.md

`TODO.md` — secondary backlog for test coverage + future features.

## Dev loop workflow
- Read ROADMAP.md + TODO.md + git status
- Pick first uncompleted 🔲 task (P0 → P1 priority)
- Execute with Claude Code (`claude -p "..." --allowedTools Read,Edit,Write,Bash --max-turns 20`)
- Verify: `make test`, `make lint`, check `tsc` (JS parts) if applicable
- Commit conventional + push to main
- Cron runs every 60m

## Commands
```bash
make install       # uv sync --all-packages
make dev           # FastAPI on :8000
make test          # run pytest
make lint          # ruff check
```

## Key conventions
- Conventional commits (feat/fix/chore/refactor/docs)
- Push to main directly (solo dev)
- React hooks (JS): MUST be before early return
- `CLOUDFLARE_API_TOKEN` (not the typo)
- Caveman mode preferred
- Claude Code subagent for implementation
