from pathlib import Path

import pytest

from littleman_tools.runner import Littleman, Snapshot

REPO = Path(__file__).parents[1]
TRIANGLE = REPO / "tasks" / "solutions" / "triangle.man"


def test_snapshot_normalizes_null_collections() -> None:
    snapshot = Snapshot.model_validate(
        {
            "entities": {"runners": None, "pipes": None, "rooms": None},
            "output": None,
            "halted": True,
        }
    )
    assert snapshot.entities.runners == []
    assert snapshot.entities.pipes == []
    assert snapshot.entities.rooms == []
    assert snapshot.output == []


@pytest.mark.slow
def test_runner_executes_triangle() -> None:
    snapshot = Littleman().run(TRIANGLE, input=[4])
    assert snapshot.output == [10]
