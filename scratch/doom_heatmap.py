"""Where does deadman-3d_taped stand?  A sampling profiler + a rendered heatmap.

    uv run python scratch/doom_heatmap.py --out <dir> [--rounds 8] [--stride 1]

This is `littleman/tools/heatmap.mjs` rebuilt on the native `fast_littleman`
engine, because the .mjs one drives `littleman.wasm` and that engine OOMs its
4 GB Go heap before it can even load this machine.  The two design properties
that make the original sound are kept:

* **Sample positions, not instructions.**  Every `--stride` ticks each live
  man's cell is recorded.  A man parked on an `r` waiting for a pipe is counted
  every single sample, so a blocking hot spot shows up as a tall bar instead of
  vanishing the way an instruction counter would hide it.  At `--stride 1` the
  counts are exact runner-ticks, not an estimate.
* **Run gated.**  deadman-3d is display-judged, so the case is run with
  `frames`; ungated the judge releases every round at once and the profile
  measures a jam rather than the program.

One extra thing the .mjs version cannot do: the engine knows *why* a man is
standing still (it parks him on a wait list), so "stalled" here is exact rather
than inferred from "sampled twice in the same cell".

Output: a ranked per-region table (`Machine.regions`, so blocks are named), a
per-man table, `heat.png` / `wait.png` at machine scale, and an `index.html`
that carries all of it.
"""

from __future__ import annotations

import argparse
import base64
import html
import json
import math
from pathlib import Path

from doom_case import (
    DEFAULT_ROUNDS,
    cell_labels,
    gated_case,
    machine,
    pipe_names,
    profile,
    room_labels,
)
from randomfun2026solvers.man_png import Png

# Sequential encoding is one hue, light -> dark (dataviz skill, palette.md):
# blue for occupancy, orange for the blocked subset — two sequential contexts,
# so the second takes the next categorical slot's hue as its own one-hue ramp.
BLUE = ["#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95", "#0d366b"]
ORANGE = ["#fbdccd", "#f7bda4", "#f39d7b", "#eb6834", "#c9531f", "#9e4118", "#6e2d11"]
SURFACE = (252, 252, 251)
ROOM_BG = (238, 238, 235)
WALL = (206, 206, 200)
INK = (26, 26, 25)


def rgb(hexstr: str) -> tuple[int, int, int]:
    return tuple(int(hexstr[i : i + 2], 16) for i in (1, 3, 5))  # type: ignore[return-value]


def ramp(value: int, peak: int, colors: list[str]) -> tuple[int, int, int]:
    """Log-scaled step.  Occupancy spans five decades; a linear ramp would show
    one cell and a black field."""
    if value <= 0:
        return ROOM_BG
    t = math.log1p(value) / math.log1p(peak)
    return rgb(colors[min(len(colors) - 1, int(t * len(colors)))])


def render(
    rows: list[str],
    counts: dict[tuple[int, int], int],
    colors: list[str],
    path: Path,
    scale: int = 4,
    mark: int = 8,
) -> None:
    """A per-cell image at machine scale, log-ramped.

    The top `mark` cells also get a ring.  Without it the picture lies by area:
    the ROM's drum is a 252x93 field of a man walking every cell about equally,
    and it out-shouts the five single cells that hold 40% of the run.  Intensity
    ranks; the rings say where to look.
    """
    w, h = max(len(r) for r in rows), len(rows)
    canvas = Png(w * scale, h * scale, SURFACE)
    peak = max(counts.values(), default=1)
    for y, row in enumerate(rows):
        for x, ch in enumerate(row.ljust(w)):
            n = counts.get((x, y), 0)
            if n:
                colour = ramp(n, peak, colors)
            elif ch in "+-|=:":
                colour = WALL
            elif ch != " ":
                colour = ROOM_BG
            else:
                continue
            canvas.rect(x * scale, y * scale, scale, scale, colour)
    for (x, y), _n in sorted(counts.items(), key=lambda kv: -kv[1])[:mark]:
        canvas.frame(x * scale - scale, y * scale - scale, 3 * scale, 3 * scale, INK)
    canvas.write(path)


