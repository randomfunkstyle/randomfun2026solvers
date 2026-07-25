from pathlib import Path

import pytest

from littleman_tools.scoring import footprint, load_problem, score_program

REPO = Path(__file__).parents[1]
TRIANGLE = REPO / "tasks" / "solutions" / "triangle.man"


def test_footprint_uses_square_of_largest_dimension() -> None:
    assert footprint("+--+\n|  |\n+--+\n") == (4, 3, 16)


def test_load_problem_resolves_slug() -> None:
    problem = load_problem("triangle")
    assert problem["slug"] == "triangle"
    assert problem["publicTestData"][2] == {
        "name": "four",
        "in": ["4"],
        "out": ["10"],
    }


@pytest.mark.slow
def test_triangle_has_a_finite_score() -> None:
    result = score_program(TRIANGLE, "triangle")
    assert result.scoring == "footprint-tick"
    assert result.score is not None
    assert result.score > 0
    assert len(result.cases) == 6
