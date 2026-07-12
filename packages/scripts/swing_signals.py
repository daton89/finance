#!/usr/bin/env python3
"""
swing_signals.py — Swing Momentum Signals for MU + AMD.

Regole:
  BUY  : RSI(14) < 35 che risale >= 35  E  prezzo > EMA20
  SELL : RSI(14) > 70  OPPURE  prezzo < EMA10  OPPURE  prezzo <= entry_price * 0.93
  HOLD : nessuna condizione soddisfatta

Stato posizioni salvato in data/swing_state.json.
Invia report giornaliero con segnali.
"""

import json
import os
import sys
from datetime import datetime, date

import yfinance as yf
import pandas as pd
import numpy as np

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if os.path.basename(SCRIPT_DIR) == "scripts":
    SCRIPT_DIR = os.path.dirname(SCRIPT_DIR)

STATE_FILE = os.path.join(SCRIPT_DIR, "data", "swing_state.json")
LAST_SIGNALS_FILE = os.path.join(SCRIPT_DIR, "data", "last_signals.json")
os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)

TICKERS = ["MU", "AMD"]
CAPITAL = 10000.0          # 10k da far diventare 11k
ALLOC = {"MU": 0.5, "AMD": 0.5}

# ── Calcoli tecnici ──

def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()

def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(span=period, adjust=False).mean()
    avg_loss = loss.ewm(span=period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))

# ── Stato posizioni ──

def load_state() -> dict:
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            return json.load(f)
    return {"capital": CAPITAL, "cash": CAPITAL, "positions": {}}

def save_state(state: dict):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)

def reset_state():
    """Resetta lo stato (nuovo mese)."""
    save_state({"capital": CAPITAL, "cash": CAPITAL, "positions": {}})
    print("🔄 Stato swing resettato — 10k pronti.")

# ── Gestione segnali ultimi ──

def load_last_signals() -> dict:
    """Carica i segnali dell'ultima esecuzione."""
    if os.path.exists(LAST_SIGNALS_FILE):
        with open(LAST_SIGNALS_FILE) as f:
            return json.load(f)
    return {}

def save_last_signals(signals: dict):
    """Salva i segnali correnti per il prossimo confronto."""
    with open(LAST_SIGNALS_FILE, "w") as f:
        json.dump(signals, f, indent=2)

def extract_signal_key(result: dict) -> tuple[str, str]:
    """Estrae ticker e segnale da un risultato."""
    return (result["ticker"], result["signal"])

def signals_changed(current_results: list[dict], last_signals: dict) -> bool:
    """Verifica se qualcosa è cambiato rispetto all'ultima esecuzione."""
    for result in current_results:
        ticker = result["ticker"]
        current_signal = result["signal"]
        last_signal = last_signals.get(ticker, None)

        # Se il segnale è cambiato, ritorna True
        if last_signal != current_signal:
            return True

        # Se il segnale corrente è BUY o SELL, emetti il report
        if current_signal in ("BUY", "SELL") or current_signal.startswith("SELL"):
            return True

    return False

# ── Segnale ──

