"""Is `stream:router` on the critical path, or is it idle between words?

The router is a room the CPU's single `SND` lane feeds and four DOOM units hang
off.  Two entirely different things could be true of it and only one is worth
optimising:

* the CPU **blocks** on `cpu->router` because the room cannot service words as
  fast as they are sent — the demux is then in the command path and every tick
  of its trie walk is charged to the frame; or
* the pipe is a buffer the CPU drops words into and never waits on, in which
  case the router's walk overlaps the CPU's next instruction entirely and its
  width costs nothing at all.

`FastLittleman.run(profile=True)` answers it exactly rather than by argument:
`FastProfile.send`/`send_blocked` are counted per pipe (exact, not sampled), so
this reports how many command words a frame carries, how often the CPU parked
sending one, and — from the sampled `heat` over the router's own cells — what
fraction of the run the router's man is even inside the room.

    python scratch/deadman3d-opt/router_load.py [n_rounds] [build_dir]
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "solvers" / "python"))

from randomfun2026solvers.fast_littleman import FastLittleman  # noqa: E402

LOCAL = REPO / "littleman" / "examples" / "local"


def router_box(build: Path) -> tuple[int, int, int, int]:
    dbg = json.loads((build / "deadman-3d_hires.debug.json").read_text())
    for r in dbg["regions"]:
        if r["name"] == "stream:router":
            return r["x"], r["y"], r["w"], r["h"]
    raise SystemExit("no stream:router region in the debug map")


def main(argv: list[str]) -> int:
    n = int(argv[0]) if argv else 4
    build = Path(argv[1]) if len(argv) > 1 else LOCAL
    src = (build / "deadman-3d_hires.man").read_text()
    case = json.loads((build / "deadman-3d_hires.cases.json").read_text())
    rounds = case["publicTestData"][0]["rounds"][:n]
    rx, ry, rw, rh = router_box(build)

    lm = FastLittleman(src)
    # Every pipe whose destination room is the router room, and every pipe whose
    # source room is: one command lane in, four legs out.
    room_of = {}
    for room in lm.rooms:
        if room.min[0] == rx and room.min[1] == ry:
            room_of["router"] = room.id
    rid = room_of["router"]
    inbound = [p.id for p in lm.pipes if p.dst == rid]
    outbound = [p.id for p in lm.pipes if p.src == rid]
    print(f"router room {rid} at ({rx},{ry}) {rw}x{rh}: "
          f"inbound={inbound} outbound={outbound}")

    inp = " / ".join(" ".join(r["in"]) for r in rounds)
    frames = [r["frames"] for r in rounds]
    t0 = time.time()
    res = lm.run(inp, frames=frames, frame_tiles=(2, 2),
                 max_ticks=40_000_000_000, profile=True, profile_stride=64)
    print(f"ticks={res.step:,} fatal={res.fatal} passed={res.passed} "
          f"({time.time() - t0:.0f}s)")
    p = res.profile
    assert p is not None
    span = res.frame_ticks[-1] - res.frame_ticks[0] if len(res.frame_ticks) > 1 else res.step
    nfr = max(1, len(res.frame_ticks) - 1)

    print(f"\nframes 1..{nfr}: {span:,} ticks, mean {span // nfr:,}")
    for pid in inbound:
        print(f"  cpu->router  pipe {pid}: sent {p.send[pid]:,} words "
              f"({p.send[pid] / nfr:,.0f} a frame), "
              f"CPU parked on it {p.send_blocked[pid]:,} times, "
              f"blocked {p.pipe_wait[pid] * p.stride:,} ticks "
              f"({100.0 * p.pipe_wait[pid] * p.stride / res.step:.4f}% of the run)")
    for pid in outbound:
        print(f"  router->tile pipe {pid}: sent {p.send[pid]:,}, "
              f"router parked {p.send_blocked[pid]:,} times, "
              f"blocked {p.pipe_wait[pid] * p.stride:,} ticks "
              f"({100.0 * p.pipe_wait[pid] * p.stride / res.step:.4f}%)")

    inside = sum(v for (x, y), v in p.heat.items()
                 if rx <= x < rx + rw and ry <= y < ry + rh)
    waiting = sum(v for (x, y), v in p.wait.items()
                  if rx <= x < rx + rw and ry <= y < ry + rh)
    print(f"\nrouter man: {inside * p.stride:,} ticks in the room "
          f"({100.0 * inside / max(1, p.samples):.3f}% of one runner's run), of which "
          f"{waiting * p.stride:,} blocked ({100.0 * waiting / max(1, inside):.1f}%)")
    print(f"  => working (not blocked): {(inside - waiting) * p.stride:,} ticks, "
          f"{100.0 * (inside - waiting) / max(1, p.samples):.3f}% of the run")
    if p.send[inbound[0]]:
        busy = (inside - waiting) * p.stride / p.send[inbound[0]]
        print(f"  => {busy:.1f} ticks of walk per command word")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
