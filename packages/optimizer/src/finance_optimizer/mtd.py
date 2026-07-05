"""Two-stage estimation of the multivariate Mixture Transition Distribution (MTD) model.

Stage 1 (Billingsley 1961 MLE): closed-form transition probabilities p_hk^(i,j)
estimated per pair of series from observed lag-1 state co-occurrence counts.

Stage 2 (per-target SLSQP): the paper's eq. (5) joint-frequency log-likelihood
would require a z^n contingency table, which is intractable. Following the
standard MTD-literature simplification (Berchtold & Raftery 2002), each
target series j's lambda column is estimated independently by maximizing
sum_t log(lambda_j . v_t), where v_t[i] = p_hat^(i,j) evaluated at the actual
observed (lag-1 state of i, current state of j) pair at time t.
"""

from __future__ import annotations

import warnings
from dataclasses import dataclass

import numpy as np
import pandas as pd
from scipy.optimize import minimize

_LOG_FLOOR = 1e-12


@dataclass
class MTDResult:
    lambdas: pd.DataFrame  # n x n; lambdas.loc[i, j] = lambda_ij, columns sum to 1
    transition: np.ndarray  # shape (n, n, z, z); transition[i, j, h, k] = p_hat_hk^(i,j)
    tickers: list[str]
    z: int = 3


def estimate_transition_tensor(states: pd.DataFrame, z: int = 3) -> np.ndarray:
    """Stage 1: f_hk^(i,j) / sum_k f_hk^(i,j) for every pair of series (i, j).

    Rows with zero observed outflow (a state h never preceded a transition in
    the window) fall back to a uniform 1/z row so every row stays stochastic.
    """
    arr = states.to_numpy()
    lag = arr[:-1]
    curr = arr[1:]

    lag_onehot = np.eye(z)[lag]
    curr_onehot = np.eye(z)[curr]
    counts = np.einsum("tih,tjk->ijhk", lag_onehot, curr_onehot)

    row_sums = counts.sum(axis=3, keepdims=True)
    uniform = np.full_like(counts, 1.0 / z)
    probs = np.divide(counts, row_sums, out=uniform, where=row_sums > 0)
    return probs


def estimate_lambda_column(
    states: pd.DataFrame, transition: np.ndarray, target_j: int
) -> np.ndarray:
    """Stage 2: SLSQP-maximize sum_t log(lambda . v_t) over the simplex, for one target series."""
    arr = states.to_numpy()
    n = arr.shape[1]
    lag = arr[:-1]
    curr_j = arr[1:, target_j]
    t_steps = lag.shape[0]

    i_idx = np.broadcast_to(np.arange(n), (t_steps, n))
    j_idx = np.full((t_steps, n), target_j)
    k_idx = np.broadcast_to(curr_j[:, None], (t_steps, n))
    v = transition[i_idx, j_idx, lag, k_idx]

    def neg_log_lik(lam: np.ndarray) -> float:
        scores = np.clip(v @ lam, _LOG_FLOOR, None)
        return -np.sum(np.log(scores))

    x0 = np.full(n, 1.0 / n)
    bounds = [(0.0, 1.0)] * n
    constraints = [{"type": "eq", "fun": lambda lam: lam.sum() - 1.0}]
    result = minimize(neg_log_lik, x0, method="SLSQP", bounds=bounds, constraints=constraints)
    if not result.success:
        warnings.warn(
            f"SLSQP did not converge for target series {target_j} ({states.columns[target_j]!r}): "
            f"{result.message}",
            stacklevel=2,
        )

    lam = np.clip(result.x, 0.0, None)
    total = lam.sum()
    return lam / total if total > 0 else x0


def fit_mtd(states: pd.DataFrame, z: int = 3) -> MTDResult:
    tickers = list(states.columns)
    n = len(tickers)
    transition = estimate_transition_tensor(states, z=z)

    lambdas = np.zeros((n, n))
    for j in range(n):
        lambdas[:, j] = estimate_lambda_column(states, transition, j)

    lambdas_df = pd.DataFrame(lambdas, index=tickers, columns=tickers)
    return MTDResult(lambdas=lambdas_df, transition=transition, tickers=tickers, z=z)
