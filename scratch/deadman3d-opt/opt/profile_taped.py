#!/usr/bin/env python3
"""Exact per-opcode tick attribution for `deadman-3d_hires` **taped**.

The gate the whole model hangs on: how many ticks per instruction is the CPU
*walking*, and how many is it *blocked*?  ``FastOpProfile`` answers both without
a stride -- it cuts the CPU man's timeline at the fetch and folds each segment
into the lane it entered, so the trie descent and the return walk are charged to
the instruction that caused them.

Unlike ``scratch/doom_opcodes.py`` (which this borrows from) the row bands are
read off ``Machine.regions`` rather than hard-coded, because the hi-res taped CPU
is a different shape: it has **two** return buses, a high one at the top lanes'
foot and the collector proper below.

Everything written goes to /tmp: the grid and the counts are IWAD-derived.

Usage:  python profile_taped.py /tmp/taped_ship <rounds> [out.json]
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "solvers" / "python"))

CLASSES = (
    "fetch",
    "trie",
    "lane",
    "drop",
    "return:high",
    "return:bus",
    "return:riser",
    "slab",
    "return:slab",
    "seek",
)
BOUNDARY = CLASSES.index("fetch")


def classify(label: str) -> str:
    if label == "cpu:fetch":
        return "fetch"
    if label == "cpu:trie":
        return "trie"
    if label.startswith("cpu:lane:"):
        return "lane"
    if label.startswith("cpu:slab:") or label.startswith("cpu:discard:"):
        return "slab"
    if label.startswith("cpu:entry:") or label.startswith("cpu:riser:"):
        return "return:slab"
    if label == "cpu:return:collector":
        return "return:bus"
    if label == "cpu:return:high":
        return "return:high"
    if label == "cpu:return:riser":
        return "return:riser"
    if label.startswith("cpu:seek"):
        return "seek"
    if label == "cpu:drops":
        return "drop"
    return "drop"  # cpu:other -- the descent columns the band misses


def drop_cells(rows, regions):
    """Descent columns, traced from each lane's own exit glyph."""
    out = set()
    for name, (rx, ry, rw, _rh) in regions.items():
        if not name.startswith("cpu:lane:"):
            continue
        exits = [x for x in range(rx, rx + rw) if rows[ry][x] == "v"]
        if not exits:
            continue
        y, x = ry + 1, exits[-1]
        while y < len(rows) and x < len(rows[y]) and rows[y][x] in ".v":
            out.add((x, y))
            y += 1
    return out


