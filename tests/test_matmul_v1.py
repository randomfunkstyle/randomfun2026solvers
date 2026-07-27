"""The assembled bespoke ``matmul`` machine: MAIN + the ADDER + five rings + I/O.

Every fault this machine shipped with was invisible to `analyze`: the grid loaded,
all sixteen pipes anchored, all thirty-four of MAIN's pipe ops bound to the right
pipe, and it computed nothing. So the fast tier pins the two things that *are*
checkable without simulating — the artifact matches its generator (``AGENTS.md``:
that is what makes a shape change show up as a diff), and the structural traps that
cost the most debugging get an assertion each:

* the pipe **count**, because a bend whose backward cell lands on a room's wall
  parses as an extra pipe out of that room, ends at the same destination cell, and
  wins the `r` by reading order — silently starving the real one;
* the three **ring capacities**, because a ring shorter than its contents deadlocks
  only on the case that fills it, and `cmd` in particular buffers 3N words before a
  single ring is filled;
* the drive loop's **fetch column**, because ring rK has to sit below the marker
  test — the man reads it on his way down and cannot go back up for it.

The engine-backed pass over all seven public cases is marked slow.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).parents[1]
PKG = REPO / "solvers" / "python"
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

from randomfun2026solvers import matmul_adder3 as a3  # noqa: E402
from randomfun2026solvers import matmul_asm3 as asm  # noqa: E402
from randomfun2026solvers import matmul_main as mm  # noqa: E402

LM = REPO / "littleman"
GRID = REPO / "tasks" / "solutions" / "matmul-v1.man"

node_required = pytest.mark.skipif(
    shutil.which("node") is None or not (LM / "lm.mjs").exists(),
    reason="node and littleman/lm.mjs required",
)


@pytest.fixture(scope="module")
def built():
    g, caps = asm.build()
    return "\n".join(g.rows()) + "\n", caps


def test_checked_in_grid_matches_generator(built):
    text, _ = built
    assert GRID.read_text() == text, (
        f"{GRID.name} is stale; regenerate with "
        "`python -m randomfun2026solvers.matmul_asm3 tasks/solutions/matmul-v1.man`"
    )


def test_ring_capacities_cover_the_largest_case(built):
    """256 scalars per storage ring, K accumulators in ring C, 3N words in `cmd`."""
    _, caps = built
    assert caps["a_fwd"] + caps["a_ret"] >= 257, "ring A cannot hold N*M = 256"
    assert caps["b_fwd"] + caps["b_ret"] >= 257, "ring B cannot hold M*K = 256"
    assert caps["cout"] + caps["cin"] >= 17, "ring C cannot hold K = 16 accumulators"
    assert caps["cmd"] >= 48, "cmd cannot buffer 3N = 48 count words"


def test_fetch_column_reads_ring_rk_below_the_marker_test():
    """The whole fetch is one column walked south, so rK must be under the shifts."""
    test_row = mm.A_RET + 10
    assert mm.RK_RET > test_row, "the man passes rK before he knows to read it"
    assert mm.RK_FWD == mm.RK_RET + 1
    assert mm.RK_FWD + 2 < mm.BAND_B, "no room to turn out of the fetch column"


def test_adder_binds_cmd_above_prod():
    """`cmd` leaves MAIN below `prod`, so it must arrive at the ADDER above it."""
    assert a3.CMD < a3.PROD < a3.CIN < a3.COUT <= a3.OUT
    # every riser reads `cmd` from READ, which has to be nearer CMD than PROD
    assert abs(a3.READ - a3.CMD) < abs(a3.READ - a3.PROD)


@node_required
def test_analyze_finds_every_pipe_and_no_extras(built, tmp_path):
    text, _ = built
    p = tmp_path / "v1.man"
    p.write_text(text)
    out = subprocess.run(
        ["node", "lm.mjs", "analyze", str(p), "--json"],
        cwd=LM, capture_output=True, text=True, check=True,
    )
    info = json.loads(out.stdout)
    assert len(info["pipes"]) == len(asm.PIPES), (
        "pipe count differs from the sixteen drawn: a bend's backward cell has "
        "landed on a room wall and parsed as a phantom pipe"
    )
    assert not [i for i, q in enumerate(info["pipes"]) if q["src"] < 0 or q["dst"] < 0]
    assert len(info["rooms"]) == 10


@pytest.mark.slow
@node_required
def test_all_public_cases_pass_on_the_reference_engine(tmp_path):
    cases = json.loads((REPO / "tasks" / "problems" / "matmul.json").read_text())
    for case in cases["publicTestData"]:
        for rnd in case["rounds"]:
            out = subprocess.run(
                ["node", "lm.mjs", "judge", str(GRID), "--input", " ".join(rnd["in"]),
                 "--expected", " ".join(rnd["out"]), "--json", "--max-ticks", "2000000"],
                cwd=LM, capture_output=True, text=True, check=True,
            )
            got = json.loads(out.stdout)
            assert got["output"] == rnd["out"], case["name"]
