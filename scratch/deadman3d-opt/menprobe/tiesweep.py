"""``INPUT_NORTH_WEST`` x strict/reading-order tie rule, on the pad floor.

Two facts from ``tieprobe.py`` and ``inwest.py``:

* men-v3 at ``in_west`` 0 floors at pad 3 because ``in`` and ``mem_resp`` are an
  exact 21-21 tie at pad 2 -- and ``in`` **wins** it by reading order, so the
  refusal is the machine's, not the checker's.
* at ``in_west`` >= 1 the rival at pad 2 becomes ``rom``, also an exact tie
  (30-30). Whether that one is real depends on which attach cell reads first.

So the lever is the *pair*. Reports the winner of every tie it meets.

usage: tiesweep.py [in_west ...]
"""
from __future__ import annotations

import sys

sys.path.insert(0, "/tmp/menprobe")
from common import setup, SLUG  # noqa: E402

KEY = (SLUG, "men-v3")


def install(M, relaxed: bool):
    Band, MachineError = M.Band, M.MachineError
    strict = M._strict_check_bindings

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
            best = min(rivals.values())
            tied = sorted(n for n, d in rivals.items() if d == best)
            winner = min(rivals, key=lambda n: (rivals[n], touches[n][1], touches[n][0]))
            if winner != want:
                why = (
                    f"tie at {best} among {tied}, attach "
                    f"{ {n: touches[n] for n in tied} } -> {winner!r} reads first"
                    if len(tied) > 1
                    else f"{winner!r} is nearer ({rivals[winner]} vs {rivals[want]})"
                )
                raise MachineError(f"{glyph!r} at {(x, y)} must bind {want!r}: {why}")
    M.check_bindings = check if relaxed else strict


def floor(M, prog, w, relaxed):
    M.INPUT_NORTH_WEST[KEY] = w
    install(M, relaxed)
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
        return None, None, [(None, str(e))]
    finally:
        M._assemble = orig
    return m, min(p for p, e in seen if e is None), [t for t in seen if t[1]]


def main():
    d3, hires, M, prog = setup()
    M._strict_check_bindings = M.check_bindings
    ws = [int(a) for a in sys.argv[1:]] or [0, 1, 5, 9]
    try:
        for w in ws:
            for relaxed in (False, True):
                m, pad, rejects = floor(M, prog, w, relaxed)
                tag = "reading-order" if relaxed else "strict       "
                if m is None:
                    print(f"in_west={w:<2} {tag} BUILD FAILED {rejects[0][1][:110]}")
                    continue
                print(f"in_west={w:<2} {tag} mem_pad={pad} {m.width}x{m.height}")
                for p, e in rejects:
                    print(f"      pad={p}: {e[:160]}")
    finally:
        M.check_bindings = M._strict_check_bindings
        M.INPUT_NORTH_WEST.pop(KEY, None)


if __name__ == "__main__":
    main()
