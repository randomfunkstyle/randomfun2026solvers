"""The store's **answer leg** on taped: where it is, what it costs, what floors it.

The coordinator's proposal is a geometric edit to the return-from-memory route --
move the drop column west and collapse ``<---<`` to ``<<``. The question is
whether that is the same quantity ``store_dy`` already moves
(``route_lengths["store->cpu"]``: 5 cells shipped, 2 on the upper store level) or
a second, composable one.

This dumps the grid around the answer room at a given knob setting, prints the
route table, and -- with ``--patch`` -- rewrites the leg on ``m.rows`` directly so
the claim can be tested on the grid before anything touches the builder.

Everything written goes to /tmp: the grid is IWAD-derived.

    python tapedleg.py show '<knobs json>'
    python tapedleg.py patch '<knobs json>' [rounds]
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

REPO = Path(os.environ.get("LM1_REPO") or Path(__file__).resolve().parents[3])
sys.path.insert(0, str(REPO / "solvers" / "python"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from taped import KEY, SLUG, TIER, apply_knobs, restore, setup  # noqa: E402

WAD = Path.home() / "Downloads" / "doom1_0" / "DOOM1.WAD"


def build(M, prog, kn):
    saved = apply_knobs(M, kn)
    kw = {}
    if "drop" in kn:
        kw["rom_touch_drop"] = kn["drop"]
    if "squash" in kn:
        kw["squash_band"] = kn["squash"]
    try:
        return M.build_for(SLUG, program=prog, store=TIER, **kw)
    finally:
        restore(M, saved)


def find_room(rows, needle="@>R"):
    for y, r in enumerate(rows):
        x = r.find(needle)
        if x >= 0:
            return x, y
    return None


def window(rows, x0, y0, w=26, h=14):
    out = []
    for y in range(max(0, y0 - 3), min(len(rows), y0 + h)):
        r = rows[y]
        out.append(f"{y:>4} |{r[max(0, x0 - 6):x0 + w]}|")
    return "\n".join(out)


def main():
    mode = sys.argv[1]
    kn = json.loads(sys.argv[2]) if len(sys.argv) > 2 else {}
    _, hires, M, prog = setup()
    m = build(M, prog, kn)
    rows = list(m.rows)
    print(f"{kn} -> {m.width}x{m.height} pad={m.mem_pad}", flush=True)
    print(f"routes: {m.route_lengths}", flush=True)
    p = find_room(rows)
    if p is None:
        print("no '@>R' room found", flush=True)
        return
    print(f"'@>R' at {p}", flush=True)
    print(window(rows, *p), flush=True)
    Path(f"/tmp/z3work/leg_{mode}.man").write_text("\n".join(rows))


if __name__ == "__main__":
    main()
