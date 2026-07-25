#!/usr/bin/env python3
"""Trace where the little men actually walk, and render it as a debug view.

The structural view in :mod:`manstruct` can say a cell is *blank*. It cannot say
whether that blank is **load bearing**, and that is the question a compactor has
to answer, because the two look identical in the ASCII:

* a **corridor** blank the man walks through to get from one body to the next —
  delete it and his path changes;
* a **dead** blank nobody ever enters — pure wasted footprint, and since the
  score is ``max(w,h)**2``, deleting a line of them is a free win.

Only execution distinguishes them, so this module runs the program and records
every cell each man stands on. The reference engine is the oracle: it has no
trace command, but ``tick n`` reports each runner's position at tick *n*, so the
trace is assembled from one engine call per tick. They are independent processes,
so a thread pool hides the latency.

The rendered view answers three questions at a glance:

* **order** — every walked cell is shaded by the tick it was *first* entered, so
  the man's route reads as a gradient rather than a tangle;
* **traffic** — how many times each cell was entered, which is where the ticks go;
* **waste** — in-room blanks that were never entered at all, listed per room and
  per line, so a whole dead row or column is obvious.

A caveat the view states rather than hides: a trace is evidence about *the input
it ran on*. A cell unvisited in one trace may be a rarely-taken branch, not dead
space, so the report labels its coverage and never calls unvisited cells
"deletable" on its own.
"""

from __future__ import annotations

import argparse
import os
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from html import escape
from pathlib import Path

from .littleman import Littleman
from .manparse import parse_program
from .manstruct import Kind, _build_cells

__all__ = ["Step", "Walk", "Flow", "flow_of", "trace", "to_html", "to_svg", "to_text"]


@dataclass(frozen=True)
class Step:
    """One runner's state at one tick."""

    tick: int
    runner: int
    pos: tuple[int, int]
    a: int = 0
    b: int = 0
    backpack: int = 0
    halted: bool = False


@dataclass
class Walk:
    """A trace, plus the per-cell aggregates the view is built from."""

    steps: list[Step] = field(default_factory=list)
    ticks: int = 0
    runners: int = 0
    complete: bool = False  # did the program halt inside the traced window?
    #: cell -> tick it was first entered
    first: dict[tuple[int, int], int] = field(default_factory=dict)
    #: cell -> how many ticks a runner stood on it
    count: Counter = field(default_factory=Counter)
    #: cell -> which runners ever stood on it
    who: dict[tuple[int, int], set[int]] = field(default_factory=dict)

    def path_of(self, runner: int) -> list[tuple[int, int]]:
        return [s.pos for s in self.steps if s.runner == runner]


def trace(
    program: str | os.PathLike[str],
    *,
    input: str | None = None,
    ticks: int = 600,
    workers: int = 8,
    lm: Littleman | None = None,
) -> Walk:
    """Record every runner's position for ticks ``0..ticks``.

    One engine call per tick. That is O(n^2) work inside the engine — each call
    re-simulates from the start — but it is the only authoritative source, and
    for the few hundred ticks a debug view needs it costs seconds.
    """
    lm = lm or Littleman()
    src = Path(os.fspath(program)) if isinstance(program, os.PathLike) else program

    def at(n: int) -> list[Step]:
        snap = lm.tick(src, n, input=input)
        return [
            Step(
                tick=n,
                runner=r.id,
                pos=(r.pos.x, r.pos.y),
                a=r.a,
                b=r.b,
                backpack=r.backpack,
                halted=r.halted,
            )
            for r in snap.entities.runners
        ]

    with ThreadPoolExecutor(max_workers=workers) as pool:
        frames = list(pool.map(at, range(ticks + 1)))

    walk = Walk(ticks=ticks)
    for frame in frames:
        for s in frame:
            walk.steps.append(s)
            walk.first.setdefault(s.pos, s.tick)
            walk.count[s.pos] += 1
            walk.who.setdefault(s.pos, set()).add(s.runner)
    walk.runners = len({s.runner for s in walk.steps})
    walk.complete = bool(frames) and all(s.halted for s in frames[-1])
    return walk


# ── flow: the route as directed edges, forks, joins and stalls ───────────────
Cell = tuple[int, int]
Dir = tuple[int, int]
_ARROW = {(1, 0): "→", (-1, 0): "←", (0, -1): "↑", (0, 1): "↓"}


