"""Local and global network assortativity measures.

Two measures are implemented, both operating on excess in-/out-strength as the
node "characteristic" (Section 2.4 of the paper):

- Extended Piraveenan et al. (2010): a node-level decomposition of the global
  (weighted Pearson) assortativity such that sum_i rho_i == rho_global.
- Sabek & Pigorsch (2023): an edge-level measure averaged over each node's
  out-neighbors (Appendix 1, eq. A1-A2). Does NOT sum to a global value.
"""

from __future__ import annotations

from typing import Literal

import networkx as nx
import numpy as np
import pandas as pd

Modality = Literal["In-In", "In-Out", "Out-In", "Out-Out"]

MODALITY_MODES: dict[str, tuple[str, str]] = {
    "In-In": ("in", "in"),
    "In-Out": ("in", "out"),
    "Out-In": ("out", "in"),
    "Out-Out": ("out", "out"),
}

MAXIMIZE_MODALITIES = {"In-In", "Out-In"}
MINIMIZE_MODALITIES = {"In-Out", "Out-Out"}


def _dense_weights(graph: nx.DiGraph) -> pd.DataFrame:
    nodes = list(graph.nodes())
    return nx.to_pandas_adjacency(graph, nodelist=nodes, weight="weight", nonedge=0.0)


def _edge_excess_strengths(weights: pd.DataFrame, m1: str, m2: str) -> pd.DataFrame:
    out_strength = weights.sum(axis=1)
    in_strength = weights.sum(axis=0)

    i_idx, j_idx = np.nonzero(weights.to_numpy() > 0)
    nodes = weights.index.to_numpy()
    src = nodes[i_idx]
    dst = nodes[j_idx]
    w_ij = weights.to_numpy()[i_idx, j_idx]
    w_ji = weights.to_numpy()[j_idx, i_idx]

    out_i = out_strength.to_numpy()[i_idx]
    out_j = out_strength.to_numpy()[j_idx]
    in_i = in_strength.to_numpy()[i_idx]
    in_j = in_strength.to_numpy()[j_idx]
    es_i = (out_i - w_ij) if m1 == "out" else (in_i - w_ji)
    es_j = (out_j - w_ji) if m2 == "out" else (in_j - w_ij)

    return pd.DataFrame({"i": src, "j": dst, "w": w_ij, "es_i": es_i, "es_j": es_j})


def _global_moments(edges: pd.DataFrame) -> tuple[float, float, float, float, float]:
    w_tot = edges["w"].sum()
    x_bar = (edges["w"] * edges["es_i"]).sum() / w_tot
    y_bar = (edges["w"] * edges["es_j"]).sum() / w_tot
    sigma_x = np.sqrt((edges["w"] * edges["es_i"] ** 2).sum() / w_tot - x_bar**2)
    sigma_y = np.sqrt((edges["w"] * edges["es_j"] ** 2).sum() / w_tot - y_bar**2)
    return w_tot, x_bar, y_bar, sigma_x, sigma_y


def global_assortativity(graph: nx.DiGraph, modality: Modality) -> float:
    m1, m2 = MODALITY_MODES[modality]
    edges = _edge_excess_strengths(_dense_weights(graph), m1, m2)
    if edges.empty:
        return 0.0
    w_tot, x_bar, y_bar, sigma_x, sigma_y = _global_moments(edges)
    if sigma_x == 0 or sigma_y == 0:
        return 0.0
    cov = (edges["w"] * edges["es_i"] * edges["es_j"]).sum() / w_tot - x_bar * y_bar
    return float(cov / (sigma_x * sigma_y))


def local_assortativity_piraveenan(graph: nx.DiGraph, modality: Modality) -> pd.Series:
    m1, m2 = MODALITY_MODES[modality]
    nodes = list(graph.nodes())
    edges = _edge_excess_strengths(_dense_weights(graph), m1, m2)
    result = pd.Series(0.0, index=nodes)
    if edges.empty:
        return result

    w_tot, _, y_bar, sigma_x, sigma_y = _global_moments(edges)
    if sigma_x == 0 or sigma_y == 0:
        return result

    denom = w_tot * sigma_x * sigma_y
    contrib = edges["w"] * edges["es_i"] * (edges["es_j"] - y_bar) / denom
    grouped = contrib.groupby(edges["i"]).sum()
    result.update(grouped)
    return result


def local_assortativity_sabek_pigorsch(graph: nx.DiGraph, modality: Modality) -> pd.Series:
    m1, m2 = MODALITY_MODES[modality]
    nodes = list(graph.nodes())
    edges = _edge_excess_strengths(_dense_weights(graph), m1, m2)
    result = pd.Series(0.0, index=nodes)
    if edges.empty:
        return result

    _, x_bar, y_bar, sigma_x, sigma_y = _global_moments(edges)
    if sigma_x == 0 or sigma_y == 0:
        return result

    edge_rho = edges["w"] * (edges["es_i"] - x_bar) * (edges["es_j"] - y_bar) / (sigma_x * sigma_y)
    grouped = edge_rho.groupby(edges["i"]).mean()
    result.update(grouped)
    return result
