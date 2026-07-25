import json
from pathlib import Path

import pytest

from littleman_tools.runner import Littleman

REPO = Path(__file__).parents[1]
TRIANGLE = REPO / "tasks" / "solutions" / "triangle.man"
PROBLEM = REPO / "tasks" / "problems" / "triangle.json"
EXPECTED_SOURCE = """\
+------+
|@rM*+v|
|s/W2M<|
+------+
 +-+>^ v
 |I|+-+<
 +-+|O| 
    +-+
"""


def test_triangle_source_is_the_approved_grid() -> None:
    assert TRIANGLE.read_text(encoding="utf-8") == EXPECTED_SOURCE


@pytest.mark.slow
def test_triangle_passes_every_public_case() -> None:
    problem = json.loads(PROBLEM.read_text(encoding="utf-8"))
    runner = Littleman()
    for case in problem["publicTestData"]:
        snapshot = runner.judge(
            TRIANGLE,
            input=case["in"],
            expected=case["out"],
            max_ticks=problem["tickCap"] or 5_000_000,
        )
        assert snapshot.output == [int(value) for value in case["out"]], case["name"]
        assert snapshot.output_settled is True, case["name"]
