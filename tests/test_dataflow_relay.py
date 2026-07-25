"""What a pipe-ring rotation actually costs, measured rather than modelled.

``DATAFLOW-SURVEY.md`` §1 quotes "one rotation of a pipe ring (``r``+``s`` in a
counted loop) = ~3.2 ticks", and §4.4 prices the whole ``subset-sum`` design on
``b = 3.2``.  Both numbers were inherited, not measured, and the first thing this
module found is that **with the relay the repo actually has, a rotation costs 6.0
ticks and no amount of worker tuning changes it**:

* ``circuit.counted_loop("rs")`` walks 8 cells per value  -> measured **8.0**
* ``circuit.counted_ring("rs" * m)`` walks ``2 + 3/m``    -> measured **6.0 at
  m = 1, 2 and 3 alike**

The worker got more than twice as fast and total throughput did not move, because
the binding constraint is the *turnaround room*: ``value_ring.RELAY`` is a 6-cell
walking cycle carrying one word per lap, so it caps the ring at 6 ticks/rotation.
That cap is invisible from the worker's side and would have silently doubled the
ring term of any design costed on §1's table.

:mod:`randomfun2026solvers.dataflow_relay` removes it geometrically -- a room with
a longer perimeter carries more words per lap -- and the measured cost becomes

    ticks per rotation = max(2 + 3/m, (2(w+h) - 4) / relay_words(w, h))

which reproduces every engine measurement below **exactly**.  So 3.2 is reachable
after all, at ``m = 3`` with a 6x4 relay, and 2.67 is reachable with an 8x6 one.

The probe is a worker that loads ``L`` values into a ring, rotates ``N`` times
under a counted loop, then emits one value; the slope of (tick of first output) vs
``N`` is the cost per rotation.  It is exact, not fitted: every slope below is a
whole or half tick with zero residual.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
PKG = REPO / "solvers" / "python"
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

from randomfun2026solvers.circuit import Circuit, E, S, W  # noqa: E402
from randomfun2026solvers.dataflow_relay import (  # noqa: E402
    ROTATION_MODEL,
    relay,
    relay_words,
    ticks_per_rotation,
)
from randomfun2026solvers.value_ring import draw_pipe, stamp, walls  # noqa: E402

#: The repo's original minimal turnaround room, inlined here because
#: :mod:`randomfun2026solvers.value_ring` has since specialised it into
#: ``RELAY_NORTH``/``RELAY_SOUTH`` (which differ in *pipe attachment side*, not in
#: throughput).  This is the 6-cell walking cycle whose one-word-per-lap cap is
#: the subject of :func:`test_the_minimal_relay_caps_every_ring_at_six_ticks`, so
#: it has to stay byte-identical to what was measured even as ``value_ring``
#: moves on.
MINIMAL_RELAY = [
    "+----+",
    "|@ >v|",
    "|  sr|",
    "|  ^<|",
    "+----+",
]

LM_MJS = REPO / "littleman" / "lm.mjs"

node_required = pytest.mark.skipif(
    shutil.which("node") is None or not LM_MJS.exists(),
    reason="node and littleman/lm.mjs required",
)
slow = pytest.mark.skipif(
    os.environ.get("LM1_SLOW") != "1",
    reason="set LM1_SLOW=1 to run the reference-interpreter sweeps",
)

#: Values parked in the probe's ring.  Small enough that every ring below has the
#: mandatory ``payload + 1`` capacity (SURVEY §6) with slack.
PROBE_L = 12
IN_COL, OUT_COL, FWD_ROW = 8, 2, 0


# ── the probe ─────────────────────────────────────────────────────────────────
def _worker(m: int) -> tuple[Circuit, int]:
    """Load ``L`` values, read ``N``, rotate ``N`` counted units, emit one value."""
    c = Circuit(12, 2 * m + 16)
    c.set(0, 0, ">")
    c.run(1, 0, "@")
    c.horizontal(0, 1, 7)
    c.run(7, 0, "rb")                        # A = L from input, BP = L
    c.set(9, 0, "v")
    c.set(9, 1, " ")
    assert c.counted_loop(9, 2, "rs")[0] == 11        # LOAD
    c.route((11, 2), E, [(11, 6)], (1, 6), S)
    c.set(1, 7, ">")
    c.run(2, 7, "rb")                        # A = N from input, BP = N
    c.set(9, 7, "v")
    c.horizontal(7, 3, 9)
    c.counted_ring(9, 8, "rs" * m)           # ROT: 2m values per lap, BP per m
    tail = 10 + 2 * m
    c.set(11, 8, "v")
    c.vertical(11, 8, tail)
    for col in (11, 8):                      # both counted_ring exits, then merge west
        c.set(col, tail, "v")
        c.set(col, tail + 1, "r")
        c.set(col, tail + 2, "<")
    c.horizontal(tail + 2, 11, 8)
    c.horizontal(tail + 2, 8, 2)
    c.run(2, tail + 2, "s", d=W)
    c.set(1, tail + 2, "H")
    return c, tail + 2


def probe(m: int, relay_art: list[str], relay_h: int) -> tuple[list[str], int]:
    """The whole probe grid: worker + I/O + one ring closed by ``relay_art``."""
    w, ret_row = _worker(m)
    iw, ih = w.w, w.h
    g = Circuit(40, max(ih + 14, relay_h + 22))
    wx, wy = 1, 6
    stamp(g, wx, wy, w.rows())
    walls(g, wx, wy, iw, ih)
    icol = wx + IN_COL
    stamp(g, icol - 1, 0, ["+-+", "|I|", "+-+"])
    draw_pipe(g, [(icol, 3), (icol, 4)])
    ocol = wx + OUT_COL
    stamp(g, ocol - 1, 0, ["+-+", "|O|", "+-+"])
    draw_pipe(g, [(ocol, 4), (ocol, 3)])
    east = wx + iw + 1
    ry = wy + 4
    stamp(g, east + 1, ry, relay_art)
    fwd = draw_pipe(g, [(east, wy + FWD_ROW), (east + 3, wy + FWD_ROW), (east + 3, ry - 1)])
    ret = draw_pipe(g, [(east + 3, ry + relay_h + 2), (east + 3, wy + ret_row),
                        (east, wy + ret_row)])
    return [r.rstrip() for r in g.rows() if r.strip()], fwd + ret + 2


# ── engine measurement ────────────────────────────────────────────────────────
def _output_len(man: Path, n: int, inp: str, cache: dict[int, int]) -> int:
    if n not in cache:
        proc = subprocess.run(
            ["node", str(LM_MJS), "tick", str(man), str(n), "--input", inp, "--json"],
            capture_output=True, text=True, check=True,
        )
        snap = json.loads(proc.stdout)
        assert snap.get("fatal") is None, f"fatal at tick {n}: {snap['fatal']}"
        cache[n] = len(snap.get("output") or [])
    return cache[n]


def first_output_tick(man: Path, inp: str, cap: int = 4_000_000) -> int:
    """Smallest tick at which the probe has emitted its one value.

    Exponential search then bisection, exactly as ``scoring._ticks_for_case`` does:
    the probe halts its worker but the relay keeps blocking on a full return pipe,
    so ``lm.mjs run`` never reports a halt and cannot be used here.
    """
    cache = {0: 0}
    hi = 64
    while _output_len(man, hi, inp, cache) < 1:
        assert hi < cap, f"probe emitted nothing within {cap} ticks"
        hi = min(hi * 2, cap)
    lo = 1
    while lo < hi:
        mid = (lo + hi) // 2
        if _output_len(man, mid, inp, cache) >= 1:
            hi = mid
        else:
            lo = mid + 1
    return lo


def measure_ticks_per_rotation(tmp_path: Path, m: int, relay_art: list[str],
                               relay_h: int) -> float:
    rows, capacity = probe(m, relay_art, relay_h)
    assert capacity >= PROBE_L + 2, (
        f"ring holds {capacity} words, needs >= {PROBE_L + 2} or it deadlocks silently"
    )
    man = tmp_path / f"probe_m{m}_h{relay_h}.man"
    man.write_text("\n".join(rows) + "\n")
    values = " ".join(str(i + 1) for i in range(PROBE_L))
    lo, hi = 100, 400
    t_lo = first_output_tick(man, f"{PROBE_L} {values} {lo}")
    t_hi = first_output_tick(man, f"{PROBE_L} {values} {hi}")
    # BP counts half-laps of m values, so (hi - lo) BP units are m x that many rotations
    return (t_hi - t_lo) / ((hi - lo) * m)


# ── the model ─────────────────────────────────────────────────────────────────
def test_the_relay_geometry_is_what_sets_its_throughput() -> None:
    """Words per lap, and the ``r``/``s`` alternation that keeps the room a FIFO."""
    for (w, h), words in {(4, 3): 2, (6, 4): 5, (8, 6): 9, (10, 8): 13}.items():
        assert relay_words(w, h) == words
        art = relay(w, h)
        assert len(art) == h + 2
        assert all(len(row) == w + 2 for row in art)
        interior = "".join(row[1:-1] for row in art[1:-1])
        assert interior.count("r") == interior.count("s") == words
        assert interior.count("@") == 1


def test_a_relay_needs_a_perimeter_to_walk() -> None:
    for bad in ((2, 3), (3, 2)):
        with pytest.raises(ValueError):
            relay(*bad)


def test_the_cost_model_is_the_worse_of_the_two_laps() -> None:
    """A ring is worker-bound or relay-bound, and the fix differs in each case."""
    assert ticks_per_rotation(1, 8, 6) == pytest.approx(5.0)     # worker-bound
    assert ticks_per_rotation(9, 4, 3) == pytest.approx(5.0)     # relay-bound
    assert ticks_per_rotation(3, 6, 4) == pytest.approx(3.2)     # relay-bound
    assert ticks_per_rotation(3, 8, 6) == pytest.approx(3.0)     # worker-bound


# ── engine-measured, the reason any of the above is quotable ──────────────────
@node_required
@slow
@pytest.mark.parametrize(("m", "w", "h"), sorted(ROTATION_MODEL))
def test_the_model_matches_the_engine(tmp_path: Path, m: int, w: int, h: int) -> None:
    """Every row of ``ROTATION_MODEL``, measured on the reference interpreter.

    Pinned exactly rather than as a bound: ticks on this engine are deterministic
    and every measured slope is a whole or half tick, so an inequality would hide a
    regression as readily as an improvement.
    """
    measured = measure_ticks_per_rotation(tmp_path, m, relay(w, h), h)
    assert measured == pytest.approx(ROTATION_MODEL[(m, w, h)], abs=1e-9)
    assert measured == pytest.approx(ticks_per_rotation(m, w, h), abs=1e-9)


@node_required
@slow
@pytest.mark.parametrize("m", [1, 2, 3])
def test_the_minimal_relay_caps_every_ring_at_six_ticks(tmp_path: Path, m: int) -> None:
    """The survey's ``b = 3.2`` was unreachable with the relay the repo had.

    ``value_ring.RELAY`` carries one word per 6-cell lap, so tuning the worker from
    8 ticks/rotation down to 3.5 buys nothing at all -- the measured cost is 6.0 at
    every width.  This is the assertion that makes the fat relay load-bearing
    rather than a micro-optimisation.
    """
    measured = measure_ticks_per_rotation(tmp_path, m, MINIMAL_RELAY, 3)
    assert measured == pytest.approx(6.0, abs=1e-9)
