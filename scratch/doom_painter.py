"""Is the DOOM painter on the critical path — at 64x48, and at 128x96?

The one question a "draw in runs" change turns on: how much of the run is the
CPU standing still on `cpu->stream:unit` waiting for the painter to take a
command word.  Everything else about the painter is concurrent with the next
raycast and therefore free.

Both tiers are run **ungated** here, deliberately:

* hires *cannot* be gated — the engine's display judge wants exactly one display
  and the 128x96 machine has four (`scratch/deadman3d-opt/hires_gate2.py`);
* and ungated is the **upper bound** on painter backpressure.  With every round's
  input already available the CPU never parks at `IN`, so it produces command
  words as fast as it ever can and the unit has the least slack it will ever
  have.  Gating can only lower the number.

The 64x48 tier is run the same way so the two are comparable, and its gated
figure is known (0.55%, `scratch/DOOM-PROFILE.md` §3) as a calibration.

    uv run python scratch/doom_painter.py 64      # the shipped taped machine
    uv run python scratch/doom_painter.py hires   # builds 128x96 from the IWAD
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "solvers" / "python"))
sys.path.insert(0, str(REPO / "scratch"))

from doom_case import room_labels  # noqa: E402
from randomfun2026solvers.fast_littleman import FastLittleman  # noqa: E402

IWAD = Path(os.environ.get("DEADMAN3D_IWAD", "")) if os.environ.get("DEADMAN3D_IWAD") \
    else Path.home() / "Downloads" / "doom1_0" / "DOOM1.WAD"

#: Wait-sampling stride.  The quantity wanted here is a *fraction of the run*
#: over tens of millions of ticks, so a coarse stride is exact enough (it is an
#: unbiased sample of where the men stand) and stride 1 on a 500x348 grid is
#: hours.  `DOOM-PROFILE.md`'s 0.55% is the stride-1 figure to check against.
STRIDE = int(os.environ.get("DOOM_PROFILE_STRIDE", "64"))


def cpu_block(grid: FastLittleman, built, prof, ticks: int) -> None:
    """Print what the CPU man is parked on, by pipe."""
    labels = room_labels(grid, built)
    names = [f"{labels[p.src]}->{labels[p.dst]}" for p in grid.pipes]
    cpu = labels.index("cpu")
    # `room_labels` only knows the 64x48 stream room names; on the wall the
    # painter sits behind the router and comes back as `roomN`.  Name it from
    # the builder's own regions rather than guessing, so "is this the painter
    # pipe?" is never decided by a substring.
    for rid, room in enumerate(grid.rooms):
        if not labels[rid].startswith("room"):
            continue
        covering = [
            name for name, (rx, ry, rw, rh) in built.regions.items()
            if rx <= room.min[0] and ry <= room.min[1]
            and room.max[0] <= rx + rw and room.max[1] <= ry + rh
        ]
        if covering:
            labels[rid] = min(covering, key=len) + f"[{labels[rid]}]"
    names = [f"{labels[p.src]}->{labels[p.dst]}" for p in grid.pipes]
    parked: dict[int, int] = {}
    for cell, n in prof.wait.items():
        if not grid.rooms[cpu].contains(cell):
            continue
        binding = grid._bindings.get(cell)
        for pid in binding if isinstance(binding, tuple) else (binding,):
            if isinstance(pid, int) and pid >= 0:
                parked[pid] = parked.get(pid, 0) + n
    # `prof.wait` counts *samples*, not ticks: one per sampled tick, so at
    # stride N each entry stands for N ticks.  (doom_pipes.py divides by `ticks`
    # directly because it runs at stride 1, where the two coincide — the
    # distinction is invisible there and silently divides by 64 here.)
    parked = {pid: n * STRIDE for pid, n in parked.items()}
    total = sum(parked.values())
    print(f"\n  the CPU is blocked on — of {ticks:,} ticks "
          f"(sampled every {STRIDE}):")
    for pid, n in sorted(parked.items(), key=lambda kv: -kv[1]):
        print(f"    {names[pid]:<32}{n:>14,}{100 * n / ticks:>8.2f}%")
    print(f"    {'blocked, all pipes':<32}{total:>14,}{100 * total / ticks:>8.2f}%")
    print(f"    {'walking his own dispatch':<32}{ticks - total:>14,}"
          f"{100 * (ticks - total) / ticks:>8.2f}%")
    # The painter path by elimination: every pipe *out of* the CPU that is not
    # the store's request leg, the ROM or the input.
    painter = sum(
        n for pid, n in parked.items()
        if grid.pipes[pid].src == cpu
        and not any(k in names[pid] for k in ("adapter", "store", "rom", "input"))
    )
    print(f"\n  >>> painter backpressure (every cpu->stream* pipe): "
          f"{painter:,} ticks = {100 * painter / ticks:.3f}% of the run "
          f"({painter // STRIDE:,} samples, +/-{100 / max(1, painter // STRIDE) ** 0.5:.1f}% rel)")


def run_64(rounds: int, budget: int) -> None:
    """Gated, exactly as `doom_case.profile` runs it — 64x48 has one display, so
    the judge will gate it and this is directly comparable to `DOOM-PROFILE.md`."""
    from randomfun2026solvers import deadman3d as d3
    from randomfun2026solvers.lm1 import machine as M

    ex = REPO / "littleman" / "examples"
    words = [int(w) for w in (ex / "deadman-3d_tour.input.txt").read_text().split()]
    boot = d3.preamble_words() + d3.title_words()
    cmds = words[len(boot):][:rounds]
    case = d3.cases_json(cmds)["publicTestData"][0]
    inp = " / ".join(" ".join(r["in"]) for r in case["rounds"])
    built = M.build_for("deadman-3d", store="taped")
    src = "\n".join(built.rows)
    assert src + "\n" == (ex / "deadman-3d_taped.man").read_text(), "taped grid drift"
    grid = FastLittleman(src)
    t0 = time.time()
    res = grid.run(inp, frames=[r["frames"] for r in case["rounds"]],
                   max_ticks=budget, profile=True, profile_stride=STRIDE)
    print(f"64x48 taped {built.width}x{built.height} GATED: {len(cmds)} cmds, "
          f"ticks={res.step:,} passed={res.passed} fatal={res.fatal} "
          f"({time.time()-t0:.0f}s)")
    cpu_block(grid, built, res.profile, res.step)


def run_hires(rounds: int, budget: int) -> None:
    """Ungated and **tick-bounded**: the run is deliberately cut off inside the
    demo at `budget` ticks.

    Two reasons.  Gating is impossible (four displays), and an ungated run that
    reaches the end of its input does not stop — the CPU parks on `IN` and the
    engine keeps ticking, so letting it run to a large cap measures the idle
    tail rather than the demo (that mistake produced a 0.000% painter figure on
    the 64x48 tier and 98.4% "walking his own dispatch", which is the tail).
    Cutting inside the demo samples steady-state rendering only.
    """
    import tempfile

    from randomfun2026solvers import deadman3d_hires as hires

    if not IWAD.is_file():
        raise SystemExit(f"no IWAD at {IWAD}")
    t0 = time.time()
    cmds = list(hires.WALK[:rounds])
    with tempfile.TemporaryDirectory() as tmp:
        built = hires.build_local(IWAD, Path(tmp), cmds, pngs=False)
    m = built["machine"]
    inp = " ".join(str(w) for w in hires.input_words(cmds))
    src = "\n".join(m.rows)
    print(f"hires 128x96 {m.width}x{m.height} UNGATED, cut at {budget:,} ticks: "
          f"{rounds} cmds, built in {time.time()-t0:.0f}s", flush=True)
    grid = FastLittleman(src)
    t0 = time.time()
    res = grid.run(inp, max_ticks=budget, profile=True, profile_stride=STRIDE)
    print(f"  ticks={res.step:,} fatal={res.fatal} ({time.time()-t0:.0f}s)")
    cpu_block(grid, m, res.profile, res.step)


if __name__ == "__main__":
    which = sys.argv[1] if len(sys.argv) > 1 else "64"
    n = int(sys.argv[2]) if len(sys.argv) > 2 else 8
    cap = int(sys.argv[3]) if len(sys.argv) > 3 else 6_000_000_000
    (run_hires if which == "hires" else run_64)(n, cap)
