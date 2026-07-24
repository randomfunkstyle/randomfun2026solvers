"""The per-program CPU synthesiser (ARCH.md §7.5): program in, machine out.

The machines are run on the bundled reference interpreter, so these assert on
real littleman semantics rather than on the generator's own idea of them.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest
from randomfun2026solvers.littleman import Littleman
from randomfun2026solvers.lm1.synth import ECHO, TRIANGLE, assign, synthesise

PROBLEMS = Path(__file__).resolve().parents[1] / "tasks" / "problems"


def _man(prog) -> Path:
    path = Path(tempfile.mkdtemp()) / f"{prog.name}.man"
    path.write_text("\n".join(synthesise(prog)) + "\n")
    return path


def _output(prog, value: int, ticks: int) -> list[int]:
    """Snapshot the output after ``ticks``.

    A synthesised machine has a *looping* ROM (ARCH.md 5.3), so its ROM man never
    halts -- once the CPU halts he simply blocks on a full pipe. `run` would raise
    on the tick cap, so correctness is checked by snapshotting instead. Grading
    works the same way: it stops at the last correct output and never requires a
    halt.
    """
    return Littleman().tick(_man(prog), ticks, input=[value]).output


def test_depth_and_lane_count_follow_the_opcode_set() -> None:
    """The whole point of synthesis: fewer opcodes, smaller machine."""
    k_tri, _, rows_tri = assign(TRIANGLE)
    k_echo, _, rows_echo = assign(ECHO)
    assert (len(TRIANGLE.used), k_tri) == (7, 3)  # 7 opcodes -> depth 3, 8 lanes
    assert (len(ECHO.used), k_echo) == (3, 2)  # 3 opcodes -> depth 2, 4 lanes
    assert max(rows_tri.values()) == 15
    assert max(rows_echo.values()) == 7


def test_echo_machine_is_smaller_than_triangle_machine() -> None:
    tri, echo = synthesise(TRIANGLE), synthesise(ECHO)
    assert len(echo) < len(tri)
    assert max(map(len, echo)) < max(map(len, tri))


def test_io_lanes_hug_the_walls_they_need() -> None:
    """IN must be the top lane and OUT the bottom one, or their pipes mis-resolve."""
    for prog in (TRIANGLE, ECHO):
        _, _, rows = assign(prog)
        span = 2 * (1 << assign(prog)[0]) - 1
        assert rows["IN"] == 1
        assert rows["OUT"] == span


def test_opcode_numbers_are_a_bit_reversal_of_the_lane_row() -> None:
    k, nums, rows = assign(TRIANGLE)
    for name, row in rows.items():
        idx = (row - 1) // 2
        assert nums[name] == int(format(idx, f"0{k}b")[::-1], 2)


def test_synthesised_echo_echoes() -> None:
    assert _output(ECHO, 42, 150) == [42]


@pytest.mark.parametrize(
    ("n", "want"),
    [
        (int(t["in"][0]), int(t["out"][0]))
        for t in json.loads((PROBLEMS / "triangle.json").read_text())["publicTestData"]
    ],
)
def test_synthesised_triangle_passes_public_cases(n: int, want: int) -> None:
    assert _output(TRIANGLE, n, 600) == [want]


@pytest.mark.parametrize("n", [2, 999, 1000])
def test_synthesised_triangle_at_the_constraint_limit(n: int) -> None:
    assert _output(TRIANGLE, n, 600) == [n * (n + 1) // 2]
