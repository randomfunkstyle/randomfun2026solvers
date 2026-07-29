"""Two LM-75 panels: what the engine allows, and the shape that satisfies it.

Design: docs/superpowers/specs/2026-07-29-mnist-cnn-design.md §2.1 and §6.

SPEC.md's "exactly one display" is a *judging* rule for display-judged problems, not
an engine limit. These two rules were found by bisection against the bundled wasm and
are what force the CPU -> relay -> panel shape: the CPU is one room, and a room may
feed at most one display.

Every wasm probe below asserts on ``analyze`` first (room/pipe/display counts) and
only then on the pass/fail outcome. A malformed panel box can make the engine see
one display instead of two without erroring at all -- the run-level assertion alone
would then pass for the wrong reason (there is nothing to violate R1/R2 against),
pinning "this malformed grid fails" rather than the rule it is supposed to be about.
An earlier version of this file learned that the hard way: a missing panel wall
made the R1 grid fail for an unrelated pipe-parsing reason, and nothing here caught
that the grid had stopped meaning what it claimed to.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from randomfun2026solvers.fast_littleman import FastLittleman, FastLittlemanError

REPO = Path(__file__).resolve().parents[1]
LM = REPO / "littleman" / "lm.mjs"

node_required = pytest.mark.skipif(
    shutil.which("node") is None or not LM.exists(),
    reason="needs node and the bundled littleman engine",
)


def _run(grid: str, tmp_path: Path, cmd: str = "run") -> str:
    man = tmp_path / "probe.man"
    man.write_text(grid)
    out = subprocess.run([str(LM), cmd, str(man)], capture_output=True, text=True)
    return out.stdout + out.stderr


def _analyze(grid: str, tmp_path: Path) -> dict:
    man = tmp_path / "probe.man"
    man.write_text(grid)
    out = subprocess.run(
        [str(LM), "analyze", str(man)], capture_output=True, text=True, check=True
    )
    return json.loads(out.stdout)


# Both panels well formed (each its own complete +====+/:....:/+====+ box); the
# room spans both panel rows and sends into each with its own pipe. R1 violation:
# one room, two pipes to two displays.
ONE_ROOM_TWO_PANELS = """\
+--------+    +====+
|@1v.....|>>>>:....:
|........|    :....:
|........|    +====+
|........|
|........|    +====+
|..>1sH..|>>>>:....:
|........|    :....:
+--------+    +====+
"""

TWO_ROOMS_TWO_PANELS = """\
+------+   +==+
|@1sH..|>>>:..:
+------+   +==+
+------+   +==+
|@2sH..|>>>:..:
+------+   +==+
"""

# R2: only the FIRST panel (in reading order) is wired. Legal -- a wired display
# may be followed by an unwired one.
R2_FIRST_WIRED = """\
+--------+    +====+
|@1sH....|>>>>:....:
|........|    :....:
|........|    +====+
|........|
|........|    +====+
|........|    :....:
|........|    :....:
+--------+    +====+
"""

# R2: only the SECOND panel is wired, the first is not. Illegal -- an unwired
# display may not precede a wired one.
R2_SECOND_WIRED = """\
+--------+    +====+
|........|    :....:
|........|    :....:
|........|    +====+
|........|
|........|    +====+
|@1sH....|>>>>:....:
|........|    :....:
+--------+    +====+
"""

# Two independent rooms, each an independent DATA send (a distinct colour, 3 and
# 7) then an independent SWAP send from a room that loops forever (so the whole
# grid can never finish before both displays commit). The DATA pipe is 6 cells and
# the SWAP pipe 12, so DATA always lands before its own panel's SWAP fires.
TWO_PANEL_JUDGED_PROBE = [
    "+----+      +=+",
    "|@3sH|>---->:.:",
    "+----+      +=+",
    "             ^",
    "             ^",
    "             ^",
    "             ^",
    "             ^",
    "             ^",
    "             ^",
    "             ^",
    "             ^",
    "             ^",
    "             ^",
    "            +----+",
    "            |>  v|",
    "            |^  <|",
    "            |@1s^|",
    "            +----+",
    "+----+      +=+",
    "|@7sH|>---->:.:",
    "+----+      +=+",
    "             ^",
    "             ^",
    "             ^",
    "             ^",
    "             ^",
    "             ^",
    "             ^",
    "             ^",
    "             ^",
    "             ^",
    "             ^",
    "            +----+",
    "            |>  v|",
    "            |^  <|",
    "            |@1s^|",
    "            +----+",
]


@pytest.mark.slow
@node_required
def test_r1_one_room_may_not_feed_two_displays(tmp_path: Path) -> None:
    """The rule that forces a relay room per panel: the CPU is one room.

    ``lm.mjs``'s one error path in ``main()`` prints ``error: <e.message>``, and for
    a load failure ``e.message`` is itself ``load failed: <reason>`` -- the reason
    is the Go runtime's own panic text, propagated through wasm as ``e.message``.
    """
    analysis = _analyze(ONE_ROOM_TWO_PANELS, tmp_path)
    assert len(analysis["rooms"]) == 1
    assert len(analysis["displays"]) == 2
    assert len(analysis["pipes"]) == 2

    out = _run(ONE_ROOM_TWO_PANELS, tmp_path)
    assert "load failed" in out
    assert "# halted" not in out, "this must fail before ticking starts, not at runtime"


@pytest.mark.slow
@node_required
def test_two_rooms_each_feeding_its_own_display_is_legal(tmp_path: Path) -> None:
    analysis = _analyze(TWO_ROOMS_TWO_PANELS, tmp_path)
    assert len(analysis["rooms"]) == 2
    assert len(analysis["displays"]) == 2
    assert len(analysis["pipes"]) == 2

    out = _run(TWO_ROOMS_TWO_PANELS, tmp_path)
    assert "load failed" not in out
    assert "done" in out


@pytest.mark.slow
@node_required
def test_the_engine_reports_both_displays(tmp_path: Path) -> None:
    analysis = _analyze(TWO_ROOMS_TWO_PANELS, tmp_path)
    assert len(analysis["displays"]) == 2


@pytest.mark.slow
@node_required
def test_r2_a_wired_display_before_an_unwired_one_is_legal(tmp_path: Path) -> None:
    """R2: the wired displays must be a *prefix* of the displays in reading order.

    The first (only) panel wired, the second left untouched -- a legal prefix.
    """
    analysis = _analyze(R2_FIRST_WIRED, tmp_path)
    assert len(analysis["rooms"]) == 1
    assert len(analysis["displays"]) == 2
    assert len(analysis["pipes"]) == 1

    out = _run(R2_FIRST_WIRED, tmp_path)
    assert "load failed" not in out
    assert "done" in out


@pytest.mark.slow
@node_required
def test_r2_an_unwired_display_before_a_wired_one_fails_to_load(tmp_path: Path) -> None:
    """The other direction: the second (only) panel wired, the first left unwired.

    ``{second}`` is not a prefix of ``{first, second}`` -- illegal, same grid
    otherwise.
    """
    analysis = _analyze(R2_SECOND_WIRED, tmp_path)
    assert len(analysis["rooms"]) == 1
    assert len(analysis["displays"]) == 2
    assert len(analysis["pipes"]) == 1

    out = _run(R2_SECOND_WIRED, tmp_path)
    assert "load failed" in out
    assert "# halted" not in out


def test_native_engine_reports_frames_for_each_display() -> None:
    """The native engine's per-display accessor, on the same two-room/two-panel grid.

    No ``frames=`` is passed to ``run`` here -- this checks the structural fact (one
    committed-frame list per display room, in reading order), not judged content.
    Neither room ever executes ``S``/``s``-into-SWAP in this probe, so both lists
    are empty; the tests below cover a display that actually commits.
    """
    machine = FastLittleman(TWO_ROOMS_TWO_PANELS.splitlines())
    assert len(machine.display_rooms) == 2
    result = machine.run(max_ticks=100)
    assert result.fatal is None, result.fatal
    frames = result.frames_per_display()
    assert len(frames) == 2, "one frame list per panel, in reading order"
    assert frames == [[], []]


def test_native_engine_judges_two_panels_independently_with_the_right_content() -> None:
    """The riskiest half of the C++ change: per-display judging, not just arity.

    Each panel commits its own distinct, known colour (3 and 7). Judging both
    correctly must pass, and ``frames_per_display()`` must return the *right*
    content for the *right* panel, in reading order -- not just two lists of the
    right length.
    """
    machine = FastLittleman(TWO_PANEL_JUDGED_PROBE)
    assert len(machine.display_rooms) == 2
    result = machine.run(frames=[[[["3"]]], [[["7"]]]], max_ticks=300)
    assert result.fatal is None, result.fatal
    assert result.passed is True
    assert result.frames_per_display() == [[["3"]], [["7"]]]


def test_native_engine_catches_a_wrong_frame_on_either_panel() -> None:
    """Per-display judging must actually discriminate, not just report shape.

    A correct first panel with a wrong second panel must die on the second panel's
    mismatch -- proving the comparison is keyed to the right display, not a shared
    stream (the bug the pre-existing single-stream code had before this task, see
    task-5-report.md).
    """
    wrong_second = FastLittleman(TWO_PANEL_JUDGED_PROBE).run(
        frames=[[[["3"]]], [[["0"]]]], max_ticks=300
    )
    assert wrong_second.fatal == "wrong-frame"
    assert wrong_second.passed is False

    wrong_first = FastLittleman(TWO_PANEL_JUDGED_PROBE).run(
        frames=[[[["0"]]], [[["7"]]]], max_ticks=300
    )
    assert wrong_first.fatal == "wrong-frame"
    assert wrong_first.passed is False


def test_frames_per_display_raises_on_the_python_fallback() -> None:
    """The pure-Python backend never captures frames; ``frames_per_display()`` must
    say so rather than returning ``[]``, which would read as "nothing was drawn."
    """
    machine = FastLittleman(TWO_ROOMS_TWO_PANELS.splitlines())
    result = machine.run(max_ticks=100, native=False)
    with pytest.raises(FastLittlemanError, match="native backend"):
        result.frames_per_display()


def test_mismatched_frame_spec_shape_raises_clearly() -> None:
    """A single-display-shaped ``frames=`` fed to a two-display program must raise,
    not silently judge the wrong data against the wrong panel.

    Two rounds of one frame each is exactly the shape a caller might pass thinking
    of the old single-display convention, and it happens to have exactly as many
    "rounds" (2) as this probe has displays -- precisely the case a length check
    alone would accept as "one spec per display" and misinterpret.
    """
    machine = FastLittleman(TWO_PANEL_JUDGED_PROBE)
    single_display_shaped = [[["3"]], [["7"]]]  # 2 rounds, meant for ONE display
    with pytest.raises(FastLittlemanError, match="nested one level too shallow"):
        machine.run(frames=single_display_shaped, max_ticks=300)


def test_round_gated_input_is_refused_when_two_displays_are_judged() -> None:
    """Judging two displays at once has no single commit stream to gate rounds
    against (see fast_littleman_native.cpp's constructor comment). Rather than
    silently releasing every input round upfront, the combination that would need
    gating -- more than one input round -- is refused outright.
    """
    machine = FastLittleman(TWO_PANEL_JUDGED_PROBE)
    with pytest.raises(FastLittlemanError, match="round-gated input"):
        machine.run("1 2/3 4", frames=[[[["3"]]], [[["7"]]]], max_ticks=300)


def test_frames_per_display_can_under_report_a_commit_still_in_flight_at_halt() -> None:
    """A known, pre-existing engine divergence, pinned rather than surprising later.

    The engine ends a run once every runner has halted with no *output* in flight;
    it does not drain display pipes first. ``littleman/examples/panel-latency-swap-
    equal.man`` sends its one SWAP write right before halting, with a pipe long
    enough that the value has not yet arrived when the runner halts: the reference
    wasm engine (``lm.mjs run --json``) reports ``frames: 1``, but the native engine
    here halts first and reports zero. This is not something Task 5 changes -- the
    halt condition (``live == 0 and not output_in_flight()``, with no equivalent
    drain for display pipes) predates it in both the Python and C++ engines -- only
    something ``frames_per_display()`` now documents instead of leaving as a
    surprise for Task 8.
    """
    man = REPO / "littleman" / "examples" / "panel-latency-swap-equal.man"
    machine = FastLittleman(man.read_text().splitlines())
    assert len(machine.display_rooms) == 1
    result = machine.run(max_ticks=1000)
    assert result.reason == "done"
    assert result.frames_per_display() == [[]], (
        "known divergence from the reference engine's frames: 1 -- see the docstring"
    )
