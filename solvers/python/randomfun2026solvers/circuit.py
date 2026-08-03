#!/usr/bin/env python3
"""Route a little man's track inside one room.

Same idea as layout.py but one level down: layout.py places *rooms* and routes
*pipes*; Circuit places *code segments* and routes the *man's path* between them,
allocating free columns/rows for the vertical/horizontal connectors and failing
loudly on a collision instead of producing a silently-wrong grid.

Directions are (dx, dy) with y growing down. Turn glyphs: > < ^ v.
"""
from __future__ import annotations

E, W, N, S = (1, 0), (-1, 0), (0, -1), (0, 1)
GLYPH = {E: ">", W: "<", N: "^", S: "v"}
# clockwise / counter-clockwise successor of a heading
CW = {E: S, S: W, W: N, N: E}
CCW = {v: k for k, v in CW.items()}


class Collision(RuntimeError):
    pass


class Circuit:
    """A grid of cells; every write is checked against what is already there."""

    def __init__(self, width: int, height: int, *, strict_corridors: bool = False) -> None:
        self.w, self.h = width, height
        self.strict_corridors = strict_corridors
        self.cell: dict[tuple[int, int], str] = {}
        # Cells a corridor walks straight through. Code may not later land on
        # one: the man would execute it mid-transit. Tracked separately because a
        # corridor cell looks like a blank.
        self.reserved: set[tuple[int, int]] = set()

    # -- primitives ----------------------------------------------------------
    def set(self, x: int, y: int, ch: str) -> None:
        if not (0 <= x < self.w and 0 <= y < self.h):
            raise Collision(f"({x},{y}) outside the {self.w}x{self.h} interior")
        old = self.cell.get((x, y))
        if (
            self.strict_corridors
            and ch != " "
            and (x, y) in self.reserved
            and old != ch
        ):
            raise Collision(
                f"({x},{y}) is reserved as a corridor; placing {ch!r} there "
                "would run mid-transit"
            )
        if old is None or old == ch:
            self.cell[(x, y)] = ch
            return
        # A blank is compatible with anything: a man just walks over it.
        if old == " ":
            self.cell[(x, y)] = ch
            return
        if ch == " ":
            return
        raise Collision(f"({x},{y}) holds {old!r}, cannot place {ch!r}")

    def free(self, x: int, y: int) -> bool:
        return 0 <= x < self.w and 0 <= y < self.h and self.cell.get((x, y), " ") == " "

    def get(self, x: int, y: int) -> str:
        return self.cell.get((x, y), " ")

    # -- code segments -------------------------------------------------------
    def run(self, x: int, y: int, ops: str, d=E) -> tuple[int, int]:
        """Lay `ops` starting at (x,y) heading `d`. Returns the cell after the last op."""
        for ch in ops:
            self.set(x, y, ch)
            x, y = x + d[0], y + d[1]
        return x, y

    def blanks(self, x: int, y: int, n: int, d=E) -> None:
        for _ in range(n):
            self.set(x, y, " ")
            x, y = x + d[0], y + d[1]

    # -- connectors ----------------------------------------------------------
    def turn(self, x: int, y: int, d) -> None:
        """Place a turn glyph that sets heading `d` at (x,y)."""
        self.set(x, y, GLYPH[d])

    def path(self, cells: list[tuple[int, int]], *, enter, exit_) -> None:
        """Draw a polyline the man walks: turn glyphs at bends, blanks on straights.

        `enter` is the heading he arrives with at cells[0]; `exit_` is the heading
        he must leave cells[-1] with.
        """
        d = enter
        for i, (x, y) in enumerate(cells):
            nxt = cells[i + 1] if i + 1 < len(cells) else None
            nd = (nxt[0] - x, nxt[1] - y) if nxt else exit_
            if nd != d:
                self.turn(x, y, nd)
                d = nd
            else:
                # Straight run. Crossing a live glyph would silently re-steer the
                # man, so only a blank -- or a turn already pointing the way we
                # are walking (a genuine merge) -- is acceptable here.
                cur = self.get(x, y)
                if cur not in (" ", GLYPH[d]):
                    raise Collision(
                        f"corridor through ({x},{y}) would cross {cur!r} "
                        f"while heading {GLYPH[d]}"
                    )
                self.set(x, y, cur if cur != " " else " ")
                # Endpoints are named handoffs: a following code block may own
                # either one. Only a corridor's interior is permanently transit.
                if i not in (0, len(cells) - 1):
                    self.reserved.add((x, y))

    def vertical(self, x: int, y0: int, y1: int) -> None:
        """Reserve a straight vertical run of blanks (exclusive of endpoints)."""
        step = 1 if y1 > y0 else -1
        for y in range(y0 + step, y1, step):
            if not self.free(x, y):
                raise Collision(f"column {x} blocked at row {y} ({self.get(x,y)!r})")
            self.set(x, y, " ")

    def horizontal(self, y: int, x0: int, x1: int) -> None:
        step = 1 if x1 > x0 else -1
        for x in range(x0 + step, x1, step):
            if not self.free(x, y):
                raise Collision(f"row {y} blocked at column {x} ({self.get(x,y)!r})")
            self.set(x, y, " ")

    def route(self, start: tuple[int, int], enter, corners: list[tuple[int, int]],
              end: tuple[int, int], exit_) -> None:
        """Walk a rectilinear polyline start -> corners… -> end.

        Turn glyphs land on `start` (if it turns), every corner, and `end`;
        straight runs in between are reserved as blanks. Every cell is checked,
        so a corridor that crosses live code raises Collision.
        """
        pts = [start, *corners, end]
        cells: list[tuple[int, int]] = [pts[0]]
        for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
            if x0 != x1 and y0 != y1:
                raise Collision(f"route leg {(x0,y0)}->{(x1,y1)} is not rectilinear")
            sx = (x1 > x0) - (x1 < x0)
            sy = (y1 > y0) - (y1 < y0)
            x, y = x0, y0
            while (x, y) != (x1, y1):
                x, y = x + sx, y + sy
                cells.append((x, y))
        self.path(cells, enter=enter, exit_=exit_)

    def connect(self, src: tuple[int, int], src_dir, dst: tuple[int, int], dst_dir,
                *, via_col: int | None = None, via_row: int | None = None) -> None:
        """Route the man from `src` (heading src_dir) to `dst` (arriving dst_dir).

        Goes straight along src_dir to the turn column/row, turns, travels, then
        turns into dst. `via_col`/`via_row` pick the corridor; by default the
        turn happens at dst's column (vertical-last) .
        """
        sx, sy = src
        dx, dy = dst
        if src_dir in (E, W) and dst_dir in (E, W):
            col = via_col if via_col is not None else dx
            # src row -> col, then vertical to dst row, then into dst
            self.horizontal(sy, sx, col)
            self.turn(col, sy, S if dy > sy else N)
            self.vertical(col, sy, dy)
            self.turn(col, dy, dst_dir)
            self.horizontal(dy, col, dx)
        elif src_dir in (N, S) and dst_dir in (E, W):
            row = via_row if via_row is not None else dy
            self.vertical(sx, sy, row)
            self.turn(sx, row, dst_dir)
            self.horizontal(row, sx, dx)
        else:
            raise NotImplementedError("unsupported connector orientation")

    # -- loops ---------------------------------------------------------------
    def counted_loop(self, x: int, y: int, body: str) -> tuple[int, int]:
        """`b`-counted loop entered heading EAST at (x,y). **Tests before the body**,
        so a count of 0 runs it zero times.

            (x,y)=`>`  (x+1,y)=`d`      d: BP>0 -> turn CW/south into the body,
            (x+1,y+1..)= body            BP==0 -> straight east, out
            (x+1,y+k+1)=`<`  (x,y+k+1)=`^`   bottom corners turn back up
            (x,y+1)=`m`                  decrement on the return leg

        Occupies 2 columns x (len(body)+2) rows. Returns the exit cell.
        """
        k = len(body)
        self.set(x, y, ">")
        self.set(x + 1, y, "d")
        self.run(x + 1, y + 1, body, d=S)
        self.set(x + 1, y + k + 1, "<")
        self.set(x, y + k + 1, "^")
        self.set(x, y + 1, "m")
        self.blanks(x, y + 2, k - 1, d=S)
        return x + 2, y

    def counted_loop_horizontal(
        self,
        x: int,
        y: int,
        body: str,
    ) -> tuple[int, int]:
        """Rotate :meth:`counted_loop` clockwise into a two-row block.

        Enter heading SOUTH at the top-right cell. A positive BP turns west
        through ``body``; zero continues south through the bottom-right exit.

        For ``body="rs"``::

            > mv
            ^srd
        """
        k = len(body)
        if not k:
            raise ValueError("counted loop body cannot be empty")
        right = x + k + 1
        self.set(x, y, ">")
        self.blanks(x + 1, y, k - 1, d=E)
        self.set(x + k, y, "m")
        self.set(right, y, "v")
        self.set(x, y + 1, "^")
        self.run(x + k, y + 1, body, d=W)
        self.set(right, y + 1, "d")
        return right, y + 2

    # -- render --------------------------------------------------------------
    def rows(self) -> list[str]:
        return ["".join(self.get(x, y) for x in range(self.w)) for y in range(self.h)]

    def ruler(self) -> str:
        out = ["    " + "".join(str(x % 10) for x in range(self.w))]
        for y, r in enumerate(self.rows()):
            out.append(f"{y:3} |{r}|")
        return "\n".join(out)


    def counted_ring(self, x: int, y: int, body: str = "rs") -> list[tuple[int, int]]:
        """Counted loop that moves TWO values per lap. Entered heading EAST at (x,y).

        A clockwise ring can host a `d` test at every corner (arriving east, CW is
        south; arriving south, CW is west; and so on), so both side columns carry
        their own `[body, m]` with a test in front of it. BP still counts passes,
        exactly like :meth:`counted_loop`. For the usual two-cell body:

            (x,y)=`>`      (x+1,y)=`d`        <- test, then the right column
            (x,y+1)=`m`    (x+1,y+1)=body[0]
            (x,y+2)=body[1](x+1,y+2)=body[1]
            (x,y+3)=body[0](x+1,y+3)=`m`
            (x,y+4)=`d`    (x+1,y+4)=`<`      <- test, then the left column (going up)

        10 cells, 2 values/lap = 5 ticks/value against 8 for :meth:`counted_loop`.
        A longer body grows both columns symmetrically. Returns the two exit
        cells: [east of the top-right `d`, west of the bottom-left `d`]. Both
        must be routed to the same continuation.
        """
        k = len(body)
        if not k:
            raise ValueError("counted ring body cannot be empty")
        # right column, walked downward: test, body, m
        self.set(x + 1, y, "d")
        self.run(x + 1, y + 1, body + "m", d=S)
        self.set(x + 1, y + k + 2, "<")
        # left column, walked upward: test, body, m
        self.set(x, y + k + 2, "d")
        self.run(x, y + k + 1, body + "m", d=N)
        self.set(x, y, ">")
        return [(x + 2, y), (x - 1, y + k + 2)]

    def counted_ring_horizontal_fanout(
        self,
        x: int,
        y: int,
        body: str = "rs",
        men: int = 2,
        pitch: int = 4,
    ) -> list[tuple[int, int]]:
        """``men`` copies of :meth:`counted_ring_horizontal`, fed by a ``Y`` cascade.

        A single man in a ring spends 5 ticks a value (``r`` ``s`` ``m`` ``d``
        and a turn) but the ring's two pipes each move **one value a tick**, so
        one man leaves four fifths of the throughput unused.  ``men`` runners on
        ``men`` rings divide the wall clock by ``men`` until the pipes saturate
        at five.

        Entered heading **south** at ``(x + len(body) + 3, y - 1)`` with
        ``BP = count // men``; the caller owes the shifts.  Each ``Y`` is entered
        heading south, so its clockwise child is born **west** (into that level's
        ring) and its counter-clockwise child **east** (carrying on down).  The
        cascade therefore drifts one column east a level, and each ring drifts
        with it.

        Ordering is by construction rather than by agreement.  Runners act in
        creation order and ``Y``'s clockwise child keeps the parent's slot, so
        man *i* is strictly older than man *i+1*; he also enters his ring
        ``pitch - 1`` cells sooner.  Man ``men-1`` is therefore last in and last
        out, which is why only his exits are returned — every earlier ring's two
        exits get an ``H``.

        Returns the last ring's ``[south exit, north exit]``.
        """
        if men < 1:
            raise ValueError(f"a fan-out needs at least one man, not {men}")
        if men == 1:
            return self.counted_ring_horizontal(x, y, body)
        if pitch < 4:
            # Two rows of ring plus **two** spare: one carries the level's own
            # south exit `H`, the other is the odd-exit merge corridor of the
            # ring below it. At pitch 3 they are the same row and collide.
            raise ValueError(f"rings need two rows and two spare: pitch {pitch} < 4")
        k = len(body)
        exits: list[tuple[int, int]] = []
        for i in range(men):
            # Every level's fork stands at ``rgt + 1`` and sends its east child
            # down ``rgt + 2``, which is the *next* level's fork column — so the
            # rings drift one column east a level and the cascade stays aligned.
            # The last ring has no fork of its own, so the man arrives one column
            # east of where the others do and the ring has to move with him;
            # without this he descends past its entry into the wall.
            rx = x + i + (1 if i == men - 1 else 0)
            ry = y + pitch * i
            rgt = rx + k + 2
            got = self.counted_ring_horizontal(rx, ry, body)
            if i == men - 1:
                # **Merge the odd exit rather than returning it.** A single-man
                # ring leaves by the south exit whenever the count's parity says
                # so, and its callers discard the north one; halving BP for the
                # fan-out changes that parity, so the north exit becomes
                # reachable and walks the man back into the ring above.  Send him
                # east to the entry column instead and drop him back in: BP is
                # zero by then, so the `d` passes him straight out south and both
                # parities leave by the one exit the caller routes.
                ox, oy = got[1]
                self.set(ox, oy, ">")
                for cx in range(ox + 1, rgt):
                    self.set(cx, oy, ".")
                self.set(rgt, oy, "v")
                exits = [got[0], got[0]]
                break
            # this level's fork, one row above its ring
            self.set(rgt + 1, ry - 1, "Y")
            self.set(rgt, ry - 1, "v")      # clockwise child -> down into the ring
            self.set(rgt + 2, ry - 1, "v")  # counter-clockwise child -> down the cascade
            self.blanks(rgt + 2, ry, pitch - 2, S)
            for ex in got:                  # this man is not the one who continues
                self.set(ex[0], ex[1], "H")
        return exits

    def counted_ring_horizontal(
        self,
        x: int,
        y: int,
        body: str = "rs",
    ) -> list[tuple[int, int]]:
        """Rotate :meth:`counted_ring` clockwise into a two-row block.

        Enter heading SOUTH at the top-right cell.  As with
        :meth:`counted_ring`, BP is tested once for each value, so odd counts
        leave through the second exit without consuming an extra value.  For
        ``body="rs"``::

            d r s m v
            ^ m s r d

        Returns ``[south exit, north exit]``.  A useful compact arrangement
        routes the north (odd-tail) exit east across the row above the ring and
        back through the entry; BP is then zero and the runner immediately
        takes the single south exit.
        """
        k = len(body)
        if not k:
            raise ValueError("counted ring body cannot be empty")
        right = x + k + 2
        # Bottom side, walked west: test, body, m.
        self.set(right, y + 1, "d")
        self.run(right - 1, y + 1, body + "m", d=W)
        self.set(x, y + 1, "^")
        # Top side, walked east: test, body, m.
        self.set(x, y, "d")
        self.run(x + 1, y, body + "m", d=E)
        self.set(right, y, "v")
        return [(right, y + 2), (x, y - 1)]