@dataclass
class Flow:
    """The traced route as a graph, which is what makes the movement legible.

    A shaded cell says "visited"; an **edge** says *which way he went*.

    The important distinction, and one that observation alone gets wrong both
    ways, is **fork vs crossing**:

    ``forks``
        a genuine branch in control flow, and that is a property of the *glyph*,
        not of the trace: ``X`` turns on the main hand, ``a``/``d``/``x`` on the
        backpack. An ``X`` is a three-way fork even in a trace where it happened
        to go straight every time — so forks are read from the grid and a trace
        can only say which of its arms were *exercised*.
    ``crossings``
        any other cell left in more than one direction. This is not a branch: it
        is two independent lanes passing through the same blank, which is only
        possible because a blank is a nop in every heading. Calling one a fork
        was the false positive; missing an unexercised ``X`` was the false
        negative.
    ``joins``
        cells entered from more than one heading — where paths merge, so the
        corridor is shared and therefore load bearing.

    ``stalls`` counts ticks a runner stood still: an ``r`` on an empty pipe or an
    ``s`` on a full one. High stall counts are *waiting*, not work, and reading
    them as hot code is a mistake the plain visit-count view invites.
    """

    edges: Counter = field(default_factory=Counter)  # (from, to, runner) -> ticks
    out_dirs: dict[Cell, set[Dir]] = field(default_factory=dict)
    in_dirs: dict[Cell, set[Dir]] = field(default_factory=dict)
    stalls: Counter = field(default_factory=Counter)  # cell -> ticks stood still
    runners: set[int] = field(default_factory=set)
    #: conditional-turn cells read from the grid, independent of any trace
    forks: set[Cell] = field(default_factory=set)

    @property
    def crossings(self) -> set[Cell]:
        """Multi-exit cells that are *not* branches: shared corridors."""
        return {c for c, d in self.out_dirs.items() if len(d) > 1} - self.forks

    @property
    def joins(self) -> set[Cell]:
        return {c for c, d in self.in_dirs.items() if len(d) > 1}

    def exercised(self, cell: Cell) -> set[Dir]:
        """Which arms of a fork this trace actually took (possibly none)."""
        return self.out_dirs.get(cell, set())

    @property
    def cold_forks(self) -> set[Cell]:
        """Forks with fewer than two arms taken — untested branches."""
        return {c for c in self.forks if len(self.exercised(c)) < 2}


def flow_of(walk: Walk, cells: dict[Cell, object] | None = None) -> Flow:
    """Turn per-tick positions into a directed route graph.

    `cells` is the classified lattice from :mod:`manstruct`. Pass it so forks can
    be read from the grid rather than inferred from coverage — without it a
    conditional turn the trace never branched at is invisible.
    """
    f = Flow()
    if cells:
        f.forks = {c for c, i in cells.items() if getattr(i, "kind", None) is Kind.BRANCH}
    by_runner: dict[int, list[Step]] = {}
    for s in walk.steps:
        by_runner.setdefault(s.runner, []).append(s)
    for runner, steps in by_runner.items():
        f.runners.add(runner)
        steps.sort(key=lambda s: s.tick)
        for a, b in zip(steps, steps[1:], strict=False):
            if a.pos == b.pos:
                f.stalls[a.pos] += 1  # blocked: same cell two ticks running
                continue
            d = (b.pos[0] - a.pos[0], b.pos[1] - a.pos[1])
            if d not in _ARROW:
                continue  # a teleport cannot happen; guard anyway
            f.edges[(a.pos, b.pos, runner)] += 1
            f.out_dirs.setdefault(a.pos, set()).add(d)
            f.in_dirs.setdefault(b.pos, set()).add(d)
    return f


# ── reporting ────────────────────────────────────────────────────────────────
@dataclass
class Waste:
    """Per-room blanks that no runner ever entered, and the fully dead lines."""

    room: int
    kind: str
    blanks: int
    walked: int
    dead: int
    dead_rows: list[int]  # absolute grid rows entirely unvisited inside this room
    dead_cols: list[int]


def _waste(prog, cells, walk: Walk) -> list[Waste]:
    out = []
    for room in prog.rooms:
        x0, y0 = room.min_
        x1, y1 = room.max_
        blanks = walked = 0
        per_row: dict[int, list[bool]] = {}
        per_col: dict[int, list[bool]] = {}
        for y in range(y0 + 1, y1):
            for x in range(x0 + 1, x1):
                info = cells.get((x, y))
                if info is None or info.kind not in (Kind.FLOOR, Kind.NOP):
                    continue
                blanks += 1
                seen = (x, y) in walk.first
                walked += seen
                per_row.setdefault(y, []).append(seen)
                per_col.setdefault(x, []).append(seen)
        # a line is dead only if it has blanks and *none* were entered, and it
        # holds no live glyph either (a live glyph would make it uncuttable)
        live_rows = {
            y
            for y in range(y0 + 1, y1)
            for x in range(x0 + 1, x1)
            if (i := cells.get((x, y))) and i.kind not in (Kind.FLOOR, Kind.NOP, Kind.WALL)
        }
        live_cols = {
            x
            for y in range(y0 + 1, y1)
            for x in range(x0 + 1, x1)
            if (i := cells.get((x, y))) and i.kind not in (Kind.FLOOR, Kind.NOP, Kind.WALL)
        }
        out.append(
            Waste(
                room=room.id,
                kind=room.kind,
                blanks=blanks,
                walked=walked,
                dead=blanks - walked,
                dead_rows=[y for y, v in per_row.items() if not any(v) and y not in live_rows],
                dead_cols=[x for x, v in per_col.items() if not any(v) and x not in live_cols],
            )
        )
    return out


