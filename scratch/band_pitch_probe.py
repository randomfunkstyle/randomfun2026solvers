"""Is the CPU lane band's pitch of 2 slack, or is it the trie's own floor?

Read-only. Three questions, in order:

1. **Census.** How many ``x`` nodes does the built trie have, and how many gap
   rows are there? (`gap_probe.py` prints the rows; this counts them.)
2. **The floor.** An ``x`` fans out perpendicular on both sides, so for every
   internal node ``row(up child) < row(v) < row(down child)``. That is the BST /
   in-order condition, and it forces **every node of the tree onto its own row** —
   any two distinct nodes lie in opposite halves of their common ancestor, so
   their rows are strictly ordered. A 22-leaf binary tree in which every internal
   node branches has 21 internal nodes, so the band's floor is 22 + 21 = 43 rows.
3. **The demonstration.** Re-run :func:`machine._uneven_trie` on pitch-1 slot rows
   and count the cells it overwrites. At pitch 1 there is no row left between two
   adjacent leaves, so ``xrow = slot_rows[min(down)] - 1`` lands *on* the up
   child, and the ``>`` that turns the man east overwrites the ``x`` that sent
   him there.
"""

import inspect
import textwrap

from randomfun2026solvers.lm1 import machine

_CLASHES: list[tuple[tuple[int, int], str, str]] = []


class _Watched(dict):
    """A dict that records every write landing on an occupied, differing cell."""

    def __setitem__(self, key, value):
        if key in self and self[key] != value:
            _CLASHES.append((key, self[key], value))
        super().__setitem__(key, value)


def _watched_uneven_trie(k, slot_rows, lane_x0):
    """Run the real `_uneven_trie` body with its `cells` map made observable.

    `_uneven_trie` builds `cells` from a `{}` literal, so patching the `dict`
    global does not reach it — the source is recompiled with the one literal
    swapped instead, which keeps the geometry byte-for-byte the shipped code's.
    """
    src = textwrap.dedent(inspect.getsource(machine._uneven_trie))
    marker = 'cells: dict[tuple[int, int], str] = {}'
    assert src.count(marker) == 1, "the `cells` literal moved; re-point the probe"
    src = src.replace(marker, "cells: dict[tuple[int, int], str] = _Watched()")
    ns = dict(machine.__dict__)
    ns["_Watched"] = _Watched
    exec(compile(src, "<uneven_trie:watched>", "exec"), ns)
    _CLASHES.clear()
    entry, cells = ns["_uneven_trie"](k, slot_rows, lane_x0)
    return entry, cells, list(_CLASHES)


def census() -> dict[str, int]:
    rows = machine.build_for("deadman-3d", store="taped").rows
    band = range(100, 143)
    xs = sum(row.count("x") for y, row in enumerate(rows) if y in band)
    lanes = sum(1 for y in band if y % 2 == 0)
    gaps = sum(1 for y in band if y % 2 == 1)
    per_gap = {y: rows[y].count("x") for y in band if y % 2 == 1}
    return dict(
        band_rows=len(band),
        lane_rows=lanes,
        gap_rows=gaps,
        x_nodes=xs,
        gaps_with_exactly_one_x=sum(1 for n in per_gap.values() if n == 1),
    )


def trie_at_pitch(pitch: int, n: int = 22, k: int = 5) -> dict[str, object]:
    """Draw the same trie with the lane rows at ``pitch`` and count collisions.

    ``_uneven_trie`` writes into a plain dict, so an overwrite is silent. Wrap it
    and record every write that lands on an occupied cell.
    """
    slots = sorted(machine.OPCODE_SLOTS[("deadman-3d", "taped")].values())
    assert len(slots) == n, (len(slots), n)
    rank = {s: i for i, s in enumerate(slots)}
    slot_rows = {s: 1 + pitch * rank[s] for s in slots}
    lane_x0 = 4 + 2 * k

    entry, cells, clashes = _watched_uneven_trie(k, slot_rows, lane_x0)

    lane_rows = set(slot_rows.values())
    x_on_lane = sorted(xy for xy, c in cells.items() if c == "x" and xy[1] in lane_rows)
    return dict(
        pitch=pitch,
        band_rows=max(slot_rows.values()) - min(slot_rows.values()) + 1,
        entry_row=entry,
        x_nodes=sum(1 for c in cells.values() if c == "x"),
        overwrites=len(clashes),
        first_overwrites=clashes[:6],
        x_cells_landing_on_a_lane_row=len(x_on_lane),
    )


if __name__ == "__main__":
    print("built grid census:")
    for k, v in census().items():
        print(f"  {k:28s} {v}")
    print()
    for pitch in (2, 1):
        print(f"trie redrawn at pitch {pitch}:")
        for k, v in trie_at_pitch(pitch).items():
            print(f"  {k:28s} {v}")
        print()
