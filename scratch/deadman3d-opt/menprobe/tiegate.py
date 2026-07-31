"""Gate the reading-order tie rule (+ men-v3 ``INPUT_NORTH_WEST``) at 21 rounds.

The frame gate is the only arbiter: a wrong binding reads the wrong pipe and
renders a wrong frame, so ``passed``/``fatal`` over the full tour is the test.

env:
  RELAX=1     use the engine's tie rule instead of check_bindings' strict one
  INWEST=9    INPUT_NORTH_WEST for (hires, men-v3); unset leaves the registry alone
  ROUNDS=21
usage: tiegate.py [men-v3|taped ...]
"""
from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, "/tmp/menprobe")
from common import setup, tour, run, SLUG  # noqa: E402

INSTR = 880_332
BASE = {"men-v3": 81_042_708, "taped": 140_379_566}


def relax(M):
    Band, MachineError = M.Band, M.MachineError

    def check(glyphs, touches):
        incoming = {"rom", "in", "mem_resp", Band.STREAM_RESP}
        for x, y, glyph, band in glyphs:
            want = ("mem_req" if glyph == "s" else "mem_resp") if band == Band.MEM else band
            rivals = {
                name: abs(px - x) + abs(py - y)
                for name, (px, py) in touches.items()
                if (name in incoming) == (glyph == "r")
            }
            if want not in rivals:
                raise MachineError(f"{glyph!r} at {(x, y)} wants pipe {want!r}, which is absent")
            if min(rivals, key=lambda n: (rivals[n], touches[n][1], touches[n][0])) != want:
                order = sorted(rivals.items(), key=lambda kv: kv[1])
                raise MachineError(
                    f"{glyph!r} at {(x, y)} must bind {want!r} but distances are {order}"
                )
    M.check_bindings = check


def main():
    d3, hires, M, prog = setup()
    if os.environ.get("RELAX"):
        relax(M)
        print("check_bindings: reading-order (engine rule)")
    if os.environ.get("INWEST"):
        M.INPUT_NORTH_WEST[(SLUG, "men-v3")] = int(os.environ["INWEST"])
        print(f"INPUT_NORTH_WEST[hires,men-v3] = {os.environ['INWEST']}")
    rounds = int(os.environ.get("ROUNDS", "21"))
    inp, frames = tour(hires, rounds)
    for store in (sys.argv[1:] or ["men-v3", "taped"]):
        t = time.time()
        m = M.build_for(SLUG, program=prog, store=store)
        print(f"built {store} {m.width}x{m.height} mem_pad={m.mem_pad} "
              f"({time.time()-t:.0f}s)", flush=True)
        res = run(m, inp, frames, store)
        if rounds == 21:
            t_i = res.frame_ticks[-1] / INSTR
            b = BASE[store]
            print(f"    t/instr={t_i:.4f}   vs base {b:,}: "
                  f"{100*(res.frame_ticks[-1]-b)/b:+.3f}%", flush=True)


if __name__ == "__main__":
    main()