def to_text(prog, cells, walk: Walk) -> str:
    out = [
        f"traced {walk.ticks:,} ticks, {walk.runners} runner(s), "
        f"{'halted' if walk.complete else 'still running'}",
        f"cells entered: {len(walk.first):,}   busiest: "
        + ", ".join(f"{c}x{n}" for c, n in walk.count.most_common(5)),
        "",
        "WASTE  (blanks nobody entered — the compaction target)",
    ]
    for w in _waste(prog, cells, walk):
        out.append(
            f"  room{w.room} {w.kind:8s} blanks {w.blanks:5d}  walked {w.walked:5d}  "
            f"dead {w.dead:5d}   fully dead rows {len(w.dead_rows):3d} cols {len(w.dead_cols):3d}"
        )
    if not walk.complete:
        out += [
            "",
            "NOTE: the program had not halted, so this is one input's coverage. An",
            "unvisited cell may be a rarely-taken branch rather than dead space.",
        ]
    return "\n".join(out)


_RUNNER_HUE = ["#2563eb", "#c026d3", "#0d9488", "#ea580c"]

#: Branch hues for the flow view. Validated with the dataviz palette checker in
#: both light and dark (lightness band, chroma floor, CVD separation -- worst
#: adjacent pair deutan dE 12.8 -- and contrast). Colour means *branch* and only
#: branch there; the runner is carried by the dash pattern instead, so the two
#: never compete for the same channel and neither is encoded by colour alone.
_BRANCH_HUE = ["#2563eb", "#ea580c", "#0d9488", "#c026d3"]
_RUNNER_DASH = ["none", "5 2.5", "1.5 2", "7 2 1.5 2"]
_ROUTE_INK = "#334155"


