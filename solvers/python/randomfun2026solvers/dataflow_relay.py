#!/usr/bin/env python3
"""Turnaround rooms whose throughput scales with their perimeter.

A pipe ring needs two rooms — a pipe may not loop back to its own room — so every
ring pays for a **turnaround room** that does nothing but receive a word and send
it on.  ``value_ring.RELAY`` is the minimal such room::

    +----+
    |@ >v|
    |  sr|
    |  ^<|
    +----+

Its walking cycle is 6 cells and carries **one** word per lap, so it caps the ring
at 6 ticks per rotation *no matter how fast the worker rotates*.  That cap is not
obvious from the worker's side, and it is the reason
``DATAFLOW-SURVEY.md`` §4.4's modelled ``b = 3.2 ticks/rotation`` was unreachable
with the relay the repo actually had: measured on the engine, a worker loop tuned
from 8 down to 3.5 ticks/rotation produced **6.0 either way** (see
``tests/test_dataflow_relay.py``).

The fix is geometric.  A room with interior ``w*h`` has a perimeter walk of
``2(w+h)-4`` cells; four are corner turns and one is the spawn ``@``, and the rest
alternate ``r``/``s``, so the room carries ``(2(w+h)-9)//2`` words per lap and the
cost per word falls toward **2 ticks**::

    +------+          +--------+
    |>@rsrv|          |>@rsrsrv|
    |     s|          |       s|
    |s    r|          |s      r|
    |^rsrs<|          |r      s|
    +------+          |s      r|
     5 words/lap      |^rsrsrs<|
     3.20 ticks/word  +--------+
                       9 words/lap, 2.67 ticks/word

Two things make this safe rather than clever:

* **Binding is unambiguous however many ``r``/``s`` the room holds.** A turnaround
  room has exactly one incoming and one outgoing pipe, so SPEC's "nearest, not
  nearest-ready" rule has nothing to choose between — the usual failure mode of a
  multi-pipe room (a silent wrong read) cannot arise here.
* **Order is preserved.** One man walks one cycle, receiving and sending
  alternately, so the room is a FIFO of depth 1 repeated — never a reorder buffer.

Measured cost per rotation is ``max(2 + 3/m, (2(w+h)-4)/words)`` where ``m`` is the
worker's :meth:`~randomfun2026solvers.circuit.Circuit.counted_ring` width; both
bounds were confirmed to the tick, so a ring is sized by making the *cheaper* term
the binding one rather than by guessing.
"""

from __future__ import annotations

__all__ = ["ROTATION_MODEL", "relay", "relay_words", "ticks_per_rotation"]


def relay_words(w: int, h: int) -> int:
    """How many words a ``w*h``-interior turnaround room carries per lap."""
    if w < 3 or h < 3:
        raise ValueError(f"interior {w}x{h}: a perimeter walk needs at least 3x3")
    # perimeter, less four corner turns, less the spawn cell, in r/s pairs
    return (2 * (w + h) - 4 - 4 - 1) // 2


def relay(w: int, h: int) -> list[str]:
    """Art for a turnaround room with a ``w*h`` interior, walked clockwise.

    The man spawns at the top-left-plus-one facing east, runs the perimeter, and
    alternates ``r``/``s`` over every cell that is not a corner turn.  Any odd cell
    left over after pairing is a blank, so the walk never sends what it has not
    received.
    """
    relay_words(w, h)  # rejects an interior too small to hold a perimeter walk
    path = (
        [(x, 0) for x in range(w)]
        + [(w - 1, y) for y in range(1, h)]
        + [(x, h - 1) for x in range(w - 2, -1, -1)]
        + [(0, y) for y in range(h - 2, 0, -1)]
    )
    cell: dict[tuple[int, int], str] = {
        (0, 0): ">",
        (w - 1, 0): "v",
        (w - 1, h - 1): "<",
        (0, h - 1): "^",
    }
    free = [p for p in path if p not in cell]
    cell[free[0]] = "@"
    ops = free[1:]
    for i in range(len(ops) // 2):
        cell[ops[2 * i]] = "r"
        cell[ops[2 * i + 1]] = "s"
    art = ["+" + "-" * w + "+"]
    for y in range(h):
        art.append("|" + "".join(cell.get((x, y), " ") for x in range(w)) + "|")
    art.append("+" + "-" * w + "+")
    return art


def ticks_per_rotation(m: int, w: int, h: int) -> float:
    """Cost of one ring rotation: the worse of the worker's and the relay's laps.

    ``m`` is the width of the worker's ``counted_ring`` (values moved per half-lap):
    it walks ``4m+6`` cells to move ``2m`` values, hence ``2 + 3/m``.  The relay
    walks its whole perimeter to move :func:`relay_words` words.  Verified against
    the engine at five points in ``tests/test_dataflow_relay.py``.
    """
    return max(2 + 3 / m, (2 * (w + h) - 4) / relay_words(w, h))


#: ``(worker m, relay w, relay h) -> measured ticks per rotation``. Engine-measured
#: on the probe in ``tests/test_dataflow_relay.py``; the model above reproduces
#: every row exactly, which is why it is quoted as fact rather than as an estimate.
ROTATION_MODEL: dict[tuple[int, int, int], float] = {
    (1, 4, 3): 5.0,
    (2, 4, 3): 5.0,
    (2, 6, 4): 3.5,
    (3, 6, 4): 3.2,
    (3, 8, 6): 3.0,
}


if __name__ == "__main__":
    for w, h in ((4, 3), (6, 4), (8, 6), (10, 8)):
        words = relay_words(w, h)
        print(
            f"interior {w}x{h}  perimeter {2 * (w + h) - 4}  words/lap {words}  "
            f"{(2 * (w + h) - 4) / words:.3f} ticks/word"
        )
        print("\n".join("    " + r for r in relay(w, h)))