def table(title: str, header: list[str], body: list[list[str]], widths: list[int]) -> str:
    pairs = list(zip(header, widths, strict=False))
    line = "  ".join(h.rjust(w) if i else h.ljust(w) for i, (h, w) in enumerate(pairs))
    out = [title, "-" * len(line), line, "-" * len(line)]
    for r in body:
        cells = list(zip(r, widths, strict=False))
        out.append("  ".join(c.rjust(w) if i else c.ljust(w) for i, (c, w) in enumerate(cells)))
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--rounds", type=int, default=DEFAULT_ROUNDS)
    ap.add_argument("--stride", type=int, default=1)
    ap.add_argument("--top", type=int, default=30)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    case = gated_case(args.rounds)
    built = machine()
    grid, prof, ticks = profile(case, stride=args.stride)
    labels = cell_labels(grid, built)
    rooms = room_labels(grid, built)
    pipes = pipe_names(grid, built)
    rows = grid.grid

    total = sum(prof.heat.values())
    waiting = sum(prof.wait.values())

    # ── per region ──────────────────────────────────────────────────────────
    by_region: dict[str, list[int]] = {}
    for cell, n in prof.heat.items():
        entry = by_region.setdefault(labels.get(cell, "outside"), [0, 0])
        entry[0] += n
        entry[1] += prof.wait.get(cell, 0)
    region_rows = sorted(by_region.items(), key=lambda kv: -kv[1][0])

    # ── per man (one compute room = one runner; the grid has no `Y`) ────────
    by_room: dict[int, list[int]] = {}
    hottest: dict[int, tuple[int, tuple[int, int]]] = {}
    for rid, room in enumerate(grid.rooms):
        for cell, n in prof.heat.items():
            if room.contains(cell):
                e = by_room.setdefault(rid, [0, 0])
                e[0] += n
                e[1] += prof.wait.get(cell, 0)
                if n > hottest.get(rid, (0, (0, 0)))[0]:
                    hottest[rid] = (n, cell)
    man_rows = sorted(by_room.items(), key=lambda kv: -(kv[1][0] - kv[1][1]))

    # ── the pipe a man is parked on, charged to the man ─────────────────────
    parked: dict[str, int] = {}
    for cell, n in prof.wait.items():
        binding = grid._bindings.get(cell)
        ids = binding if isinstance(binding, tuple) else (binding,)
        for pid in ids:
            if isinstance(pid, int) and pid >= 0:
                key = f"{pipes[pid]} @{cell} {rows[cell[1]][cell[0]]!r}"
                parked[key] = parked.get(key, 0) + n

    # ── the headline: only one man is on the critical path ──────────────────
    # 87% of all runner-ticks are "parked", but sixteen of the eighteen men are
    # servants idling on an `r` until work arrives, and their idling costs
    # nothing.  The CPU's parked time is the only parked time that is a cost.
    cpu = rooms.index("cpu")
    cpu_parked: dict[str, int] = {}
    for cell, n in prof.wait.items():
        if not grid.rooms[cpu].contains(cell):
            continue
        binding = grid._bindings.get(cell)
        for pid in binding if isinstance(binding, tuple) else (binding,):
            if isinstance(pid, int) and pid >= 0:
                cpu_parked[pipes[pid]] = cpu_parked.get(pipes[pid], 0) + n
    worst = max(cpu_parked.items(), key=lambda kv: kv[1]) if cpu_parked else ("-", 0)

    head = (
        f"deadman-3d_taped {grid.width}x{grid.height} — native fast_littleman (Python engine's "
        f"C++ backend), gated {case.rounds}-round case WALK[:{args.rounds}]\n"
        f"{ticks:,} ticks, {prof.samples:,} samples @ stride {args.stride}, "
        f"{len(prof.heat):,} distinct cells occupied\n"
        f"{total:,} runner-ticks total, {waiting:,} of them parked on a pipe "
        f"({100 * waiting / max(1, total):.1f}%) — but only the CPU man is on the critical path.\n"
        f"CPU: {sum(cpu_parked.values()):,} ticks blocked "
        f"({100 * sum(cpu_parked.values()) / ticks:.2f}% of the run), "
        f"{ticks - sum(cpu_parked.values()):,} walking his own dispatch "
        f"({100 * (ticks - sum(cpu_parked.values())) / ticks:.2f}%).\n"
        f"Worst single block: {worst[0]} — {worst[1]:,} ticks, {100 * worst[1] / ticks:.2f}% "
        f"of the whole run."
    )

    t_region = table(
        f"\ntop {args.top} regions by runner-ticks (Machine.regions)",
        ["region", "runner-ticks", "%all", "parked", "%parked", "working", "%work"],
        [
            [
                name,
                f"{n:,}",
                f"{100 * n / max(1, total):.2f}%",
                f"{w:,}",
                f"{100 * w / max(1, n):.0f}%",
                f"{n - w:,}",
                f"{100 * (n - w) / max(1, total - waiting):.2f}%",
            ]
            for name, (n, w) in region_rows[: args.top]
        ],
        [26, 14, 7, 14, 8, 13, 7],
    )
    # The CPU man is alive for every tick of the run, so his own region split
    # *is* the run's tick budget — this is the table that answers "where first".
    by_cpu_region: dict[str, list[int]] = {}
    for cell, n in prof.heat.items():
        if not grid.rooms[cpu].contains(cell):
            continue
        e = by_cpu_region.setdefault(labels.get(cell, "cpu:other"), [0, 0])
        e[0] += n
        e[1] += prof.wait.get(cell, 0)
    cpu_regions = sorted(((k, v[0], v[1]) for k, v in by_cpu_region.items()), key=lambda t: -t[1])
    t_cpu = table(
        "\nTHE RUN'S TICK BUDGET — the CPU man's own time by region "
        f"(he is alive for all {ticks:,} ticks, so these are ticks of the run)",
        ["region", "ticks", "%run", "blocked", "%run", "walking", "%run"],
        [
            [
                name,
                f"{n:,}",
                f"{100 * n / ticks:.2f}%",
                f"{w:,}",
                f"{100 * w / ticks:.2f}%",
                f"{n - w:,}",
                f"{100 * (n - w) / ticks:.2f}%",
            ]
            for name, n, w in cpu_regions
        ],
        [24, 13, 7, 13, 7, 13, 7],
    )
    t_man = table(
        "\nper man (one compute room holds exactly one runner here)",
        ["room", "runner-ticks", "parked", "%parked", "working", "%work", "hottest cell"],
        [
            [
                f"{rooms[rid]} #{rid}",
                f"{n:,}",
                f"{w:,}",
                f"{100 * w / max(1, n):.0f}%",
                f"{n - w:,}",
                f"{100 * (n - w) / max(1, total - waiting):.2f}%",
                (
                    f"{hottest[rid][1]} {rows[hottest[rid][1][1]][hottest[rid][1][0]]!r}"
                    f" {100 * hottest[rid][0] / max(1, n):.0f}%"
                ),
            ]
            for rid, (n, w) in man_rows
        ],
        [24, 14, 14, 8, 13, 7, 26],
    )
    t_cell = table(
        f"\ntop {args.top} single cells (a tall bar on an `r` is a man blocked there)",
        ["cell", "glyph", "region", "runner-ticks", "%all", "parked"],
        [
            [
                f"({x},{y})",
                repr(rows[y][x]),
                labels.get((x, y), "?"),
                f"{n:,}",
                f"{100 * n / max(1, total):.2f}%",
                f"{prof.wait.get((x, y), 0):,}",
            ]
            for (x, y), n in sorted(prof.heat.items(), key=lambda kv: -kv[1])[: args.top]
        ],
        [12, 6, 26, 14, 7, 14],
    )
    t_parked = table(
        f"\ntop {args.top} parks: which pipe each blocked man is waiting on",
        ["pipe (src->dst)", "ticks parked", "%all"],
        [
            [k, f"{n:,}", f"{100 * n / max(1, total):.2f}%"]
            for k, n in sorted(parked.items(), key=lambda kv: -kv[1])[: args.top]
        ],
        [58, 14, 7],
    )

    text = "\n".join([head, t_cpu, t_region, t_man, t_cell, t_parked, ""])
    print(text)
    (args.out / "heatmap.txt").write_text(text, encoding="utf-8")

    render(rows, prof.heat, BLUE, args.out / "heat.png")
    render(rows, prof.wait, ORANGE, args.out / "wait.png")
    (args.out / "heatmap.json").write_text(
        json.dumps(
            {
                "grid": [grid.width, grid.height],
                "ticks": ticks,
                "samples": prof.samples,
                "stride": args.stride,
                "rounds": case.rounds,
                "total_runner_ticks": total,
                "parked_runner_ticks": waiting,
                "regions": {k: {"ticks": v[0], "parked": v[1]} for k, v in region_rows},
                "rooms": {
                    f"{rooms[rid]}#{rid}": {"ticks": v[0], "parked": v[1]} for rid, v in man_rows
                },
                "cells": {f"{x},{y}": n for (x, y), n in prof.heat.items()},
                "parked_on": parked,
            }
        ),
        encoding="utf-8",
    )
    write_html(
        args.out, head, region_rows, man_rows, rooms, parked, total, waiting, prof, rows,
        ticks, cpu_parked, worst, cpu_regions,
    )
    print(f"wrote {args.out}/heat.png, wait.png, heatmap.txt, heatmap.json, index.html")
    return 0


