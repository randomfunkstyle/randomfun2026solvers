#!/usr/bin/env python3
"""Build + profile a list of knob settings in one process.

For each trial: build ``deadman-3d_hires`` taped with the knobs, write the grid
to /tmp, then run the short gated tour with **exact per-opcode attribution**.
What comes back per trial is the pair the whole question turns on:

* ``walk``  -- cells the CPU man stepped on, per instruction (a pure geometry
  number: every opcode's per-execution walk is an integer);
* ``blk``   -- ticks he spent parked on a pipe, per instruction.

A router can only move ``walk``.  Whether moving it moves ``t/instr`` is the
thing this sweep is here to find out, and it is not assumed anywhere.

Usage:  python sweep.py <rounds> '<json list of knob dicts>' [out.jsonl]
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
sys.path.insert(0, str(REPO / "solvers" / "python"))
sys.path.insert(0, str(REPO / "scratch"))
sys.path.insert(0, str(HERE))

import build_taped as B  # noqa: E402
import profile_taped as P  # noqa: E402


def tags_for(grid, regions):
    from randomfun2026solvers.fast_littleman import OpcodeTags
    from doom_case import cell_labels, room_labels

    class _B:
        pass

    b = _B()
    b.regions = regions
    labels = cell_labels(grid, b)
    rooms = room_labels(grid, b)
    room = grid.rooms[rooms.index("cpu")]
    drops = P.drop_cells(grid.grid, regions)
    ops = sorted({n.rsplit(":", 1)[1] for n in regions if n.startswith("cpu:lane:")})
    oi = {n: i for i, n in enumerate(ops)}
    classes = list(P.CLASSES)
    tags = {}
    for (x, y), label in labels.items():
        if not room.contains((x, y)):
            continue
        cls = classes.index(P.classify(label))
        opc = -1
        if label.startswith(("cpu:lane:", "cpu:slab:", "cpu:discard:", "cpu:entry:",
                             "cpu:riser:")):
            opc = oi.get(label.rsplit(":", 1)[1], -1)
        vert = (cls, opc)
        if (x, y) in drops and label.startswith("cpu:lane:"):
            vert = (classes.index("drop"), -1)
        for d in (0, 2):
            tags[(x, y, d)] = (cls, opc)
        for d in (1, 3):
            tags[(x, y, d)] = vert
    return OpcodeTags(classes=classes, ops=ops, tags=tags, boundary=P.BOUNDARY)


def main():
    from randomfun2026solvers import deadman3d_hires as hires
    from randomfun2026solvers.fast_littleman import FastLittleman

    nwalk = int(sys.argv[1])
    trials = json.loads(sys.argv[2])
    outp = Path(sys.argv[3]) if len(sys.argv) > 3 else Path("/tmp/taped_sweep.jsonl")

    _, _, M, prog = B.setup()
    print(f"shipped={B.shipped(M)}  {len(trials)} trials", flush=True)
    case = hires.cases_json(list(hires.WALK[:nwalk]))["publicTestData"][0]
    inp = " / ".join(" ".join(r["in"]) for r in case["rounds"])
    frames = [r["frames"] for r in case["rounds"]]

    fh = outp.open("a")
    for i, kn in enumerate(trials):
        t0 = time.time()
        rec = {"knobs": kn}
        try:
            m, cpu, secs = B.build(M, prog, kn, gate=kn.pop("_nogate", False) is False)
        except Exception as e:  # noqa: BLE001 - the refusal is the measurement
            rec["error"] = f"{type(e).__name__}: {e}"
            print(f"[{i + 1}/{len(trials)}] {kn} BUILD-FAIL {rec['error'][:150]}", flush=True)
            fh.write(json.dumps(rec) + "\n")
            fh.flush()
            continue
        man = Path(f"/tmp/sw_{i}.man")
        man.write_text("\n".join(m.rows) + "\n", encoding="utf-8")
        rec.update(w=m.width, h=m.height, mem_pad=m.mem_pad, build_s=secs)
        geo = B.cpu_geometry(m, cpu)
        rec["lanes"] = geo["lanes"]
        rec["cpu_regions"] = geo["regions"]
        try:
            grid = FastLittleman(man)
            spec = tags_for(grid, {k: tuple(v) for k, v in m.regions.items()})
            res = grid.run(inp, frames=frames, frame_tiles=(2, 2),
                           max_ticks=2_000_000_000, profile=True, profile_stride=1,
                           opcodes=spec)
            op = res.opcodes
            n = sum(op.execs)
            tot = sum(sum(r) for r in op.ticks)
            blk = sum(sum(r) for r in op.blocked)
            rec.update(ticks=res.step, passed=res.passed, fatal=res.fatal, instrs=n,
                       walk_per=(tot - blk) / n, blk_per=blk / n, t_per=tot / n,
                       ops=op.ops, execs=op.execs,
                       op_walk=[[op.ticks[j][c] - op.blocked[j][c]
                                 for c in range(len(op.classes))]
                                for j in range(len(op.execs))],
                       op_blk=[sum(op.blocked[j]) for j in range(len(op.execs))],
                       classes=op.classes)
            print(f"[{i + 1}/{len(trials)}] {kn} {m.width}x{m.height} pad={m.mem_pad} "
                  f"ticks={res.step:,} n={n:,} t/i={tot / n:.3f} "
                  f"walk={(tot - blk) / n:.3f} blk={blk / n:.3f} "
                  f"passed={res.passed} ({time.time() - t0:.0f}s)", flush=True)
        except Exception as e:  # noqa: BLE001
            rec["run_error"] = f"{type(e).__name__}: {e}"
            print(f"[{i + 1}/{len(trials)}] {kn} RUN-FAIL {rec['run_error'][:200]}",
                  flush=True)
        fh.write(json.dumps(rec) + "\n")
        fh.flush()
    fh.close()


if __name__ == "__main__":
    main()
