#!/usr/bin/env python3
"""Sweep the CPU for *early-turn* reroutes that liveness proves transparent.

The one reroute family this machine actually has is the **early turn**.  Every
lane and every slab arm ends the same way: the man walks east along his own row
to a turn column, turns onto a vertical run, and rides it to the shared collector
row, where a ``<`` sends him west to the fetch.  Moving the turn column ``k``
cells west saves ``2k`` ticks per execution — ``k`` off the eastward walk and
``k`` off the westward one — and costs nothing, because the vertical distance is
unchanged.

There are **two** obligations, and the first sweep of this file only had one,
which is why it happily proposed skipping a lane's ``s``:

*crossing* — every cell the new vertical run walks over must be transparent:
  no steer (other than one pointing where he is already going), no branch, no
  split, no halt, no pipe traffic, and every register it writes dead there;

*abandoning* — every cell the old path walked and the new one does not must be
  transparent **too**.  A glyph whose execution changes nothing observable is
  exactly a glyph whose omission changes nothing observable, so it is the same
  predicate, evaluated against the liveness the *old* path had there.  This is
  the obligation that stops the turn at its own lane's last real operation, and
  it is what "return paths are exact" already knew empirically.

Plus two structural conditions from the same graph: the new turn cell must be a
nop (or already the steer we want), nothing else standing on it may be derailed
by the steer, and the run must land on a ``<`` that already carries a westbound
man, so the join is proven code rather than new geometry.

    uv run python scratch/deadman3d-opt/live/reroute.py [--all]
"""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import cpu_live as L  # noqa: E402

GRID = Path("/tmp/d3hires-taped/grid.man")
META = Path("/tmp/d3hires-taped/meta.json")

NOP = ". "
#: The vertical steer a turn column carries, by the direction it turns.
VERT = {"N": "^", "S": "v"}


@dataclass
class Turn:
    """An eastbound man steered onto a vertical run at ``(x, row)``."""

    x: int
    row: int
    dirn: str          # "N" or "S"
    join_row: int      # the collector row the vertical run lands on
    region: str


@dataclass
class Cand:
    turn: Turn
    col: int
    saved: int
    proved: bool
    why: str
    cross: list = field(default_factory=list)
    drop_row: list = field(default_factory=list)
    drop_col: list = field(default_factory=list)


def region_of(regions: dict, x: int, y: int) -> str:
    hits = [k for k, (bx, by, bw, bh) in regions.items()
            if k.startswith("cpu:") and bx <= x < bx + bw and by <= y < by + bh]
    hits.sort(key=lambda k: -len(k))
    return hits[0] if hits else "?"


#: Glyphs that end a vertical run's search for its landing: only ``<`` is a real
#: landing; the rest mean the run is not a simple ride to a collector.
_STOP = set("<>Xxda") | {"H", "Y", "U"}


def find_turns(w: L.WalkGraph, regions: dict) -> list[Turn]:
    """Every ``(x, row)`` where an eastbound man is steered onto a vertical run
    that ends on a ``<``.

    The run is followed through the *walk graph*, so neither a live glyph on the
    way (``b`` on a slab's taken arm) nor a **repeated steer** hides it — drop
    columns are shared, so a lane's ``v`` frequently sits directly above another
    lane's ``v``, and a ``v`` crossed by a southbound man is a no-op.
    """
    out = []
    for (x, y, h) in w.succ:
        if h != "E" or w.at(x, y) not in ("^", "v"):
            continue
        d = "N" if w.at(x, y) == "^" else "S"
        step = -1 if d == "N" else 1
        yy = y + step
        while (x, yy, d) in w.succ and w.at(x, yy) != "<":
            g = w.at(x, yy)
            if g in _STOP or (g in ("^", "v") and g != VERT[d]):
                yy = None
                break
            yy += step
        if yy is None or w.at(x, yy) != "<":
            continue
        out.append(Turn(x, y, d, yy, region_of(regions, x, y)))
    return out


def _can_land(w: L.WalkGraph, c: int, row: int) -> bool:
    """Could a vertical run land on ``(c, row)`` and be sent west from there?

    Either the ``<`` is already there, or the cell is a nop whose only traffic is
    westbound, in which case writing ``<`` is a no-op for everyone who already
    crosses it.
    """
    g = w.at(c, row)
    if g == "<":
        return True
    return g in NOP and w.headings_on(c, row) <= {"W"}


