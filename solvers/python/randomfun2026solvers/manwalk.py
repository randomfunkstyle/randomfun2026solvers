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

__all__ = ["Step", "Walk", "trace", "to_html", "to_text"]


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


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("grid", type=Path)
    ap.add_argument("--input", default=None)
    ap.add_argument("--ticks", type=int, default=600)
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--html", type=Path)
    args = ap.parse_args()

    lm = Littleman()
    prog = parse_program(args.grid, lm=lm, bind=False)
    cells = _build_cells(prog)
    walk = trace(
        args.grid, input=args.input, ticks=args.ticks, workers=args.workers, lm=lm
    )
    print(to_text(prog, cells, walk))
    if args.html:
        args.html.write_text(
            to_html(prog, cells, walk, title=args.grid.name, input=args.input),
            encoding="utf-8",
        )
        print(f"\nwrote {args.html}")


if __name__ == "__main__":
    main()
