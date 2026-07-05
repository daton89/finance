"""Persistent snapshots of portfolio rebalances."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pandas as pd

HISTORY_FILENAME = "history.csv"
WEIGHTS_FILENAME = "weights.csv"
METADATA_FILENAME = "metadata.json"
PRICES_FILENAME = "prices.parquet"


@dataclass
class RebalanceSnapshot:
    as_of: date
    weights: pd.Series
    prices: pd.DataFrame
    config: dict[str, Any] = field(default_factory=dict)
    diagnostics: dict[str, Any] = field(default_factory=dict)
    capital_eur: float | None = None
    ticker_names: dict[str, str] = field(default_factory=dict)
    ticker_buckets: dict[str, str] = field(default_factory=dict)


def _snapshot_dir(root: Path, as_of: date) -> Path:
    return root / as_of.isoformat()


def save_rebalance_snapshot(snapshot: RebalanceSnapshot, root: Path | str) -> Path:
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    out_dir = _snapshot_dir(root, snapshot.as_of)
    out_dir.mkdir(parents=True, exist_ok=True)

    weights_df = pd.DataFrame(
        {
            "ticker": snapshot.weights.index,
            "weight": snapshot.weights.values,
            "eur_value": (
                snapshot.weights.values * snapshot.capital_eur
                if snapshot.capital_eur is not None
                else [None] * len(snapshot.weights)
            ),
            "name": [snapshot.ticker_names.get(t, "") for t in snapshot.weights.index],
            "bucket": [snapshot.ticker_buckets.get(t, "") for t in snapshot.weights.index],
        }
    )
    weights_df.to_csv(out_dir / WEIGHTS_FILENAME, index=False)

    metadata = {
        "as_of": snapshot.as_of.isoformat(),
        "saved_at": datetime.now().isoformat(timespec="seconds"),
        "capital_eur": snapshot.capital_eur,
        "n_assets_held": int((snapshot.weights > 1e-4).sum()),
        "n_assets_universe": int(len(snapshot.weights)),
        "config": snapshot.config,
        "diagnostics": snapshot.diagnostics,
    }
    (out_dir / METADATA_FILENAME).write_text(json.dumps(metadata, indent=2, default=str))

    try:
        snapshot.prices.to_parquet(out_dir / PRICES_FILENAME)
    except (ImportError, ValueError):
        snapshot.prices.to_csv(out_dir / PRICES_FILENAME.replace(".parquet", ".csv"))

    _append_to_history(root, snapshot)
    return out_dir


def _append_to_history(root: Path, snapshot: RebalanceSnapshot) -> None:
    history_path = root / HISTORY_FILENAME
    new_rows = pd.DataFrame(
        {
            "date": [snapshot.as_of.isoformat()] * len(snapshot.weights),
            "ticker": snapshot.weights.index,
            "weight": snapshot.weights.values,
            "eur_value": (
                snapshot.weights.values * snapshot.capital_eur
                if snapshot.capital_eur is not None
                else [None] * len(snapshot.weights)
            ),
            "bucket": [snapshot.ticker_buckets.get(t, "") for t in snapshot.weights.index],
        }
    )

    if history_path.exists():
        existing = pd.read_csv(history_path)
        existing = existing.loc[existing["date"] != snapshot.as_of.isoformat()]
        combined = pd.concat([existing, new_rows], ignore_index=True)
    else:
        combined = new_rows

    combined.sort_values(["date", "ticker"]).to_csv(history_path, index=False)


def load_history(root: Path | str) -> pd.DataFrame:
    history_path = Path(root) / HISTORY_FILENAME
    if not history_path.exists():
        return pd.DataFrame(columns=["date", "ticker", "weight", "eur_value", "bucket"])
    return pd.read_csv(history_path, parse_dates=["date"])


def load_snapshot(root: Path | str, as_of: date) -> RebalanceSnapshot:
    root = Path(root)
    snap_dir = _snapshot_dir(root, as_of)
    if not snap_dir.exists():
        raise FileNotFoundError(f"No snapshot for {as_of.isoformat()} under {root}")

    weights_df = pd.read_csv(snap_dir / WEIGHTS_FILENAME)
    weights = pd.Series(
        weights_df["weight"].values, index=weights_df["ticker"].values, name="weight"
    )
    metadata = json.loads((snap_dir / METADATA_FILENAME).read_text())

    prices_pq = snap_dir / PRICES_FILENAME
    prices_csv = snap_dir / PRICES_FILENAME.replace(".parquet", ".csv")
    if prices_pq.exists():
        prices = pd.read_parquet(prices_pq)
    elif prices_csv.exists():
        prices = pd.read_csv(prices_csv, index_col=0, parse_dates=True)
    else:
        prices = pd.DataFrame()

    ticker_names = dict(zip(weights_df["ticker"], weights_df.get("name", "").fillna("")))
    ticker_buckets = dict(zip(weights_df["ticker"], weights_df.get("bucket", "").fillna("")))

    return RebalanceSnapshot(
        as_of=as_of,
        weights=weights,
        prices=prices,
        config=metadata.get("config", {}),
        diagnostics=metadata.get("diagnostics", {}),
        capital_eur=metadata.get("capital_eur"),
        ticker_names=ticker_names,
        ticker_buckets=ticker_buckets,
    )
