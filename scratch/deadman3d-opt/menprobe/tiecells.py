"""List every ``r``/``s`` in the shipped men-v3 build whose binding is decided by
a **tie** — i.e. the cells that only bind at all because ties break by reading
order — with the pipe each one wants and the attach cells involved.

These are the cells to put in front of the reference WASM oracle: they are the
whole exposure of the relaxed clause.
"""
from __future__ import annotations

import sys

sys.path.insert(0, "/tmp/menprobe")
from common import setup, SLUG  # noqa: E402

FOUND: list[tuple] = []


def main():
    d3, hires, M, prog = setup()
    Band = M.Band
    strict = M.check_bindings

    def spy(glyphs, touches):
        incoming = {"rom", "in", "mem_resp", Band.STREAM_RESP}
        local = []
        for x, y, glyph, band in glyphs:
            want = ("mem_req" if glyph == "s" else "mem_resp") if band == Band.MEM else band
            rivals = {
                n: abs(px - x) + abs(py - y)
                for n, (px, py) in touches.items()
                if (n in incoming) == (glyph == "r")
            }
            if want not in rivals:
                continue
            best = min(rivals.values())
            tied = sorted(n for n, d in rivals.items() if d == best)
            if len(tied) > 1:
                local.append((x, y, glyph, want, best, tuple(tied),
                              tuple(touches[n] for n in tied)))
        strict(glyphs, touches)      # only a surviving call reaches here
        FOUND[:] = local             # ...so this is the geometry that shipped
    M.check_bindings = spy
    try:
        m = M.build_for(SLUG, program=prog, store="men-v3")
    finally:
        M.check_bindings = strict
    print(f"men-v3 {m.width}x{m.height} mem_pad={m.mem_pad}")
    print(f"{len(FOUND)} glyph(s) bind only because ties break by reading order:")
    for x, y, glyph, want, d, tied, at in FOUND:
        print(f"  {glyph!r} at ({x},{y}) wants {want!r}, tied at {d} among {tied} "
              f"attach {at}")
    print("\nquery these with littleman/tools/route-check.mjs")
    for x, y, *_ in FOUND:
        print(f"  cell {x} {y}")


if __name__ == "__main__":
    main()
