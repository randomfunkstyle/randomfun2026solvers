#!/usr/bin/env python3
"""Per-case tick profile for a matmul .man grid."""
import json
import sys
from pathlib import Path

REPO = Path("/Users/oleg/projects/randomfun2026solvers/.claude/worktrees/agent-a408bcddfaf92d05c")
sys.path.insert(0, str(REPO / "solvers" / "python"))

from randomfun2026solvers.fast_littleman import FastLittleman

PROBLEM = REPO / "tasks" / "problems" / "matmul.json"


def cases():
    prob = json.loads(PROBLEM.read_text())
    for c in prob["publicTestData"]:
        r = c["rounds"][0]
        yield c["name"], [int(v) for v in r["in"]], [int(v) for v in r["out"]]


def main(path):
    src = Path(path).read_text()
    rows = src.split("\n")
    while rows and not rows[-1].strip():
        rows.pop()
    w = max(len(r) for r in rows)
    h = len(rows)
    side = max(w, h)
    lm = FastLittleman(src)
    total = 0
    print(f"{path}  {w}x{h}  area2={side*side}")
    print(f"{'case':22s} {'N,M,K':10s} {'MACs':>6s} {'vals':>5s} {'ticks':>9s} {'t/MAC':>7s}")
    for name, inp, exp in cases():
        res = lm.run(inp, expected=exp)
        ok = list(res.output) == exp
        n, m, k = inp[0], inp[1], inp[2]
        macs = n * m * k
        vals = 3 + n * m + m * k + n * k
        t = res.step
        total += t
        print(f"{name:22s} {n}x{m}x{k:<6} {macs:6d} {vals:5d} {t:9d} {t/macs:7.2f}"
              + ("" if ok else "  MISMATCH"))
    avg = total / 7
    print(f"avg_ticks={avg:.1f}  score={side*side*avg:,.0f}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else str(REPO / "tasks/solutions/matmul_ring.man"))
