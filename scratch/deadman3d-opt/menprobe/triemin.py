"""Can ``lane_x0`` go below 12?  Build-free search over LEAN_TRIE shapes.

``_trie_shape`` and ``_trie_columns`` are pure functions, so the band's origin
``max(cols)+1`` can be searched without building anything. ``build_cpu``'s greedy
pass only ever *preserves* ``lane_x0`` (``if _shape(trial)[0] <= lane_x0``); it
never tries to reduce it. This asks whether any lean subset can.

Captures the real arguments by monkeypatching ``_trie_shape`` during one build.

usage: triemin.py [store]
"""
import itertools
import random
import sys

from common import setup, SLUG


def main():
    d3, hires, M, prog = setup()
    store = sys.argv[1] if len(sys.argv) > 1 else "men-v3"

    calls = []
    real_shape = M._trie_shape

    def spy(k, slot_rows, straight=False, inline_far=False, lean=False):
        calls.append((k, dict(slot_rows), straight, inline_far, lean))
        return real_shape(k, slot_rows, straight, inline_far, lean)

    M._trie_shape = spy
    try:
        m = M.build_for(SLUG, program=prog, store=store)
    finally:
        M._trie_shape = real_shape

    print(f"===== {store}: {m.width}x{m.height} =====")
    print(f"captured {len(calls)} _trie_shape calls")
    # the CPU's own trie: the one with the most slots
    k, slot_rows, straight, inline_far, _lean = max(calls, key=lambda c: len(c[1]))
    print(f"k={k} straight={straight} inline_far={inline_far} slots={len(slot_rows)}")
    print(f"slot_rows={slot_rows}")

    def shape(mode):
        e, el, tree, root = real_shape(k, slot_rows, straight, inline_far, mode)
        cols = M._trie_columns(tree, root, el, True)
        return (max(cols.values(), default=4) + 1), tree, root, el, cols

    n_nodes = len(shape(True)[1])
    print(f"\nn_nodes={n_nodes}")

    base = {}
    for name, mode in (("no lean (False)", False), ("full lean (True)", True),
                       ("'safe'", "safe"), ("'greedy'", "greedy")):
        lx = shape(mode)[0]
        base[name] = lx
        print(f"  lane_x0({name:18s}) = {lx}")

    # replicate build_cpu's greedy pass exactly
    lx0 = shape(True)[0]
    picked = frozenset()
    for i in range(n_nodes):
        trial = picked | {i}
        if shape(trial)[0] <= lx0:
            picked = trial
    print(f"  lane_x0(build_cpu greedy pass) = {shape(picked)[0]}  picked={sorted(picked)}")

    # --- exhaustive over small subsets, then randomised over all ---
    best = min((shape(frozenset(s))[0], tuple(sorted(s)))
               for r in (0, 1, 2)
               for s in itertools.combinations(range(n_nodes), r))
    print(f"\n  best over all subsets of size <= 2: lane_x0={best[0]} at {best[1]}")

    rng = random.Random(20260731)
    bestr = (10**9, None)
    for _ in range(200_000):
        s = frozenset(i for i in range(n_nodes) if rng.random() < rng.choice((.2, .5, .8)))
        v = shape(s)[0]
        if v < bestr[0]:
            bestr = (v, tuple(sorted(s)))
    print(f"  best over 200k random lean subsets: lane_x0={bestr[0]} at {bestr[1]}")

    # --- where do the extra columns come from?  Walk the deepest path. ---
    lx, tree, root, el, cols = shape(picked)
    deepest = max(cols, key=lambda i: cols[i])
    print(f"\n  shipped shape: lane_x0={lx}, root col={cols[root]}, deepest node "
          f"{deepest} at col {cols[deepest]}")
    parent = {}
    for i, nd in enumerate(tree):
        for sign, crow, clevel, ci in nd["kids"]:
            if ci is not None:
                parent[ci] = (i, sign, crow, clevel)
    path = []
    cur = deepest
    while cur in parent:
        path.append(cur)
        cur = parent[cur][0]
    path.append(root)
    path.reverse()
    print(f"  {'node':>5s} {'lvl':>4s} {'row':>5s} {'col':>4s} {'inline':>7s} "
          f"{'slack':>6s} {'shifts':>7s} {'pays':>5s}")
    for i in path:
        nd = tree[i]
        if i == root:
            print(f"  {i:5d} {nd['level']:4d} {nd['row']:5d} {cols[i]:4d} "
                  f"{str(nd['inline']):>7s} {'-':>6s} {'-':>7s} {'-':>5s}")
            continue
        p, sign, crow, clevel = parent[i]
        pn = tree[p]
        slack = 0 if (pn["inline"] and sign < 0) else max(0, abs(crow - pn["row"]) - 1)
        shifts = clevel - pn["level"]
        pays = 1 + max(0, shifts - slack)
        print(f"  {i:5d} {nd['level']:4d} {nd['row']:5d} {cols[i]:4d} "
              f"{str(nd['inline']):>7s} {slack:6d} {shifts:7d} {pays:5d}")


if __name__ == "__main__":
    main()
