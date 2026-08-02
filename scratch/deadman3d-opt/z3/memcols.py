"""What column does each MEM lane's band actually start in?

``mem_pad`` is not the walked quantity and neither, quite, is ``mem_x``.
:func:`machine._flat_lane` pushes each band's **first** glyph out to ``band_x``
with ``while x < target``, so a ``mem_x`` *below* a lane's natural column is
silently ignored for that lane -- the band stops being a column and becomes a
ragged edge. That makes ``mem_pad < 0`` a partial move, not a free one, and this
prints the per-row truth instead of the registry value.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from bind import geom, load  # noqa: E402


def main():
    recs = []
    for p in sys.argv[1:]:
        recs += load(p)
    for r in recs:
        if "glyphs" not in r:
            continue
        g, t = geom(r)
        first: dict[int, int] = {}
        for x, y, gl, b in g:
            if b == "mem" and (y not in first or x < first[y]):
                first[y] = x
        cols = sorted(first.values())
        hist: dict[int, int] = {}
        for c in cols:
            hist[c] = hist.get(c, 0) + 1
        print(
            f"{str(r['knobs']):58} {r.get('w')}x{r.get('h')}  "
            f"MEM band first-glyph columns: {hist}   west={min(cols)} east={max(cols)}",
            flush=True,
        )


if __name__ == "__main__":
    main()
