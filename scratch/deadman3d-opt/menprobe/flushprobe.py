"""Build men-v3 hires with knob overrides, gate it, and report the discard/flush pool.

Every knob is an env var so a sweep is a shell loop and each variant is a fresh
process — the builder caches nothing across runs, and a mutated module-level dict
is exactly how two variants get silently differenced.

    ROUNDS=21 PROFILE=1 python flushprobe.py
    ROUNDS=21 DRAIN_SEEK=3 python flushprobe.py
    ROUNDS=3  ROM_DROP=12 python flushprobe.py

Reports, per ``cpu:*`` region: ticks, %run, ``r``-executions (words) and ticks a
word — the five boxes of the discard/flush pool first, then the rest.
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import SLUG, setup, tour  # noqa: E402

from randomfun2026solvers.lm1.profile import _region_of  # noqa: E402

POOL = (
    "cpu:seek:flush",
    "cpu:seek:discard",
    "cpu:discard:BRN",
    "cpu:discard:BRZ",
    "cpu:slab:JMPF",
)


def main() -> int:
    rounds = int(os.environ.get("ROUNDS", "21"))
    profile = os.environ.get("PROFILE", "1") != "0"
    tag = os.environ.get("TAG", "base")

    d3, hires, M, prog = setup()

    # ── knobs ────────────────────────────────────────────────────────────────
    key = (SLUG, "men-v3")
    if (v := os.environ.get("DRAIN_SEEK")) is not None:
        M.SEEK_CLASSIC_DRAIN[key] = int(v)
    if (v := os.environ.get("DRAIN_OPS")) is not None:
        M.SEEK_CLASSIC_DRAIN_OPS[key] = tuple(v.split(","))
    if (v := os.environ.get("MEM_PAD")) is not None:
        M.MEM_PAD_FOR[key] = int(v)
    if (v := os.environ.get("ROM_DROP")) is not None:
        M.ROM_TOUCH_DROP[key] = int(v)
    if (v := os.environ.get("SQUASH")) is not None:
        M.SQUASH_BAND[key] = int(v)
    if (v := os.environ.get("FLUSH_TIGHT")) is not None:
        M.SEEK_FLUSH_TIGHT[key] = int(v) != 0
    if (v := os.environ.get("ROM_SNAKE")) is not None:
        M.ROM_CORRIDOR_PAD[key] = int(v)

    inp, frames = tour(hires, rounds)
    t0 = time.time()
    m = M.build_for(SLUG, program=prog, store="men-v3")
    tb = time.time() - t0
    print(f"[{tag}] built {m.width}x{m.height} in {tb:.0f}s  "
          f"rom_capacity={m.rom_capacity} mem_pad={m.mem_pad}", flush=True)

    from randomfun2026solvers.fast_littleman import FastLittleman

    t0 = time.time()
    kw = dict(profile=True, profile_stride=17) if profile else {}
    res = FastLittleman("\n".join(m.rows)).run(
        inp, frames=frames, frame_tiles=(2, 2), max_ticks=40_000_000_000, **kw)
    dt = time.time() - t0
    print(f"[{tag}] rounds={rounds} step={res.step:,} last_frame={res.frame_ticks[-1]:,} "
          f"passed={res.passed} fatal={res.fatal} ({dt:.0f}s)", flush=True)
    if not profile:
        return 0

    p = res.profile
    S, stride = p.samples, p.stride
    total = res.step
    rows = "\n".join(m.rows).split("\n")

    def cell(x, y):
        r = rows[y] if y < len(rows) else ""
        return r[x] if x < len(r) else " "

    # ``profile._region_of``, verbatim: the smallest box containing the cell owns
    # it. Summing over boxes instead double-counts — the CPU's boxes overlap by
    # construction (a riser crosses a slab), and a naive sum reads 139% of the run.
    regions = m.regions
    owner: dict[tuple[int, int], str] = {}

    def own(c):
        r = owner.get(c)
        if r is None:
            r = owner[c] = _region_of(c[0], c[1], regions)
        return r

    # ``heat`` counts *ticks on a cell*, not executions: an `r` blocked for 20
    # ticks contributes 20. A word is therefore ``heat - wait`` at an `r`, which
    # is the one tick the read actually costs once it can proceed.
    agg: dict[str, list[int]] = {}
    tot_h = tot_w = 0
    for c, v in p.heat.items():
        name = own(c)
        if not name.startswith("cpu"):
            continue
        a = agg.setdefault(name, [0, 0, 0])
        a[0] += v
        if cell(*c) == "r":
            a[2] += v
        tot_h += v
    for c, v in p.wait.items():
        name = own(c)
        if not name.startswith("cpu"):
            continue
        a = agg.setdefault(name, [0, 0, 0])
        a[1] += v
        if cell(*c) == "r":
            a[2] -= v
        tot_w += v
    out = [(n, a[0], a[1], a[2]) for n, a in agg.items()]
    print(f"[{tag}] samples={S:,} stride={stride}  CPU total {100*tot_h/S:.3f}% "
          f"(blocked {100*tot_w/S:.3f}%)")
    print(f"[{tag}] {'region':<26}{'ticks':>12}{'%run':>9}{'words':>10}"
          f"{'t/word':>9}{'unblk%':>9}")

    def line(name, hh, ww, words):
        ticks = hh * stride
        wds = words * stride
        tw = ticks / wds if wds else 0.0
        print(f"[{tag}] {name:<26}{ticks:>12,}{100*hh/S:>9.3f}{wds:>10,}"
              f"{tw:>9.2f}{100*(hh-ww)/S:>9.3f}")

    pool_h = pool_w = 0
    for name in POOL:
        for n, hh, ww, words in out:
            if n == name:
                line(n, hh, ww, words)
                pool_h += hh
                pool_w += ww
    print(f"[{tag}] {'POOL':<26}{pool_h*stride:>12,}{100*pool_h/S:>9.3f}"
          f"{'':>10}{'':>9}{100*(pool_h-pool_w)/S:>9.3f}")
    print(f"[{tag}] -- rest --")
    for n, hh, ww, words in sorted(out, key=lambda r: -r[1]):
        if n in POOL:
            continue
        if 100 * hh / S < 0.20:
            continue
        line(n, hh, ww, words)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
