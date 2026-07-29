"""Shared harness for the two DOOM profilers (`doom_pipes.py`, `doom_heatmap.py`).

Why not `littleman/tools/heatmap.mjs`: that profiler drives `littleman.wasm`,
whose Go heap OOMs at 4 GB on this machine — it cannot load deadman-3d at all.
Everything here therefore runs on the *native* `fast_littleman` engine
(`fast_littleman_native.cpp`), instrumented with an opt-in profile mode that is
off for every other caller.

Two properties are carried over from the .mjs profiler deliberately:

* **Positions, not instructions.** A man blocked on `r` stands still; sampling
  where the men *are* counts him every sample, which is the only way a blocking
  hot spot shows up at all.
* **Gated.** deadman-3d is display-judged, so the case is run with `frames`.
  Ungated, the judge hands every round over at once and the profile measures a
  jam instead of the program.

Room and region names come from the *builder* (`lm1.machine.build_for`), and
the harness asserts the builder reproduces the checked-in grid byte for byte,
so every coordinate in a report provably belongs to the machine being profiled.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "solvers" / "python"))

from randomfun2026solvers import deadman3d as d3  # noqa: E402
from randomfun2026solvers.fast_littleman import FastLittleman, FastProfile  # noqa: E402
from randomfun2026solvers.lm1 import machine as lm1_machine  # noqa: E402

TAPED = REPO / "littleman" / "examples" / "deadman-3d_taped.man"
DEFAULT_ROUNDS = 8  # WALK[:8] -> a 9-round case (boot + 8 moves)


@dataclass(frozen=True)
class Case:
    input: str
    frames: list[list[list[str]]]
    rounds: int


def gated_case(walk_len: int = DEFAULT_ROUNDS) -> Case:
    """The reproducible gated case: boot plus `walk_len` demo moves."""
    case = d3.cases_json(d3.WALK[:walk_len])["publicTestData"][0]
    return Case(
        input=" / ".join(" ".join(r["in"]) for r in case["rounds"]),
        frames=[r["frames"] for r in case["rounds"]],
        rounds=len(case["rounds"]),
    )


def machine() -> lm1_machine.Machine:
    """The builder's machine, pinned to the checked-in grid."""
    built = lm1_machine.build_for("deadman-3d", store="taped")
    if "\n".join(built.rows) + "\n" != TAPED.read_text(encoding="utf-8"):
        raise SystemExit(f"{TAPED} is not what build_for('deadman-3d', store='taped') makes")
    return built


def profile(
    case: Case,
    *,
    stride: int = 1,
    max_ticks: int = 400_000_000,
) -> tuple[FastLittleman, FastProfile, int]:
    """Run the taped machine gated, with profiling on.  Returns (grid, profile, ticks)."""
    grid = FastLittleman(TAPED)
    result = grid.run(
        case.input,
        frames=case.frames,
        max_ticks=max_ticks,
        profile=True,
        profile_stride=stride,
    )
    if result.fatal or result.passed is False:
        raise SystemExit(f"run did not pass: reason={result.reason} fatal={result.fatal}")
    assert result.profile is not None
    return grid, result.profile, result.step


# ── naming ──────────────────────────────────────────────────────────────────
# Room labels are derived from the builder's regions plus the pipe topology, so
# they survive a re-layout: nothing here hard-codes a coordinate or a room id.


def room_labels(grid: FastLittleman, built: lm1_machine.Machine) -> list[str]:
    regions = built.regions
    rooms = grid.rooms

    def smallest_region(rid: int) -> str | None:
        room = rooms[rid]
        best: tuple[int, str] | None = None
        for name, (rx, ry, rw, rh) in regions.items():
            if (
                rx <= room.min[0]
                and ry <= room.min[1]
                and room.max[0] <= rx + rw
                and room.max[1] <= ry + rh
            ):
                area = rw * rh
                if best is None or area < best[0]:
                    best = (area, name)
        return None if best is None else best[1]

    labels: list[str | None] = [None] * len(rooms)
    for rid, room in enumerate(rooms):
        if room.kind == "input":
            labels[rid] = "input"
        elif room.kind == "output":
            labels[rid] = "output"
        elif room.kind == "display":
            labels[rid] = "display"
        else:
            region = smallest_region(rid)
            if region in ("rom", "adapter", "stream:unit", "stream:relay", "stream:relay2"):
                labels[rid] = region

    cpu = next(rid for rid, r in enumerate(rooms) if r.contains(regions["cpu:fetch"][:2]))
    labels[cpu] = "cpu"

    # The store tier, read off its own wiring: the collector is the room that
    # answers the CPU; a bank is anything that feeds the collector; the rest are
    # the request relays (they feed banks) and the ring legs (fed by banks).
    to_cpu = [p for p in grid.pipes if p.dst == cpu and labels[p.src] is None]
    collector = to_cpu[0].src if to_cpu else -1
    if collector >= 0:
        labels[collector] = "store:collector"
    banks = sorted(
        {p.src for p in grid.pipes if p.dst == collector and p.src != cpu},
        key=lambda rid: (rooms[rid].min[0], rooms[rid].min[1]),
    )
    for i, rid in enumerate(banks):
        labels[rid] = f"store:bank{i}"
    bank_index = {rid: i for i, rid in enumerate(banks)}
    for rid in range(len(rooms)):
        if labels[rid] is not None:
            continue
        feeds = [bank_index[p.dst] for p in grid.pipes if p.src == rid and p.dst in bank_index]
        fed_by = [bank_index[p.src] for p in grid.pipes if p.dst == rid and p.src in bank_index]
        if fed_by:
            labels[rid] = f"store:ring->bank{fed_by[0]}"
        elif feeds:
            labels[rid] = f"store:req->bank{feeds[0]}"
        else:
            labels[rid] = f"room{rid}"
    return [label or f"room{rid}" for rid, label in enumerate(labels)]


def pipe_names(grid: FastLittleman, built: lm1_machine.Machine) -> list[str]:
    labels = room_labels(grid, built)
    return [f"{labels[p.src]}->{labels[p.dst]}" for p in grid.pipes]


def cell_labels(grid: FastLittleman, built: lm1_machine.Machine) -> dict[tuple[int, int], str]:
    """Every cell a runner can stand on, mapped to the finest name that covers it.

    A region wins over its room, so `cpu:lane:JMPS` beats `cpu`; a room-only
    cell falls back to `<room>:other`, which is itself a finding (it is the
    walking that the named structures do not account for).
    """
    regions = sorted(built.regions.items(), key=lambda kv: kv[1][2] * kv[1][3])
    labels = room_labels(grid, built)
    out: dict[tuple[int, int], str] = {}
    for rid, room in enumerate(grid.rooms):
        # A region no smaller than the room it sits in says nothing the room
        # does not; only finer ones subdivide it.
        area = (room.max[0] - room.min[0]) * (room.max[1] - room.min[1])
        finer = {
            name
            for name, (rx, ry, rw, rh) in regions
            if rw * rh < area
            and rx >= room.min[0]
            and ry >= room.min[1]
            and rx + rw <= room.max[0] + 1
            and ry + rh <= room.max[1] + 1
        }
        for y in range(room.min[1] + 1, room.max[1]):
            for x in range(room.min[0] + 1, room.max[0]):
                name = None
                for rname, (rx, ry, rw, rh) in regions:
                    if rname in finer and rx <= x < rx + rw and ry <= y < ry + rh:
                        name = rname
                        break
                out[(x, y)] = name or (f"{labels[rid]}:other" if finer else labels[rid])
    return out
