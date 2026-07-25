from pathlib import Path

import pytest

from littleman_tools.runner import Littleman

REPO = Path(__file__).parents[1]
PRIMITIVES = REPO / "tasks" / "solutions" / "primitives"
PAIR_INPUT = [0, 0, 0, 1, 1, 0, 1, 1]


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("xor-gate.man", [0, 1, 1, 0]),
        ("or-gate.man", [0, 1, 1, 1]),
    ],
)
def test_logic_gate_is_a_y_free_single_runner_streaming_cell(
    name: str, expected: list[int]
) -> None:
    gate = PRIMITIVES / name
    source = gate.read_text(encoding="utf-8")
    snapshot = Littleman().judge(gate, input=PAIR_INPUT, expected=expected, max_ticks=200)

    assert "Y" not in source
    assert source.count("@") == 1
    assert snapshot.output == expected
    assert snapshot.output_settled is True
    assert snapshot.fatal is None


@pytest.mark.slow
@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("xor-gate.man", [0, 1, 1, 0]),
        ("or-gate.man", [0, 1, 1, 1]),
    ],
)
def test_logic_gate_has_compact_steady_streaming_timing(
    name: str, expected: list[int]
) -> None:
    gate = PRIMITIVES / name
    runtime = Littleman()
    analysis = runtime.analyze(gate)
    first = runtime.tick(gate, 9, input=PAIR_INPUT)
    second = runtime.tick(gate, 17, input=PAIR_INPUT)
    snapshot = runtime.judge(gate, input=PAIR_INPUT * 8, expected=expected * 8, max_ticks=400)

    assert (
        max(room.max_.x for room in analysis.rooms) - min(room.min_.x for room in analysis.rooms)
    ) == 6
    assert (
        max(room.max_.y for room in analysis.rooms) - min(room.min_.y for room in analysis.rooms)
    ) == 7
    assert sorted((room.max_.x - room.min_.x, room.max_.y - room.min_.y) for room in analysis.rooms) == [
        (2, 2),
        (2, 2),
        (6, 3),
    ]
    assert len(analysis.pipes) == 2
    assert first.entities.runners[0].halted is False
    assert second.entities.runners[0].halted is False
    assert first.output == expected[:1]
    assert second.output == expected[:2]
    assert snapshot.output == expected * 8
    assert snapshot.output_settled is True
    assert snapshot.step == 257
    assert len(snapshot.entities.rooms) == 3
    assert len(snapshot.entities.pipes) == 2
