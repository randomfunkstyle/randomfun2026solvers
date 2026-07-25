from __future__ import annotations

import pytest
from randomfun2026solvers.circuit import Circuit, Collision, E


def test_strict_corridor_allows_same_direction_merge() -> None:
    circuit = Circuit(6, 3, strict_corridors=True)
    circuit.turn(2, 1, E)
    circuit.route((0, 1), E, [], (4, 1), E)

    circuit.turn(2, 1, E)

    assert circuit.get(2, 1) == ">"


def test_strict_corridor_rejects_conflicting_turn() -> None:
    circuit = Circuit(6, 3, strict_corridors=True)
    circuit.route((0, 1), E, [], (4, 1), E)

    with pytest.raises(Collision, match="reserved as a corridor"):
        circuit.turn(2, 1, (0, 1))


def test_counted_ring_grows_symmetrically_for_spaced_body() -> None:
    circuit = Circuit(4, 8)

    exits = circuit.counted_ring(1, 1, "0.s")

    assert exits == [(3, 1), (0, 6)]
    assert circuit.get(2, 2) == "0"
    assert circuit.get(2, 4) == "s"
    assert circuit.get(1, 3) == "s"
