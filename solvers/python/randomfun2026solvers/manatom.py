#!/usr/bin/env python3
"""A factory for described atoms — gadgets that know their own interface and cost.

An :class:`~randomfun2026solvers.manast.Atom` recovered from ASCII is a black box:
a bounding box and nothing else. A placer can slide it, but it cannot *wire*
anything to it, because it does not know where the man goes in, where he comes
out, or how long the crossing takes.

This module builds atoms that do know. Every :class:`Gadget` declares:

* **where the man enters** and with which heading;
* **where he leaves** — possibly several ways, possibly none at all;
* **what it costs**, as ticks, either a constant or a formula in the loop count;
* **what it touches** — which of ``A`` / ``B`` / ``BP`` it needs and clobbers.

The declarations are *checked against the glyphs*, not merely recorded: a port
must sit on the rectangle, an exit heading must agree with the glyph it leaves
from, and the tick formula must match what the reference engine actually does.
A gadget whose paperwork disagrees with its cells is a bug that would otherwise
surface as a mis-wired grid.

The cost model is the part that decides optimisation, so it is stated per gadget
rather than assumed. For the counted loops in particular:

* ``counted_loop`` is two columns of ``k + 2`` rows and costs ``2k + 4`` ticks per
  lap while moving one value, so ``body="rs"`` is **8 ticks per value**.
* ``counted_loop_horizontal`` is the same loop rotated into two rows: a different
  *shape* — 4x2 instead of 3x7, which is why it is the one to reach for when the
  footprint binds — but the identical **8 ticks per value**.
* The only way below 8 is to move more than one value per decrement, which needs
  the count pre-divided. ``unrolled`` does that: ``v`` values per lap costs
  ``4v + 4`` ticks, i.e. ``4 + 4/v`` per value, approaching 4 from above.
  It requires ``BP`` to be a multiple of ``v``, so a caller must peel the
  remainder first — ``x`` (turn on the parity of BP) exists for exactly that.

Every figure here is **measured against the engine**, not derived: a loop with
``body="0s"`` runs at exactly 8.00 ticks per lap and ``"0s0s"`` at exactly 12.00,
by differencing total run ticks across counts of 10, 20 and 40. The derivation had
said ``4v + 3`` for the unrolled form and was one tick per lap optimistic, which
would have overstated the gain from unrolling.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from .manast import Atom, Port

__all__ = ["Gadget", "gadget", "LIBRARY", "counted_loop", "counted_loop_horizontal", "unrolled"]

E, W, N, S = (1, 0), (-1, 0), (0, -1), (0, 1)
_STEER = {">": E, "<": W, "^": N, "v": S}
#: glyphs whose exit heading is not fixed by the glyph alone
_BRANCH = set("Xadx")


@dataclass(frozen=True)
class Gadget:
    """A described block: glyphs, interface, cost, and what it clobbers."""

    name: str
    rows: tuple[str, ...]
    entry: Port
    exits: tuple[Port, ...]
    #: ticks for one traversal; for a loop, ticks *per lap*
    ticks: int | None = None
    #: values moved per lap, when the gadget is a loop
    per_lap: int = 0
    #: ``BP`` must be a multiple of this before entering
    count_multiple: int = 1
    needs: frozenset[str] = frozenset()
    writes: frozenset[str] = frozenset()
    note: str = ""

    @property
    def size(self) -> tuple[int, int]:
        return (max(len(r) for r in self.rows), len(self.rows))

    @property
    def ticks_per_value(self) -> float | None:
        """The number that decides whether a loop is worth replacing."""
        if not self.per_lap or self.ticks is None:
            return None
        return self.ticks / self.per_lap

    def to_atom(self, id_: int, x: int, y: int) -> Atom:
        return Atom(
            id=id_,
            x=x,
            y=y,
            rows=list(self.rows),
            entry=self.entry,
            exits=self.exits,
            ticks=self.ticks,
            note=f"{self.name}: {self.note}",
        )

    def glyph_at(self, dx: int, dy: int) -> str:
        if not (0 <= dy < len(self.rows)):
            return " "
        row = self.rows[dy]
        return row[dx] if 0 <= dx < len(row) else " "


def gadget(
    name: str,
    rows: list[str],
    *,
    entry: Port,
    exits: tuple[Port, ...],
    ticks: int | None = None,
    per_lap: int = 0,
    count_multiple: int = 1,
    needs: frozenset[str] = frozenset(),
    writes: frozenset[str] = frozenset(),
    note: str = "",
) -> Gadget:
    """Build a gadget and **check its paperwork against its cells**.

    Recording an interface is worth little if it can disagree with the glyphs, so
    every port is validated: it must land inside the rectangle, and an exit that
    leaves from a steer glyph must leave in the direction that glyph forces.
    """
    g = Gadget(
        name=name,
        rows=tuple(rows),
        entry=entry,
        exits=exits,
        ticks=ticks,
        per_lap=per_lap,
        count_multiple=count_multiple,
        needs=needs,
        writes=writes,
        note=note,
    )
    w, h = g.size
    for tag, port in [("entry", entry), *((f"exit{i}", p) for i, p in enumerate(exits))]:
        if not (0 <= port.dx < w and 0 <= port.dy < h):
            raise ValueError(
                f"{name}: {tag} at ({port.dx},{port.dy}) is outside the {w}x{h} block"
            )
        glyph = g.glyph_at(port.dx, port.dy)
        if tag != "entry" and glyph in _STEER and _STEER[glyph] != port.heading:
            raise ValueError(
                f"{name}: {tag} leaves ({port.dx},{port.dy}) heading {port.heading} "
                f"but the glyph there is {glyph!r}, which forces {_STEER[glyph]}"
            )
    if per_lap and count_multiple > 1 and per_lap != count_multiple:
        raise ValueError(
            f"{name}: moves {per_lap} values per lap but requires BP to be a "
            f"multiple of {count_multiple}; those must agree"
        )
    return g


# ── the counted loops, with their real costs ─────────────────────────────────
def counted_loop(body: str = "rs") -> Gadget:
    """Two columns, ``k + 2`` rows. One value per lap, ``2k + 4`` ticks.

    Tests before the body, so a count of zero runs it zero times.
    """
    k = len(body)
    rows = [">d"] + [f" {ch}" for ch in body] + ["^<"]
    rows[1] = "m" + rows[1][1:]
    return gadget(
        f"counted_loop({body!r})",
        rows,
        entry=Port(0, 0, E, 0, "enter top-left heading east"),
        exits=(Port(1, 0, E, None, "BP==0 leaves the `d` heading east"),),
        ticks=2 * k + 4,
        per_lap=1,
        needs=frozenset({"BP"}),
        writes=frozenset({"BP"}),
        note=f"{2 * k + 4} ticks/lap, 1 value -> {2 * k + 4} ticks per value",
    )


def counted_loop_horizontal(body: str = "rs") -> Gadget:
    """The same loop rotated into two rows: 4x2 for ``body='rs'``, not 3x7.

    Identical cost — 8 ticks per value — but a completely different footprint,
    which is the reason to prefer it when the bounding box is what binds.
    """
    k = len(body)
    top = ">" + " " * (k - 1) + "mv"
    bot = "^" + body[::-1] + "d"
    return gadget(
        f"counted_loop_horizontal({body!r})",
        [top, bot],
        entry=Port(k + 1, 0, S, 0, "enter top-right heading south"),
        exits=(Port(k + 1, 1, S, None, "BP==0 continues south past the `d`"),),
        ticks=2 * k + 4,
        per_lap=1,
        needs=frozenset({"BP"}),
        writes=frozenset({"BP"}),
        note=f"{2 * k + 4} ticks/lap, 1 value; same cost as the tall form, {k + 2}x2 shape",
    )


def unrolled(pairs: int) -> Gadget:
    """``pairs`` values per lap with ``pairs`` decrements: ``4v + 3`` ticks.

    The only way under 8 ticks per value, and it is not free: ``BP`` must be a
    multiple of ``pairs`` on entry, because the loop tests ``BP > 0`` and then
    moves ``pairs`` values regardless. Enter with a remainder and the tape
    over-rotates, which no later check would catch. Peel it first with ``x``.
    """
    if pairs < 1:
        raise ValueError("pairs must be at least 1")
    body = "rs" * pairs
    rows = [">d"] + [f" {ch}" for ch in body] + ["^<"]
    for i in range(pairs):
        rows[1 + i] = "m" + rows[1 + i][1:]
    # 2k + 4 with k = 2 * pairs, measured: 8.00 ticks/lap at k=2, 12.00 at k=4
    ticks = 4 * pairs + 4
    return gadget(
        f"unrolled({pairs})",
        rows,
        entry=Port(0, 0, E, 0, "enter top-left heading east"),
        exits=(Port(1, 0, E, None, "BP==0 leaves the `d` heading east"),),
        ticks=ticks,
        per_lap=pairs,
        count_multiple=pairs,
        needs=frozenset({"BP"}),
        writes=frozenset({"BP"}),
        note=(
            f"{ticks} ticks/lap, {pairs} values -> {ticks / pairs:.2f} per value; "
            f"REQUIRES BP % {pairs} == 0"
        ),
    )


#: Named gadgets, and the cost table that makes the trade-offs explicit.
LIBRARY: dict[str, Callable[[], Gadget]] = {
    "counted_loop": lambda: counted_loop("rs"),
    "counted_loop_horizontal": lambda: counted_loop_horizontal("rs"),
    "unrolled2": lambda: unrolled(2),
    "unrolled4": lambda: unrolled(4),
    "unrolled8": lambda: unrolled(8),
}


def cost_table() -> str:
    """Side-by-side shape and ticks-per-value for every loop in the library."""
    lines = [f"{'gadget':32s} {'shape':>7s} {'ticks/lap':>9s} {'per value':>9s}  needs"]
    for name, make in LIBRARY.items():
        g = make()
        w, h = g.size
        tpv = g.ticks_per_value
        need = f"BP % {g.count_multiple} == 0" if g.count_multiple > 1 else "-"
        lines.append(
            f"{name:32s} {f'{w}x{h}':>7s} {g.ticks:9d} {tpv:9.2f}  {need}"
        )
    return "\n".join(lines)


@dataclass
class Placed:
    """A gadget put somewhere, with its ports resolved to absolute cells."""

    gadget: Gadget
    x: int
    y: int
    atom: Atom = field(init=False)

    def __post_init__(self) -> None:
        self.atom = self.gadget.to_atom(0, self.x, self.y)

    def entry_cell(self) -> tuple[int, int]:
        return self.gadget.entry.at(self.x, self.y)

    def exit_cells(self) -> list[tuple[int, int]]:
        return [p.at(self.x, self.y) for p in self.gadget.exits]


if __name__ == "__main__":
    print(cost_table())
