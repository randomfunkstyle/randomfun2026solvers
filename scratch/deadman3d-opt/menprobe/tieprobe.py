"""Is the ``mem_pad`` floor the machine's, or ``check_bindings``'s?

``check_bindings`` refuses a §7.1 tie outright::

    if rivals[want] != best or sum(1 for d in rivals.values() if d == best) > 1:

``SPEC.md:183`` breaks ties by **reading order (top to bottom, left to right)**,
and ``fast_littleman._bind_pipe_ops`` implements exactly that::

    min(candidates, key=lambda pid: (dist, attach[1], attach[0]))

So the engine's rule is "the intended pipe must *win*", not "must avoid". This
monkeypatches ``check_bindings`` to the engine's rule and reports what the pad
floor becomes, plus which cells are decided by a tie and who wins each.

Nothing is written to the shipped path here; the gate comes after.

usage: tieprobe.py [men-v3|taped ...]
"""
from __future__ import annotations

import sys

sys.path.insert(0, "/tmp/menprobe")
from common import setup, SLUG  # noqa: E402

TIES: list[tuple] = []


def install(M):
    """Replace check_bindings with the engine's own tie rule."""
    Band = M.Band
    MachineError = M.MachineError

    def relaxed(glyphs, touches):
        incoming = {"rom", "in", "mem_resp", Band.STREAM_RESP}
        for x, y, glyph, band in glyphs:
            if band == Band.MEM:
                want = "mem_req" if glyph == "s" else "mem_resp"
            else:
                want = band
            rivals = {
                name: abs(px - x) + abs(py - y)
                for name, (px, py) in touches.items()
                if (name in incoming) == (glyph == "r")
            }
            if want not in rivals:
                raise MachineError(f"{glyph!r} at {(x, y)} wants pipe {want!r}, which is absent")
            # the engine's key, verbatim: distance, then attach row, then attach col
            winner = min(rivals, key=lambda n: (rivals[n], touches[n][1], touches[n][0]))
            best = min(rivals.values())
            tied = [n for n, d in rivals.items() if d == best]
            if winner != want:
                order = sorted(rivals.items(), key=lambda kv: kv[1])
                raise MachineError(
                    f"{glyph!r} at {(x, y)} must bind {want!r} but distances are {order}"
                )
            if len(tied) > 1:
                TIES.append((x, y, glyph, want, best, tuple(sorted(tied)),
                             tuple(touches[n] for n in sorted(tied))))
    M.check_bindings = relaxed


def main():
    d3, hires, M, prog = setup()
    strict = M.check_bindings
    for store in (sys.argv[1:] or ["men-v3", "taped"]):
        print(f"\n===== {store} =====")
        for mode in ("strict", "reading-order"):
            TIES.clear()
            M.check_bindings = strict
            if mode == "reading-order":
                install(M)
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
                m = M.build_for(SLUG, program=prog, store=store)
            finally:
                M._assemble = orig
                M.check_bindings = strict
            pad = min(p for p, e in seen if e is None)
            print(f"  {mode:14s} mem_pad={pad}  grid={m.width}x{m.height}")
            for p, e in seen:
                if e is not None:
                    print(f"      reject pad={p}: {e[:150]}")
            if mode == "reading-order" and TIES:
                print(f"      {len(TIES)} cell(s) decided BY a tie, intended pipe winning:")
                for t in TIES[:12]:
                    print(f"        {t[2]!r} at ({t[0]},{t[1]}) want {t[3]} d={t[4]} "
                          f"tied={t[5]} attach={t[6]}")


if __name__ == "__main__":
    main()
