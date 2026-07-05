"""Max Quadratic Utility portfolio optimization, classic and assortativity-augmented."""

from __future__ import annotations

import pandas as pd
from pypfopt import EfficientFrontier

from finance_optimizer.assortativity import MINIMIZE_MODALITIES, Modality


def _resolve_bounds(
    weight_bounds: tuple[float, float],
    max_position: float | None,
) -> tuple[float, float]:
    if max_position is None:
        return weight_bounds
    return (weight_bounds[0], float(max_position))


def classic_max_quadratic_utility(
    mu: pd.Series,
    cov: pd.DataFrame,
    risk_aversion: float = 10.0,
    weight_bounds: tuple[float, float] = (0.0, 1.0),
    max_position: float | None = None,
) -> pd.Series:
    bounds = _resolve_bounds(weight_bounds, max_position)
    ef = EfficientFrontier(mu, cov, weight_bounds=bounds)
    ef.max_quadratic_utility(risk_aversion=risk_aversion)
    return pd.Series(ef.clean_weights())


def assortative_max_quadratic_utility(
    mu: pd.Series,
    cov: pd.DataFrame,
    rho: pd.Series,
    modality: Modality,
    risk_aversion: float = 10.0,
    weight_bounds: tuple[float, float] = (0.0, 1.0),
    max_position: float | None = None,
) -> pd.Series:
    bounds = _resolve_bounds(weight_bounds, max_position)
    ef = EfficientFrontier(mu, cov, weight_bounds=bounds)
    rho_vector = rho.reindex(mu.index).fillna(0.0).to_numpy()
    sign = 1.0 if modality in MINIMIZE_MODALITIES else -1.0
    ef.add_objective(lambda w: sign * (rho_vector @ w))
    ef.max_quadratic_utility(risk_aversion=risk_aversion)
    return pd.Series(ef.clean_weights())
