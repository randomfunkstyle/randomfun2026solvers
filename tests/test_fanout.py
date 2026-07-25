import re
from pathlib import Path
from types import MappingProxyType

import pytest

from littleman_tools.composer import (
    FanOut,
    Gate,
    Netlist,
    _lower_layout_rooms,
    _RoomRole,
    compose,
)
from littleman_tools.runner import Littleman


def _fanout_half_adder() -> Netlist:
    return Netlist(
        inputs=("a", "b"),
        fanouts=(
            FanOut(
                source=("a", "b"),
                branches=(("xor_a", "xor_b"), ("and_a", "and_b")),
            ),
        ),
        gates=(
            Gate("xor-gate.man", ("xor_a", "xor_b"), "sum"),
            Gate("and-gate.man", ("and_a", "and_b"), "carry"),
        ),
        outputs=("sum", "carry"),
    )


def test_netlist_records_explicit_input_frame_fanout() -> None:
    netlist = _fanout_half_adder()

    assert netlist.producers["xor_a"] == netlist.fanouts[0]
    assert netlist.levels["xor_a"] == 0
    assert netlist.consumers["xor_a"] == (netlist.gates[0],)
    assert isinstance(netlist.producers, MappingProxyType)
    assert isinstance(netlist.consumers, MappingProxyType)
    assert isinstance(netlist.levels, MappingProxyType)


def test_explicit_input_frame_fanout_avoids_scalar_adapters() -> None:
    source = compose(_fanout_half_adder())
    analysis = Littleman().analyze(source)

    assert len(analysis.rooms) == 6  # I, S, XOR, AND, joiner, O
    assert source.count("@") == 4  # S, XOR, AND, output joiner


def test_explicit_input_frame_fanout_lowers_to_one_fanout_without_scalar_adapters() -> None:
    primitive_root = Path(__file__).parents[1] / "tasks" / "solutions" / "primitives"

    specs, _ = _lower_layout_rooms(_fanout_half_adder(), primitive_root)
    roles = [spec.role for spec in specs]

    assert roles.count(_RoomRole.FANOUT) == 1
    assert _RoomRole.INPUT_DEMULTIPLEXER not in roles
    assert _RoomRole.PACKER not in roles


def test_explicit_frame_fanout_preserves_scalar_input_fallback() -> None:
    netlist = _fanout_half_adder()
    netlist = Netlist(
        inputs=netlist.inputs,
        fanouts=netlist.fanouts,
        gates=netlist.gates,
        outputs=("sum", "carry", "a"),
    )
    primitive_root = Path(__file__).parents[1] / "tasks" / "solutions" / "primitives"

    specs, connections = _lower_layout_rooms(netlist, primitive_root)
    roles = [spec.role for spec in specs]

    assert roles.count(_RoomRole.FANOUT) == 1
    assert roles.count(_RoomRole.INPUT_DEMULTIPLEXER) == 1
    assert _RoomRole.PACKER not in roles
    assert any(
        connection.target.room == "output" and connection.target.port == "field[2]"
        for connection in connections
    )


def test_explicit_input_subset_uses_one_demultiplexer_and_one_frame_packer() -> None:
    netlist = Netlist(
        inputs=("a", "b", "passthrough"),
        fanouts=(
            FanOut(
                source=("a", "b"),
                branches=(("xor_a", "xor_b"), ("and_a", "and_b")),
            ),
        ),
        gates=(
            Gate("xor-gate.man", ("xor_a", "xor_b"), "sum"),
            Gate("and-gate.man", ("and_a", "and_b"), "carry"),
        ),
        outputs=("sum", "carry", "passthrough"),
    )
    primitive_root = Path(__file__).parents[1] / "tasks" / "solutions" / "primitives"

    specs, _ = _lower_layout_rooms(netlist, primitive_root)
    roles = [spec.role for spec in specs]

    assert roles.count(_RoomRole.INPUT_DEMULTIPLEXER) == 1
    assert roles.count(_RoomRole.PACKER) == 1
    assert roles.count(_RoomRole.FANOUT) == 1


