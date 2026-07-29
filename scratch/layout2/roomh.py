"""Room H's placement, solved rather than discovered — and the store is the wall.

The ``SQUASH_BAND`` docstring frames this as "whether room H can park elsewhere",
under "a hard full-width constraint". Captured and solved, the constraint is much
narrower than that, and it is not really about H's width at all.

``_seek_teleport`` anchors H's **bottom** to the store's underside::

    hy1 = y_b + 1              y_b = SY - 2, and (SX, SY) is placed at CY + H + 1
    hy0 = hy1 - (_TELE_H + 1)  so the initial room is four rows
    clear(hx0, hy0, hx1, hy1)  over H's whole width, else MachineError

then raises ``hy0`` opportunistically while the rows above stay clear. So H is a
*bottom-anchored, four-row minimum, opportunistically-grown* room, and the only
quantity that matters is

    available(k) = hy1(k) - floor_y = (y_b0 + 1 - k) - floor_y

where ``floor_y`` is the first occupied row above H across its width — the store
block's underside — and ``k`` is the squash. H builds iff ``available(k) >= 4``.

Both ends move for different reasons, which is the whole reason a squash breaks it:

* ``hy1`` follows ``CY + H`` through the STREAM unit, so **it moves north with k**;
* ``floor_y`` is the store, anchored to ``CY``, which a squash does not move.

So a squash squeezes the band from one side only, and the ceiling is arithmetic:

    k <= y_b0 + 1 - floor_y - 4

On the shipped machine ``y_b0 = 205`` and ``floor_y = 194``, giving ``k <= 8`` —
which is exactly where the built grid stops. H's own height comes out ``12 - k``,
also exactly as built. Nothing here is a distance, so no ``ROM_TOUCH_DROP`` helps,
and the docstring is right about that.

**The finding: rehousing H is the wrong repair.** H already sits in the largest
clear band available and grows into whatever it is given. What blocks ``k > 8`` is
that the **store cannot follow the CPU north** — and ``store_offset`` dy, the
registry that would move it, does not route on hires *at all*: dy=-1..-20 every
one fails with a collision around ``(64, 128)``, and dy=-2 fails identically with
the squash off, so it is an independent obstruction and not a squash interaction.

That reframes the open problem from "find room H a new home" (it does not need one)
to "unblock the store's northward travel", which is a routing question about the
adapter's request legs.
"""

from __future__ import annotations

from dataclasses import dataclass

from .capture import Capture, capture

#: ``_TELE_H + 2``: two interior rows for the teleport gadget plus two walls.
H_MIN_ROWS = 4


@dataclass
class BandModel:
    """Room H's feasible squash depth, solved from one capture."""

    y_b0: int
    floor_y: int
    available0: int

    @property
    def k_max(self) -> int:
        """The deepest squash room H survives."""
        return self.available0 - H_MIN_ROWS

    def available(self, k: int) -> int:
        return self.available0 - k

    def height(self, k: int) -> int:
        """H's grown height, which is the whole band it is given."""
        return self.available(k)

    def explain(self, k: int) -> str:
        a = self.available(k)
        verdict = "builds" if a >= H_MIN_ROWS else "refused"
        return (f"k={k}: available={a} rows (need {H_MIN_ROWS}) -> {verdict}"
                f"{'' if a < H_MIN_ROWS else f', H is {a} rows tall'}")


def model(cap: Capture | None = None) -> BandModel:
    """Read the band off a real build (k=0) and return the solved model."""
    if cap is None:
        cap = capture()
    s = cap.seek
    if not s or s.get("floor_y") is None:
        raise RuntimeError(f"no room-H geometry captured (seek={s})")
    return BandModel(y_b0=s["y_b"], floor_y=s["floor_y"], available0=s["available"])


def main() -> int:
    cap = capture()
    print(f"capture: config {cap.config}  build {cap.error or f'ok {cap.box}'}")
    print(f"  seek geometry: {cap.seek}")
    m = model(cap)
    print(f"\nroom H band model: y_b0={m.y_b0} floor_y={m.floor_y} "
          f"available={m.available0} rows")
    print(f"  solved ceiling: k <= {m.k_max}")
    for k in range(0, m.k_max + 3):
        print(f"  {m.explain(k)}")
    print("\nthe built grid (squash_grid.py) stops at k=8 and H's heights were "
          "12-k; compare above.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
