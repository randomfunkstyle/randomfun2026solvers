#!/usr/bin/env python3
"""The walk model, and the gate that decides whether it is allowed to be believed.

**The model.** The taped CPU's man makes a monotone round trip and every leg of
it is Manhattan-exact, so one instruction's walk is an integer read straight off
the geometry.  Writing ``c`` for the trie-root row (21), ``H`` for the high
return bus (20), ``C`` for the collector (31) and ``x0`` for ``lane_x0`` (10),
a *simple* lane on row ``y`` whose micro-program occupies ``w`` cells costs

    upper (y < H):   T(y) + w + (H - y - 1) + (x_v - 2 + 1) + 1
    lower (y > H):   T(y) + w + (C - y - 1) + (x_v - 2 + 1) + (C - c)

with ``x_v = x0 + w - 1`` the drop column.  Substituting collapses both to

    upper:  T(y) + 2w + (28 - y)
    lower:  T(y) + 2w + (48 - y)

-- **two ticks per lane column and one per row of distance from the bottom** --
plus a fetch term of ``1 + [the previous instruction returned up the riser]``.
The lone ``+1`` on an upper lane is the trie's own crossing of the high bus at
column 3, which the profiler charges to ``return:high`` and the arithmetic above
folds into the constant.

``T(y)`` -- the trie descent, root to lane -- is *not* derived here.  It is read
off the built machine, because it is what ``_uneven_trie`` decides and the model
has no independent claim on it.

**The gate.** Predicted per-opcode walk against the profiler's measured
per-opcode walk, which is exact (no stride) and an integer per execution.  If a
single simple lane misses, the model is wrong and its optimum is fiction.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

STRUCTURED = {"BRN", "BRZ", "JMPF", "JMPS"}


def load(prof_path, geom_path):
    p = json.loads(Path(prof_path).read_text())
    g = json.loads(Path(geom_path).read_text())
    n = len(p["execs"])
    cls = p["classes"]
    ops = p["ops"][:n]
    walk = {}
    blk = {}
    ex = {}
    per_cls = {}
    for i in range(n):
        nm = ops[i]
        ex[nm] = p["execs"][i]
        walk[nm] = sum(p["ticks_by_op"][i][c] - p["blocked_by_op"][i][c]
                       for c in range(len(cls)))
        blk[nm] = sum(p["blocked_by_op"][i])
        per_cls[nm] = {cls[c]: p["ticks_by_op"][i][c] - p["blocked_by_op"][i][c]
                       for c in range(len(cls))}
    return p, g, ops, ex, walk, blk, per_cls, cls


def anatomy(g):
    """centre, high bus, collector, lane_x0 -- all read off the CPU's own cells."""
    rows = {int(k): v for k, v in g["rows"].items()}
    lanes = {k: tuple(v) for k, v in g["lanes"].items()}
    x0 = min(v[0] for v in lanes.values())
    regs = g["regions"]
    c = regs["fetch"][1]
    H = regs["return:high"][1]
    C = regs["return:collector"][1]
    return rows, lanes, x0, c, H, C


def predict(g, T):
    """Per-opcode predicted walk, excluding fetch, for the simple lanes.

    ``T`` maps opcode -> trie cells, measured (the trie is _uneven_trie's answer,
    not this model's).
    """
    rows, lanes, x0, c, H, C = anatomy(g)
    out = {}
    for nm, (lx, y, w, _h) in lanes.items():
        if nm in STRUCTURED:
            continue
        assert lx == x0, (nm, lx, x0)
        k = (H + 8 - y) if y < H else (C + C - c + 7 - y)
        out[nm] = T[nm] + 2 * w + k
    return out


def main():
    prof, geom = sys.argv[1], sys.argv[2]
    p, g, ops, ex, walk, blk, per_cls, cls = load(prof, geom)
    rows, lanes, x0, c, H, C = anatomy(g)
    T = {nm: per_cls[nm]["trie"] // ex[nm] for nm in ex if ex[nm]}
    print(f"anatomy: lane_x0={x0} centre={c} high_bus={H} collector={C}", flush=True)
    pred = predict(g, T)
    n_tot = sum(ex.values())
    print(f"\n{'op':>6} {'y':>3} {'w':>3} {'T':>3} {'exec':>9} {'meas':>6} {'pred':>6} "
          f"{'err':>5}", flush=True)
    bad = 0
    wmeas = wpred = 0
    for nm, (lx, y, w, _h) in sorted(lanes.items(), key=lambda kv: kv[1][1]):
        if nm in STRUCTURED:
            continue
        m = walk[nm] / ex[nm] - per_cls[nm]["fetch"] / ex[nm]
        d = pred[nm] - m
        bad += abs(d) > 1e-9
        wmeas += m * ex[nm]
        wpred += pred[nm] * ex[nm]
        print(f"{nm:>6} {y:>3} {w:>3} {T[nm]:>3} {ex[nm]:>9,} {m:>6.2f} {pred[nm]:>6.2f} "
              f"{d:>+5.2f}{'  <-- MISS' if abs(d) > 1e-9 else ''}", flush=True)
    print(f"\nsimple lanes: {bad} misses out of {len(pred)}", flush=True)
    print(f"  weighted walk (simple, ex-fetch): measured {wmeas / n_tot:>7.4f} "
          f"predicted {wpred / n_tot:>7.4f} t/instr", flush=True)

    struct = sum(walk[nm] - per_cls[nm]["fetch"] for nm in STRUCTURED if nm in ex)
    fetch = sum(per_cls[nm]["fetch"] for nm in ex)
    tot_walk = sum(walk.values())
    tot_blk = sum(blk.values())
    print(f"\nwhole machine, per instruction ({n_tot:,} instructions):", flush=True)
    print(f"  simple-lane walk   {wmeas / n_tot:>8.3f}   <- the model's territory", flush=True)
    print(f"  structured walk    {struct / n_tot:>8.3f}   <- slabs/seek, not modelled",
          flush=True)
    print(f"  fetch              {fetch / n_tot:>8.3f}", flush=True)
    print(f"  ---- walk total    {tot_walk / n_tot:>8.3f}", flush=True)
    print(f"  blocked            {tot_blk / n_tot:>8.3f}   <- concurrent, emergent",
          flush=True)
    print(f"  ==== t/instr       {(tot_walk + tot_blk) / n_tot:>8.3f}", flush=True)


if __name__ == "__main__":
    main()
