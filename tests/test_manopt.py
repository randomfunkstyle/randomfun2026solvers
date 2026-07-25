"""Move-family coverage for the deterministic AST optimizer."""

from __future__ import annotations

from randomfun2026solvers.manast import Ast, Atom, RoomNode
from randomfun2026solvers.manopt import candidates


def _room_with_slack() -> Ast:
    room = RoomNode(
        id=0,
        x=0,
        y=0,
        kind="compute",
        w=3,
        h=3,
        children=[
            Atom(
                id=0,
                x=1,
                y=1,
                rows=["@  ", "   ", "  H"],
            )
        ],
    )
    return Ast(rooms=[room])


def test_move_sets_keep_squashing_explicit_and_deterministic() -> None:
    ast = _room_with_slack()

    layout = candidates(ast, move_set="layout")
    cuts = candidates(ast, move_set="cuts")
    squashes = candidates(ast, move_set="squash")
    all_moves = candidates(ast, move_set="all")

    assert layout and not any(move.kind == "squash" for move in layout)
    assert cuts and {move.kind for move in cuts} <= {"drop-row", "drop-col"}
    assert squashes and {move.kind for move in squashes} == {"squash"}
    assert any(move.kind == "squash" for move in all_moves)
