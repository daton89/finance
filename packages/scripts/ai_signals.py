#!/usr/bin/env python3
"""
ai_signals.py — Seconda opinione LLM dal worker Stock Signal.

Interroga il Cloudflare Worker stock-signal (Finnhub + Brave news + LLM) per
gli stock in dismissione e stampa una riga per ticker: segnale, catalyst,
headline. Da leggere in chiave EXIT (ADR-0002): catalyst negativo o WAIT su
uno stock in uscita = motivo per anticipare la vendita.

Uso:
    uv run python scripts/ai_signals.py                  # analisi stock in dismissione
    uv run python scripts/ai_signals.py sync-watchlist   # allinea watchlist worker al portfolio

Config: packages/.env (gitignorato) con STOCK_SIGNAL_URL e STOCK_SIGNAL_TOKEN.
"""

import json
import os
import sys
import time
import urllib.parse
import urllib.request

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if os.path.basename(SCRIPT_DIR) == "scripts":
    SCRIPT_DIR = os.path.dirname(SCRIPT_DIR)

ENV_PATH = os.path.join(SCRIPT_DIR, ".env")
TARGET_PATH = os.path.join(SCRIPT_DIR, "config", "target_allocation.json")

from finance_core.market import load_portfolio  # noqa: E402

ANALYZE_DELAY_S = 2.0  # Finnhub 60/min, OpenRouter free ~50/giorno
TIMEOUT_S = 30


def load_env() -> dict:
    """Parser .env minimale (KEY=VALUE), override da os.environ."""
    cfg = {}
    if os.path.exists(ENV_PATH):
        with open(ENV_PATH) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    cfg[k.strip()] = v.strip()
    for key in ("STOCK_SIGNAL_URL", "STOCK_SIGNAL_TOKEN"):
        if os.environ.get(key):
            cfg[key] = os.environ[key]
    return cfg


def api(cfg: dict, path: str, method: str = "GET", body: dict | None = None) -> dict | list:
    url = cfg["STOCK_SIGNAL_URL"].rstrip("/") + path
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("X-App-Token", cfg["STOCK_SIGNAL_TOKEN"])
    # Cloudflare blocca lo UA di default di urllib
    req.add_header("User-Agent", "finance-agent/1.0")
    if data:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        # Il worker risponde con JSON {"error": ...} anche sugli status di errore
        try:
            return json.loads(e.read().decode())
        except Exception:
            raise e


def divestment_stocks() -> list[dict]:
    """Stock in dismissione: type=stock, ticker presente, target 0%."""
    targets = {}
    if os.path.exists(TARGET_PATH):
        with open(TARGET_PATH) as f:
            targets = json.load(f).get("target", {})
    out = []
    for p in load_portfolio().get("positions", []):
        ticker = p.get("ticker", "")
        if p.get("type") == "stock" and ticker and ticker not in targets:
            out.append(p)
    return out


def _analyze_one(cfg: dict, ticker: str) -> dict | None:
    """Analizza un ticker; ritorna il dict risultato o {'error': ...}."""
    try:
        r = api(cfg, f"/api/analyze?ticker={urllib.parse.quote(ticker)}")
    except Exception as e:
        return {"error": str(e)}
    return r if isinstance(r, dict) else {"error": "risposta inattesa"}


def cmd_analyze(cfg: dict):
    stocks = divestment_stocks()
    if not stocks:
        print("🤖 Nessuno stock in dismissione — niente da analizzare.")
        return
    results: dict[str, dict] = {}
    for i, pos in enumerate(stocks):
        if i:
            time.sleep(ANALYZE_DELAY_S)
        ticker = pos["ticker"]
        results[ticker] = _analyze_one(cfg, ticker)

    # Un solo retry globale per i ticker in rate-limit, dopo che la finestra scade
    limited = [t for t, r in results.items() if "rate limit" in str(r.get("error", "")).lower()]
    if limited:
        time.sleep(62)
        for ticker in limited:
            time.sleep(ANALYZE_DELAY_S)
            results[ticker] = _analyze_one(cfg, ticker)

    for ticker, r in results.items():
        if r.get("error"):
            print(f"⚠️ {ticker}: {r['error']}")
            continue
        signal = r.get("signal", "?")
        conf = r.get("confidence", 0)
        catalyst = (r.get("catalyst") or "—").strip()
        headline = (r.get("headline") or "").strip()
        if len(headline) > 80:
            headline = headline[:77] + "..."
        line = f"🤖 {ticker} — {signal} (conf {conf}%) | catalyst: {catalyst}"
        if headline:
            line += f" | {headline}"
        print(line)
        # Reasoning solo se rilevante per la dismissione
        if signal == "BUY" or (isinstance(conf, (int, float)) and conf >= 70):
            reasoning = (r.get("reasoning") or "").strip()
            if reasoning:
                print(f"   ↳ {reasoning}")


def cmd_sync_watchlist(cfg: dict):
    positions = [p for p in load_portfolio().get("positions", []) if p.get("ticker")]
    wanted = {p["ticker"]: p.get("name", p["ticker"]) for p in positions}

    current = api(cfg, "/api/watchlist")
    current_tickers = {row["ticker"] for row in current} if isinstance(current, list) else set()

    added, removed = [], []
    for ticker, name in wanted.items():
        if ticker not in current_tickers:
            api(cfg, "/api/watchlist", method="POST", body={"ticker": ticker, "name": name})
            added.append(ticker)
    for ticker in sorted(current_tickers - set(wanted)):
        api(cfg, f"/api/watchlist?ticker={urllib.parse.quote(ticker)}", method="DELETE")
        removed.append(ticker)

    if not added and not removed:
        print(f"✅ Watchlist già allineata ({len(current_tickers)} ticker).")
    else:
        if added:
            print(f"➕ Aggiunti: {', '.join(added)}")
        if removed:
            print(f"➖ Rimossi: {', '.join(removed)}")


def main():
    cfg = load_env()
    if not cfg.get("STOCK_SIGNAL_URL") or not cfg.get("STOCK_SIGNAL_TOKEN"):
        # Il digest non deve fallire se il worker non è configurato
        print("⚠️ ai_signals non configurato (packages/.env)")
        return

    if len(sys.argv) > 1 and sys.argv[1] == "sync-watchlist":
        cmd_sync_watchlist(cfg)
    else:
        cmd_analyze(cfg)


if __name__ == "__main__":
    main()
