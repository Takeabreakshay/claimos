"""Collusion-ring detection — networkx (CLAUDE.md §7, LOGIC §3.5).

Builds a claim graph linking claims that share a high-risk entity, then derives
per-claim graph features and flags dense components as rings.

  Nodes = claims. Edge(i, j) if claims i, j share >= 1 of the config link
  entities {garage_id, surveyor_id, bank_account, phone}.
  component_size(i)      = size of i's connected component
  shared_<x>_count(i)    = # OTHER claims sharing i's <x>
  ring_risk(i)           = 1 - exp(-lambda * (component_size(i) - 1))

Detection: components with size >= ring_min_component_size are flagged rings.
Gate: recall >= 0.80 against the seeded rings (data_gen §1.7).
"""

from __future__ import annotations

import networkx as nx
import numpy as np
import pandas as pd

from src import constants


def build_graph_features(df: pd.DataFrame) -> pd.DataFrame:
    """Return per-claim graph features aligned to ``df.index``."""
    cfg = constants.load_distributions()["graph"]
    link_entities = cfg["link_entities"]
    lam = float(cfg["ring_risk_lambda"])

    g = nx.Graph()
    g.add_nodes_from(range(len(df)))

    # For each entity column, connect the members of each shared group as a star
    # to the group's first member (enough for connected-component structure and
    # far cheaper than a full clique).
    shared_counts: dict[str, np.ndarray] = {}
    for col in link_entities:
        codes = pd.factorize(df[col].to_numpy())[0]
        order = np.argsort(codes, kind="stable")
        sorted_codes = codes[order]
        # group boundaries
        boundaries = np.flatnonzero(np.diff(sorted_codes)) + 1
        groups = np.split(order, boundaries)
        counts = np.zeros(len(df), dtype=np.int64)
        for grp in groups:
            if grp.size > 1:
                root = int(grp[0])
                for member in grp[1:]:
                    g.add_edge(root, int(member))
                counts[grp] = grp.size - 1
        shared_counts[col] = counts

    component_size = np.ones(len(df), dtype=np.int64)
    for comp in nx.connected_components(g):
        size = len(comp)
        if size > 1:
            idx = np.fromiter(comp, dtype=np.int64)
            component_size[idx] = size

    ring_risk = 1.0 - np.exp(-lam * (component_size - 1))

    out = pd.DataFrame(index=df.index)
    out["component_size"] = component_size
    out["shared_garage_count"] = shared_counts.get("garage_id", np.zeros(len(df), dtype=np.int64))
    out["shared_surveyor_count"] = shared_counts.get(
        "surveyor_id", np.zeros(len(df), dtype=np.int64)
    )
    out["shared_bank_count"] = shared_counts.get("bank_account", np.zeros(len(df), dtype=np.int64))
    out["ring_risk"] = np.round(ring_risk, 4)
    return out


def detect_rings(df: pd.DataFrame, graph_df: pd.DataFrame | None = None) -> dict:
    """Flag dense components as rings and score recall vs seeded rings.

    Returns {flagged_mask, n_flagged_claims, ring_recall, n_seeded_rings}.
    """
    cfg = constants.load_distributions()["graph"]
    min_size = int(cfg["ring_min_component_size"])
    if graph_df is None:
        graph_df = build_graph_features(df)

    flagged = (graph_df["component_size"] >= min_size).to_numpy()

    # Ring-level recall: a seeded ring is "recovered" if a majority of its members
    # land in a flagged component.
    recall = np.nan
    n_seeded = 0
    if "ring_id" in df.columns:
        seeded = df["ring_id"].to_numpy()
        ring_ids = [r for r in np.unique(seeded) if r >= 0]
        n_seeded = len(ring_ids)
        if n_seeded:
            recovered = 0
            for r in ring_ids:
                members = seeded == r
                if flagged[members].mean() >= 0.5:
                    recovered += 1
            recall = recovered / n_seeded

    return {
        "flagged_mask": flagged,
        "n_flagged_claims": int(flagged.sum()),
        "ring_recall": float(recall) if recall == recall else None,
        "n_seeded_rings": int(n_seeded),
    }