def write_html(
    out: Path,
    head: str,
    region_rows,
    man_rows,
    rooms,
    parked,
    total: int,
    waiting: int,
    prof,
    grid_rows,
    ticks: int,
    cpu_parked: dict[str, int],
    cpu_worst: tuple[str, int],
    cpu_regions,
) -> None:
    def img(name: str) -> str:
        return base64.b64encode((out / name).read_bytes()).decode("ascii")

    def bar(n: int, denom: int, colour: str) -> str:
        pct = 100 * n / max(1, denom)
        return (
            f'<td class="num">{n:,}</td><td class="barcell">'
            f'<span class="bar" style="width:{min(100, pct):.2f}%;background:{colour}"></span>'
            f'<span class="pct">{pct:.2f}%</span></td>'
        )

    cpu_html = "".join(
        f"<tr><th>{html.escape(name)}</th>{bar(n, ticks, '#3987e5')}{bar(w, ticks, '#eb6834')}</tr>"
        for name, n, w in cpu_regions
    )
    region_html = "".join(
        f"<tr><th>{html.escape(name)}</th>{bar(n, total, '#3987e5')}"
        f"{bar(w, max(1, n), '#eb6834')}</tr>"
        for name, (n, w) in region_rows[:30]
    )
    man_html = "".join(
        f"<tr><th>{html.escape(rooms[rid])} <span class=dim>#{rid}</span></th>"
        f"{bar(n - w, max(1, total - waiting), '#3987e5')}{bar(w, max(1, n), '#eb6834')}</tr>"
        for rid, (n, w) in man_rows
    )
    park_html = "".join(
        f"<tr><th>{html.escape(k)}</th>{bar(n, total, '#eb6834')}</tr>"
        for k, n in sorted(parked.items(), key=lambda kv: -kv[1])[:20]
    )
    legend = "".join(
        f'<span class="sw" style="background:{c}"></span>' for c in BLUE
    )
    legend_o = "".join(
        f'<span class="sw" style="background:{c}"></span>' for c in ORANGE
    )
    page = f"""<title>deadman-3d_taped — where the men stand</title>
<style>
 :root {{ --ink:#1a1a19; --ink2:#5b5b57; --sur:#fcfcfb; --line:#e2e2dd; --panel:#f5f5f2; }}
 @media (prefers-color-scheme: dark) {{
   :root {{ --ink:#eeeeea; --ink2:#a3a39c; --sur:#1a1a19; --line:#333330; --panel:#232320; }}
 }}
 :root[data-theme="dark"] {{ --ink:#eeeeea; --ink2:#a3a39c; --sur:#1a1a19; --line:#333330; --panel:#232320; }}
 :root[data-theme="light"] {{ --ink:#1a1a19; --ink2:#5b5b57; --sur:#fcfcfb; --line:#e2e2dd; --panel:#f5f5f2; }}
 body {{ background:var(--sur); color:var(--ink); font:14px/1.5 ui-sans-serif,-apple-system,Segoe UI,sans-serif;
        margin:0 auto; padding:32px 20px 64px; max-width:1180px; }}
 h1 {{ font-size:22px; margin:0 0 4px; }}
 h2 {{ font-size:15px; margin:40px 0 8px; letter-spacing:.02em; }}
 pre.head {{ color:var(--ink2); font:12px/1.6 ui-monospace,SFMono-Regular,Menlo,monospace;
             white-space:pre-wrap; margin:0 0 8px; }}
 .hero {{ display:flex; gap:28px; flex-wrap:wrap; margin:20px 0 8px; }}
 .stat {{ background:var(--panel); border:1px solid var(--line); border-radius:8px; padding:12px 16px; }}
 .stat b {{ display:block; font-size:24px; font-variant-numeric:tabular-nums; }}
 .stat span {{ color:var(--ink2); font-size:12px; }}
 figure {{ margin:0 0 16px; overflow-x:auto; }}
 img {{ max-width:100%; image-rendering:pixelated; border:1px solid var(--line); border-radius:4px; }}
 figcaption {{ color:var(--ink2); font-size:12px; margin-top:6px; }}
 .sw {{ display:inline-block; width:22px; height:10px; }}
 table {{ border-collapse:collapse; width:100%; font-variant-numeric:tabular-nums; }}
 th, td {{ text-align:left; padding:3px 8px; border-bottom:1px solid var(--line); font-weight:400; }}
 thead th {{ color:var(--ink2); font-size:12px; border-bottom:1px solid var(--ink2); }}
 th {{ font-family:ui-monospace,Menlo,monospace; font-size:12px; }}
 td.num {{ text-align:right; width:120px; }}
 td.barcell {{ width:38%; }}
 .bar {{ display:inline-block; height:9px; border-radius:2px; vertical-align:middle; }}
 .pct {{ color:var(--ink2); font-size:11px; margin-left:6px; }}
 .dim {{ color:var(--ink2); }}
 .wrap {{ overflow-x:auto; }}
</style>
<h1>deadman-3d_taped — where the men stand</h1>
<pre class="head">{html.escape(head)}</pre>
<div class="hero">
  <div class="stat"><b>{100 * cpu_worst[1] / ticks:.2f}%</b><span>of the run: the CPU man blocked<br>on <code>{html.escape(cpu_worst[0])}</code></span></div>
  <div class="stat"><b>{100 * sum(cpu_parked.values()) / ticks:.1f}%</b><span>the CPU blocked on any pipe</span></div>
  <div class="stat"><b>{100 * (ticks - sum(cpu_parked.values())) / ticks:.1f}%</b><span>the CPU walking his own dispatch</span></div>
  <div class="stat"><b>{100 * waiting / max(1, total):.1f}%</b><span>of <i>all</i> runner-ticks parked &mdash; but<br>sixteen of eighteen men are idle servants</span></div>
</div>
<h2>The run's tick budget &mdash; the CPU man's own time, by region</h2>
<p class="dim">He is alive for every one of the {ticks:,} ticks, so his split <i>is</i> the run.</p>
<div class="wrap"><table><thead><tr><th>region</th><th>ticks</th><th>share of the run</th><th>blocked</th><th>share of the run</th></tr></thead><tbody>{cpu_html}</tbody></table></div>
<h2>Occupancy — every live man's cell, sampled every tick</h2>
<figure><img alt="occupancy heatmap" src="data:image/png;base64,{img('heat.png')}">
<figcaption>Log ramp, light&rarr;dark {legend} &nbsp;1 &rarr; {max(prof.heat.values()):,} runner-ticks.
Ringed: the eight hottest cells &mdash; intensity ranks, but area lies, so the rings say where to look.
Blocked men are the point: a man parked on an <code>r</code> is counted every sample.</figcaption></figure>
<h2>Blocked only — the parked-on-a-pipe subset</h2>
<figure><img alt="blocked heatmap" src="data:image/png;base64,{img('wait.png')}">
<figcaption>Log ramp {legend_o} &nbsp;1 &rarr; {max(prof.wait.values(), default=0):,} runner-ticks parked.</figcaption></figure>
<h2>All regions, ranked by runner-ticks (every man)</h2>
<div class="wrap"><table><thead><tr><th>region</th><th>runner-ticks</th><th>share of all</th>
<th>parked</th><th>share of the region</th></tr></thead><tbody>{region_html}</tbody></table></div>
<h2>Per man — working ticks (parked time removed)</h2>
<div class="wrap"><table><thead><tr><th>room</th><th>working ticks</th><th>share of all work</th>
<th>parked</th><th>share of his own time</th></tr></thead><tbody>{man_html}</tbody></table></div>
<h2>Which pipe each blocked man is waiting on</h2>
<div class="wrap"><table><thead><tr><th>pipe @ cell</th><th>ticks parked</th><th>share of all runner-ticks</th>
</tr></thead><tbody>{park_html}</tbody></table></div>
"""
    (out / "index.html").write_text(page, encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
