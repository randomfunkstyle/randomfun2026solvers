from pathlib import Path

import pytest

from littleman_tools.runner import Littleman

REPO = Path(__file__).parents[1]
NOT_GATE = REPO / "tasks" / "solutions" / "primitives" / "not-gate.man"


@pytest.mark.slow
def test_not_gate_complements_a_stream_of_strict_bits() -> None:
    runner = Littleman()
    snapshot = runner.judge(
        NOT_GATE,
        input=[0, 1, 1, 0],
        expected=[1, 0, 0, 1],
        max_ticks=100,
    )
    profile = runner.activity_profile(
        NOT_GATE,
        input=[0, 1, 1, 0],
        expected=[1, 0, 0, 1],
        max_ticks=100,
    )

    source = NOT_GATE.read_text(encoding="utf-8").splitlines()
    analysis = runner.analyze(NOT_GATE)
    incoming_pipe = analysis.pipes[1]

    assert snapshot.output == [1, 0, 0, 1]
    assert snapshot.output_settled is True
    assert "Y" not in "\n".join(source)
    assert max(len(source), *(len(row) for row in source)) == 8
    assert [segment.pos.as_tuple() for segment in incoming_pipe.path] == [(3, 3), (2, 3)]
    assert [segment.dir.as_tuple() for segment in incoming_pipe.path] == [(-1, 0), (0, 1)]
    assert [cell.as_tuple() for cell in runner.route(NOT_GATE, 2, 5)] == [(3, 3), (2, 3)]
    assert profile.total_ticks == 34
    assert profile.walking_ticks == 1
    assert profile.stall_ticks == 1
    assert profile.straight_through_arrows == 0