def generate_signals() -> list[dict]:
    state = load_state()
    results = []

    for ticker in TICKERS:
        # Fetch dati — yfinance ritorna MultiIndex columns anche per singolo ticker
        raw = yf.download(ticker, period="3mo", interval="1d", auto_adjust=True)
        if raw.empty:
            results.append({"ticker": ticker, "signal": "ERROR", "reason": "Nessun dato"})
            continue

        # Se MultiIndex columns, estrai la serie Close per questo ticker
        if isinstance(raw.columns, pd.MultiIndex):
            close = raw["Close"][ticker]
        else:
            close = raw["Close"]

        last_close = float(close.iloc[-1])
        prev_close = float(close.iloc[-2]) if len(close) > 1 else last_close

        # Calcola indicatori
        rsi_vals = rsi(close, 14)
        ema10 = ema(close, 10)
        ema20 = ema(close, 20)
        ema50 = ema(close, 50)

        last_rsi = float(rsi_vals.iloc[-1])
        prev_rsi = float(rsi_vals.iloc[-2]) if len(rsi_vals) > 1 else last_rsi
        last_ema10 = float(ema10.iloc[-1])
        last_ema20 = float(ema20.iloc[-1])
        last_ema50 = float(ema50.iloc[-1]) if len(ema50) >= 50 else None

        # Trend direction
        trend_up = last_ema20 > last_ema50 if last_ema50 else None

        # Prezzo % cambiamento
        pct_1d = ((last_close - prev_close) / prev_close) * 100

        # ── Logica segnale ──
        pos = state.get("positions", {}).get(ticker)
        active = pos is not None

        signal = "HOLD"
        reason = []

        # Check stop loss se posizione attiva
        if active:
            entry = pos["entry_price"]
            stop = entry * 0.93
            if last_close <= stop:
                signal = "SELL"
                reason.append(f"Stop loss -7%: {last_close:.2f} <= {stop:.2f}")
            elif last_rsi > 70:
                signal = "SELL"
                reason.append(f"RSI ipercomprato: {last_rsi:.1f} > 70")
            elif last_close < last_ema10:
                signal = "SELL"
                reason.append(f"Prezzo sotto EMA10: {last_close:.2f} < {last_ema10:.2f}")

        # Check entry se non attivo
        if not active:
            rsi_cross = prev_rsi < 35 and last_rsi >= 35
            price_over_ema20 = last_close > last_ema20
            if rsi_cross and price_over_ema20:
                signal = "BUY"
                reason.append(f"RSI {prev_rsi:.1f}→{last_rsi:.1f} cross + prezzo > EMA20")
            elif rsi_cross:
                signal = "HOLD"
                reason.append(f"RSI cross ma prezzo < EMA20 — no entry")
            elif last_rsi < 35:
                signal = "HOLD (watch)"
                reason.append(f"RSI ipervenduto {last_rsi:.1f} — in attesa di inversione")

        # P&L se attivo
        pl_pct = None
        pl_value = None
        if active:
            entry = pos["entry_price"]
            pl_pct = ((last_close - entry) / entry) * 100
            pl_value = (last_close - entry) * pos.get("shares", 0)
            if signal.startswith("SELL"):
                reason.append(f"P&L: {pl_pct:+.2f}% ({pl_value:+.2f}€)" if pl_value is not None else f"P&L: {pl_pct:+.2f}%")

        # Allocazione suggerita per BUY
        alloc_amount = None
        target_price = None
        if signal == "BUY" and not active:
            alloc_amount = CAPITAL * ALLOC[ticker]
            shares = int(alloc_amount / last_close)
            actual_cost = shares * last_close
            target_price = last_close * 1.10  # +10% target
            stop_price = last_close * 0.93     # -7% stop

        results.append({
            "ticker": ticker,
            "price": last_close,
            "change_1d": pct_1d,
            "rsi": last_rsi,
            "ema10": last_ema10,
            "ema20": last_ema20,
            "ema50": last_ema50,
            "trend_up": trend_up,
            "signal": signal,
            "reason": "; ".join(reason) if reason else "—",
            "active": active,
            "pl_pct": pl_pct,
            "pl_value": pl_value,
            "alloc_amount": alloc_amount,
            "shares_suggested": shares if signal == "BUY" and not active else None,
            "actual_cost": actual_cost if signal == "BUY" and not active else None,
            "target_price": target_price,
            "stop_price": stop_price if signal == "BUY" and not active else None,
        })

    return results

# ── Report ──

SPARK = "▁▂▃▄▅▆▇█"

def sparkline(series: pd.Series, width: int = 10) -> str:
    """Mini sparkline testuale."""
    vals = series.tail(width).values
    if len(vals) == 0:
        return ""
    mn, mx = min(vals), max(vals)
    if mx == mn:
        return SPARK[-1] * width
    bucket = (np.array(vals) - mn) / (mx - mn) * (len(SPARK) - 1)
    return "".join(SPARK[int(round(b))] for b in bucket)

def format_report(results: list[dict]) -> str:
    today = date.today().strftime("%A %d %B %Y")
    lines = [
        f"📊 SWING MOMENTUM REPORT — {today}",
        f"💰 Capitale: 10.000€ | Target: 11.000€ (+10%)",
        "",
    ]

    for r in results:
        t = r["ticker"]
        sig = r["signal"]
        price = r["price"]
        rsi = r["rsi"]
        ema10 = r["ema10"]
        ema20 = r["ema20"]

        # Icona segnale
        if sig == "BUY":
            icon = "🟢 BUY"
        elif sig.startswith("SELL"):
            icon = "🔴 SELL"
        elif "watch" in sig:
            icon = "🟡 WATCH"
        else:
            icon = "⚪ HOLD"

        lines.append(f"─" * 40)
        lines.append(f"  {t}  |  Prezzo: {price:.2f}€  |  1g: {r['change_1d']:+.2f}%")
        lines.append(f"  {icon}  {r['reason']}")
        lines.append(f"  📈 RSI(14): {rsi:.1f}  |  EMA10: {ema10:.0f}  |  EMA20: {ema20:.0f}" +
                      (f"  |  EMA50: {r['ema50']:.0f}" if r['ema50'] else ""))
        lines.append(f"  🧭 Trend: {'🟢 rialzista' if r['trend_up'] else '🔴 ribassista' if r['trend_up'] is not None else 'N/A'}")

        if r["active"]:
            lines.append(f"  💰 Posizione attiva: {r['pl_pct']:+.2f}% ({r['pl_value']:+.2f}€)")

        if sig == "BUY" and not r["active"] and r["shares_suggested"]:
            lines.append(f"  💡 Suggerimento: {r['shares_suggested']} x {t} @ ~{price:.2f}€ = {r['actual_cost']:.0f}€")
            lines.append(f"  🎯 Target: {r['target_price']:.2f}€ (+10%)  |  🛑 Stop: {r['stop_price']:.2f}€ (-7%)")

        lines.append("")

    # Riepilogo
    active_positions = [r for r in results if r["active"]]
    if active_positions:
        total_pl = sum(r["pl_value"] or 0 for r in active_positions)
        lines.append(f"─" * 40)
        lines.append(f"📋 Riepilogo posizioni attive: {total_pl:+.2f}€ P&L")
    else:
        lines.append(f"📋 Tutte le posizioni in attesa — 10k disponibili.")

    lines.append("")
    lines.append("⚙️  Regole: BUY=(RSI<35→>=35 & price>EMA20) | SELL=(RSI>70 | price<EMA10 | -7%)")
    return "\n".join(lines)

