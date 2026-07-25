"""Fast STORE replacement policy tests."""

from randomfun2026solvers.lm1 import storeopt
from randomfun2026solvers.manast import Ast, PipeNode, RoomNode


def test_objective_uses_the_binding_side() -> None:
    assert storeopt._objective(100, 80, 12.5, "brackets") == 125_000


def test_shape_ignores_serialization_padding() -> None:
    assert storeopt._shape(["+--+   ", "|  |"]) == (4, 2)


def test_detach_removes_store_rooms_and_remembers_both_route_stubs() -> None:
    rooms = [
        RoomNode(id=0, x=0, y=0, kind="compute", w=1, h=1),
        RoomNode(id=1, x=5, y=0, kind="compute", w=1, h=1),
        RoomNode(id=2, x=10, y=0, kind="compute", w=1, h=1),
    ]
    pipes = [
        PipeNode(
            id=0,
            x=3,
            y=1,
            path=[(3, 1), (4, 1)],
            glyphs=[">", ">"],
            src=0,
            dst=1,
            entry_dir=(1, 0),
            exit_dir=(1, 0),
        ),
        PipeNode(
            id=1,
            x=8,
            y=1,
            path=[(8, 1), (9, 1)],
            glyphs=[">", ">"],
            src=1,
            dst=2,
            entry_dir=(1, 0),
            exit_dir=(1, 0),
        ),
    ]
    ast = Ast(rooms=rooms, pipes=pipes)
    seam = storeopt._seam(ast, (1,))
    detached = storeopt.detach_store(ast, seam)

    assert [room.id for room in detached.ast.rooms] == [0, 2]
    assert detached.ast.pipes == []
    assert [(route.direction, route.stub_cell) for route in seam.boundary_routes] == [
        ("in", (3, 1)),
        ("out", (9, 1)),
    ]