def candidates(w: L.WalkGraph, t: Turn) -> list[Cand]:
    step = -1 if t.dirn == "N" else 1
    out: list[Cand] = []
    # The cells the old path abandons, accumulated as the turn walks west.
    row_drop: list = []          # on the turn's own row, walked E
    col_drop: list = []          # on the collector row, walked W
    # The turn cell itself is abandoned first: it steered, and the new turn
    # replaces it.  It is not an obligation — it is the thing being moved.
    c = t.x - 1
    while (c, t.row, "E") in w.succ:
        # abandoning (c, t.row): it used to be crossed heading E …
        n = (c, t.row, "E")
        row_drop = [((c, t.row), w.at(c, t.row), "E",
                     L.transparent(w.at(c, t.row), w.live_out[n], "E"))] + row_drop
        # … and (c+1 .. t.x) on the collector row used to be crossed heading W.
        jn = (c + 1, t.join_row, "W")
        if jn in w.succ:
            col_drop = [((c + 1, t.join_row), w.at(c + 1, t.join_row), "W",
                         L.transparent(w.at(c + 1, t.join_row), w.live_out[jn], "W"))
                        ] + col_drop
        cell = w.at(c, t.row)
        cross: list = []
        if cell not in NOP and cell != VERT[t.dirn]:
            why = f"({c},{t.row}) holds {cell!r} — the new steer would destroy it"
        elif not (w.headings_on(c, t.row) <= {"E", t.dirn}):
            why = (f"({c},{t.row}) is also walked "
                   f"{sorted(w.headings_on(c, t.row) - {'E', t.dirn})} — "
                   f"the steer would derail it")
        elif not _can_land(w, c, t.join_row):
            # A `.` on the collector row can be *upgraded* to `<` for free when
            # the only traffic over it is already westbound — `<` is idempotent
            # for a westbound man.  So "there is no `<` here" is not a reason;
            # "something else crosses this cell" is.
            why = (f"({c},{t.join_row}) is {w.at(c, t.join_row)!r} and is walked "
                   f"{sorted(w.headings_on(c, t.join_row))} — cannot become a "
                   f"landing '<'")
        elif (c - 1, t.join_row, "W") not in w.succ:
            why = f"({c-1},{t.join_row}) carries no westbound man — nothing to join"
        elif bad := [r for r in row_drop if not r[3]]:
            why = f"abandoning ({bad[0][0][0]},{bad[0][0][1]}): {bad[0][3].reason}"
        elif bad := [r for r in col_drop if not r[3]]:
            why = f"abandoning ({bad[0][0][0]},{bad[0][0][1]}): {bad[0][3].reason}"
        else:
            cells = [(c, y) for y in range(t.row + step, t.join_row, step)]
            live_after = w.live_in[(c - 1, t.join_row, "W")]
            cross = L.walk_back(w, cells, live_after, t.dirn)
            blocked = [r for r in cross if not r[3]]
            if blocked:
                (bx, by), _bg, _lv, v = blocked[0]
                why = f"crossing ({bx},{by}): {v.reason}"
            else:
                why = (f"{len(cells)} crossed cell(s) transparent, "
                       f"{len(row_drop)}+{len(col_drop)} abandoned cell(s) inert")
                out.append(Cand(t, c, 2 * (t.x - c), True, why, cross,
                                list(row_drop), list(col_drop)))
                c -= 1
                continue
        out.append(Cand(t, c, 2 * (t.x - c), False, why, cross,
                        list(row_drop), list(col_drop)))
        c -= 1
    return out


def load_graph():
    grid = L.load(GRID)
    meta = json.loads(META.read_text())
    regions = {k: tuple(v) for k, v in meta["regions"].items()}
    cpu = [v for k, v in regions.items() if k.startswith("cpu:")]
    box = (min(v[0] for v in cpu) - 2, min(v[1] for v in cpu) - 2,
           max(v[0] + v[2] for v in cpu) + 4, max(v[1] + v[3] for v in cpu) + 4)
    return L.build(grid, L.find_spawn(grid, box)), regions


