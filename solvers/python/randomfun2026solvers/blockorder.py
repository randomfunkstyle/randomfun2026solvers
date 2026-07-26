#!/usr/bin/env python3
"""Choose the order blocks are laid down in: minimum linear arrangement.

A CFG machine has no jump: an edge from block `u` to block `v` is a *corridor*,
and the man walks every cell of it, so the edge costs `|row(u) - row(v)|` ticks
every time it is taken.  The layout question is therefore not "which depth-first
walk", which is what `lllm_layout.block_order` answers, but the classic
**minimum linear arrangement**: choose a permutation minimising

    sum over CFG edges of  w(u,v) * |pos(u) - pos(v)|

Two things fall out of writing it down that way.

**Blocks may flow up as well as down.**  A depth-first order puts every loop's
back edge across the whole machine; a MinLA order puts roughly half of each
block's successors above it and half below, which is where most of the win is.

**The weights must be structural, not measured.**  An edge inside a loop is
taken once per iteration and an edge on the init or death path once per case, so
`w` is `LOOP_FACTOR ** (loop nesting depth of the edge)`, derived from the graph
by back-edge analysis.  Weighting by a *profile* of the public cases was tried on
snake and measured worse — 17.7k ticks against 15.8k — because it folds the cold
death paths into the hot loop and lengthens the corridors that carry it.  Loop
depth is a property of the program; a profile is a property of six inputs.
"""

from __future__ import annotations

import random

__all__ = ["anneal", "cost", "edges_of", "loop_depth", "structural_weights"]

#: How much more a corridor one loop deeper is worth shortening.  The exact
#: value hardly matters — what matters is that it is much bigger than the number
#: of blocks, so no amount of cold-path shortening can outbid a hot edge.
LOOP_FACTOR = 16


def edges_of(worker) -> list[tuple[str, str]]:
    out = []
    for name, (_toks, succ) in worker.items():
        for target in ([succ] if isinstance(succ, str) else succ.values()):
            out.append((name, target))
    return out


def loop_depth(worker, entry: str) -> dict[str, int]:
    """Loop nesting depth of every block, by iterated strongly-connected components.

    A set of blocks that can all reach each other is a loop; peel its header off
    and whatever is *still* mutually reachable is a nested loop.  That is a
    coarse reading of nesting — an irreducible graph has no headers — but it is
    exactly the distinction that matters here: run-once-per-case against
    run-once-per-round against run-once-per-cell.
    """
    import networkx as nx

    g = nx.DiGraph()
    g.add_nodes_from(worker)
    g.add_edges_from(edges_of(worker))
    order = list(nx.dfs_preorder_nodes(g, entry)) if entry in g else list(worker)
    rank = {n: i for i, n in enumerate(order)}
    depth = dict.fromkeys(worker, 0)

    def peel(nodes: set[str], d: int) -> None:
        sub = g.subgraph(nodes)
        for scc in nx.strongly_connected_components(sub):
            if len(scc) == 1:
                n = next(iter(scc))
                if not sub.has_edge(n, n):
                    continue
            for n in scc:
                depth[n] = d
            if len(scc) > 1:
                header = min(scc, key=lambda n: rank.get(n, len(rank)))
                peel(scc - {header}, d + 1)

    peel(set(worker), 1)
    return depth


def structural_weights(worker, entry: str) -> dict[tuple[str, str], int]:
    """`LOOP_FACTOR ** depth` per edge; the depth of an edge is its shallower end."""
    depth = loop_depth(worker, entry)
    return {(u, v): LOOP_FACTOR ** min(depth[u], depth[v])
            for u, v in edges_of(worker)}


def cost(order: list[str], rows: dict[str, int],
         weights: dict[tuple[str, str], int]) -> float:
    """Weighted MinLA cost of an order, with each block as tall as it really is."""
    top, y = {}, 0
    for name in order:
        top[name] = y
        y += rows[name]
    return sum(w * abs(top[v] - top[u]) for (u, v), w in weights.items())


def anneal(base: list[str], rows: dict[str, int],
           weights: dict[tuple[str, str], int], *, entry_first: bool = True,
           steps: int = 40_000, seeds=(1, 5, 11, 23, 42, 99)) -> list[str]:
    """Descend on `cost` by moving one block at a time, restarting per seed.

    Plateau moves are accepted so the descent does not stick on the many equal
    arrangements a symmetric CFG has; the search is restarted rather than
    lengthened because it flattens long before the step budget runs out.
    """
    lo = 1 if entry_first else 0
    best, best_c = list(base), cost(base, rows, weights)
    for seed in seeds:
        rng = random.Random(seed)
        cur, cur_c = list(base), best_c if base == best else cost(base, rows, weights)
        for _ in range(steps):
            i, j = rng.randrange(lo, len(cur)), rng.randrange(lo, len(cur))
            if i == j:
                continue
            cand = list(cur)
            cand.insert(j, cand.pop(i))
            c = cost(cand, rows, weights)
            if c <= cur_c:
                cur, cur_c = cand, c
                if c < best_c:
                    best, best_c = list(cand), c
    return best
