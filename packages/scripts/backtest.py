#!/usr/bin/env python3
"""
backtest.py — Backtest engine for the swing momentum strategy in swing_signals.py.

Rules (mirrored exactly from swing_signals.py):
  BUY  : RSI(14) crosses back up through 35 (was <35, now >=35) AND price > EMA20
  SELL : RSI(14) > 70  OR  price < EMA10  OR  price <= entry_price * 0.93 (stop -7%)
  Allocation: 50/50 capital split MU/AMD, 10000 EUR starting capital, long-only,
              full allocation per ticker on BUY, exit fully on SELL.

Execution choice: signals are evaluated on each day's close, and the trade
(buy/sell) is executed at that SAME day's close price ("close-on-signal-day").
This is a simplification vs a more realistic next-day-open execution, but it
matches exactly what swing_signals.py itself measures (it reports the signal
using the latest available close), so the backtest is consistent with how the
live script would be read/acted on when checked once per day after close.

Usage:
    uv run python scripts/backtest.py [--years 5] [--tickers MU AMD]
    uv run python scripts/backtest.py --compare          # rank rule variants
    uv run python scripts/backtest.py --variant exit-ema20   # full report, one variant
"""

import argparse
import sys
from datetime import date

import numpy as np
import pandas as pd
import yfinance as yf

TICKERS_DEFAULT = ["MU", "AMD"]
CAPITAL = 10000.0
ALLOC = {"MU": 0.5, "AMD": 0.5}
STOP_PCT = 0.93
RSI_BUY_CROSS = 35
RSI_SELL = 70

# ── Indicators (identical to swing_signals.py) ──────────────────────────────


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


# ── Data ─────────────────────────────────────────────────────────────────────


def fetch_close(ticker: str, years: int) -> pd.Series:
    raw = yf.download(ticker, period=f"{years}y", interval="1d", auto_adjust=True, progress=False)
    if raw.empty:
        raise RuntimeError(f"No data for {ticker}")
    if isinstance(raw.columns, pd.MultiIndex):
        close = raw["Close"][ticker]
    else:
        close = raw["Close"]
    return close.dropna()


# ── Strategy variants ────────────────────────────────────────────────────────
#
# Ogni variante è un dict di parametri per simulate():
#   entry: "rsi_cross" (RSI risale sopra entry_rsi & price>EMA20)
#          "rsi_over"  (RSI > entry_rsi & price>EMA20 — meno restrittiva)
#          "ema_cross" (EMA10 incrocia sopra EMA20)
#          "breakout"  (chiusura > massimo 20gg precedente)
#   exit_ema:     periodo EMA sotto cui vendere (None = disattivo)
#   exit_rsi:     soglia RSI di vendita (None = disattivo)
#   stop_pct:     stop fisso da entry (0.93 = -7%; None = disattivo)
#   trailing_pct: trailing stop dal massimo post-entry (None = disattivo)

VARIANTS = {
    "live": dict(entry="rsi_cross", entry_rsi=35, exit_ema=10, exit_rsi=70,
                 stop_pct=0.93, trailing_pct=None),
    "exit-ema20": dict(entry="rsi_cross", entry_rsi=35, exit_ema=20, exit_rsi=70,
                       stop_pct=0.93, trailing_pct=None),
    "exit-ema50": dict(entry="rsi_cross", entry_rsi=35, exit_ema=50, exit_rsi=None,
                       stop_pct=0.93, trailing_pct=None),
    "entry-rsi50": dict(entry="rsi_cross", entry_rsi=50, exit_ema=20, exit_rsi=None,
                        stop_pct=0.93, trailing_pct=None),
    "entry-rsi-over45": dict(entry="rsi_over", entry_rsi=45, exit_ema=20, exit_rsi=None,
                             stop_pct=0.93, trailing_pct=None),
    "ema-cross": dict(entry="ema_cross", entry_rsi=None, exit_ema=20, exit_rsi=None,
                      stop_pct=0.93, trailing_pct=None),
    "breakout20": dict(entry="breakout", entry_rsi=None, exit_ema=20, exit_rsi=None,
                       stop_pct=0.93, trailing_pct=None),
    "breakout-trail15": dict(entry="breakout", entry_rsi=None, exit_ema=None, exit_rsi=None,
                             stop_pct=0.93, trailing_pct=0.15),
    "ema-cross-trail15": dict(entry="ema_cross", entry_rsi=None, exit_ema=None, exit_rsi=None,
                              stop_pct=0.93, trailing_pct=0.15),
}


