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


def simulate(ticker: str, close: pd.Series, capital: float):
    """Long-only simulation, full allocation on BUY, exit fully on SELL.

    Returns dict with equity curve (pd.Series indexed like close), trades list,
    and final cash/shares state (position may still be open at series end —
    in that case we mark-to-market for equity curve purposes but do not close
    the trade for win/loss stats).
    """
    rsi_vals = rsi(close, 14)
    ema10 = ema(close, 10)
    ema20 = ema(close, 20)

    cash = capital
    shares = 0
    entry_price = None
    trades: list[Trade] = []
    open_trade: Trade | None = None

    equity = pd.Series(index=close.index, dtype=float)

    prev_rsi = None
    for i, dt in enumerate(close.index):
        price = float(close.iloc[i])
        cur_rsi = float(rsi_vals.iloc[i])
        cur_ema10 = float(ema10.iloc[i])
        cur_ema20 = float(ema20.iloc[i])

        in_position = shares > 0

        if in_position:
            stop = entry_price * STOP_PCT
            sell = False
            reason = None
            if price <= stop:
                sell, reason = True, "stop -7%"
            elif cur_rsi > RSI_SELL:
                sell, reason = True, "RSI>70"
            elif price < cur_ema10:
                sell, reason = True, "price<EMA10"

            if sell:
                cash += shares * price
                open_trade.exit_date = dt
                open_trade.exit_price = price
                open_trade.reason = reason
                trades.append(open_trade)
                open_trade = None
                shares = 0
                entry_price = None
                in_position = False

        if not in_position and prev_rsi is not None:
            rsi_cross = prev_rsi < RSI_BUY_CROSS and cur_rsi >= RSI_BUY_CROSS
            price_over_ema20 = price > cur_ema20
            if rsi_cross and price_over_ema20:
                alloc_amount = cash  # full allocation of this ticker's cash bucket
                buy_shares = int(alloc_amount / price)
                if buy_shares > 0:
                    cost = buy_shares * price
                    cash -= cost
                    shares = buy_shares
                    entry_price = price
                    open_trade = Trade(dt, price)

        equity.iloc[i] = cash + shares * price
        prev_rsi = cur_rsi

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


# ── CLI ──────────────────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(description="Backtest the swing momentum strategy")
    parser.add_argument("--years", type=int, default=5, help="Lookback period in years")
    parser.add_argument("--tickers", nargs="+", default=TICKERS_DEFAULT, help="Tickers to test")
    args = parser.parse_args()

    tickers = args.tickers
    years = args.years

    # Rebuild allocation map for arbitrary ticker lists (equal split)
    n = len(tickers)
    alloc = {t: 1.0 / n for t in tickers} if n else {}

    today = date.today().strftime("%Y-%m-%d")
    print(f"SWING MOMENTUM BACKTEST — {today}")
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
        sim = simulate(ticker, close, cap)
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