@pytest.mark.slow
def test_explicit_fanout_half_adder_preserves_ordered_sum_and_carry() -> None:
    snapshot = Littleman().judge(
        compose(_fanout_half_adder()),
        input=[0, 0, 0, 1, 1, 0, 1, 1],
        expected=[0, 0, 1, 0, 1, 0, 0, 1],
        max_ticks=1_000,
    )

    assert snapshot.output == [0, 0, 1, 0, 1, 0, 0, 1]
    assert snapshot.output_settled is True
    assert snapshot.fatal is None


def test_fanout_and_netlist_normalize_caller_sequences_to_tuples() -> None:
    source = ["a", "b"]
    branches = [["xor_a", "xor_b"], ["and_a", "and_b"]]
    fanouts = [FanOut(source, branches)]
    netlist = Netlist(
        inputs=["a", "b"],
        fanouts=fanouts,
        gates=[
            Gate("xor-gate.man", ("xor_a", "xor_b"), "sum"),
            Gate("and-gate.man", ("and_a", "and_b"), "carry"),
        ],
        outputs=["sum", "carry"],
    )

    source.clear()
    branches.clear()
    fanouts.clear()

    assert netlist.fanouts == (FanOut(("a", "b"), (("xor_a", "xor_b"), ("and_a", "and_b"))),)
    assert isinstance(netlist.fanouts, tuple)
    assert isinstance(netlist.fanouts[0].source, tuple)
    assert all(isinstance(branch, tuple) for branch in netlist.fanouts[0].branches)


@pytest.mark.parametrize(
    ("fanout", "gates", "outputs", "message"),
    [
        (
            FanOut(("a", "later"), (("x", "y"), ("u", "v"))),
            (),
            (),
            "FanOut source signal 'later' is not a declared input",
        ),
        (
            FanOut(("a", "b"), (("x", "y"), ("u",))),
            (),
            (),
            "FanOut branch 1 has width 1, expected 2",
        ),
        (
            FanOut(("a",), (("x",), ("x",))),
            (),
            (),
            "Duplicate FanOut branch signal 'x'",
        ),
        (
            FanOut(("a",), (("a",), ("x",))),
            (),
            (),
            "FanOut branch signal 'a' conflicts with a declared input",
        ),
        (
            FanOut(("a",), (("x",), ("y",))),
            (Gate("not-gate.man", ("x",), "y"),),
            (),
            "Produced signal 'y' conflicts with a FanOut branch",
        ),
        (
            FanOut(("a", "b"), (("x", "y"), ("u", "v"))),
            (Gate("not-gate.man", ("x",), "out"),),
            (),
            "Gate 0 must consume complete FanOut branch ('x', 'y')",
        ),
        (
            FanOut(("a",), (("x",), ("y",))),
            (
                Gate("not-gate.man", ("x",), "first"),
                Gate("not-gate.man", ("x",), "second"),
                Gate("not-gate.man", ("y",), "third"),
            ),
            (),
            "FanOut branch ('x',) is consumed more than once",
        ),
        (
            FanOut(("a",), (("x",), ("y",))),
            (
                Gate("not-gate.man", ("x",), "first"),
                Gate("not-gate.man", ("y",), "second"),
            ),
            ("first", "x"),
            "Selected output 'x' is a FanOut branch signal",
        ),
    ],
)
def test_netlist_rejects_invalid_explicit_fanouts(
    fanout: FanOut,
    gates: tuple[Gate, ...],
    outputs: tuple[str, ...],
    message: str,
) -> None:
    with pytest.raises(ValueError, match=re.escape(message)):
        Netlist(inputs=("a", "b"), fanouts=(fanout,), gates=gates, outputs=outputs)
