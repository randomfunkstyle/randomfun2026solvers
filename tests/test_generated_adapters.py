from collections import defaultdict

import pytest

from littleman_tools.composer import (
    ActiveRoom,
    _AdapterFlow,
    _AdapterPort,
    _GeneratedAdapter,
    _make_input_demultiplexer,
    _make_output_joiner,
    _make_scalar_fanout,
    _make_two_field_packer,
)
from littleman_tools.primitive_contracts import Side
from littleman_tools.runner import Littleman


def test_input_demultiplexer_has_one_frame_input_and_ordered_scalar_outputs() -> None:
    adapter = _make_input_demultiplexer(2)

    assert adapter.room == ActiveRoom(
        text="+-----------+\n|@>rs  rs  v|\n|.^        <|\n+-----------+",
        grid=("+-----------+", "|@>rs  rs  v|", "|.^        <|", "+-----------+"),
        width=13,
        height=4,
        runner=(1, 1),
    )
    assert adapter.ports == (
        _AdapterPort(
            name="frame",
            flow=_AdapterFlow.INCOMING,
            side=Side.WEST,
            wall=(0, 1),
            instructions=((3, 1), (7, 1)),
        ),
        _AdapterPort(
            name="field[0]",
            flow=_AdapterFlow.OUTGOING,
            side=Side.NORTH,
            wall=(4, 0),
            instructions=((4, 1),),
        ),
        _AdapterPort(
            name="field[1]",
            flow=_AdapterFlow.OUTGOING,
            side=Side.NORTH,
            wall=(8, 0),
            instructions=((8, 1),),
        ),
    )


def test_scalar_fanout_uses_the_validated_atomic_send_room() -> None:
    adapter = _make_scalar_fanout(2)

    assert adapter.room.text == "+-----+\n|@>rSv|\n| ^  <|\n+-----+"
    assert adapter.room.grid == ("+-----+", "|@>rSv|", "| ^  <|", "+-----+")
    assert adapter.ports == (
        _AdapterPort(
            name="input",
            flow=_AdapterFlow.INCOMING,
            side=Side.NORTH,
            wall=(3, 0),
            instructions=((3, 1),),
        ),
        _AdapterPort(
            name="copy[0]",
            flow=_AdapterFlow.OUTGOING,
            side=Side.EAST,
            wall=(6, 1),
            instructions=((4, 1),),
        ),
        _AdapterPort(
            name="copy[1]",
            flow=_AdapterFlow.OUTGOING,
            side=Side.SOUTH,
            wall=(4, 3),
            instructions=((4, 1),),
        ),
    )


def test_two_field_packer_retains_the_validated_control_flow() -> None:
    adapter = _make_two_field_packer()

    assert adapter.room.text == "+-----+\n|@>rsv|\n|.^sr<|\n+-----+"
    assert adapter.room.grid == ("+-----+", "|@>rsv|", "|.^sr<|", "+-----+")
    assert adapter.ports == (
        _AdapterPort(
            name="field[0]",
            flow=_AdapterFlow.INCOMING,
            side=Side.NORTH,
            wall=(3, 0),
            instructions=((3, 1),),
        ),
        _AdapterPort(
            name="field[1]",
            flow=_AdapterFlow.INCOMING,
            side=Side.SOUTH,
            wall=(4, 3),
            instructions=((4, 2),),
        ),
        _AdapterPort(
            name="frame",
            flow=_AdapterFlow.OUTGOING,
            side=Side.EAST,
            wall=(6, 1),
            instructions=((4, 1), (3, 2)),
        ),
    )


def test_output_joiner_receives_fields_in_order_and_uses_one_frame_output() -> None:
    adapter = _make_output_joiner(3)

    assert adapter.room.grid == (
        "+---------------+",
        "|@>rs  rs  rs  v|",
        "|.^            <|",
        "+---------------+",
    )
    assert adapter.ports == (
        _AdapterPort(
            name="field[0]",
            flow=_AdapterFlow.INCOMING,
            side=Side.NORTH,
            wall=(3, 0),
            instructions=((3, 1),),
        ),
        _AdapterPort(
            name="field[1]",
            flow=_AdapterFlow.INCOMING,
            side=Side.NORTH,
            wall=(7, 0),
            instructions=((7, 1),),
        ),
        _AdapterPort(
            name="field[2]",
            flow=_AdapterFlow.INCOMING,
            side=Side.NORTH,
            wall=(11, 0),
            instructions=((11, 1),),
        ),
        _AdapterPort(
            name="frame",
            flow=_AdapterFlow.OUTGOING,
            side=Side.EAST,
            wall=(16, 1),
            instructions=((4, 1), (8, 1), (12, 1)),
        ),
    )