def to_html(prog, cells, walk: Walk, *, title: str, input: str | None) -> str:
    """Grid shaded by first-visit tick, with a scrubber and a waste report."""
    rows = prog.to_grid()
    w = max((len(r) for r in rows), default=0)
    h = len(rows)
    span = max(walk.ticks, 1)

    # per-runner ordered paths, for the scrubber
    paths: dict[int, list[list[int]]] = {}
    for s in walk.steps:
        paths.setdefault(s.runner, []).append([s.tick, s.pos[0], s.pos[1]])

    body = []
    for y in range(h):
        tds = []
        for x in range(w):
            info = cells.get((x, y))
            glyph = rows[y][x] if x < len(rows[y]) else " "
            kind = info.kind if info else Kind.VOID
            first = walk.first.get((x, y))
            n = walk.count.get((x, y), 0)
            who = sorted(walk.who.get((x, y), ()))
            cls = "c"
            style = ""
            if first is not None:
                # sequential ramp on *order*: light = early, dark = late
                frac = first / span
                hue = _RUNNER_HUE[who[0] % len(_RUNNER_HUE)] if who else "#2563eb"
                style = f"--o:{frac:.4f};--h:{hue}"
                cls += " walked"
            elif kind in (Kind.FLOOR, Kind.NOP) and info and info.room is not None:
                cls += " dead"
            elif kind is Kind.WALL:
                cls += " wall"
            elif kind is Kind.PIPE:
                cls += " pipe"
            tip = f"({x},{y}) {kind.value}"
            if first is not None:
                tip += f" · first entered tick {first} · {n} visit(s) · runner {who}"
            elif cls.endswith("dead"):
                tip += " · never entered: dead footprint"
            tds.append(
                f'<i class="{cls}" style="{style}" data-x="{x}" data-y="{y}" '
                f'title="{escape(tip)}">{escape(glyph) if glyph != " " else "&nbsp;"}</i>'
            )
        body.append("".join(tds))

    waste_rows = "".join(
        f"<tr><td>room{k.room}</td><td>{k.kind}</td><td>{k.blanks}</td>"
        f"<td>{k.walked}</td><td><b>{k.dead}</b></td>"
        f"<td>{len(k.dead_rows)}</td><td>{len(k.dead_cols)}</td></tr>"
        for k in _waste(prog, cells, walk)
    )
    busiest = "".join(
        f"<tr><td>({c[0]},{c[1]})</td><td><code>"
        f"{escape(rows[c[1]][c[0]] if c[1] < h and c[0] < len(rows[c[1]]) else ' ')}"
        f"</code></td><td>{n}</td><td>{walk.first.get(c)}</td></tr>"
        for c, n in walk.count.most_common(12)
    )
    legend = "".join(
        f'<span><i style="background:{c}"></i>runner {i}</span>'
        for i, c in enumerate(_RUNNER_HUE[: max(walk.runners, 1)])
    )
    note = (
        ""
        if walk.complete
        else '<p class="warn">The program had not halted at the end of the traced '
        "window, so this is <b>one input’s coverage</b>. A cell never entered "
        "here may be a rarely-taken branch rather than dead space — confirm "
        "before cutting.</p>"
    )
    return f"""<!doctype html>
<meta charset="utf-8"><title>{escape(title)}</title>
<style>
 body{{font:14px/1.5 ui-sans-serif,system-ui,sans-serif;margin:0;padding:24px;
  background:#fff;color:#0f172a}}
 h1{{font-size:19px;margin:0 0 4px}} h2{{font-size:15px;margin:26px 0 8px}}
 .sub{{color:#64748b;margin:0 0 14px}}
 .warn{{background:#fef3c7;border-left:3px solid #d97706;padding:8px 12px;
  margin:12px 0;color:#713f12;font-size:13px}}
 #grid{{font:11px/1 ui-monospace,Menlo,monospace;white-space:pre;overflow:auto;
  border:1px solid #cbd5e1;border-radius:8px;padding:8px;background:#fff}}
 #grid i{{display:inline-block;width:11px;height:14px;text-align:center;
  font-style:normal;background:#f8fafc;color:#94a3b8}}
 /* order ramp: light early -> dark late, one hue per runner */
 i.walked{{background:color-mix(in oklab, var(--h) calc(18% + var(--o)*72%), white);
  color:#0b1120;font-weight:600}}
 i.dead{{background:repeating-linear-gradient(45deg,#fee2e2 0 3px,#fecaca 3px 6px);
  color:#b91c1c}}
 i.wall{{background:#475569;color:#f8fafc}} i.pipe{{background:#d1fae5;color:#065f46}}
 i.here{{outline:2px solid #111827;outline-offset:-1px;background:#fde047!important}}
 .legend{{display:flex;gap:14px;flex-wrap:wrap;margin:10px 0;color:#475569;font-size:12px}}
 .legend span{{display:flex;align-items:center;gap:5px}}
 .legend i{{width:12px;height:12px;border-radius:3px;display:inline-block}}
 .ramp{{width:80px;height:12px;border-radius:3px;display:inline-block;
  background:linear-gradient(90deg,color-mix(in oklab,#2563eb 18%,white),#2563eb)}}
 table{{border-collapse:collapse;font-size:12px}}
 th,td{{text-align:left;padding:4px 10px;border-bottom:1px solid #e2e8f0}}
 th{{color:#64748b}} code{{background:#f1f5f9;padding:1px 4px;border-radius:3px}}
 #bar{{display:flex;gap:10px;align-items:center;margin:14px 0}}
 input[type=range]{{flex:1;max-width:640px}}
 @media(prefers-color-scheme:dark){{
  body{{background:#0b1120;color:#e2e8f0}} #grid{{background:#0f172a;border-color:#334155}}
  #grid i{{background:#131c2e;color:#475569}} th{{color:#94a3b8}}
  td{{border-color:#1e293b}} code{{background:#1e293b}} .sub,.legend{{color:#94a3b8}}
  i.walked{{color:#f8fafc}} .warn{{background:#422006;color:#fde68a}}}}
</style>
<h1>{escape(title)} — movement order</h1>
<p class="sub">{walk.ticks:,} ticks traced · {walk.runners} runner(s) ·
 {len(walk.first):,} distinct cells entered ·
 input <code>{escape(input or "(none)")}</code></p>
{note}
<div id="bar">
  <button id="play">▶ play</button>
  <input type="range" id="t" min="0" max="{walk.ticks}" value="0">
  <output id="lab">tick 0</output>
</div>
<div id="grid">{chr(10).join(f"<div>{r}</div>" for r in body)}</div>
<div class="legend">
  <span><span class="ramp"></span> first entered: early → late</span>
  {legend}
  <span><i style="background:#fecaca"></i>never entered (dead footprint)</span>
  <span><i style="background:#d1fae5"></i>pipe</span>
  <span><i style="background:#475569"></i>wall</span>
</div>
<h2>Waste per room — blanks nobody entered</h2>
<table><tr><th>room</th><th>kind</th><th>blanks</th><th>walked</th><th>dead</th>
<th>fully dead rows</th><th>fully dead cols</th></tr>{waste_rows}</table>
<h2>Busiest cells — where the ticks go</h2>
<table><tr><th>cell</th><th>glyph</th><th>visits</th><th>first tick</th></tr>{busiest}</table>
<script>
const paths = {paths!r};
const cells = new Map();
document.querySelectorAll('#grid i').forEach(e =>
  cells.set(e.dataset.x + ',' + e.dataset.y, e));
const t = document.getElementById('t'), lab = document.getElementById('lab');
let marked = [];
function show(tick) {{
  marked.forEach(e => e.classList.remove('here'));
  marked = [];
  for (const r of Object.keys(paths)) {{
    const p = paths[r].filter(s => s[0] === tick);
    for (const s of p) {{
      const e = cells.get(s[1] + ',' + s[2]);
      if (e) {{ e.classList.add('here'); marked.push(e); }}
    }}
  }}
  lab.textContent = 'tick ' + tick;
}}
t.addEventListener('input', () => show(+t.value));
let timer = null;
document.getElementById('play').addEventListener('click', e => {{
  if (timer) {{ clearInterval(timer); timer = null; e.target.textContent = '▶ play'; return; }}
  e.target.textContent = '❚❚ pause';
  timer = setInterval(() => {{
    const n = (+t.value + 1) % (+t.max + 1);
    t.value = n; show(n);
  }}, 60);
}});
show(0);
</script>
"""


