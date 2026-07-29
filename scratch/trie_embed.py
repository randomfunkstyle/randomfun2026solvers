"""How few rows can the CPU's lane band be, given that `x` always turns?

An `x` node must sit **strictly between** its two children's entry rows: turning
clockwise takes the man south and counter-clockwise north, and there is no third
outcome that leaves him on his own row. So the band is an embedding problem:

* the 22 leaves keep their order and each owns a row (a lane's micro-program);
* every node needs a row strictly between its two children's rows;
* a node **may share** a leaf's row when its column is west of that leaf's entry
  column — a node at level ``L`` sits at ``3 + 2L`` and a leaf entered from a
  node at level ``L' > L`` starts at ``3 + 2L'``, so the leaf's man begins east
  of the node and never walks onto it.

The tight case is a **cherry** — a node whose two children are both leaves. Its
row must fall strictly between two *adjacent* leaves, so no leaf row can serve
and it costs a row of its own. Everything else can, in principle, share.

This mirrors :func:`randomfun2026solvers.lm1.machine._uneven_trie`'s recursion so
the tree it measures is the one the generator actually builds.
"""

from randomfun2026solvers.lm1 import machine as M


def tree(k, slots):
    """Rebuild `_uneven_trie`'s shape: (kind, level, leaf-range) per node."""
    used = sorted(slots)
    nodes = []

    def node(level, lo, hi):
        sl = [s for s in used if lo <= s < hi]
        mid, up, down = lo, [], []
        while len(sl) > 1:
            mid = (lo + hi) // 2
            up = [s for s in sl if s < mid]
            down = [s for s in sl if s >= mid]
            if up and down:
                break
            lo, hi = (lo, mid) if up else (mid, hi)
            level += 1
        if len(sl) == 1:
            return ("leaf", sl[0], level)
        a = node(level + 1, lo, mid)
        b = node(level + 1, mid, hi)
        nodes.append((level, a, b))
        return ("node", len(nodes) - 1, level)

    root = node(1, 0, 1 << k)
    return nodes, root


def census(slug="deadman-3d", store="taped"):
    prog = M._tier_program(slug, store)
    p = M.plan(prog, middle_order=M.LANE_ORDER.get(slug),
               slots=M.OPCODE_SLOTS.get((slug, store)))
    slots = sorted((p.row[m] - 1) // 2 for m in p.number)
    nodes, _ = tree(p.k, slots)
    cherries = [n for n in nodes if n[1][0] == "leaf" and n[2][0] == "leaf"]
    one_leaf = [n for n in nodes if (n[1][0] == "leaf") ^ (n[2][0] == "leaf")]
    by_level = {}
    for lvl, _, _ in nodes:
        by_level[lvl] = by_level.get(lvl, 0) + 1
    return dict(leaves=len(slots), nodes=len(nodes), cherries=len(cherries),
                one_leaf=len(one_leaf), by_level=dict(sorted(by_level.items())))


if __name__ == "__main__":
    c = census()
    print(c)
    lo = c["leaves"] + c["cherries"]
    print(f"band today = {c['leaves'] + c['nodes']} rows; "
          f"floor with perfect node/leaf sharing = {lo} rows")
