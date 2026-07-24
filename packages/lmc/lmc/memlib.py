"""Memory & list codegen — the ring-as-array building block.

The machine has **no RAM**: state lives in registers (`A`, `B`, `BP`) or in
**pipe cells** (a length-L pipe is an L-slot FIFO). A *list* is therefore built as
a **ring**: a CPU wired to a BUF forwarder by two pipes — `up` (CPU→ring, append
at the tail) and `down` (ring→CPU, pop the head). The BUF just bounces values, so
they circulate forever; a value kept cycling and not consumed *is* stored memory.

Two invariants make this composable, and every fragment below relies on them:

- **Ring ops touch only `A`.** `r`/`s` read/write `A` and never `B`/`BP`, so a
  loop counter in `B` or `BP` survives an arbitrary number of memory accesses.
  This is why `reverse` needs no spill: `rem` sits in `B` across every rotate.
- **`r` blocks until a value is present.** A `dequeue` on an empty pipe slot waits
  rather than erroring, so a ring self-synchronises — you never race the BUF.

Ring convention used throughout: `up` = enqueue (append at tail), `down` =
dequeue (pop head). "Rotate once" = pop the head and re-append it = `r down ; s up`,
which also leaves the popped value in `A` (so it doubles as "peek head, advance").

These fragments are the exact ones `demos.reverse_program` was hand-written from;
`tests/test_memlib.py` rebuilds reverse out of them and checks it byte-for-byte
against the reference engine, so the block is correct by construction.
"""

from __future__ import annotations

from .blockspec import Instr
from .loopgen import linear_block, seq_block, while_loop
from .trail import TrailLayout

Op = Instr


# --- element primitives (each is one or two trail cells; B and BP survive) ----

def read_from(src: str) -> list[Instr]:
    """`A = recv(src)` — pull one value from an external in-pipe into A."""
    return [Op("r", src)]


def emit_to(out: str) -> list[Instr]:
    """`send(out, A)` — push A to an external out-pipe."""
    return [Op("s", out)]


def enqueue(up: str) -> list[Instr]:
    """`ring.append(A)` — store A at the ring tail."""
    return [Op("s", up)]


def dequeue(down: str) -> list[Instr]:
    """`A = ring.pop_head()` — remove and return the ring head."""
    return [Op("r", down)]


def rotate_once(down: str, up: str) -> list[Instr]:
    """Move the head to the tail; `A` is left holding that value.

    So this is both "rotate the ring by one" and "peek the head, then advance".
    The ring's contents are unchanged in cyclic order, only the head pointer moves.
    """
    return [Op("r", down), Op("s", up)]


def length_to_bp(down: str) -> list[Instr]:
    """`BP = len(ring)` via `q` on the nearest in-pipe (a length check).

    Note `q` counts one pipe's live slots, not total ring occupancy — reliable
    only at a sync point where the whole list sits in that pipe. Prefer carrying a
    known count (e.g. the input's `n`) when you have one.
    """
    return [Op("q", down)]


# --- counted loops over the ring (caller sets BP to the trip count) -----------

def load_run(src: str, up: str) -> TrailLayout:
    """Append `BP` values read from `src` into the ring (zero-trip).

    `a += [recv() for _ in range(BP)]`. Body reads one value, enqueues it, and
    decrements the counter. Used to slurp a length-prefixed input list.
    """
    return while_loop([], [Op("d")], linear_block([*read_from(src), *enqueue(up), Op("m")]), [])


def rotate_run(down: str, up: str) -> TrailLayout:
    """Rotate the ring `BP` times (zero-trip) — bring element `BP` to the head.

    `a[i]` addressing: set `BP = i`, run this, and the head is now `a[i]`; the last
    rotated value is left in `A`. Random access is O(i); sequential is O(1)/step.
    """
    return while_loop([], [Op("d")], linear_block([*rotate_once(down, up), Op("m")]), [])


def drain_run(down: str, out: str) -> TrailLayout:
    """Pop `BP` heads to `out` (zero-trip) — emit and consume the list front.

    `for _ in range(BP): emit(a.pop_head())`. Empties that many cells from the ring.
    """
    return while_loop([], [Op("d")], linear_block([*dequeue(down), *emit_to(out), Op("m")]), [])


def pop_emit(down: str, out: str) -> list[Instr]:
    """Extract the current head and send it out (one element, no loop)."""
    return [*dequeue(down), *emit_to(out)]


# --- composed list programs ---------------------------------------------------

def reverse_round(src: str, out: str, down: str, up: str) -> TrailLayout:
    """One round of `reverse_list`, built entirely from the fragments above.

    read n -> append n values -> emit head-of-reversed n times. The remaining
    count `rem` lives in `B` (survives every ring op); the rotate counter is `BP`.
    Emits `a[n-1] .. a[0]` by rotating `rem-1` then extracting the head.
    """
    push = while_loop(
        prologue=[Op("@"), *read_from(src), Op("M"), Op("b")],  # A=n, B=n(rem), BP=n
        test=[Op("d")],
        body=linear_block([*read_from(src), *enqueue(up), Op("m")]),
        epilogue=[],
    )
    emit = while_loop(
        prologue=[],
        test=[Op("W"), Op("M"), Op("X")],  # bring rem to A, restore B, loop while rem>0
        body=seq_block(
            [
                linear_block([Op("b"), Op("m")]),  # BP = rem-1 (rotations)
                rotate_run(down, up),
                linear_block(pop_emit(down, out)),  # extract head a[rem-1], emit
                linear_block([Op("W"), Op("M"), Op("1"), Op("-"), Op("N"), Op("M")]),  # rem--
            ]
        ),
        epilogue=[],
    )
    return seq_block([push, emit])
