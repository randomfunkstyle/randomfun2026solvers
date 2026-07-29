"""Two LM-75 panels: what the engine allows, and the shape that satisfies it.

Design: docs/superpowers/specs/2026-07-29-mnist-cnn-design.md §2.1 and §6.

SPEC.md's "exactly one display" is a *judging* rule for display-judged problems, not
an engine limit. These two rules were found by bisection against the bundled wasm and
are what force the CPU -> relay -> panel shape: the CPU is one room, and a room may
feed at most one display.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from randomfun2026solvers.fast_littleman import FastLittleman

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


# A gap row ("|......|") separates the two panels here — drop it and the room's two
# pipe mouths sit in the same columns one row apart, which the reference engine's
# pipe tracer merges into a single interrupted pipe instead of reporting the R1
# violation this grid exists to demonstrate.
ONE_ROOM_TWO_PANELS = """\
+------+   +==+
|@1sv..|>>>:..:
|......|   +==+
|......|
|...>2s|>>>:..:
+------+   +==+
"""

TWO_ROOMS_TWO_PANELS = """\
+------+   +==+
|@1sH..|>>>:..:
+------+   +==+
+------+   +==+
|@2sH..|>>>:..:
+------+   +==+
"""


@pytest.mark.slow
@node_required
def test_r1_one_room_may_not_feed_two_displays(tmp_path: Path) -> None:
    """The rule that forces a relay room per panel: the CPU is one room.

    ``lm.mjs`` never prints the literal phrase "load failed" — every load/parse
    failure goes through its one error path as ``error: <message>`` (see
    ``littleman/lm.mjs``'s ``catch`` in ``main()``). For this grid that message is
    ``pipe interrupted: expected '-' or an arrowhead to continue it, but found ':'``
    — the reference engine's pipe tracer conflating the room's two outgoing mouths
    — not the ``runtime error: index out of range`` panic ARCH.md quotes for a
    simpler repro. Different message, same rule: one room, two pipes to two
    displays, fails before a single tick runs.
    """
    out = _run(ONE_ROOM_TWO_PANELS, tmp_path)
    assert "error:" in out
    assert "# halted" not in out, "this must fail before ticking starts, not at runtime"


@pytest.mark.slow
@node_required
def test_two_rooms_each_feeding_its_own_display_is_legal(tmp_path: Path) -> None:
    out = _run(TWO_ROOMS_TWO_PANELS, tmp_path)
    assert "error:" not in out
    assert "done" in out


@pytest.mark.slow
@node_required
def test_the_engine_reports_both_displays(tmp_path: Path) -> None:
    analysis = json.loads(_run(TWO_ROOMS_TWO_PANELS, tmp_path, cmd="analyze"))
    assert len(analysis["displays"]) == 2


def test_native_engine_reports_frames_for_each_display(tmp_path: Path) -> None:
    """The native engine's per-display accessor, on the same two-room/two-panel grid.

    No ``frames=`` is passed to ``run`` here — this checks the structural fact (one
    committed-frame list per display room, in reading order), not judged content.
    Neither room ever executes ``S``/``s``-into-SWAP in this probe, so both lists are
    empty; :mod:`test_fast_littleman` and the deadman-3d/matmul/snake/plotter tests
    cover a display that actually commits.
    """
    machine = FastLittleman(TWO_ROOMS_TWO_PANELS.splitlines())
    assert len(machine.display_rooms) == 2
    result = machine.run(max_ticks=100)
    assert result.fatal is None, result.fatal
    frames = result.frames_per_display()
    assert len(frames) == 2, "one frame list per panel, in reading order"
