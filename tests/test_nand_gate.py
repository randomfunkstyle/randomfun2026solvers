from pathlib import Path

import pytest

from littleman_tools.runner import Littleman

REPO = Path(__file__).parents[1]
NAND_GATE = REPO / "tasks" / "solutions" / "primitives" / "nand-gate.man"


@pytest.mark.slow
def test_nand_gate_complements_the_binary_product_stream() -> None:
    runner = Littleman()
    snapshot = runner.judge(
        NAND_GATE,
        input=[0, 0, 0, 1, 1, 0, 1, 1],
        expected=[1, 1, 1, 0],
        max_ticks=100,
    )
    profile = runner.activity_profile(
        NAND_GATE,
        input=[0, 0, 0, 1, 1, 0, 1, 1],
        expected=[1, 1, 1, 0],
        max_ticks=100,
    )

    source = NAND_GATE.read_text(encoding="utf-8").splitlines()
    analysis = runner.analyze(NAND_GATE)
    incoming_pipe = analysis.pipes[1]

    assert snapshot.output == [1, 1, 1, 0]
    assert snapshot.output_settled is True
    assert "Y" not in "\n".join(source)
    assert max(len(source), *(len(row) for row in source)) == 9
    assert [segment.pos.as_tuple() for segment in incoming_pipe.path] == [(3, 3), (2, 3)]
    assert [segment.dir.as_tuple() for segment in incoming_pipe.path] == [(-1, 0), (0, 1)]
    assert [cell.as_tuple() for cell in runner.route(NAND_GATE, 7, 5)] == [(3, 3), (2, 3)]
    assert profile.total_ticks == 56
    assert profile.walking_ticks == 2
    assert profile.stall_ticks == 0
    assert profile.straight_through_arrows == 1
