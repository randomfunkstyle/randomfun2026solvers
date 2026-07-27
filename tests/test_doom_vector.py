"""The ``doom-screen`` vector-display demo: segment data, artifacts, and the frame.

``doom_vector.py`` decomposes the DOOM (1993) title screen into coloured horizontal
runs, and ``lm1/programs/doom-screen.asm`` — plotter refitted as a general
vector-display processor — draws them and commits ONE frame. This is an ungraded
demo, so nothing here talks to a problem's public cases; the cases file under
``littleman/examples`` is the demo's own.

Fast tier: the decomposition's invariants, the emulator end-to-end run, and the
artifact-matches-generator pins. Slow tier: the reference wasm committing the frame.
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).parents[1]
PKG = REPO / "solvers" / "python"
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

from randomfun2026solvers import doom_vector  # noqa: E402
from randomfun2026solvers.lm1 import machine, programs  # noqa: E402
from randomfun2026solvers.lm1.display import frames_from_writes  # noqa: E402
from randomfun2026solvers.lm1.emulator import Emulator, Round  # noqa: E402

LM_MJS = REPO / "littleman" / "lm.mjs"
FRAME_TOOL = REPO / "littleman" / "tools" / "display-frames.mjs"
MAN = REPO / "littleman" / "examples" / "doom-screen-cpu.man"
CASES = REPO / "littleman" / "examples" / "doom-screen-cpu.cases.json"

node_required = pytest.mark.skipif(
    shutil.which("node") is None or not LM_MJS.exists() or not FRAME_TOOL.exists(),
    reason="node, littleman/lm.mjs and littleman/tools/display-frames.mjs required",
)
slow = pytest.mark.slow


# ── the decomposition ────────────────────────────────────────────────────────
def test_segment_replay_reproduces_the_frame() -> None:
    """Lossless by construction — a run per colour change, black skipped — but
    asserted anyway, because the segments are what the CPU actually receives."""
    doom_vector.check()
    rows = doom_vector.frame_rows()
    assert len(rows) == doom_vector.HEIGHT == 24
    assert all(len(r) == doom_vector.WIDTH == 32 for r in rows)


def test_segments_are_horizontal_left_to_right_and_never_black() -> None:
    for x0, y0, x1, y1, colour in doom_vector.segments():
        assert y0 == y1, "the decomposition emits horizontal runs only"
        assert 0 <= x0 <= x1 < doom_vector.WIDTH
        assert 0 <= y0 < doom_vector.HEIGHT
        assert 1 <= colour <= 15, "black runs are skipped: the buffer starts black"


def test_input_stream_is_five_words_per_segment_plus_one_sentinel() -> None:
    words = doom_vector.input_words()
    segs = doom_vector.segments()
    assert len(words) == 5 * len(segs) + 1
    assert words[-1] == doom_vector.SENTINEL < 0
    assert all(w >= 0 for w in words[:-1]), "the sentinel is the only negative word"


# ── the program, end to end on the emulator (milliseconds) ───────────────────
def test_the_program_draws_the_frame_and_halts_on_the_emulator() -> None:
    """The whole demo in one round: ~18.5k instructions, no output, ONE commit."""
    prog = programs.load("doom-screen")
    res = Emulator(prog).run(
        [Round(input=tuple(doom_vector.input_words()))], max_instructions=1_000_000
    )
    assert res.reason == "halted", res.reason
    assert res.output == (), "a display program must emit no program output"
    frames = frames_from_writes(res.display_writes, width=32, height=24)
    assert len(frames) == 1, "segments accumulate in `next`; DSPS runs exactly once"
    assert frames[0] == doom_vector.frame_rows()


# ── the checked-in artifacts match their generators ──────────────────────────
def test_checked_in_cases_json_matches_the_generator() -> None:
    assert json.loads(CASES.read_text(encoding="utf-8")) == doom_vector.cases_json()


@node_required
def test_checked_in_man_matches_the_generator() -> None:
    expected = "\n".join(machine.build_for("doom-screen").rows) + "\n"
    assert MAN.read_text(encoding="utf-8") == expected, (
        "doom-screen-cpu.man is stale; regenerate with `python -m "
        f"randomfun2026solvers.lm1.machine doom-screen --man {MAN}`"
    )


def test_the_demo_borrows_only_plotters_panel() -> None:
    """The slug maps onto plotter's problem JSON for the 32x24 resolution only."""
    assert programs.problem_of("doom-screen") == "plotter"
    assert machine.display_for("doom-screen") == (32, 24)
    assert machine.TAPE_SIZE["doom-screen"] == 12  # plotter's 11 slots + COL


# ── the reference engine commits exactly the DOOM frame ──────────────────────
@node_required
@slow
def test_the_engine_commits_exactly_the_doom_frame() -> None:
    """The generated ``.man`` on the reference wasm: one commit, the whole screen.

    The engine measured 4,179,549 ticks to the commit; the cap leaves room for the
    machine to grow without this test deciding its budget (the demo is ungraded).
    """
    from randomfun2026solvers.littleman import Littleman

    cases = json.loads(CASES.read_text(encoding="utf-8"))["publicTestData"]
    (res,) = Littleman().display_frames(MAN, cases, max_ticks=40_000_000)
    assert res.fatal is None, res.fatal
    assert not res.output, "a display program must emit no program output"
    assert (res.width, res.height) == (32, 24)
    assert len(res.frames) == 1
    assert res.frames[0] == doom_vector.frame_rows()