# ── CLI ──

def cmd_enter(ticker: str, entry_price: float, shares: int):
    """Registra un'entrata manuale."""
    state = load_state()
    if "positions" not in state:
        state["positions"] = {}
    if ticker in state["positions"]:
        print(f"❌ Posizione già attiva per {ticker}. Esci prima.")
        return
    cost = shares * entry_price
    if cost > state.get("cash", 0):
        print(f"❌ Costo {cost:.2f}€ supera la cassa disponibile {state.get('cash', 0):.2f}€")
        return
    state["positions"][ticker] = {
        "entry_price": entry_price,
        "entry_date": str(date.today()),
        "shares": shares,
        "cost": cost,
    }
    state["cash"] -= cost
    save_state(state)
    print(f"✅ Entrato {shares} x {ticker} @ {entry_price:.2f}€ (costo: {cost:.2f}€, cassa residua: {state['cash']:.2f}€)")

def cmd_exit(ticker: str, exit_price: float):
    """Registra un'uscita manuale."""
    state = load_state()
    pos = state.get("positions", {}).get(ticker)
    if not pos:
        print(f"❌ Nessuna posizione attiva per {ticker}")
        return
    shares = pos["shares"]
    entry = pos["entry_price"]
    gross = shares * exit_price
    pl_pct = ((exit_price - entry) / entry) * 100
    pl_value = gross - pos["cost"]
    state["cash"] += gross
    del state["positions"][ticker]
    save_state(state)
    print(f"✅ Uscito {shares} x {ticker} @ {exit_price:.2f}€")
    print(f"   P&L: {pl_pct:+.2f}% ({pl_value:+.2f}€) — Cassa: {state['cash']:.2f}€")

def cmd_status():
    """Mostra lo stato del fondo swing."""
    state = load_state()
    print(f"💰 Fondo Swing Momentum")
    print(f"   Capitale iniziale: {state.get('capital', 0):.2f}€")
    print(f"   Cassa disponibile: {state.get('cash', 0):.2f}€")
    print(f"   Posizioni attive: {len(state.get('positions', {}))}")
    for ticker, pos in state.get("positions", {}).items():
        pl_pct = 0
        print(f"   • {ticker}: {pos['shares']}x @ {pos['entry_price']:.2f}€ (costo: {pos['cost']:.2f}€, dal {pos.get('entry_date', '?')})")

def main():
    if len(sys.argv) > 1:
        cmd = sys.argv[1]
        if cmd == "reset":
            reset_state()
        elif cmd == "enter":
            if len(sys.argv) < 5:
                print("Uso: swing_signals.py enter <TICKER> <PREZZO> <QUANTITÀ>")
                return
            cmd_enter(sys.argv[2], float(sys.argv[3]), int(sys.argv[4]))
        elif cmd == "exit":
            if len(sys.argv) < 4:
                print("Uso: swing_signals.py exit <TICKER> <PREZZO>")
                return
            cmd_exit(sys.argv[2], float(sys.argv[3]))
        elif cmd == "status":
            cmd_status()
        elif cmd == "--force":
            # Forzza emissione del report completo
            results = generate_signals()
            print(format_report(results))
            # Salva i segnali per il prossimo confronto
            last_signals_dict = {r["ticker"]: r["signal"] for r in results}
            save_last_signals(last_signals_dict)
        else:
            print("Comandi: reset | enter <T> <P> <Q> | exit <T> <P> | status | --force")
        return

    # Default: genera report e verifica delta
    results = generate_signals()
    last_signals = load_last_signals()

    # Se nessun cambiamento e tutti sono HOLD, stampa una riga breve
    if not signals_changed(results, last_signals):
        print("HOLD MU/AMD — nessun cambiamento")
        return

    # Altrimenti, stampa il report completo
    print(format_report(results))

    # Salva i segnali per il prossimo confronto
    last_signals_dict = {r["ticker"]: r["signal"] for r in results}
    save_last_signals(last_signals_dict)

if __name__ == "__main__":
    main()
