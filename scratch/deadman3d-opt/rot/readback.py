"""Standalone readback for the rotating banks: ascending, descending, random.

The shipped 901-address readback ascends, so it never exercises the wraparound:
under rotation ``ROT = (n + addr - head) % n`` and an ascending sweep keeps the
delta at exactly 1 forever. The two extra orders are the only thing standing
between an off-by-one in the head update and a bank that desynchronises
silently — a wrong head does not error, it answers the wrong slot.

usage: readback.py [banks]        banks: comma-separated address-order indices
"""
from __future__ import annotations

import random
import sys
import time
from pathlib import Path

WT = Path("/Users/ptaykalo/Projects/icfpc-2026/randomfun2026solvers/"
          ".claude/worktrees/compactor")
sys.path.insert(0, str(WT / "solvers" / "python"))
sys.path.insert(0, str(WT / "tests"))

from randomfun2026solvers.lm1 import machine  # noqa: E402
from randomfun2026solvers.memory_taped import taped_plan, taped_store_block  # noqa: E402
from test_memory_taped import _standalone  # noqa: E402

assert "worktrees/compactor" in machine.__file__, machine.__file__

N = 902
PLAN = tuple(machine.TAPED_BANKS["deadman-3d_hires"])
ORDER = machine.TAPED_BANK_ORDER[("deadman-3d_hires", "taped")]
KW = dict(
    skip_batch=None,
    jump_threshold=machine.TAPED_JUMP_THRESHOLD["deadman-3d_hires"],
    compact_gate=True,
    gate_park_const=True,
    gate_south_reuse_b=True,
    tape_park_const=True,
    order=list(ORDER),
    chain_reach=True,
    feed_teleport=True,
    feed_share_riser=True,
    bank_lift=5,
    gate_return_slack=0,
    request_roof=20,
    bank_west_grow=machine.TAPED_BANK_WEST_GROW.get(("deadman-3d_hires", "taped"), 0),
    protocol="v5",
)


def bounds():
    b = [1]
    for m in PLAN:
        b.append(b[-1] + m)
    return b


def main() -> int:
    rot = tuple(int(x) for x in sys.argv[1].split(",")) if len(sys.argv) > 1 else ()
    t0 = time.time()
    block = taped_store_block(N, PLAN, **KW, rotate_banks=rot)
    print(f"built {block.width}x{block.height} rotate={rot} ({time.time()-t0:.0f}s)",
          flush=True)
    engine = _standalone(block)
    val = {a: (a * 37 + 11) % 9973 for a in range(1, N)}
    writes = [w for a in range(1, N) for w in (2 * a - 1, val[a])]

    bad = 0
    bd = bounds()
    rng = random.Random(20260802)
    for name, key in (
        ("ascending", lambda xs: xs),
        ("descending", lambda xs: xs[::-1]),
        ("random", lambda xs: rng.sample(xs, len(xs))),
        ("random2", lambda xs: rng.sample(xs, len(xs))),
    ):
        got: dict[int, int] = {}
        t0 = time.time()
        for lo, hi in zip(bd, bd[1:]):
            hi = min(hi, N)
            if lo >= hi:
                continue
            addrs = key(list(range(lo, hi)))
            reads = [2 * a for a in addrs]
            want = [val[a] for a in addrs]
            res = engine.run(writes + reads, expected=want, max_ticks=900_000_000)
            if res.fatal is not None:
                print(f"  {name}: FATAL {res.fatal} at bank [{lo},{hi})", flush=True)
                bad += 1
                break
            got.update(zip(addrs, res.output, strict=False))
        else:
            wrong = {a: (got.get(a), val[a]) for a in val if got.get(a) != val[a]}
            print(f"  {name}: {len(got)} addresses, {len(wrong)} wrong "
                  f"({time.time()-t0:.0f}s)", flush=True)
            if wrong:
                bad += 1
                for a in sorted(wrong)[:12]:
                    print(f"     addr {a}: got {wrong[a][0]} want {wrong[a][1]}")
    # ... and an interleaved read/write stream inside one bank, which is the
    # case where the head has to survive a write it also moved.
    for k, (lo, hi) in enumerate(zip(bd, bd[1:])):
        hi = min(hi, N)
        if hi - lo < 4:
            continue
        addrs = rng.sample(range(lo, hi), min(40, hi - lo))
        stream: list[int] = []
        want: list[int] = []
        cur = dict(val)
        for i, a in enumerate(addrs):
            if i % 3 == 2:
                cur[a] = (a * 91 + 5) % 7919
                stream += [2 * a - 1, cur[a]]
            else:
                stream += [2 * a]
                want.append(cur[a])
        res = engine.run(writes + stream, expected=want, max_ticks=900_000_000)
        ok = res.fatal is None and res.output == want
        if not ok:
            print(f"  mixed bank {k} [{lo},{hi}): FAIL fatal={res.fatal}", flush=True)
            bad += 1
    if bad == 0:
        print("  mixed read/write streams: all banks OK", flush=True)
    print(f"RESULT rotate={rot} {'OK' if bad == 0 else f'{bad} FAILURES'}", flush=True)
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
