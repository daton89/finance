# TODO

## Fatto ✅

- [x] Monorepo strutturato (uv workspace in `packages/`, pnpm workspace per JS)
- [x] `packages/core` — ORM models, engine, config, calendario, tipi
- [x] `packages/indicators` — 32 indicatori puri Python
- [x] `packages/signal-engine` — VV stubs (`_composite_score`, `_rating_downgrade`)
- [x] `packages/ai-signals` — HTTP adapter verso Stock Signal Worker
- [x] `packages/optimizer` — MTD, assortatività, backtest, snapshot
- [x] `packages/portfolio` — posizioni, P&L, import CSV Scalable Capital
- [x] TradingView-backed market data (`packages/market-data`) + composite scoring in `packages/signal-engine` — replaces unused VectorVest models
- [x] `apps/backend/` — FastAPI con upload CSV Scalable (localhost:8000)
- [x] Tutti i 7 package buildano (sdist + wheel) e passano `ruff check`
- [x] Git init, `.gitignore`
- [x] Makefile con target `dev`, `dev-backend`, `build`, `test`, `lint`

## Da fare

### Testing
- [ ] Test per `packages/indicators`
- [ ] Test per `packages/signal-engine`
- [ ] Test per `packages/ai-signals`
- [ ] Test per `packages/optimizer`
- [ ] Test per `packages/portfolio`

### Apps
- [ ] `apps/desktop/` — Vite + React + TypeScript (TradeForge frontend)
- [ ] `apps/mobile/` — Stock Signal PWA
- [ ] `apps/workers/` — Cloudflare Workers (API proxy, CDN)

### Cleanup
- [ ] Eliminare `~/Coding/stock-monitor/`
- [ ] Eliminare `~/Coding/stock-signal/`
- [ ] Eliminare `~/Coding/TradeForge/`
- [ ] Mantieni `~/Coding/portfolio-optimization/` come dipendenza git
