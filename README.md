# Finance

Monorepo unificato per trading, portafoglio e segnali azionari.

## Struttura

```
packages/          # Python uv workspace (librerie condivise)
  core/            # modelli DB, tipi, exchange config
  market-data/     # provider astratto (Polygon, yfinance, Finnhub)
  indicators/      # SMA, RSI, EMA, divergenza, BB, MACD, stocastico
  signal-engine/   # segnali tecnici + AI
  portfolio/       # posizioni, P&L, trade journal
  optimizer/       # wrapper portfolio-optimization (MTD + Markowitz)
apps/              # Applicazioni
  backend/         # FastAPI (chiama packages/)
  desktop/         # TradeForge React SPA
  mobile/          # Stock Signal PWA
  workers/         # Cloudflare Workers (API proxy, CDN)
cli/               # Script operativi
archive/           # Link simbolici ai progetti originali
```

## Setup

```bash
make install       # uv sync + npm install in tutti i sub-progetti
make dev-backend   # FastAPI su :8000
make dev-desktop   # React su :5173
make dev-mobile    # PWA su :5174 (o diverso)
```

## Progetti originali (NON eliminare fino a migrazione completa)

I progetti originali restano in `~/Coding/` finché la migrazione non è
completata e verificata. Vedi `TODO.md`.
