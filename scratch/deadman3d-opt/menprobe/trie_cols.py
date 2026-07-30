"""What the decode trie's horizontal cost *has* to be.

``_uneven_trie`` gives every level **two** columns (an ``x`` at ``3 + 2L``) so that
an edge contracting ``d`` levels has ``2d - 1 >= d`` cells for its ``]``s. That
sets ``lane_x0 = 4 + 2k`` and therefore the whole band's origin: the trie's own
horizontal traverse is ``lane_x0 - 5`` for **every** instruction, and every drop
column east of it inherits the same offset, paid again on the collector walk west.

Two columns per level is a sufficient condition, not a necessary one. This model
enumerates the real tree and prices three column rules:

* ``two``      — the shipped ``3 + 2L``;
* ``one``      — one column per level, which is short by exactly one cell an edge;
* ``adaptive`` — per-**node** columns: a child sits at ``parent + 1`` unless its
  edge owes more shifts than the run can hold, and a shift that does not fit
  horizontally is placed on the edge's **vertical** leg, which is ``.`` today.

The vertical leg is legal for a ``]``: the man keeps his heading over it, and a
node's column is strictly west of every lane in its subtree (the argument
``_uneven_gaps`` already makes), so no lane man ever executes it.
"""
from __future__ import annotations

import sys

from common import setup  # noqa: E402


def slot_rows_of(p, *, straight=True, pitch=1, squash=7, y0=1):
    """``build_cpu``'s row assignment, replicated."""
    from randomfun2026solvers.lm1 import machine as M

    used = list(p.number)
    slots = sorted((p.row[m] - 1) // 2 for m in used)
    rank = {s: i for i, s in enumerate(slots)}
    n_rows = len(slots)
    if pitch == 1:
        gaps = M._uneven_gaps(p.k, slots, straight)
        at = [y0]
        for i in range(n_rows - 1):
            at.append(at[-1] + (2 if i in gaps else 1))
        slack = (2 * n_rows - 1) - (at[-1] - y0 + 1)
        take = min(int(squash), slack) if squash else 0
        if take < slack:
            at = [r + (slack - take) for r in at]
    else:
        at = [y0 + 2 * i for i in range(n_rows)]
    row_of = {m: at[rank[(p.row[m] - 1) // 2]] for m in used}
    return {(p.row[m] - 1) // 2: row_of[m] for m in used}, row_of, at


def edges(k, slot_rows, straight=True, inline_far=False):
    """Every contracted edge: (parent level, parent row, child level|None, child row,
    inline?, which side)."""
    used = sorted(slot_rows)
    out = []
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
            return slot_rows[sl[0]], None
        xrow = slot_rows[min(down)] - 1
        inline = (straight and len(up) == 1 and up[0] == lo
                  and (inline_far or slot_rows[up[0]] == xrow))
        if inline:
            xrow = slot_rows[up[0]]
        me = len(nodes)
        nodes.append({"level": level, "row": xrow, "inline": inline, "kids": []})
        for half, sign in (((lo, mid), -1), ((mid, hi), +1)):
            crow, clevel = node(level + 1, *half)
            skip = inline and sign < 0
            nodes[me]["kids"].append((sign, crow, clevel, skip))
            out.append((me, level, xrow, clevel, crow, skip, sign))
        return xrow, level

    entry, elevel = node(1, 0, 1 << k)
    return entry, elevel, nodes, out


def price(nodes, elevel, rule):
    """Columns per node under ``rule``; returns (cols, lane_x0) or None if infeasible."""
    # index of root is 0 (first appended)
    cols = {}

    def place(i, col):
        cols[i] = col
        n = nodes[i]
        for sign, crow, clevel, skip in n["kids"]:
            if clevel is None:
                continue
            j = next(
                x for x in range(len(nodes))
                if nodes[x]["level"] == clevel and nodes[x]["row"] == crow and x not in cols
            )
            d = clevel - n["level"]
            if skip:
                vspare = 0  # the inline straight-through: no vertical leg at all
            else:
                vspare = max(0, abs(crow - n["row"]) - 1)
            if rule == "two":
                child = col + 2 * d
            elif rule == "one":
                child = col + d
            else:
                need = max(0, d - vspare)
                child = col + 1 + need
            place(j, child)

    root_col = {"two": 3 + 2 * elevel, "one": 4 + elevel, "adaptive": 4 + elevel}[rule]
    place(0, root_col)
    # feasibility: every edge must hold its shifts
    for i, n in enumerate(nodes):
        for sign, crow, clevel, skip in n["kids"]:
            if clevel is None:
                continue
            j = next(x for x in cols if nodes[x]["level"] == clevel and nodes[x]["row"] == crow)
            d = clevel - n["level"]
            vspare = 0 if skip else max(0, abs(crow - n["row"]) - 1)
            cap = (cols[j] - cols[i] - 1) + vspare
            if cap < d:
                return None, None, (i, j, d, cap)
    return cols, max(cols.values()) + 1, None


def capture():
    """Run the real build far enough to see ``_uneven_trie``'s own arguments."""
    d3, hires, M, prog = setup()
    from randomfun2026solvers.lm1 import machine as MM

    seen = {}
    orig = MM._uneven_trie

    def spy(k, slot_rows, lane_x0, straight=False, inline_far=False):
        if not seen:
            seen.update(k=k, slot_rows=dict(slot_rows), lane_x0=lane_x0,
                        straight=straight, inline_far=inline_far)
        return orig(k, slot_rows, lane_x0, straight, inline_far)

    MM._uneven_trie = spy
    try:
        MM.build_for("deadman-3d_hires", program=prog, store="men-v3")
    finally:
        MM._uneven_trie = orig
    return seen


def main():
    seen = capture()
    k, sr, straight = seen["k"], seen["slot_rows"], seen["straight"]
    print(f"k={k} lane_x0={seen['lane_x0']} straight={straight} lanes={len(sr)}")
    print("band rows:", min(sr.values()), "..", max(sr.values()))
    entry, elevel, nodes, es = edges(k, sr, straight, seen.get("inline_far", False))
    print(f"root row {entry} level {elevel}; {len(nodes)} internal nodes")
    for rule in ("two", "one", "adaptive"):
        cols, lx0, bad = price(nodes, elevel, rule)
        if bad:
            print(f"  {rule:9s}: INFEASIBLE at edge {bad}")
        else:
            print(f"  {rule:9s}: lane_x0 = {lx0}  (max node col {lx0-1})")
    cols, lx0, _ = price(nodes, elevel, "adaptive")
    print("\n  node level/row/col, and each edge's shift budget (adaptive):")
    for i, n in enumerate(sorted(range(len(nodes)), key=lambda i: nodes[i]["row"])):
        nd = nodes[n]
        print(f"   node L{nd['level']} row {nd['row']:3d} col {cols[n]:2d} "
              f"{'inline' if nd['inline'] else '      '}", end="")
        for sign, crow, clevel, skip in nd["kids"]:
            if clevel is None:
                print(f" | {'up' if sign<0 else 'dn'}->lane r{crow}", end="")
            else:
                j = next(x for x in cols if nodes[x]["level"] == clevel and nodes[x]["row"] == crow)
                d = clevel - nd["level"]
                v = 0 if skip else max(0, abs(crow - nd["row"]) - 1)
                print(f" | {'up' if sign<0 else 'dn'}->L{clevel} r{crow} col{cols[j]} "
                      f"d={d} h={cols[j]-cols[n]-1} v={v}", end="")
        print()


if __name__ == "__main__":
    main()
