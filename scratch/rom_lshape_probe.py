"""Battle test: a real L-shaped looping ROM, run on the engine."""
import sys
sys.path.insert(0, "/private/tmp/claude-502/-Users-ptaykalo-Projects-icfpc-2026-randomfun2026solvers/a57b04b4-f087-447f-9f3f-4a8a475868e0/scratchpad")
from lpack import pack_region
from randomfun2026solvers.lm1.rom import token_cells


def build_l_rom(words, *, wide_w, wide_rows, arm_w):
    """Wide band of ``wide_rows`` rows, then a narrow arm indented from the west.

    Turn columns are per block. Consecutive blocks share a turn column, so the last
    wide row must end on the east side — i.e. be eastbound — which fixes
    ``wide_rows`` odd.
    """
    if wide_rows % 2 == 0:
        raise ValueError("wide_rows must be odd so the band exits east into the arm")
    tokens = [token_cells(w) for w in words]
    L, R = 1, wide_w + 2                  # wide turns
    AL = R - arm_w - 1                    # arm's west turn
    riser = R + 1
    spans = [(L + 1, R - 1)] * wide_rows + [(AL + 1, R - 1)] * 64
    lit, placed, parity = pack_region(tokens, spans)
    if placed != len(tokens):
        raise ValueError(f"only {placed}/{len(tokens)} tokens fit")
    # the arm's west turn column must be closed when the wide band ends
    wide_only, _, wp = pack_region(tokens, [(L + 1, R - 1)] * wide_rows)
    if wp[AL] % 2:
        raise ValueError(f"arm turn column {AL} left at ODD parity by the wide band")

    cells = {}
    def put(x, y, ch):
        old = cells.get((x, y))
        if old is not None and old != ch:
            raise ValueError(f"collision at {(x,y)}: {old!r} vs {ch!r}")
        cells[(x, y)] = ch

    for i, body in enumerate(lit):
        y, east = i + 1, i % 2 == 0
        x0 = L if i < wide_rows else AL
        if east: put(x0, y, ">"); put(R, y, "v")
        else:    put(R, y, "<");  put(x0, y, "v")
        for x, ch in enumerate(body):
            if ch != " " and x0 < x < R: put(x, y, ch)

    put(riser, 0, "<"); put(L, 0, "v")
    spawn = riser - 1
    for x in range(L + 1, riser): put(x, 0, "@" if x == spawn else "<")
    bottom = len(lit) + 1
    exit_col = R if (len(lit) - 1) % 2 == 0 else (L if len(lit) <= wide_rows else AL)
    put(exit_col, bottom, ">")
    for x in range(exit_col + 1, riser): put(x, bottom, " ")
    put(riser, bottom, "^")
    for y in range(1, bottom): put(riser, y, "^")
    return cells, riser + 1, bottom + 1, len(lit)
