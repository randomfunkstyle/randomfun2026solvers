"""Stores and collection ADTs — the data-abstraction layer.

One physical substrate: a **ring** (a circulating pipe store, drawn by
`router.render` as a forwarder BUF). A **cell** is just a ring used one-word-deep.
On top of the ring sit three access disciplines — `List` (sequential), `Array`
(random access), `Stack` (LIFO) — so a program is written against an ADT and the
*representation* can be swapped later for scoring without touching the program.

Register-footprint discipline (the contract that makes fragments compose): every
ring op touches only **A**, so a value parked in **B** and any **BP** counter survive
it -- *except* the counted runs (`rotate_run`/`load_run`/`drain_run`) and
`Array.get/set`, which consume **BP** as their loop counter (noted per method).
`replace_head` also clobbers **B** (leaves the old head there).

A store contributes rooms+pipes to a `BlockGraph`; `router.render` draws the BUF
geometry from the pipe sides (N ring / S spill), so the ids here are just wiring.
"""

from __future__ import annotations

from .blockspec import E, Instr, N, Pipe, S, W
from .loopgen import linear_block, while_loop
from .trail import TrailLayout

Op = Instr

_OPP = {N: S, S: N, E: W, W: E}


# --- physical stores ----------------------------------------------------------

class RingStore:
    """A circulating word store on one CPU side (default North).

    `up` = CPU -> BUF (append at tail); `down` = BUF -> CPU (pop the head).
    """

    def __init__(self, id: str, side: str = N):
        self.id = id
        self.side = side
        self.up = f"{id}_up"
        self.down = f"{id}_down"
        self.buf = f"{id}_buf"

    # --- graph wiring ---
    def rooms(self) -> dict[str, str]:
        return {self.buf: "buf"}

    def pipes(self, cpu: str) -> list[Pipe]:
        opp = _OPP[self.side]
        return [
            Pipe(self.up, cpu, self.side, self.buf, opp),  # CPU -> BUF (append)
            Pipe(self.down, self.buf, opp, cpu, self.side),  # BUF -> CPU (pop head)
        ]

    # --- element fragments (touch only A; B/BP survive) ---
    def enqueue(self) -> list[Instr]:
        """ring.append(A) -- store A at the tail."""
        return [Op("s", self.up)]

    def dequeue(self) -> list[Instr]:
        """A = ring.pop_head() -- remove and return the head."""
        return [Op("r", self.down)]

    def rotate_once(self) -> list[Instr]:
        """Move head to tail; A = that value (peek head, advance)."""
        return [Op("r", self.down), Op("s", self.up)]

    def replace_head(self) -> list[Instr]:
        """Replace the head with the value in B; clobbers A and B (=old head)."""
        return [Op("r", self.down), Op("W"), Op("s", self.up)]

    def length_to_bp(self) -> list[Instr]:
        """BP = live count of the down-pipe (reliable only at a sync point)."""
        return [Op("q", self.down)]

    # --- counted runs (caller sets BP to the trip count; each consumes BP) ---
    def rotate_run(self) -> TrailLayout:
        """Rotate the ring BP times (bring element BP to the head)."""
        return while_loop([], [Op("d")], linear_block([*self.rotate_once(), Op("m")]), [])

    def load_run(self, src: str) -> TrailLayout:
        """Append BP values read from `src`."""
        return while_loop([], [Op("d")], linear_block([Op("r", src), *self.enqueue(), Op("m")]), [])

    def drain_run(self, out: str) -> TrailLayout:
        """Pop BP heads to `out`."""
        return while_loop([], [Op("d")], linear_block([*self.dequeue(), Op("s", out), Op("m")]), [])


class CellStore(RingStore):
    """A one-word spill cell (a degenerate ring). Default South, out of the ring's way.

    `store` parks A; `take` pulls it back out (consuming); `peek` reads it while
    keeping it (rotate-in-place). Use store/take in pairs, or peek to read-and-keep.
    """

    def __init__(self, id: str, side: str = S):
        super().__init__(id, side)

    def store(self) -> list[Instr]:
        """spill = A (park a value)."""
        return self.enqueue()

    def take(self) -> list[Instr]:
        """A = spill (pull the value out, emptying the cell)."""
        return self.dequeue()

    def peek(self) -> list[Instr]:
        """A = spill, and keep it (read without emptying)."""
        return self.rotate_once()


# --- collection ADTs (access disciplines over a RingStore) --------------------

class List:
    """Sequential/growable view: append & pop-head are O(1); iterate by rotate."""

    def __init__(self, ring: RingStore):
        self.ring = ring

    def append(self) -> list[Instr]:
        return self.ring.enqueue()

    def pop_head(self) -> list[Instr]:
        return self.ring.dequeue()

    def step(self) -> list[Instr]:
        """One iteration step: bring the next element to A (and advance)."""
        return self.ring.rotate_once()

    def load_run(self, src: str) -> TrailLayout:
        return self.ring.load_run(src)

    def drain_run(self, out: str) -> TrailLayout:
        return self.ring.drain_run(out)


class Array:
    """Random-access view: get(i)/set(i) via rotate-to-index + canonical restore.

    Precondition: index in A. Both consume BP (rotate counters) and assume the ring
    holds exactly `n` elements at canonical rotation. O(n) per access.
    """

    def __init__(self, ring: RingStore, n: int):
        self.ring = ring
        self.n = n


class Stack:
    """LIFO view: push is O(1) (append at tail); pop/top rotate to expose the tail."""

    def __init__(self, ring: RingStore):
        self.ring = ring

    def push(self) -> list[Instr]:
        return self.ring.enqueue()
