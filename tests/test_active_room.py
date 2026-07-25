from pathlib import Path

import pytest

from littleman_tools.composer import ActiveRoom, extract_active_room

REPO = Path(__file__).parents[1]
PRIMITIVES = REPO / "tasks" / "solutions" / "primitives"


def test_extract_active_room_from_primitive_path_preserves_local_geometry() -> None:
    room = extract_active_room(PRIMITIVES / "and-gate.man")

    assert room == ActiveRoom(
        text="+-----+\n|@>rMU|\n| ^s*<|\n+-----+",
        grid=("+-----+", "|@>rMU|", "| ^s*<|", "+-----+"),
        width=7,
        height=4,
        runner=(1, 1),
    )


def test_extract_active_room_selects_runner_room_regardless_of_room_order() -> None:
    primitive = (PRIMITIVES / "xor-gate.man").read_text(encoding="utf-8")
    active = "+-----+\n|@>rMU|\n| ^s~<|\n+-----+"
    first = "+-+\n|I|\n+-+\n" + active + "\n+-+\n|O|\n+-+"
    last = "+-+\n|O|\n+-+\n+-+\n|I|\n+-+\n" + active

    assert extract_active_room(first).text == active
    assert extract_active_room(last).text == active
    assert extract_active_room(primitive).text == active


@pytest.mark.parametrize(
    ("source", "message"),
    [
        ("+-+\n|I|\n+-+", "No runner '@' found"),
        (
            "+-+ +-+\n|@| |@|\n+-+ +-+",
            "Multiple closed rooms contain '@' runners",
        ),
        ("+--+\n|@  \n+--+", "Active room containing '@' is not closed"),
    ],
)
def test_extract_active_room_rejects_invalid_runner_rooms(source: str, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        extract_active_room(source)
