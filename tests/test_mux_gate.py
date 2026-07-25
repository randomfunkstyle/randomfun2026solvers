from pathlib import Path

import pytest

from littleman_tools.runner import Littleman

REPO = Path(__file__).parents[1]
PRIMITIVES = REPO / "tasks" / "solutions" / "primitives"
MUX_GATE = PRIMITIVES / "mux-gate.man"
COMPACT_BACKUP = PRIMITIVES / "mux-gate-select-first-user-u-compact.man"

# Each triplet is (select, i1, i2), emitting i1 for select=0 and i2 for select=1.
SELECT_FIRST_INPUT = [
    0, 0, 0,
    0, 0, 1,
    0, 1, 0,
    0, 1, 1,
    1, 0, 0,
    1, 0, 1,
    1, 1, 0,
    1, 1, 1,
]
SELECT_FIRST_OUTPUT = [0, 0, 1, 1, 0, 1, 0, 1]


def test_mux_gate_is_a_y_free_select_first_single_runner_cell() -> None:
    source = MUX_GATE.read_text(encoding="utf-8")

    assert "Y" not in source
    assert source.count("@") == 1
    assert "U" in source
    assert "a" in source


@pytest.mark.slow
@pytest.mark.parametrize(
    ("gate", "ticks", "walking", "side"),
    [
        (MUX_GATE, 106, 17, 10),
        (COMPACT_BACKUP, 126, 31, 9),
    ],
)
def test_mux_gate_variants_stream_the_select_first_truth_table(
    gate: Path, ticks: int, walking: int, side: int
) -> None:
    runner = Littleman()
    snapshot = runner.judge(
        gate,
        input=SELECT_FIRST_INPUT,
        expected=SELECT_FIRST_OUTPUT,
        max_ticks=ticks,
    )
    profile = runner.activity_profile(
        gate,
        input=SELECT_FIRST_INPUT,
        expected=SELECT_FIRST_OUTPUT,
        max_ticks=ticks,
    )
    source = gate.read_text(encoding="utf-8")
    source_lines = source.splitlines()

    assert snapshot.output == SELECT_FIRST_OUTPUT
    assert snapshot.output_settled is True
    assert snapshot.step == ticks
    assert profile.walking_ticks == walking
    assert "Y" not in source
    assert source.count("@") == 1
    assert "U" in source
    assert "a" in source
    assert max(len(source_lines), *(len(row) for row in source_lines)) == side
