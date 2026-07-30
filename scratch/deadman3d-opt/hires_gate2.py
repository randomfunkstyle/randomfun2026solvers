"""Native round-gated tick gate for a built ``deadman-3d_hires`` machine.

Reads a local build (``littleman/examples/local/deadman-3d_hires.*`` by default)
rather than rebuilding it, because the build is minutes and the gate is seconds.

**This is real frame gating, not an ungated tick count.**  The hi-res family
paints four 64x48 panels and the judge used to want exactly one display, so this
machine has never had a tick number.  ``FastLittleman.run(frame_tiles=(2, 2))``
now checks each panel against its own tile of the expected 128x96 frame on that
panel's *n*-th COMMIT, and releases round *n+1* only once the **slowest** panel
has committed frame *n* — composition by index, exactly the invariant
``lm1.display.tiled_frames_from_writes`` enforces.  ``FastResult.frame_ticks``
is the tick each logical frame completed on, so the per-frame cost is the
difference of successive entries.

usage: hires_gate2.py [n_rounds] [build_dir]
"""
import json
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "solvers" / "python"))

from randomfun2026solvers.fast_littleman import FastLittleman  # noqa: E402

LOCAL = REPO / "littleman" / "examples" / "local"


def main(argv: list[str]) -> int:
    n = int(argv[0]) if argv else 21
    build = Path(argv[1]) if len(argv) > 1 else LOCAL
    src = (build / "deadman-3d_hires.man").read_text()
    case = json.loads((build / "deadman-3d_hires.cases.json").read_text())
    rounds = case["publicTestData"][0]["rounds"][:n]

    rows = src.splitlines()
    print(f"machine {max(len(r) for r in rows)}x{len(rows)}, {len(rounds)} rounds")
    inp = " / ".join(" ".join(r["in"]) for r in rounds)
    frames = [r["frames"] for r in rounds]
    t0 = time.time()
    res = FastLittleman(src).run(inp, frames=frames, frame_tiles=(2, 2),
                                 max_ticks=40_000_000_000)
    wall = time.time() - t0
    print(f"ticks={res.step:,} fatal={res.fatal} passed={res.passed} "
          f"frames={len(res.frame_ticks)} ({wall:.1f}s)")
    if res.fatal or len(res.frame_ticks) < len(rounds):
        return 1
    prev = 0
    deltas = []
    for i, t in enumerate(res.frame_ticks):
        d = t - prev
        prev = t
        note = "  (boot + title; not comparable)" if i == 0 else ""
        if i:
            deltas.append(d)
        print(f"  frame {i:2d}: commit at {t:>13,}  cost {d:>12,}{note}")
    if deltas:
        print(f"  frames 1..{len(deltas)} mean: {sum(deltas) // len(deltas):,} ticks")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
