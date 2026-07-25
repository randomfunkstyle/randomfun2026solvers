from pathlib import Path

import pytest

from littleman_tools.runner import Littleman

REPO = Path(__file__).parents[1]
TRANSISTOR = REPO / "tasks" / "solutions" / "primitives" / "transistor.man"
EXPECTED_SOURCE = """\
+------------+
|>.@rMrX0v...|
|^.....Ws<<..|
|......v.....|
|......>..^..|
+------------+
  ^         v
  ^         v
 +-+       +-+
 |I|       |O|
 +-+       +-+
"""


def test_transistor_source_is_a_y_free_streaming_cell() -> None:
    source = TRANSISTOR.read_text(encoding="utf-8")

    assert source == EXPECTED_SOURCE
    assert "Y" not in source
    assert source.count("r") == 2
    assert source.count("s") == 1


@pytest.mark.slow
def test_transistor_streams_controlled_forwarding_truth_table() -> None:
    snapshot = Littleman().judge(
        TRANSISTOR,
        input=[0, 0, 0, 1, 1, 0, 1, 1],
        expected=[0, 0, 0, 1],
        max_ticks=100,
    )

    assert snapshot.output == [0, 0, 0, 1]
    assert snapshot.output_settled is True
