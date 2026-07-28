"""The trimmed variant of the squared deadman-3d machine.

``deadman-3d_trim.*`` is the squared machine with ``trim_dead=True``: the
decode trie re-routed as an uneven tree so the ~9 dead leaf lanes contribute
no rows (band 63 -> 41). Same program, same input protocol, same frames --
the canonical artifacts stay flag-off until the taped store tier lands, so
this file pins the suffixed variant against its own build.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).parents[1]
PKG = REPO / "solvers" / "python"
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

MAN = REPO / "littleman" / "examples" / "deadman-3d_trim.man"


def test_trim_artifact_matches_the_builder() -> None:
    from randomfun2026solvers.lm1 import machine

    m = machine.build_for("deadman-3d", trim_dead=True)
    assert MAN.read_text().rstrip("\n").split("\n") == m.rows
    # The artifact equality above already pins every byte; assert only the
    # doctrine — near-square, size class bounded — so layout retunes don't
    # break a test that caught nothing.
    assert max(m.width, m.height) <= 390
    assert max(m.width, m.height) - min(m.width, m.height) <= max(m.width, m.height) // 10
