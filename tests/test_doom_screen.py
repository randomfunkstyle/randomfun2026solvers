"""doom-screen: the DOOM (1993) title screen on an LM-75 display.

Fast tier: the generator still produces the checked-in grid verbatim.
Slow tier: the reference engine commits exactly one frame equal to the
32x24 pixel image, with no fatal error.
"""
from pathlib import Path

import pytest

from randomfun2026solvers.doom_screen import HEX_ROWS, build
from randomfun2026solvers.littleman import Littleman

REPO = Path(__file__).resolve().parents[1]
MAN = REPO / "littleman" / "examples" / "doom-screen.man"


def test_build_matches_checked_in_grid():
    assert "\n".join(build()) + "\n" == MAN.read_text(encoding="utf-8")


@pytest.mark.slow
def test_reference_engine_commits_the_frame():
    lm = Littleman()
    cases = [{"name": "doom", "rounds": [{"in": [], "out": [], "frames": [HEX_ROWS]}]}]
    (run,) = lm.display_frames(MAN, cases, max_ticks=200_000)
    assert run.fatal is None
    assert run.frames == [HEX_ROWS]
    assert run.output == []
