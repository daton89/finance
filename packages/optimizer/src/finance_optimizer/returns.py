"""Log returns and Markov-chain state discretization."""

from __future__ import annotations

import numpy as np
import pandas as pd

NEGATIVE, NULL, POSITIVE = 0, 1, 2


def log_returns(prices: pd.DataFrame) -> pd.DataFrame:
    """Daily log returns r_t = ln(P_t / P_{t-1}). Drops the first (NaN) row."""
    returns = np.log(prices).diff()
    return returns.iloc[1:]


def discretize_states(returns: pd.DataFrame) -> pd.DataFrame:
    """Map each asset's returns to 3 Markov-chain states using a per-asset threshold.

    sigma_i is the unconditional std of returns[i] over the full input frame, so the
    caller must already have sliced the estimation window before calling this --
    no look-ahead is performed here.

    state = NEGATIVE if r < -0.5*sigma_i
          = NULL     if -0.5*sigma_i <= r <= 0.5*sigma_i
          = POSITIVE if r > 0.5*sigma_i
    """
    sigma = returns.std(axis=0)
    lower = -0.5 * sigma
    upper = 0.5 * sigma
    states = pd.DataFrame(NULL, index=returns.index, columns=returns.columns, dtype=np.int8)
    states[returns.lt(lower, axis=1)] = NEGATIVE
    states[returns.gt(upper, axis=1)] = POSITIVE
    return states
