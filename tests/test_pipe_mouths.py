"""A pipe body that hugs a room wall mints a second pipe out of that room.

This is a **known divergence between the two engines**, pinned here because it
shipped a wrong grid.  `AGENTS.md` presents `FastLittleman` as the default
validation backend; on this input it is wrong, so an exhaustive sweep against it
proves nothing.

The rule the reference engine applies: a pipe is discovered by looking *behind*
an arrowhead, and any arrowhead whose backward cell is a room border is the mouth
of a pipe leaving that room.  Nothing requires the author to have meant it — a
corridor that merely turns against the underside of a wall is a mouth too, and
`lm.mjs analyze` still reports only the pipes that were drawn.

Two consequences, both bugs in the making:

* if the phantom lands on a room that may hold several incoming pipes, the grid
  **loads** and that room's sends split silently across two queues by nearest
  column.  That is what put `brackets`' classifier tokens into three FIFOs and
  made the worker read them out of order;
* if it lands on an output room, the reference engine refuses the grid outright —
  and `FastLittleman` loads it happily, which is the divergence below.

When `FastLittleman` learns the rule, :func:`test_fast_littleman_does_not_see_it`
will fail.  That is the point: delete it and keep the other one.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest
from randomfun2026solvers.circuit import Circuit
from randomfun2026solvers.fast_littleman import FastLittleman
from randomfun2026solvers.plotter_block import pipe as draw_pipe
from randomfun2026solvers.value_ring import stamp, walls

ROOT = Path(__file__).resolve().parents[1]
LM = ROOT / "littleman"


def grid(hug: bool) -> list[str]:
    """One compute room, one output room, one drawn pipe.

    The pipe leaves the compute room's south wall at column 4, doglegs west, and
    turns south again at column 1.  `hug` puts that second turn on the row
    directly under the wall, which makes it a mouth; otherwise it is one row
    lower and the grid is the same pipe with nothing else in it.
    """
    g = Circuit(9, 10)
    walls(g, 1, 1, 4, 1)
    for i, ch in enumerate("@1sH"):
        g.set(1 + i, 1, ch)
    stamp(g, 0, 6, ["+-+", "|O|", "+-+"])
    path = ([(4, 3), (4, 4), (2, 4), (2, 3), (1, 3), (1, 5)] if hug
            else [(4, 3), (4, 4), (1, 4), (1, 5)])
    cells = [path[0]]
    for (x0, y0), (x1, y1) in zip(path, path[1:], strict=False):
        sx, sy = (x1 > x0) - (x1 < x0), (y1 > y0) - (y1 < y0)
        x, y = x0, y0
        while (x, y) != (x1, y1):
            x, y = x + sx, y + sy
            cells.append((x, y))
    draw_pipe(g, cells, into=(1, 6))
    return [r.rstrip() for r in g.rows()]


def reference_load(rows: list[str]) -> tuple[int, str]:
    """(pipe count, error) from the reference engine's own runtime."""
    with tempfile.NamedTemporaryFile("w", suffix=".man", delete=False) as f:
        f.write("\n".join(rows) + "\n")
    proc = subprocess.run(["node", "lm.mjs", "tick", f.name, "1", "--json"],
                          cwd=LM, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        return -1, proc.stderr.strip()
    return len(json.loads(proc.stdout)["entities"]["pipes"]), ""


needs_node = pytest.mark.skipif(shutil.which("node") is None,
                                reason="needs node for the reference engine")


@needs_node
def test_the_same_pipe_one_row_lower_is_fine() -> None:
    """The control: nothing about the pipe changes but the row it turns on."""
    count, err = reference_load(grid(hug=False))
    assert (count, err) == (1, "")
    assert len(FastLittleman(grid(hug=False)).pipes) == 1


@needs_node
def test_a_turn_against_a_wall_mints_a_second_pipe() -> None:
    count, err = reference_load(grid(hug=True))
    assert count == -1, f"expected a load error, got {count} pipes"
    assert "more than one incoming pipe" in err, err


@needs_node
def test_fast_littleman_does_not_see_it() -> None:
    """The divergence: `FastLittleman` loads what the reference engine refuses."""
    assert len(FastLittleman(grid(hug=True)).pipes) == 1
