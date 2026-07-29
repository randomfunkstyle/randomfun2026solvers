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


def cpu_block(grid: FastLittleman, built, prof, ticks: int) -> None:
    """Print what the CPU man is parked on, by pipe."""
    labels = room_labels(grid, built)
    names = [f"{labels[p.src]}->{labels[p.dst]}" for p in grid.pipes]
    cpu = labels.index("cpu")
    parked: dict[int, int] = {}
    for cell, n in prof.wait.items():
        if not grid.rooms[cpu].contains(cell):
            continue
        binding = grid._bindings.get(cell)
        for pid in binding if isinstance(binding, tuple) else (binding,):
            if isinstance(pid, int) and pid >= 0:
                parked[pid] = parked.get(pid, 0) + n
    total = sum(parked.values())
    print(f"\n  the CPU is blocked on — of {ticks:,} ticks:")
    for pid, n in sorted(parked.items(), key=lambda kv: -kv[1]):
        print(f"    {names[pid]:<32}{n:>14,}{100 * n / ticks:>8.2f}%")
    print(f"    {'blocked, all pipes':<32}{total:>14,}{100 * total / ticks:>8.2f}%")
    print(f"    {'walking his own dispatch':<32}{ticks - total:>14,}"
          f"{100 * (ticks - total) / ticks:>8.2f}%")
    painter = sum(n for pid, n in parked.items() if "stream" in names[pid])
    print(f"\n  >>> painter backpressure (every cpu->stream* pipe): "
          f"{painter:,} ticks = {100 * painter / ticks:.3f}% of the run")


def run_64(rounds: int) -> None:
    from randomfun2026solvers import deadman3d as d3
    from randomfun2026solvers.lm1 import machine as M

    ex = REPO / "littleman" / "examples"
    words = [int(w) for w in (ex / "deadman-3d_tour.input.txt").read_text().split()]
    boot = d3.preamble_words() + d3.title_words()
    cmds = words[len(boot):][:rounds]
    case = d3.cases_json(cmds)["publicTestData"][0]
    inp = " ".join(w for r in case["rounds"] for w in r["in"])
    built = M.build_for("deadman-3d", store="taped")
    src = "\n".join(built.rows)
    assert src + "\n" == (ex / "deadman-3d_taped.man").read_text(), "taped grid drift"
    grid = FastLittleman(src)
    t0 = time.time()
    res = grid.run(inp, max_ticks=6_000_000_000, profile=True, profile_stride=1)
    print(f"64x48 taped {built.width}x{built.height}: {len(cmds)} cmds, "
          f"ticks={res.step:,} fatal={res.fatal} ({time.time()-t0:.0f}s)")
    cpu_block(grid, built, res.profile, res.step)


def run_hires(rounds: int) -> None:
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
    print(f"hires 128x96 {m.width}x{m.height}: {rounds} cmds, built in "
          f"{time.time()-t0:.0f}s", flush=True)
    grid = FastLittleman(src)
    t0 = time.time()
    res = grid.run(inp, max_ticks=40_000_000_000, profile=True, profile_stride=1)
    print(f"  ticks={res.step:,} fatal={res.fatal} ({time.time()-t0:.0f}s)")
    cpu_block(grid, m, res.profile, res.step)


if __name__ == "__main__":
    which = sys.argv[1] if len(sys.argv) > 1 else "64"
    n = int(sys.argv[2]) if len(sys.argv) > 2 else 8
    (run_hires if which == "hires" else run_64)(n)