# ── Trade simulation ─────────────────────────────────────────────────────────


class Trade:
    __slots__ = ("entry_date", "entry_price", "exit_date", "exit_price", "reason")

    def __init__(self, entry_date, entry_price):
        self.entry_date = entry_date
        self.entry_price = entry_price
        self.exit_date = None
        self.exit_price = None
        self.reason = None

    @property
    def pl_pct(self):
        if self.exit_price is None:
            return None
        return (self.exit_price - self.entry_price) / self.entry_price * 100


def simulate(ticker: str, close: pd.Series, capital: float, params: dict | None = None):
    """Long-only simulation, full allocation on BUY, exit fully on SELL.

    params: variante di strategia (vedi VARIANTS). Default = regole live.

    Returns dict with equity curve (pd.Series indexed like close), trades list,
    and final cash/shares state (position may still be open at series end —
    in that case we mark-to-market for equity curve purposes but do not close
    the trade for win/loss stats).
    """
    p = params or VARIANTS["live"]

    rsi_vals = rsi(close, 14)
    ema10 = ema(close, 10)
    ema20 = ema(close, 20)
    ema50 = ema(close, 50)
    emas = {10: ema10, 20: ema20, 50: ema50}
    high20 = close.rolling(20).max().shift(1)  # massimo 20gg precedente (escluso oggi)

    exit_ema_series = emas.get(p["exit_ema"]) if p["exit_ema"] else None

    cash = capital
    shares = 0
    entry_price = None
    peak_since_entry = None
    trades: list[Trade] = []
    open_trade: Trade | None = None

    equity = pd.Series(index=close.index, dtype=float)

    prev_rsi = None
    prev_ema10 = None
    prev_ema20 = None
    for i, dt in enumerate(close.index):
        price = float(close.iloc[i])
        cur_rsi = float(rsi_vals.iloc[i])
        cur_ema10 = float(ema10.iloc[i])
        cur_ema20 = float(ema20.iloc[i])

        in_position = shares > 0

        if in_position:
            peak_since_entry = max(peak_since_entry, price)
            sell = False
            reason = None
            if p["stop_pct"] and price <= entry_price * p["stop_pct"]:
                sell, reason = True, f"stop {(p['stop_pct'] - 1) * 100:.0f}%"
            elif p["exit_rsi"] and cur_rsi > p["exit_rsi"]:
                sell, reason = True, f"RSI>{p['exit_rsi']}"
            elif exit_ema_series is not None and price < float(exit_ema_series.iloc[i]):
                sell, reason = True, f"price<EMA{p['exit_ema']}"
            elif p["trailing_pct"] and price <= peak_since_entry * (1 - p["trailing_pct"]):
                sell, reason = True, f"trail -{p['trailing_pct'] * 100:.0f}%"

            if sell:
                cash += shares * price
                open_trade.exit_date = dt
                open_trade.exit_price = price
                open_trade.reason = reason
                trades.append(open_trade)
                open_trade = None
                shares = 0
                entry_price = None
                peak_since_entry = None
                in_position = False

        if not in_position and prev_rsi is not None:
            entry_mode = p["entry"]
            if entry_mode == "rsi_cross":
                buy = (prev_rsi < p["entry_rsi"] and cur_rsi >= p["entry_rsi"]
                       and price > cur_ema20)
            elif entry_mode == "rsi_over":
                buy = cur_rsi > p["entry_rsi"] and price > cur_ema20
            elif entry_mode == "ema_cross":
                buy = (prev_ema10 is not None and prev_ema10 <= prev_ema20
                       and cur_ema10 > cur_ema20)
            elif entry_mode == "breakout":
                h20 = high20.iloc[i]
                buy = not pd.isna(h20) and price > float(h20)
            else:
                raise ValueError(f"Unknown entry mode: {entry_mode}")

            if buy:
                alloc_amount = cash  # full allocation of this ticker's cash bucket
                buy_shares = int(alloc_amount / price)
                if buy_shares > 0:
                    cost = buy_shares * price
                    cash -= cost
                    shares = buy_shares
                    entry_price = price
                    peak_since_entry = price
                    open_trade = Trade(dt, price)

        equity.iloc[i] = cash + shares * price
        prev_rsi = cur_rsi
        prev_ema10 = cur_ema10
        prev_ema20 = cur_ema20

    return {
        "equity": equity,
        "trades": trades,
        "open_position": open_trade is not None,
        "final_cash": cash,
        "final_shares": shares,
        "entry_price": entry_price,
    }


