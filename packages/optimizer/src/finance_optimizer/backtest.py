"""Rolling-window walk-forward backtest engine."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence

import numpy as np
import pandas as pd

from finance_optimizer.assortativity import (
    MODALITY_MODES,
    Modality,
    local_assortativity_piraveenan,
    local_assortativity_sabek_pigorsch,
)
from finance_optimizer.mtd import fit_mtd
from finance_optimizer.network import build_correlation_network, build_mtd_network
from finance_optimizer.optimize import (
    assortative_max_quadratic_utility,
    classic_max_quadratic_utility,
)
from finance_optimizer.returns import discretize_states, log_returns

TRADING_DAYS_PER_YEAR = 252
WEIGHT_TOLERANCE = 1e-4

_LOCAL_MEASURES = {
    "piraveenan": local_assortativity_piraveenan,
    "sabek_pigorsch": local_assortativity_sabek_pigorsch,
}


@dataclass
class BacktestConfig:
    estimation_window: int
    holding_period: int
    rebalance_every: int
    risk_aversion: float = 10.0
    risk_free_rate: float = 0.0
    z_states: int = 3
    transaction_cost_bps: float = 0.0
    max_position: float | None = None


@dataclass
class BacktestResult:
    summary: pd.DataFrame
    equity_curves: pd.DataFrame
    weights_history: dict[str, pd.DataFrame] = field(default_factory=dict)


def _annualized_mean_cov(window_returns: pd.DataFrame) -> tuple[pd.Series, pd.DataFrame]:
    mu = window_returns.mean() * TRADING_DAYS_PER_YEAR
    cov = window_returns.cov() * TRADING_DAYS_PER_YEAR
    return mu, cov


def _realized_return(weights: pd.Series, hold_returns: pd.DataFrame) -> pd.Series:
    aligned = hold_returns[weights.index]
    return aligned.mul(weights, axis=1).sum(axis=1)


def _turnover(new_weights: pd.Series, prev_weights: pd.Series | None) -> float:
    if prev_weights is None:
        return float(new_weights.abs().sum()) / 2.0
    union_idx = new_weights.index.union(prev_weights.index)
    new_aligned = new_weights.reindex(union_idx, fill_value=0.0)
    prev_aligned = prev_weights.reindex(union_idx, fill_value=0.0)
    diff = new_aligned - prev_aligned
    return float(diff.abs().sum()) / 2.0


def _apply_cost(hold_returns_series: pd.Series, turnover: float, cost_bps: float) -> pd.Series:
    if cost_bps <= 0 or turnover <= 0 or hold_returns_series.empty:
        return hold_returns_series
    out = hold_returns_series.copy()
    out.iloc[0] = out.iloc[0] - turnover * cost_bps / 10000.0
    return out


def _equal_weight_series(tickers: Sequence[str]) -> pd.Series:
    n = len(tickers)
    return pd.Series([1.0 / n] * n, index=list(tickers))


def _max_drawdown(equity: pd.Series) -> float:
    if equity.empty:
        return 0.0
    running_max = equity.cummax()
    drawdown = equity / running_max - 1.0
    return float(drawdown.min())


def run_backtest(
    prices: pd.DataFrame,
    config: BacktestConfig,
    modalities: Sequence[Modality] = tuple(MODALITY_MODES),
    benchmark_prices: pd.Series | None = None,
    include_equal_weight: bool = True,
) -> pd.DataFrame:
    """One row per (model, modality) with summary statistics."""
    return run_backtest_detailed(
        prices=prices,
        config=config,
        modalities=modalities,
        benchmark_prices=benchmark_prices,
        include_equal_weight=include_equal_weight,
    ).summary


def run_backtest_detailed(
    prices: pd.DataFrame,
    config: BacktestConfig,
    modalities: Sequence[Modality] = tuple(MODALITY_MODES),
    benchmark_prices: pd.Series | None = None,
    include_equal_weight: bool = True,
) -> BacktestResult:
    returns = log_returns(prices)
    records: dict[tuple[str, str], dict[str, list]] = {}
    prev_weights: dict[tuple[str, str], pd.Series] = {}

    bench_returns: pd.Series | None
    if benchmark_prices is not None:
        bench_returns = np.log(benchmark_prices / benchmark_prices.shift(1)).dropna()
    else:
        bench_returns = None

    def record(model: str, modality: str, weights: pd.Series, hold_returns: pd.DataFrame) -> None:
        key = (model, modality)
        bucket = records.setdefault(
            key,
            {"daily_returns": [], "n_stocks": [], "turnovers": [], "weights_log": []},
        )
        turnover = _turnover(weights, prev_weights.get(key))
        prev_weights[key] = weights.copy()

        daily = _realized_return(weights, hold_returns)
        daily = _apply_cost(daily, turnover, config.transaction_cost_bps)
        bucket["daily_returns"].append(daily)
        bucket["n_stocks"].append(int((weights.abs() > WEIGHT_TOLERANCE).sum()))
        bucket["turnovers"].append(turnover)
        bucket["weights_log"].append(weights.rename(hold_returns.index[0]))

    n_steps = len(returns)
    start = 0
    common_max_pos = config.max_position
    while start + config.estimation_window + config.holding_period <= n_steps:
        window = returns.iloc[start : start + config.estimation_window]
        hold_start = start + config.estimation_window
        hold = returns.iloc[hold_start : hold_start + config.holding_period]

        mu, cov = _annualized_mean_cov(window)
        states = discretize_states(window)
        mtd_result = fit_mtd(states, z=config.z_states)
        mtd_graph = build_mtd_network(mtd_result.lambdas)
        corr_graph = build_correlation_network(window)

        record(
            "classic",
            "n/a",
            classic_max_quadratic_utility(
                mu, cov, risk_aversion=config.risk_aversion, max_position=common_max_pos
            ),
            hold,
        )

        for measure_name, local_fn in _LOCAL_MEASURES.items():
            for modality in modalities:
                rho_mtd = local_fn(mtd_graph, modality)
                weights_mtd = assortative_max_quadratic_utility(
                    mu,
                    cov,
                    rho_mtd,
                    modality,
                    risk_aversion=config.risk_aversion,
                    max_position=common_max_pos,
                )
                record(f"mtd_{measure_name}", modality, weights_mtd, hold)

                rho_corr = local_fn(corr_graph, modality)
                weights_corr = assortative_max_quadratic_utility(
                    mu,
                    cov,
                    rho_corr,
                    modality,
                    risk_aversion=config.risk_aversion,
                    max_position=common_max_pos,
                )
                record(f"correlation_benchmark_{measure_name}", modality, weights_corr, hold)

        if include_equal_weight:
            ew = _equal_weight_series(mu.index)
            record("equal_weight", "n/a", ew, hold)

        start += config.rebalance_every

    rows = []
    equity_curves: dict[str, pd.Series] = {}
    weights_history: dict[str, pd.DataFrame] = {}

    for (model, modality), bucket in records.items():
        daily = pd.concat(bucket["daily_returns"])
        expected_return = daily.mean() * TRADING_DAYS_PER_YEAR
        annual_volatility = daily.std(ddof=1) * np.sqrt(TRADING_DAYS_PER_YEAR)
        excess = expected_return - config.risk_free_rate
        sharpe_ratio = excess / annual_volatility if annual_volatility > 0 else np.nan
        equity = np.exp(daily.cumsum())
        max_dd = _max_drawdown(equity)
        avg_turnover = float(np.mean(bucket["turnovers"])) if bucket["turnovers"] else 0.0
        rebalances_per_year = TRADING_DAYS_PER_YEAR / config.rebalance_every
        cost_drag_bps = avg_turnover * rebalances_per_year * config.transaction_cost_bps

        rows.append(
            {
                "model": model,
                "modality": modality,
                "expected_return": expected_return,
                "annual_volatility": annual_volatility,
                "sharpe_ratio": sharpe_ratio,
                "avg_n_stocks": float(np.mean(bucket["n_stocks"])),
                "max_drawdown": max_dd,
                "avg_turnover": avg_turnover,
                "cost_drag_bps": cost_drag_bps,
            }
        )

        label = f"{model}__{modality}"
        equity_curves[label] = equity
        weights_history[label] = pd.DataFrame(bucket["weights_log"]).fillna(0.0)

    if bench_returns is not None and records:
        any_daily = pd.concat(next(iter(records.values()))["daily_returns"])
        bench_aligned = bench_returns.reindex(any_daily.index).dropna()
        if not bench_aligned.empty:
            expected_return = bench_aligned.mean() * TRADING_DAYS_PER_YEAR
            annual_volatility = bench_aligned.std(ddof=1) * np.sqrt(TRADING_DAYS_PER_YEAR)
            sharpe_ratio = (
                (expected_return - config.risk_free_rate) / annual_volatility
                if annual_volatility > 0
                else np.nan
            )
            equity = np.exp(bench_aligned.cumsum())
            rows.append(
                {
                    "model": "buy_and_hold_benchmark",
                    "modality": "n/a",
                    "expected_return": expected_return,
                    "annual_volatility": annual_volatility,
                    "sharpe_ratio": sharpe_ratio,
                    "avg_n_stocks": 1.0,
                    "max_drawdown": _max_drawdown(equity),
                    "avg_turnover": 0.0,
                    "cost_drag_bps": 0.0,
                }
            )
            equity_curves["buy_and_hold_benchmark__n/a"] = equity

    summary = pd.DataFrame(rows)
    equity_df = pd.DataFrame(equity_curves).sort_index()
    return BacktestResult(summary=summary, equity_curves=equity_df, weights_history=weights_history)
