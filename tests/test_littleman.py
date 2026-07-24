"""Tests for the littleman Python wrapper (randomfun2026solvers.littleman)."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).parents[1]
PKG = REPO / "solvers" / "python"
EXAMPLES = REPO / "littleman" / "examples"
LM_MJS = REPO / "littleman" / "lm.mjs"

if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

from randomfun2026solvers.littleman import (  # noqa: E402
    Littleman,
    LittlemanError,
    Snapshot,
    Vec2,
)

# A captured `run --json` snapshot (echo.man with input 42) — used for the
# in-process parse tests so they need no Node.
ECHO_SNAPSHOT = """
{
  "type": "snapshot",
  "entities": {
    "runners": [
      {"id": 0, "pos": [9, 1], "dir": [1, 0], "halted": true, "a": 42, "b": 0, "backpack": 0}
    ],
    "pipes": [
      {"id": 4, "path": [[3, 1], [4, 1]], "values": null, "src": 1, "dst": 2}
    ],
    "rooms": [
      {"id": 1, "min": [0, 0], "max": [2, 2], "runners": null},
      {"id": 2, "min": [5, 0], "max": [11, 2], "runners": [0]}
    ],
    "displays": null
  },
  "output": [42],
  "halted": true,
  "reason": "done",
  "step": 4,
  "cursor": 4,
  "history": 5,
  "inputReleased": 1,
  "inputRead": 1
}
"""

FATAL_SNAPSHOT = """
{"type": "snapshot", "entities": {"runners": [], "pipes": null, "rooms": null,
 "displays": null}, "output": null, "halted": false, "reason": null, "step": 3,
 "fatal": {"reason": "wall", "pos": [3, 1], "cell": "|", "value": 0}}
"""

WALL_PROGRAM = "+--+\n|@ |\n+--+\n"

node_required = pytest.mark.skipif(
    shutil.which("node") is None or not LM_MJS.exists(),
    reason="node and littleman/lm.mjs required",
)


# ── in-process parsing (no Node) ──────────────────────────────────────────────
def test_snapshot_parses_vec2_and_aliases() -> None:
    snap = Snapshot.model_validate_json(ECHO_SNAPSHOT)
    assert snap.output == [42]
    assert snap.step == 4
    assert snap.ok  # halted, no fatal
    assert snap.input_read == 1  # camelCase alias
    assert snap.input_released == 1
    runner = snap.entities.runners[0]
    assert isinstance(runner.pos, Vec2)
    assert runner.pos.x == 9 and runner.pos.y == 1
    assert runner.dir.as_tuple() == (1, 0)
    assert runner.a == 42
    room = snap.entities.rooms[0]
    assert room.min_.as_tuple() == (0, 0)
    assert room.max_.as_tuple() == (2, 2)


def test_null_lists_become_empty() -> None:
    snap = Snapshot.model_validate_json(ECHO_SNAPSHOT)
    assert snap.entities.displays == []  # JSON null -> []
    assert snap.entities.rooms[0].runners == []  # JSON null -> []
    assert snap.entities.pipes[0].values == []  # JSON null -> []


def test_pipe_values_parse() -> None:
    # A value in transit inside a pipe is [{index, value}], not a bare int.
    snap = Snapshot.model_validate_json(
        '{"entities": {"pipes": [{"id": 4, "path": [[3,1]], '
        '"values": [{"index": 0, "value": 42}], "src": 1, "dst": 2}]}, '
        '"halted": false, "step": 2}'
    )
    pv = snap.entities.pipes[0].values[0]
    assert pv.index == 0 and pv.value == 42


def test_fatal_snapshot() -> None:
    snap = Snapshot.model_validate_json(FATAL_SNAPSHOT)
    assert not snap.ok
    assert snap.fatal is not None
    assert snap.fatal.reason == "wall"
    assert snap.fatal.pos == Vec2(x=3, y=1)
    assert snap.output == []  # null coerced


# ── end-to-end via the Node CLI ───────────────────────────────────────────────
@node_required
def test_run_io_output() -> None:
    snap = Littleman().run(EXAMPLES / "io.man")
    assert snap.output == [123]
    assert snap.ok
    assert snap.reason == "done"


@node_required
def test_run_echo_with_int_input() -> None:
    assert Littleman().run(EXAMPLES / "echo.man", input=[42]).output == [42]


@node_required
def test_run_echo_with_str_input() -> None:
    assert Littleman().run(EXAMPLES / "echo.man", input="7").output == [7]


@node_required
def test_run_walk_halts() -> None:
    snap = Littleman().run(EXAMPLES / "walk.man")
    assert snap.halted
    assert snap.step > 0
    assert not snap.output


@node_required
def test_run_inline_source_fatal() -> None:
    snap = Littleman().run(WALL_PROGRAM)  # str => inline program
    assert snap.fatal is not None
    assert snap.fatal.reason == "wall"
    assert snap.fatal.pos == Vec2(x=3, y=1)


@node_required
def test_load_error_raises() -> None:
    with pytest.raises(LittlemanError) as ei:
        Littleman().run("not a room\n")
    assert "room" in str(ei.value).lower()


@node_required
def test_tick_advances_position() -> None:
    lm = Littleman()
    s0 = lm.tick(EXAMPLES / "walk.man", 0)
    s2 = lm.tick(EXAMPLES / "walk.man", 2)
    assert s2.entities.runners[0].pos.x > s0.entities.runners[0].pos.x


@node_required
def test_python_cli_run_json() -> None:
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "randomfun2026solvers.littleman",
            "run",
            str(EXAMPLES / "io.man"),
            "--json",
        ],
        cwd=REPO,
        env={"PYTHONPATH": str(PKG), "PATH": __import__("os").environ["PATH"]},
        text=True,
        capture_output=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    snap = Snapshot.model_validate_json(proc.stdout)
    assert snap.output == [123]