def to_svg(
    prog, cells, walk: Walk, *, title: str, input: str | None, cell: int = 20
) -> str:
    """Ink the route: one arrow per traversed edge, badges on forks and stalls.

    The heat view answers "was this cell used". This answers "which way does the
    program *go*", which is the question you ask when a board looks sparse: the
    empty space is mostly corridor, and an arrow through it says so.
    """
    f = flow_of(walk, cells)
    rows = prog.to_grid()
    w = max((len(r) for r in rows), default=0)
    h = len(rows)
    pad = 26
    W, H = pad * 2 + w * cell, pad * 2 + h * cell
    cx = lambda x: pad + x * cell + cell / 2  # noqa: E731
    cy = lambda y: pad + y * cell + cell / 2  # noqa: E731

    # backdrop: rooms, then glyphs
    parts: list[str] = []
    for room in prog.rooms:
        x0, y0 = room.min_
        x1, y1 = room.max_
        parts.append(
            f'<rect class="room" x="{pad + x0 * cell}" y="{pad + y0 * cell}" '
            f'width="{(x1 - x0 + 1) * cell}" height="{(y1 - y0 + 1) * cell}"/>'
            f'<text class="rlab" x="{pad + x0 * cell + 3}" y="{pad + y0 * cell - 3}">'
            f"room{room.id} {escape(room.kind)}</text>"
        )
    for y in range(h):
        for x in range(w):
            glyph = rows[y][x] if x < len(rows[y]) else " "
            if glyph == " ":
                continue
            info = cells.get((x, y))
            k = info.kind.value if info else "void"
            parts.append(
                f'<text class="g k-{k}" x="{cx(x)}" y="{cy(y)}">{escape(glyph)}</text>'
            )

    # dead in-room blanks: mark them, so waste is visible against the route
    for (x, y), info in cells.items():
        if (
            info.room is not None
            and info.kind in (Kind.FLOOR, Kind.NOP)
            and (x, y) not in walk.first
        ):
            parts.append(
                f'<rect class="dead" x="{pad + x * cell + 3}" y="{pad + y * cell + 3}" '
                f'width="{cell - 6}" height="{cell - 6}"/>'
            )

    # Lane separation. Every arrow is pushed off the cell centre along the
    # perpendicular of its own heading, so the four headings land in four
    # different lanes and a there-and-back corridor reads as two, not one
    # overdrawn line. A further step per runner keeps two men walking the *same*
    # direction over the same cells from being drawn exactly on top of each other,
    # which is the case a heading-only offset silently hides.
    def lane(a: Cell, b: Cell, runner: int) -> tuple[float, float, float, float]:
        dx, dy = b[0] - a[0], b[1] - a[1]
        px, py = -dy, dx  # perpendicular to travel; flips with the heading
        m = cell * 0.11 + cell * 0.075 * runner
        return (
            cx(a[0]) + px * m,
            cy(a[1]) + py * m,
            cx(b[0]) + px * m,
            cy(b[1]) + py * m,
        )

    # Static structure first, faint: every steer glyph forces a heading whether or
    # not this trace happened to walk it. Without these the board looks empty in
    # exactly the places whose direction is already decided by the grid.
    for (x, y), info in cells.items():
        if info.kind is not Kind.STEER:
            continue
        d = {">": (1, 0), "<": (-1, 0), "^": (0, -1), "v": (0, 1)}.get(info.glyph)
        if d is None:
            continue
        seen = "" if (x, y) in walk.first else " (never walked in this trace)"
        x1, y1 = cx(x) - d[0] * cell * 0.34, cy(y) - d[1] * cell * 0.34
        x2, y2 = cx(x) + d[0] * cell * 0.40, cy(y) + d[1] * cell * 0.40
        parts.append(
            f'<line class="s" x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" '
            f'marker-end="url(#sa)"><title>({x},{y}) {escape(info.glyph)} forces '
            f"{_ARROW[d]} — structural, always{seen}</title></line>"
        )

    # Every arm of a fork gets its own colour, so a branch is traceable: the run
    # leaving it says *which way this one went*, which one per-runner colour
    # cannot. Arm index is by sorted heading, so the same colour means the same
    # relative arm at every fork in the picture.
    branch: dict[tuple[Cell, Dir], int] = {}
    for c in f.forks:
        for i, d in enumerate(sorted(f.exercised(c))):
            branch[(c, d)] = i % len(_BRANCH_HUE)

    # Chain consecutive same-heading moves into ONE polyline. A straight run
    # shares a heading and therefore a lane, so the line is unbroken and carries a
    # chevron at every cell it passes -- that is what makes it read as "and it
    # keeps going this way" instead of a row of separate dashes.
    busiest = max(f.edges.values(), default=1)
    nxt: dict[tuple[int, Dir], dict[Cell, tuple[Cell, int]]] = {}
    for (a, b, runner), n in f.edges.items():
        d = (b[0] - a[0], b[1] - a[1])
        nxt.setdefault((runner, d), {})[a] = (b, n)

    for (runner, d), moves in sorted(nxt.items()):
        dash = _RUNNER_DASH[runner % len(_RUNNER_DASH)]
        starts = [a for a in moves if (a[0] - d[0], a[1] - d[1]) not in moves]
        for start in sorted(starts):
            chain, traffic, cur = [start], [], start
            while cur in moves:
                b, n = moves[cur]
                traffic.append(n)
                chain.append(b)
                cur = b
            pts = []
            for i, c in enumerate(chain):
                # the lane offset is constant along a straight run, so the
                # polyline is continuous; the head overshoots the final centre so
                # it points INTO the next cell rather than stopping dead on it
                x1, y1, _, _ = lane(c, (c[0] + d[0], c[1] + d[1]), runner)
                if i == len(chain) - 1:
                    x1 += d[0] * cell * 0.24
                    y1 += d[1] * cell * 0.24
                pts.append(f"{x1:.1f},{y1:.1f}")
            peak = max(traffic)
            width = 1.0 + 2.4 * (peak / busiest) ** 0.5
            bi = branch.get((start, d))
            if bi is None:
                cls, mk, extra = "e", "ink", ""
            else:
                cls, mk = f"e b{bi}", f"b{bi}"
                extra = f" \u00b7 arm {bi + 1} of {len(f.exercised(start))} leaving a fork"
            parts.append(
                f'<polyline class="{cls}" points="{" ".join(pts)}" '
                f'stroke-width="{width:.2f}" stroke-dasharray="{dash}" '
                f'marker-mid="url(#c{mk})" marker-end="url(#a{mk})">'
                f"<title>({chain[0][0]},{chain[0][1]}) {_ARROW[d]} "
                f"({chain[-1][0]},{chain[-1][1]}) \u00b7 {len(chain) - 1} cell(s) "
                f"\u00b7 up to {peak} traversal(s) \u00b7 runner {runner}{extra}"
                f"</title></polyline>"
            )

    # Forks come from the GRID, so an `X` is ringed even where this trace only
    # ever took one arm -- and those matter most, because an untaken arm is an
    # untested branch.
    for x, y in sorted(f.forks):
        glyph = rows[y][x] if x < len(rows[y]) else " "
        taken = f.exercised((x, y))
        arms = "".join(_ARROW[dd] for dd in sorted(taken)) or "none"
        cold = len(taken) < 2
        parts.append(
            f'<circle class="fork{" cold" if cold else ""}" cx="{cx(x)}" '
            f'cy="{cy(y)}" r="{cell * 0.44:.1f}">'
            f"<title>FORK ({x},{y}) {escape(glyph)} \u2014 conditional turn. "
            f"Arms taken in this trace: {arms}"
            f"{' \u00b7 UNTESTED: fewer than two arms exercised' if cold else ''}"
            f"</title></circle>"
        )
    for x, y in sorted(f.crossings):
        parts.append(
            f'<rect class="cross" x="{cx(x) - cell * 0.4:.1f}" '
            f'y="{cy(y) - cell * 0.4:.1f}" width="{cell * 0.8:.1f}" '
            f'height="{cell * 0.8:.1f}">'
            f"<title>CROSSING ({x},{y}) \u2014 two lanes share this blank "
            f"({''.join(_ARROW[dd] for dd in sorted(f.out_dirs[(x, y)]))}). "
            f"Not a branch: a nop passes every heading through unchanged, so the "
            f"cell is load bearing for both.</title></rect>"
        )
    for (x, y), n in f.stalls.items():
        if n < max(4, walk.ticks // 200):
            continue
        parts.append(
            f'<rect class="stall" x="{pad + x * cell + 1}" y="{pad + y * cell + 1}" '
            f'width="{cell - 2}" height="{cell - 2}" rx="3">'
            f"<title>STALLED ({x},{y}) {n} ticks — blocked on a pipe, not working"
            f"</title></rect>"
        )

    def _marks(name: str, colour: str) -> str:
        return (
            f'<marker id="a{name}" viewBox="0 0 6 6" markerWidth="5" '
            f'markerHeight="5" refX="5.2" refY="3" orient="auto">'
            f'<path d="M0,0 L0,6 L6,3 z" fill="{colour}"/></marker>'
            # chevron for marker-mid: an open V, so a long run shows direction
            # at every cell without the line looking like a chain of blobs
            f'<marker id="c{name}" viewBox="0 0 6 6" markerWidth="4.5" '
            f'markerHeight="4.5" refX="3" refY="3" orient="auto">'
            f'<path d="M1,1 L4,3 L1,5" fill="none" stroke="{colour}" '
            f'stroke-width="1.6" stroke-linecap="round"/></marker>'
        )

    markers = (
        "".join(_marks(f"b{i}", c) for i, c in enumerate(_BRANCH_HUE))
        + _marks("ink", _ROUTE_INK)
        + (
            '<marker id="sa" viewBox="0 0 6 6" markerWidth="4" markerHeight="4" '
            'refX="5.2" refY="3" orient="auto"><path d="M0,0 L0,6 L6,3 z" '
            'fill="#cbd5e1"/></marker>'
        )
    )
    runner_css = "".join(f".e.b{i}{{stroke:{c}}}" for i, c in enumerate(_BRANCH_HUE))
    split_rows = "".join(
        f"<tr><td>({x},{y})</td><td><code>"
        f"{escape(rows[y][x] if x < len(rows[y]) else ' ')}</code></td>"
        f"<td>{''.join(_ARROW[d] for d in sorted(f.exercised((x, y)))) or '—'}</td>"
        f"<td>{len(f.exercised((x, y)))}</td>"
        f"<td>{walk.count.get((x, y), 0)}</td>"
        f"<td>{'UNTESTED' if len(f.exercised((x, y))) < 2 else 'ok'}</td></tr>"
        for x, y in sorted(f.forks, key=lambda c: (c[1], c[0]))
    )
    stall_rows = "".join(
        f"<tr><td>({x},{y})</td><td><code>"
        f"{escape(rows[y][x] if x < len(rows[y]) else ' ')}</code></td><td>{n}</td>"
        f"<td>{100 * n / max(walk.ticks, 1):.1f}%</td></tr>"
        for (x, y), n in f.stalls.most_common(10)
    )
    legend = "".join(
        f'<span><i style="background:{c}"></i>branch {i + 1} out of a split</span>'
        for i, c in enumerate(_BRANCH_HUE)
    ) + "".join(
        f'<span><svg width="26" height="8"><line x1="0" y1="4" x2="26" y2="4" '
        f'stroke="{_ROUTE_INK}" stroke-width="2" stroke-dasharray="'
        f'{_RUNNER_DASH[i % len(_RUNNER_DASH)]}"/></svg>runner {i}</span>'
        for i in range(max(walk.runners, 1))
    )
    n_steer = sum(1 for i in cells.values() if i.kind is Kind.STEER)
    return f"""<!doctype html>
<meta charset="utf-8"><title>{escape(title)} flow</title>
<style>
 body{{font:14px/1.5 ui-sans-serif,system-ui,sans-serif;margin:0;padding:24px;
  background:#fff;color:#0f172a}}
 h1{{font-size:19px;margin:0 0 4px}} h2{{font-size:15px;margin:26px 0 8px}}
 .sub{{color:#64748b;margin:0 0 14px}}
 .wrap{{overflow:auto;border:1px solid #cbd5e1;border-radius:8px;background:#fff}}
 svg{{display:block}}
 .room{{fill:#f8fafc;stroke:#94a3b8;stroke-width:1}}
 .rlab{{font:10px ui-monospace,monospace;fill:#94a3b8}}
 .g{{font:12px ui-monospace,Menlo,monospace;text-anchor:middle;dominant-baseline:central;
  fill:#0f172a}}
 .k-wall{{fill:#94a3b8}} .k-pipe{{fill:#059669}} .k-nop{{fill:#cbd5e1}}
 .k-steer{{fill:#ea580c}} .k-branch{{fill:#db2777;font-weight:700}}
 .k-literal{{fill:#7c3aed}} .k-io,.k-spawn{{fill:#0891b2;font-weight:700}}
 .dead{{fill:#fecaca;opacity:.75}}
 .e{{opacity:.92;stroke-linecap:round;fill:none;stroke:{_ROUTE_INK}}} {runner_css}
 .s{{stroke:#cbd5e1;stroke-width:1.1;opacity:.85}}
 .fork{{fill:none;stroke:#db2777;stroke-width:2.2}}
 .fork.cold{{stroke-dasharray:3 2;opacity:.8}}
 .cross{{fill:none;stroke:#0891b2;stroke-width:1.4;stroke-dasharray:2 2}}
 .stall{{fill:none;stroke:#f59e0b;stroke-width:2}}
 .legend{{display:flex;gap:14px;flex-wrap:wrap;margin:10px 0;color:#475569;font-size:12px}}
 .legend span{{display:flex;align-items:center;gap:5px}}
 .legend i{{width:12px;height:12px;border-radius:3px;display:inline-block}}
 table{{border-collapse:collapse;font-size:12px}}
 th,td{{text-align:left;padding:4px 10px;border-bottom:1px solid #e2e8f0}}
 th{{color:#64748b}} code{{background:#f1f5f9;padding:1px 4px;border-radius:3px}}
 @media(prefers-color-scheme:dark){{
  body{{background:#0b1120;color:#e2e8f0}} .wrap{{border-color:#334155}}
  .room{{fill:#0f172a;stroke:#334155}} .g{{fill:#e2e8f0}} .k-wall{{fill:#475569}}
  .dead{{fill:#7f1d1d}} th{{color:#94a3b8}} td{{border-color:#1e293b}}
  code{{background:#1e293b}} .sub,.legend{{color:#94a3b8}} svg{{background:#0b1120}}}}
</style>
<h1>{escape(title)} — flow: arrows, forks and crossings</h1>
<p class="sub">{walk.ticks:,} ticks · {len(f.edges):,} distinct moves ·
 <b>{len(f.forks)}</b> forks (<b>{len(f.cold_forks)}</b> untested) ·
 <b>{len(f.crossings)}</b> crossings · <b>{len(f.joins)}</b> joins ·
 {n_steer} structural ·
 input <code>{escape(input or "(none)")}</code>.
 Arrow thickness is traffic; hover anything for detail.</p>
<div class="wrap"><svg width="{W}" height="{H}" viewBox="0 0 {W} {H}">
<defs>{markers}</defs>{"".join(parts)}</svg></div>
<div class="legend">{legend}
 <span><i style="background:#cbd5e1"></i>structural arrow (steer glyph)</span>
 <span><i style="border:2px solid #db2777"></i>fork (X a d x)</span>
 <span><i style="border:2px dashed #db2777"></i>fork, &lt;2 arms taken (untested)</span>
 <span><i style="border:1.5px dashed #0891b2"></i>crossing (shared blank)</span>
 <span><i style="border:2px solid #f59e0b"></i>stalled on a pipe</span>
 <span><i style="background:#fecaca"></i>never entered</span></div>
<h2>Forks — where control branches (read from the grid, not the trace)</h2>
<table><tr><th>cell</th><th>glyph</th><th>arms taken</th><th>n arms</th>
<th>visits</th><th>coverage</th></tr>
{split_rows or '<tr><td colspan="6">no conditional turns in this grid</td></tr>'}</table>
<h2>Stalls — ticks spent blocked, not working</h2>
<table><tr><th>cell</th><th>glyph</th><th>ticks</th><th>of trace</th></tr>
{stall_rows or '<tr><td colspan="4">none</td></tr>'}</table>
"""


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("grid", type=Path)
    ap.add_argument("--input", default=None)
    ap.add_argument("--ticks", type=int, default=600)
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--html", type=Path, help="heat view: first-visit order + scrubber")
    ap.add_argument("--svg", type=Path, help="flow view: arrows, splits, stalls")
    ap.add_argument("--cell", type=int, default=20, help="flow view cell size in px")
    args = ap.parse_args()

    lm = Littleman()
    prog = parse_program(args.grid, lm=lm, bind=False)
    cells = _build_cells(prog)
    walk = trace(
        args.grid, input=args.input, ticks=args.ticks, workers=args.workers, lm=lm
    )
    print(to_text(prog, cells, walk))
    f = flow_of(walk, cells)
    print(
        f"\nFLOW  {len(f.edges):,} distinct moves, {len(f.forks)} forks "
        f"({len(f.cold_forks)} with <2 arms taken), {len(f.crossings)} crossings, "
        f"{len(f.joins)} joins, {sum(f.stalls.values()):,} stalled ticks"
    )
    for c, n in f.stalls.most_common(4):
        print(f"  stalled at {c} for {n:,} ticks ({100 * n / max(walk.ticks, 1):.0f}%)")
    if args.svg:
        args.svg.write_text(
            to_svg(
                prog, cells, walk, title=args.grid.name, input=args.input, cell=args.cell
            ),
            encoding="utf-8",
        )
        print(f"wrote {args.svg}")
    if args.html:
        args.html.write_text(
            to_html(prog, cells, walk, title=args.grid.name, input=args.input),
            encoding="utf-8",
        )
        print(f"\nwrote {args.html}")


if __name__ == "__main__":
    main()
