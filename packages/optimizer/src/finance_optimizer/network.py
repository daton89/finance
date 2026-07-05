"""Directed weighted financial network construction."""

from __future__ import annotations

import networkx as nx
import pandas as pd


def build_mtd_network(lmbda: pd.DataFrame) -> nx.DiGraph:
    """Build the directed weighted MTD network from a lambda (influence) matrix.

    lmbda.loc[i, j] = lambda_ij ("i influences j"); columns sum to 1.
    Self-loops are excluded (eq. 6-7 of the paper).
    """
    nodes = list(lmbda.index)
    graph = nx.DiGraph()
    graph.add_nodes_from(nodes)
    for i in nodes:
        for j in nodes:
            if i == j:
                continue
            weight = lmbda.loc[i, j]
            if weight > 0:
                graph.add_edge(i, j, weight=float(weight))
    return graph


def build_correlation_network(returns_window: pd.DataFrame) -> nx.DiGraph:
    """Build the undirected correlation-network benchmark.

    Edge weight w_ij = w_ji = |pearson_corr(i, j)| for all i != j, dense/complete
    (no thresholding). Represented as a DiGraph with reciprocal equal-weight edges
    so it plugs into assortativity.py unmodified.

    Because w_ij == w_ji everywhere, es_out == es_in for every node here, so
    'In-In' ~= 'Out-Out' and 'In-Out' ~= 'Out-In' numerically -- this is expected
    degeneracy from symmetry, not a bug.
    """
    corr = returns_window.corr().abs()
    nodes = list(corr.index)
    graph = nx.DiGraph()
    graph.add_nodes_from(nodes)
    for i in nodes:
        for j in nodes:
            if i == j:
                continue
            weight = corr.loc[i, j]
            if weight > 0:
                graph.add_edge(i, j, weight=float(weight))
    return graph
