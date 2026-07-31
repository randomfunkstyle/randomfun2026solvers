"""Sweep ``INPUT_NORTH_WEST`` on **men-v3**, which has never been keyed for it.

``padfloor.py`` shows men-v3's ``mem_pad`` floor is set by the **input** pipe, not
the ROM: at pad 2 ``'r' at (22,154)`` sees ``mem_resp`` 21 and ``in`` 21 — an exact
§7.1 tie, and ties fail. The I room sits at ``lane_x0`` by default (``in_west``
0), i.e. directly over the IN lane's own ``r``. :data:`INPUT_NORTH_WEST` exists to
walk it west and is keyed for hires/taped (9) but not for hires/men-v3.

Every column of ``mem_pad`` is 1.083 cells/instr on this geometry (0.5416 of
instructions carry a MEM band, paid east and west).

usage: inwest.py [w ...]   -- default sweeps 0..11
"""
from __future__ import annotations

import sys

sys.path.insert(0, "/tmp/menprobe")
from common import setup, SLUG  # noqa: E402

KEY = (SLUG, "men-v3")


def floor_at(M, prog, w):
    M.INPUT_NORTH_WEST[KEY] = w
    seen = []
    orig = M._assemble

    def spy(*a, **k):
        pad = a[5]
        try:
            out = orig(*a, **k)
        except M.MachineError as e:
            seen.append((pad, str(e)))
            raise
        seen.append((pad, None))
        return out

    M._assemble = spy
    try:
        m = M.build_for(SLUG, program=prog, store="men-v3")
    except M.MachineError as e:
        return None, None, f"BUILD FAILED: {e}"
    finally:
        M._assemble = orig
    good = min(p for p, e in seen if e is None)
    last_fail = next((e for p, e in reversed(seen) if e is not None), "")
    return m, good, last_fail


def main():
    d3, hires, M, prog = setup()
    ws = [int(a) for a in sys.argv[1:]] or list(range(0, 12))
    base = None
    for w in ws:
        m, pad, msg = floor_at(M, prog, w)
        if m is None:
            print(f"  in_west={w:<3} {msg[:150]}")
            continue
        if base is None:
            base = pad
        R = m.regions
        tx, ty, tw, th = R["cpu:trie"]
        lane_x0 = tx + tw
        io = R.get("io:I")
        print(f"  in_west={w:<3} mem_pad={pad:<3} grid={m.width}x{m.height} "
              f"lane_x0={lane_x0} io:I={io}  last_reject: {msg[:110]}")


if __name__ == "__main__":
    main()
