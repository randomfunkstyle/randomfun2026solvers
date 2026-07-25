from pathlib import Path

import pytest

from littleman_tools.runner import Littleman

REPO = Path(__file__).parents[1]
BIT_REGISTER = REPO / "tasks" / "solutions" / "primitives" / "bit-register.man"


@pytest.mark.slow
def test_bit_register_emits_the_previous_bit_with_a_zero_reset() -> None:
    runner = Littleman()
    snapshot = runner.judge(
        BIT_REGISTER,
        input=[1, 0, 1, 1],
        expected=[0, 1, 0, 1],
        max_ticks=100,
    )

    source = BIT_REGISTER.read_text(encoding="utf-8").splitlines()
    assert snapshot.output == [0, 1, 0, 1]
    assert snapshot.output_settled is True
    assert "Y" not in "\n".join(source)
    assert max(len(source), *(len(row) for row in source)) == 8
