"""Predict the whole ``(squash, drop)`` feasible region from one capture per row.

``scratch/deadman3d-opt/squash_grid.py`` found the region by brute force: 88 whole
builds, ~13 minutes, and it only knows about the eight drop values it was given.
This computes the same region from **nine** builds — one per squash depth — because
once a placement is captured the drop is solved, not sampled.

The two constraints are of different kinds and are reported separately, which is
the point ``ROM_TOUCH_DROP``'s docstring makes when it says "nothing here is a
distance":

* **geometry** — room H's four-row minimum against the store's underside
  (`roomh`), which caps the squash at ``k <= 8`` and which no drop can affect;
* **§7.1 binding** — an exact interval of drops per squash depth (`bindsolve`),
  whose upper end is set by the fetch ``r`` racing the ``in`` touch and whose lower
  end is set by the BRN slab's discard racing ``mem_resp``.

    python -m scratch.layout2.grid
"""

from __future__ import annotations

from .bindsolve import feasible
from .capture import capture, rom_wanting
from .roomh import H_MIN_ROWS, model

#: the built grid from ``squash_grid.py``, for comparison — k -> max drop that built
BUILT_MAX_DROP = {0: 28, 1: 28, 2: 26, 3: 26, 4: 22, 5: 22, 6: 22, 7: 22, 8: 18}
#: ...and the drops it actually tried, so "max" means max-of-these
BUILT_TRIED = (5, 10, 14, 18, 22, 26, 28, 30)


def solve_row(k: int):
    """Capture at squash ``k`` and solve the feasible drop interval exactly."""
    sq: bool | int = False if k == 0 else k
    cap = capture(squash_band=sq, rom_touch_drop=0)
    if not cap.glyphs:
        return cap, None, None
    fs, bounds = feasible(cap.glyphs, cap.touches, moving="rom", axis="y")
    return cap, fs, bounds


def main() -> int:
    band = None
    print("k | room H       | feasible drop (solved)      | built grid says")
    print("--+--------------+-----------------------------+----------------")
    for k in range(0, 11):
        cap, fs, bounds = solve_row(k)
        if band is None and cap.seek.get("floor_y") is not None:
            band = model(cap)
            band.available0 += k  # normalise back to k=0
        avail = cap.seek.get("available")
        geo = (f"{avail} rows {'ok' if (avail or 0) >= H_MIN_ROWS else 'REFUSED'}"
               if avail is not None else "n/a")
        if fs is None:
            print(f"{k:>2}| {geo:<13}| no capture — refused before bindings "
                  f"| {BUILT_MAX_DROP.get(k, '-')}")
            continue
        hi = fs.parts[-1].hi if fs.parts else None
        agree = ""
        if k in BUILT_MAX_DROP and hi is not None:
            # the sweep's max is the largest *tried* drop <= the solved upper bound
            pred = max([d for d in BUILT_TRIED if d <= hi], default=None)
            agree = "agrees" if pred == BUILT_MAX_DROP[k] else f"DIFFERS (pred {pred})"
        print(f"{k:>2}| {geo:<13}| {str(fs):<28}| {BUILT_MAX_DROP.get(k, '-')} {agree}")
        if k == 0 and bounds:
            lo = [b for b in bounds if b.side == "lo"]
            up = [b for b in bounds if b.side == "hi"]
            print(f"    glyphs wanting rom: {len(rom_wanting(cap))}")
            if lo:
                print(f"    lower: {lo[-1]}")
            if up:
                print(f"    upper: {up[-1]}")
    print("\ngeometry caps the squash; binding caps the drop. The two are "
          "independent, which is why no drop rescues k=9.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