@pytest.mark.parametrize(
    "adapter",
    [
        pytest.param(_make_input_demultiplexer(2), id="input-demultiplexer"),
        pytest.param(_make_scalar_fanout(2), id="scalar-fanout"),
        pytest.param(_make_two_field_packer(), id="two-field-packer"),
        pytest.param(_make_output_joiner(3), id="output-joiner"),
    ],
)
def test_every_adapter_instruction_routes_only_to_its_declared_local_port(
    adapter: _GeneratedAdapter,
) -> None:
    source, origin, paths = _attach_test_pipes(adapter)
    routes_by_instruction: dict[tuple[int, int], list[set[tuple[int, int]]]] = defaultdict(list)

    for port in adapter.ports:
        for instruction in port.instructions:
            routes_by_instruction[instruction].append(set(paths[port]))

    for instruction, intended_paths in routes_by_instruction.items():
        global_instruction = (origin[0] + instruction[0], origin[1] + instruction[1])
        actual = {
            cell.as_tuple()
            for cell in Littleman().route(source, global_instruction[0], global_instruction[1])
        }
        expected = set().union(*intended_paths)
        assert actual == expected, (instruction, source)


def _attach_test_pipes(
    adapter: _GeneratedAdapter,
) -> tuple[str, tuple[int, int], dict[_AdapterPort, tuple[tuple[int, int], ...]]]:
    """Place the local room in a disposable straight-pipe route oracle fixture."""

    origin = (8, 8)
    canvas: dict[tuple[int, int], str] = {}
    for y, row in enumerate(adapter.room.grid):
        for x, cell in enumerate(row):
            canvas[(origin[0] + x, origin[1] + y)] = cell

    paths: dict[_AdapterPort, tuple[tuple[int, int], ...]] = {}
    attachments: dict[
        tuple[_AdapterFlow, Side, tuple[int, int]], tuple[tuple[int, int], ...]
    ] = {}
    for port in adapter.ports:
        key = (port.flow, port.side, port.wall)
        if key not in attachments:
            attachments[key] = _draw_straight_attachment(canvas, origin, port)
        paths[port] = attachments[key]

    min_x = min(x for x, _ in canvas)
    max_x = max(x for x, _ in canvas)
    min_y = min(y for _, y in canvas)
    max_y = max(y for _, y in canvas)
    source = "\n".join(
        "".join(canvas.get((x, y), " ") for x in range(min_x, max_x + 1))
        for y in range(min_y, max_y + 1)
    )
    translated_origin = (origin[0] - min_x, origin[1] - min_y)
    translated_paths = {
        port: tuple((x - min_x, y - min_y) for x, y in path) for port, path in paths.items()
    }
    return source, translated_origin, translated_paths


def _draw_straight_attachment(
    canvas: dict[tuple[int, int], str],
    origin: tuple[int, int],
    port: _AdapterPort,
) -> tuple[tuple[int, int], ...]:
    wall_x = origin[0] + port.wall[0]
    wall_y = origin[1] + port.wall[1]
    outgoing = port.flow is _AdapterFlow.OUTGOING

    if port.side is Side.NORTH:
        near, far = (wall_x, wall_y - 1), (wall_x, wall_y - 2)
        pipe = (near, far) if outgoing else (far, near)
        glyph = "^" if outgoing else "v"
        _draw_terminal_room(canvas, wall_x - 1, wall_y - 5)
    elif port.side is Side.SOUTH:
        near, far = (wall_x, wall_y + 1), (wall_x, wall_y + 2)
        pipe = (near, far) if outgoing else (far, near)
        glyph = "v" if outgoing else "^"
        _draw_terminal_room(canvas, wall_x - 1, wall_y + 3)
    elif port.side is Side.WEST:
        near, far = (wall_x - 1, wall_y), (wall_x - 2, wall_y)
        pipe = (near, far) if outgoing else (far, near)
        glyph = "<" if outgoing else ">"
        _draw_terminal_room(canvas, wall_x - 5, wall_y - 1)
    else:
        near, far = (wall_x + 1, wall_y), (wall_x + 2, wall_y)
        pipe = (near, far) if outgoing else (far, near)
        glyph = ">" if outgoing else "<"
        _draw_terminal_room(canvas, wall_x + 3, wall_y - 1)

    canvas[near] = glyph
    canvas[far] = glyph
    return pipe


def _draw_terminal_room(canvas: dict[tuple[int, int], str], left: int, top: int) -> None:
    for y, row in enumerate(("+-+", "| |", "+-+")):
        for x, cell in enumerate(row):
            canvas[(left + x, top + y)] = cell