def main():
    from randomfun2026solvers import deadman3d as d3
    from randomfun2026solvers import deadman3d_hires as hires
    from randomfun2026solvers.fast_littleman import FastLittleman, OpcodeTags

    sys.path.insert(0, str(REPO / "scratch"))
    from doom_case import cell_labels, room_labels  # noqa: E402

    base = Path(sys.argv[1])
    nwalk = int(sys.argv[2]) if len(sys.argv) > 2 else 3
    outp = Path(sys.argv[3]) if len(sys.argv) > 3 else Path("/tmp/taped_prof.json")

    hires.install_wad(Path.home() / "Downloads" / "doom1_0" / "DOOM1.WAD")
    man = Path(str(base) + ".man")
    geo = json.loads(Path(str(base) + ".geom.json").read_text())

    case = hires.cases_json(list(hires.WALK[:nwalk]))["publicTestData"][0]
    inp = " / ".join(" ".join(r["in"]) for r in case["rounds"])
    frames = [r["frames"] for r in case["rounds"]]
    print(f"rounds={len(frames)}", flush=True)

    grid = FastLittleman(man)

    class _Built:
        regions = {k: tuple(v) for k, v in geo["machine_regions"].items()}

    built = _Built()
    labels = cell_labels(grid, built)
    rooms = room_labels(grid, built)
    cpu = rooms.index("cpu")
    room = grid.rooms[cpu]
    rowtxt = grid.grid
    drops = drop_cells(rowtxt, built.regions)

    ops = sorted({n.rsplit(":", 1)[1] for n in built.regions if n.startswith("cpu:lane:")})
    op_index = {n: i for i, n in enumerate(ops)}
    classes = list(CLASSES)
    tags = {}
    crossed = 0
    for (x, y), label in labels.items():
        if not room.contains((x, y)):
            continue
        cls = classes.index(classify(label))
        opc = -1
        if label.startswith(("cpu:lane:", "cpu:slab:", "cpu:discard:", "cpu:entry:",
                             "cpu:riser:")):
            nm = label.rsplit(":", 1)[1]
            opc = op_index.get(nm, -1)
        vertical = (cls, opc)
        if (x, y) in drops and label.startswith("cpu:lane:"):
            vertical = (classes.index("drop"), -1)
            crossed += 1
        for d in (0, 2):
            tags[(x, y, d)] = (cls, opc)
        for d in (1, 3):
            tags[(x, y, d)] = vertical

    spec = OpcodeTags(classes=classes, ops=ops, tags=tags, boundary=BOUNDARY)
    print(f"tagged {len(tags)} cell-dirs; drops={len(drops)} crossed={crossed}", flush=True)

    t0 = time.time()
    res = grid.run(inp, frames=frames, frame_tiles=(2, 2),
                   max_ticks=2_000_000_000, profile=True,
                   profile_stride=1, opcodes=spec)
    print(f"ticks={res.step:,} passed={res.passed} fatal={res.fatal} "
          f"({time.time() - t0:.0f}s)", flush=True)
    op = res.opcodes
    assert op is not None
    total_ex = sum(op.execs)
    rec = {
        "man": str(man), "rounds": len(frames), "ticks": res.step,
        "passed": res.passed, "fatal": res.fatal,
        "classes": op.classes, "ops": op.ops + ["<unattributed>"],
        "execs": op.execs, "ticks_by_op": op.ticks, "blocked_by_op": op.blocked,
        "outside": op.outside, "multi": op.multi,
        "knobs": geo["knobs"], "shipped": geo["shipped"],
        "grid": [geo["grid_w"], geo["grid_h"]],
    }
    outp.write_text(json.dumps(rec))
    tot_t = sum(sum(r) for r in op.ticks)
    tot_b = sum(sum(r) for r in op.blocked)
    print(f"instructions={total_ex:,}  cpu ticks={tot_t:,}  blocked={tot_b:,}  "
          f"outside={op.outside:,} multi={op.multi:,}", flush=True)
    print(f"  t/instr(cpu) = {tot_t / total_ex:.3f}   "
          f"walk = {(tot_t - tot_b) / total_ex:.3f}   "
          f"blocked = {tot_b / total_ex:.3f}", flush=True)
    print(f"  run t/instr = {res.step / total_ex:.3f}", flush=True)
    cn = [c.replace("return:", "r:") for c in op.classes]
    head = " ".join(f"{c:>7}" for c in cn)
    print(f"\n{'op':>6} {'execs':>9} {'shr%':>6} {'t/ex':>8} {'walk':>7} {'blk':>7} | {head}",
          flush=True)
    for i, name in enumerate(op.ops):
        e = op.execs[i]
        if not e:
            continue
        t = sum(op.ticks[i])
        b = sum(op.blocked[i])
        per = " ".join(f"{(op.ticks[i][c] - op.blocked[i][c]) / e:>7.2f}"
                       for c in range(len(op.classes)))
        print(f"{name:>6} {e:>9,} {100 * e / total_ex:>6.3f} {t / e:>8.2f} "
              f"{(t - b) / e:>7.2f} {b / e:>7.2f} | {per}", flush=True)
    print("\nper class (whole run):", flush=True)
    for c, cname in enumerate(op.classes):
        t = sum(op.ticks[i][c] for i in range(len(op.ticks)))
        b = sum(op.blocked[i][c] for i in range(len(op.blocked)))
        print(f"  {cname:>12} {t:>12,} {100 * t / tot_t:>6.2f}%  blocked {b:>11,}",
              flush=True)


if __name__ == "__main__":
    main()