# ── Metrics ──────────────────────────────────────────────────────────────────


def cagr(equity: pd.Series) -> float:
    start, end = equity.iloc[0], equity.iloc[-1]
    days = (equity.index[-1] - equity.index[0]).days
    years = days / 365.25
    if years <= 0 or start <= 0:
        return 0.0
    return ((end / start) ** (1 / years) - 1) * 100


def max_drawdown(equity: pd.Series) -> float:
    running_max = equity.cummax()
    dd = (equity - running_max) / running_max
    return dd.min() * 100


def trade_stats(trades: list[Trade]):
    closed = [t for t in trades if t.exit_price is not None]
    n = len(closed)
    if n == 0:
        return {"n": 0, "win_rate": 0.0, "avg_win": 0.0, "avg_loss": 0.0}
    wins = [t.pl_pct for t in closed if t.pl_pct > 0]
    losses = [t.pl_pct for t in closed if t.pl_pct <= 0]
    return {
        "n": n,
        "win_rate": len(wins) / n * 100,
        "avg_win": (sum(wins) / len(wins)) if wins else 0.0,
        "avg_loss": (sum(losses) / len(losses)) if losses else 0.0,
    }


def rolling_30d_returns(equity: pd.Series) -> pd.Series:
    """Rolling 30-calendar-day-ish (21 trading day) return series."""
    window = 21
    return (equity / equity.shift(window) - 1) * 100


# ── Report formatting ────────────────────────────────────────────────────────


def fmt_line(*cols, widths):
    return "".join(str(c).ljust(w) for c, w in zip(cols, widths))


def per_ticker_report(ticker: str, close: pd.Series, sim: dict, capital: float) -> str:
    equity = sim["equity"].dropna()
    trades = sim["trades"]
    stats = trade_stats(trades)

    total_return = (equity.iloc[-1] / equity.iloc[0] - 1) * 100
    cagr_v = cagr(equity)
    mdd = max_drawdown(equity)

    bh_equity = capital * (close / close.iloc[0])
    bh_return = (bh_equity.iloc[-1] / bh_equity.iloc[0] - 1) * 100
    bh_cagr = cagr(bh_equity)
    bh_mdd = max_drawdown(bh_equity)

    lines = []
    lines.append(f"--- {ticker} ---")
    lines.append(f"{'':14}{'Strategy':>14}{'Buy&Hold':>14}")
    lines.append(f"{'Total return':14}{total_return:>13.1f}%{bh_return:>13.1f}%")
    lines.append(f"{'CAGR':14}{cagr_v:>13.1f}%{bh_cagr:>13.1f}%")
    lines.append(f"{'Max drawdown':14}{mdd:>13.1f}%{bh_mdd:>13.1f}%")
    lines.append(f"Trades: {stats['n']}  Win rate: {stats['win_rate']:.0f}%  "
                 f"Avg win: {stats['avg_win']:+.1f}%  Avg loss: {stats['avg_loss']:+.1f}%")
    if sim["open_position"]:
        lines.append(f"(position still open at end of period, entry {sim['entry_price']:.2f})")
    if trades:
        lines.append("Trade log:")
        for t in trades:
            ed = t.entry_date.strftime("%Y-%m-%d")
            xd = t.exit_date.strftime("%Y-%m-%d") if t.exit_date else "OPEN"
            pl = f"{t.pl_pct:+.1f}%" if t.pl_pct is not None else "n/a"
            reason = t.reason or ""
            lines.append(f"  {ed} @ {t.entry_price:>7.2f} -> {xd} @ "
                         f"{t.exit_price if t.exit_price else 0:>7.2f}  {pl:>7}  ({reason})")
    return "\n".join(lines)