#: Baseline the percentages are quoted against: the shipped taped tier's 21-round
#: tour, 140,379,566 ticks over 880,332 instructions.
BASELINE_TICKS = 140_379_566
EXECS = Path("/tmp/d3hires-taped/arm_execs.json")

#: Which arm of which slab each turn cell belongs to.  A branch slab is an ``X``
#: fan-out on ``sign(ACC)`` and the three arms leave by three different columns,
#: so the opcode's total count is the *wrong* multiplier — see ``arm_execs.py``.
ARM_OF: dict[tuple[int, int], str] = {
    (24, 174): "BRZ.neg", (18, 176): "BRZ.pos",
    (32, 176): "BRN.zero", (29, 177): "BRN.pos",
}


def rank(best: list[Cand]) -> None:
    """Price each proved move: ticks saved per execution x executions."""
    if not EXECS.exists():
        print("\n(no /tmp/d3hires-taped/arm_execs.json — run arm_execs.py to rank)",
              flush=True)
        return
    counts = json.loads(EXECS.read_text())["counts"]
    print(f"\n── ranked by ticks saved x executions (21 rounds, "
          f"{BASELINE_TICKS:,} ticks) ──", flush=True)
    print(f"{'move':<34}{'arm':<11}{'t/exec':>7}{'execs':>10}{'ticks':>11}  share",
          flush=True)
    rows = []
    for b in best:
        t = b.turn
        arm = ARM_OF.get((t.x, t.row))
        n = counts.get(arm) if arm else counts.get(t.region.rsplit(":", 1)[-1])
        rows.append((b, arm or t.region, n or 0, b.saved * (n or 0)))
    total = 0
    for b, arm, n, ticks in sorted(rows, key=lambda r: -r[3]):
        total += ticks
        print(f"{f'({b.turn.x},{b.turn.row}) -> col {b.col}':<34}{arm:<11}"
              f"{b.saved:>7}{n:>10,}{ticks:>11,}  {ticks / BASELINE_TICKS:.4%}",
              flush=True)
    print(f"{'TOTAL':<62}{total:>11,}  {total / BASELINE_TICKS:.4%}", flush=True)


def main(argv: list[str]) -> int:
    w, regions = load_graph()
    print(f"walk graph {len(w.succ):,} nodes\n", flush=True)
    turns = sorted(find_turns(w, regions), key=lambda t: (t.row, t.x))
    print(f"{len(turns)} eastbound turns onto a collector-bound vertical run\n", flush=True)
    hdr = f"{'turn':<11} {'dir':<4}{'join':<6}{'best c':<8}{'saved':<7} region"
    print(hdr, flush=True)
    print("-" * 100, flush=True)
    best: list[Cand] = []
    for t in turns:
        cands = candidates(w, t)
        good = [c for c in cands if c.proved]
        line = f"({t.x},{t.row})".ljust(11) + f" {t.dirn:<4}{t.join_row:<6}"
        if good:
            b = max(good, key=lambda c: c.saved)
            best.append(b)
            print(line + f"{b.col:<8}{b.saved:<7} {t.region}", flush=True)
        else:
            stop = cands[0].why if cands else "nothing west of it on his path"
            print(line + f"{'-':<8}{0:<7} {t.region}   [{stop}]", flush=True)
        if "--all" in argv:
            for c in cands:
                print(f"      c={c.col:<4} {'PROVED' if c.proved else 'no    '} "
                      f"saved={c.saved:<4} {c.why}", flush=True)

    rank(best)

    print("\n── the proved moves ──", flush=True)
    for b in sorted(best, key=lambda c: -c.saved):
        t = b.turn
        print(f"\n{t.region}: turn ({t.x},{t.row}) -> ({b.col},{t.row}), rise {t.dirn} "
              f"to row {t.join_row}   saves {b.saved} ticks/execution", flush=True)
        for (x, y), g, live, v in b.cross:
            print(f"    cross    ({x:>3},{y:>3}) {g!r:<4} "
                  f"live_after={{{','.join(sorted(live)) or ''}}}  {v.reason}", flush=True)
        for (x, y), g, h, v in b.drop_row + b.drop_col:
            print(f"    abandon  ({x:>3},{y:>3}) {g!r:<4} heading {h}   {v.reason}",
                  flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
