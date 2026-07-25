"""What a pipe cell really costs — ``ARCH.md`` §7.4b, re-measured at scale.

§7.4b measured "**every extra pipe cell costs one tick**" on ``triangle``: a
13-tick program where the input pipe is on the critical path because its first
``r`` fires at t1 and blocks. That sentence has since been used to price two
levers — shortening the tape's ~48-cell response pipe, and keeping every pipe at
the 2-cell minimum even at the cost of a row of height.

Measured properly here, by lengthening **only** the tape's serialised response
pipe (``machine.build``'s ``resp_pad`` inserts a there-and-back jog in the
corridor above the grid; nothing else about the machine changes) and timing the
first public case on the reference interpreter:

    machine          tape N   reads   base ticks   +24 cells   per cell per read
    brackets              8      52       39,654      +1,352                1.08
    matmul (STREAM)      16      38       15,103        +804                0.88
    tcp                  52      48       40,827        +596                0.52
    matmul (old tape)   107     131      261,710        +416                0.13

So §7.4b is an **upper bound, not a law**, and what it depends on is the tape's
length. The mechanism is in ``memory.man``'s READ arm: it emits the answer
*mid-revolution* (``r(tape) ; S``) and then keeps rotating through P2, so the
answer travels the response pipe **while the tape finishes its lap**. A long tape
hides the whole pipe behind that rotation (N=107: 0.13 ticks per cell per read); a
short one has nothing to hide it behind and pays nearly the full cell (N=8: 1.08).

Consequences, both worth having in writing:

* **Pipe length is a real but second-order lever, and only on a short tape.**
  Shortening this machine's response pipe from ~48 cells to the 2-cell floor is
  worth ~6 % of ``matmul``'s largest case and ~10 % of its smallest. Worth doing;
  not worth trading a row of the bounding box for, since footprint is squared.
* **§7.4b's packing advice is backwards for big machines.** "Both pipes want to be
  the 2-cell minimum" was right for an 8x8 triangle. Here a pipe cell is worth
  ~0.1-1 tick against a footprint term that squares, so wrapping a pipe to save a
  row is usually the better trade.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).parents[1]
PKG = REPO / "solvers" / "python"
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

from randomfun2026solvers.lm1 import machine, programs  # noqa: E402
from randomfun2026solvers.lm1.emulator import Emulator  # noqa: E402
from randomfun2026solvers.lm1.store import DictStore  # noqa: E402

LM_MJS = REPO / "littleman" / "lm.mjs"
node_required = pytest.mark.skipif(
    shutil.which("node") is None or not LM_MJS.exists(),
    reason="node and littleman/lm.mjs required",
)

#: ``resp_pad`` adds this many cells (there and back) to the response pipe.
PAD = 12
ADDED = 2 * PAD


class _Reads(DictStore):
    """Counts reads only: a write never traverses the response pipe."""

    def __init__(self) -> None:
        super().__init__()
        self.reads = 0

    def _read(self, addr: int) -> int:
        self.reads += 1
        return super()._read(addr)


def _measure(slug: str, cap: int, tmp_path: Path, **kw: object) -> tuple[int, int, float]:
    """``(reads, base ticks, ticks per added cell per read)`` for ``slug``'s first case."""
    from randomfun2026solvers.littleman import Littleman

    prog = programs.load(slug)
    _name, rounds = programs.rounds_for_problem(slug)[0]
    expected = [v for r in rounds for v in r.expected]
    inp = " ".join(str(v) for r in rounds for v in r.input)
    store = _Reads()
    Emulator(prog, store=store).run(list(rounds), max_instructions=3_000_000)

    lm = Littleman()
    ticks: dict[int, int] = {}
    for pad in (0, PAD):
        m = machine.build(prog, tape_n=machine.TAPE_SIZE[slug], resp_pad=pad, **kw)  # type: ignore[arg-type]
        path = tmp_path / f"{slug}{pad}.man"
        path.write_text("\n".join(m.rows) + "\n", encoding="utf-8")
        lo, hi = 1, cap
        assert list(lm.tick(path, hi, input=inp).output)[: len(expected)] == expected
        while lo < hi:
            mid = (lo + hi) // 2
            if len(list(lm.tick(path, mid, input=inp).output)) >= len(expected):
                hi = mid
            else:
                lo = mid + 1
        ticks[pad] = lo
    per = (ticks[PAD] - ticks[0]) / ADDED / store.reads
    return store.reads, ticks[0], per


@node_required
def test_a_short_tape_pays_almost_the_whole_pipe_cell(tmp_path) -> None:
    """``brackets``, N=8: 1.08 ticks per added cell per read — §7.4b as written.

    With an 8-slot ring there is almost no post-emit rotation left to hide the
    traversal behind, so the answer's walk down the pipe is on the critical path.
    """
    reads, base, per = _measure("brackets", 200_000, tmp_path)
    assert reads > 20 and base > 10_000
    assert 0.7 < per < 1.4, f"brackets: {per:.2f} ticks per cell per read"


@node_required
def test_a_longer_tape_hides_most_of_the_pipe_behind_its_own_rotation(tmp_path) -> None:
    """``tcp``, N=52: 0.52 — half of §7.4b's figure, on the same generator.

    Same CPU shape, same pipe, same measurement; the only difference is that the
    tape takes ~4x longer to finish its lap after emitting. That is the whole
    mechanism, and it is why §7.4b does not generalise.
    """
    reads, base, per = _measure("tcp", 200_000, tmp_path)
    assert reads > 20 and base > 10_000
    assert per < 0.75, f"tcp: {per:.2f} ticks per cell per read"


@node_required
def test_the_streaming_matmuls_own_response_pipe_is_a_six_percent_lever(tmp_path) -> None:
    """The number that decides whether to bother shortening it: ~0.9 per read.

    matmul's tape is down to 16 slots now that the matrices live in the STREAM
    block, which puts it back in the regime where pipe cells are nearly fully
    charged — so its ~48-cell response pipe is worth ~6 % of the largest case. That
    is a real lever, and this is the test that would notice if it stopped being one.
    """
    reads, base, per = _measure(
        "matmul", 1_000_000, tmp_path, rom_rows=11, stream=machine.STREAM_SIZE["matmul"]
    )
    assert reads > 20
    assert 0.5 < per < 1.4, f"matmul: {per:.2f} ticks per cell per read"
    # 46 cells over the 2-cell floor, charged at `per`, against the measured base.
    saving = 46 * per * reads / base
    assert 0.03 < saving < 0.25, f"shortening it would save {saving:.0%} of {base:,} ticks"