def monthly_reality_check(ticker: str, sim: dict) -> str:
    equity = sim["equity"].dropna()
    roll = rolling_30d_returns(equity).dropna()
    if roll.empty:
        return f"{ticker}: not enough data for rolling-30d analysis"
    n = len(roll)
    hit_10 = (roll >= 10).sum()
    pct_hit = hit_10 / n * 100
    lines = [
        f"{ticker}: rolling ~30-trading-day (21d) return windows: n={n}",
        f"  mean={roll.mean():+.1f}%  median={roll.median():+.1f}%  "
        f"std={roll.std():.1f}%  min={roll.min():+.1f}%  max={roll.max():+.1f}%",
        f"  windows hitting >= +10%: {hit_10}/{n} ({pct_hit:.1f}%)",
    ]
    return "\n".join(lines)


def combined_report(results: dict, capital_total: float) -> str:
    total_equity = None
    total_bh_equity = None
    for ticker, r in results.items():
        eq = r["sim"]["equity"].dropna()
        bh = capital_total_alloc(r["capital"], r["close"])
        if total_equity is None:
            total_equity = eq.copy()
            total_bh_equity = bh.copy()
        else:
            total_equity = total_equity.add(eq, fill_value=0)
            total_bh_equity = total_bh_equity.add(bh, fill_value=0)

    total_equity = total_equity.dropna()
    total_bh_equity = total_bh_equity.dropna()

    total_return = (total_equity.iloc[-1] / total_equity.iloc[0] - 1) * 100
    bh_return = (total_bh_equity.iloc[-1] / total_bh_equity.iloc[0] - 1) * 100
    cagr_v = cagr(total_equity)
    bh_cagr = cagr(total_bh_equity)
    mdd = max_drawdown(total_equity)
    bh_mdd = max_drawdown(total_bh_equity)

    n_trades = sum(trade_stats(r["sim"]["trades"])["n"] for r in results.values())

    lines = []
    lines.append("--- COMBINED (50/50 portfolio) ---")
    lines.append(f"{'':14}{'Strategy':>14}{'Buy&Hold':>14}")
    lines.append(f"{'Total return':14}{total_return:>13.1f}%{bh_return:>13.1f}%")
    lines.append(f"{'CAGR':14}{cagr_v:>13.1f}%{bh_cagr:>13.1f}%")
    lines.append(f"{'Max drawdown':14}{mdd:>13.1f}%{bh_mdd:>13.1f}%")
    lines.append(f"Total trades: {n_trades}")
    return "\n".join(lines)


def capital_total_alloc(capital: float, close: pd.Series) -> pd.Series:
    return capital * (close / close.iloc[0])


# ── Variant comparison ───────────────────────────────────────────────────────


def compare_variants(tickers: list[str], years: int):
    """Esegue tutte le VARIANTS su ogni ticker e stampa classifica combinata."""
    closes = {}
    for t in tickers:
        try:
            closes[t] = fetch_close(t, years)
        except Exception as e:
            print(f"{t}: ERROR fetching data — {e}")
    if not closes:
        sys.exit(1)

    n = len(closes)
    cap = CAPITAL / n

    # Buy & hold benchmark combinato
    bh_total = None
    for t, close in closes.items():
        bh = capital_total_alloc(cap, close)
        bh_total = bh if bh_total is None else bh_total.add(bh, fill_value=0)
    bh_total = bh_total.dropna()
    bh_ret = (bh_total.iloc[-1] / bh_total.iloc[0] - 1) * 100
    bh_mdd = max_drawdown(bh_total)

    rows = []
    for name, params in VARIANTS.items():
        total_eq = None
        n_trades = 0
        wins = 0
        for t, close in closes.items():
            sim = simulate(t, close, cap, params)
            eq = sim["equity"].dropna()
            total_eq = eq if total_eq is None else total_eq.add(eq, fill_value=0)
            st = trade_stats(sim["trades"])
            n_trades += st["n"]
            wins += round(st["n"] * st["win_rate"] / 100)
        total_eq = total_eq.dropna()
        ret = (total_eq.iloc[-1] / total_eq.iloc[0] - 1) * 100
        mdd = max_drawdown(total_eq)
        cg = cagr(total_eq)
        roll = rolling_30d_returns(total_eq).dropna()
        hit10 = (roll >= 10).mean() * 100 if not roll.empty else 0.0
        win_rate = wins / n_trades * 100 if n_trades else 0.0
        rows.append((name, ret, cg, mdd, n_trades, win_rate, hit10))

    rows.sort(key=lambda r: r[1], reverse=True)

    print(f"VARIANT COMPARISON — {', '.join(closes)} | {years}y | combined 50/50 portfolio")
    print(f"Benchmark Buy&Hold: return {bh_ret:+.1f}%  maxDD {bh_mdd:.1f}%")
    print("=" * 78)
    print(f"{'variant':<20}{'return':>9}{'CAGR':>8}{'maxDD':>8}{'trades':>8}"
          f"{'win%':>7}{'m>=10%':>8}")
    for name, ret, cg, mdd, n_tr, wr, hit10 in rows:
        print(f"{name:<20}{ret:>+8.1f}%{cg:>+7.1f}%{mdd:>7.1f}%{n_tr:>8}"
              f"{wr:>6.0f}%{hit10:>7.1f}%")
    print("=" * 78)
    print("m>=10% = % di finestre rolling 21gg con ritorno >= +10% (target mensile)")


# ── CLI ──────────────────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(description="Backtest the swing momentum strategy")
    parser.add_argument("--years", type=int, default=5, help="Lookback period in years")
    parser.add_argument("--tickers", nargs="+", default=TICKERS_DEFAULT, help="Tickers to test")
    parser.add_argument("--compare", action="store_true",
                        help="Confronta tutte le varianti di regole e stampa classifica")
    parser.add_argument("--variant", choices=sorted(VARIANTS), default="live",
                        help="Variante di regole per il report completo (default: live)")
    args = parser.parse_args()

    tickers = args.tickers
    years = args.years

    if args.compare:
        compare_variants(tickers, years)
        return

    params = VARIANTS[args.variant]

    # Rebuild allocation map for arbitrary ticker lists (equal split)
    n = len(tickers)
    alloc = {t: 1.0 / n for t in tickers} if n else {}

    today = date.today().strftime("%Y-%m-%d")
    print(f"SWING MOMENTUM BACKTEST — {today} — variant: {args.variant}")
    print(f"Tickers: {', '.join(tickers)} | Period: {years}y | Capital: {CAPITAL:.0f} EUR (50/50 split)")
    print("Execution: signal + trade both evaluated on same day's close (close-on-signal-day).")
    print("=" * 60)

    results = {}
    for ticker in tickers:
        try:
            close = fetch_close(ticker, years)
        except Exception as e:
            print(f"{ticker}: ERROR fetching data — {e}")
            continue
        cap = CAPITAL * alloc[ticker]
        sim = simulate(ticker, close, cap, params)
        results[ticker] = {"close": close, "sim": sim, "capital": cap}
        print()
        print(per_ticker_report(ticker, close, sim, cap))

    if len(results) >= 1:
        print()
        print(combined_report(results, CAPITAL))

    print()
    print("=" * 60)
    print("MONTHLY REALITY CHECK — rolling ~30-trading-day return vs +10%/month target")
    for ticker, r in results.items():
        print()
        print(monthly_reality_check(ticker, r["sim"]))

    if not results:
        sys.exit(1)


if __name__ == "__main__":
    main()
