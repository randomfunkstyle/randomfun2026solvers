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


def _fanout_half_adder_with_subset_source() -> Netlist:
    return Netlist(
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


def _fanout_half_adder_with_scalar_fallback() -> Netlist:
    netlist = _fanout_half_adder()
    return Netlist(
        inputs=netlist.inputs,
        fanouts=netlist.fanouts,
        gates=netlist.gates,
        outputs=("sum", "carry", "a"),
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


def test_netlist_rejects_explicit_fanout_of_a_proper_input_subset() -> None:
    with pytest.raises(
        ValueError,
        match=re.escape(
            "FanOut source ('a', 'b') must exactly match declared inputs "
            "('a', 'b', 'passthrough')"
        ),
    ):
        _fanout_half_adder_with_subset_source()


def test_netlist_rejects_scalar_fallback_for_an_explicitly_fanned_out_input() -> None:
    with pytest.raises(
        ValueError,
        match=re.escape("Explicit FanOut input signal 'a' cannot be selected directly"),
    ):
        _fanout_half_adder_with_scalar_fallback()


def test_netlist_rejects_direct_gate_consumer_of_explicit_fanout_input() -> None:
    fanout = FanOut(source=("a",), branches=(("left",), ("right",)))

    with pytest.raises(
        ValueError,
        match=re.escape(
            "Explicit FanOut input signal 'a' cannot be consumed directly by a gate"
        ),
    ):
        Netlist(
            inputs=("a",),
            fanouts=(fanout,),
            gates=(
                Gate("not-gate.man", ("left",), "left_result"),
                Gate("not-gate.man", ("right",), "right_result"),
                Gate("not-gate.man", ("a",), "direct_result"),
            ),
            outputs=("left_result", "right_result", "direct_result"),
        )


def test_netlist_rejects_more_than_one_explicit_fanout_in_v1() -> None:
    fanout = FanOut(source=("a",), branches=(("left",), ("right",)))

    with pytest.raises(ValueError, match="V1 supports at most one explicit FanOut"):
        Netlist(
            inputs=("a",),
            fanouts=(fanout, fanout),
            gates=(),
            outputs=(),
        )


def test_thirteen_explicit_branches_reject_scalar_fallback_clearly() -> None:
    branches = tuple((f"branch_{index}",) for index in range(13))

    with pytest.raises(
        ValueError,
        match=re.escape("Explicit FanOut input signal 'a' cannot be selected directly"),
    ):
        Netlist(
            inputs=("a",),
            fanouts=(FanOut(source=("a",), branches=branches),),
            gates=tuple(
                Gate("not-gate.man", branch, f"result_{index}")
                for index, branch in enumerate(branches)
            ),
            outputs=tuple(f"result_{index}" for index in range(13)) + ("a",),
        )


def test_thirteen_explicit_branches_fit_one_direct_fanout_room() -> None:
    branches = tuple((f"branch_{index}",) for index in range(13))
    netlist = Netlist(
        inputs=("a",),
        fanouts=(FanOut(source=("a",), branches=branches),),
        gates=tuple(
            Gate("not-gate.man", branch, f"result_{index}")
            for index, branch in enumerate(branches)
        ),
        outputs=tuple(f"result_{index}" for index in range(13)),
    )
    primitive_root = Path(__file__).parents[1] / "tasks" / "solutions" / "primitives"

    specs, connections = _lower_layout_rooms(netlist, primitive_root)

    assert sum(spec.role is _RoomRole.FANOUT for spec in specs) == 1
    assert any(
        connection.source.room == "fanout.frame[0]"
        and connection.source.port == "copy[12]"
        for connection in connections
    )


def test_netlist_rejects_more_than_thirteen_direct_fanout_branches() -> None:
    branches = tuple((f"branch_{index}",) for index in range(14))

    with pytest.raises(ValueError, match="at most 13 branches"):
        Netlist(
            inputs=("a",),
            fanouts=(FanOut(source=("a",), branches=branches),),
            gates=tuple(
                Gate("not-gate.man", branch, f"result_{index}")
                for index, branch in enumerate(branches)
            ),
            outputs=tuple(f"result_{index}" for index in range(14)),
        )


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
    ("inputs", "fanout", "gates", "outputs", "message"),
    [
        (
            ("a", "b"),
            FanOut(("a", "later"), (("x", "y"), ("u", "v"))),
            (),
            (),
            "FanOut source signal 'later' is not a declared input",
        ),
        (
            ("a", "b"),
            FanOut(("a", "b"), (("x", "y"), ("u",))),
            (),
            (),
            "FanOut branch 1 has width 1, expected 2",
        ),
        (
            ("a",),
            FanOut(("a",), (("x",), ("x",))),
            (),
            (),
            "Duplicate FanOut branch signal 'x'",
        ),
        (
            ("a",),
            FanOut(("a",), (("a",), ("x",))),
            (),
            (),
            "FanOut branch signal 'a' conflicts with a declared input",
        ),
        (
            ("a",),
            FanOut(("a",), (("x",), ("y",))),
            (Gate("not-gate.man", ("x",), "y"),),
            (),
            "Produced signal 'y' conflicts with a FanOut branch",
        ),
        (
            ("a", "b"),
            FanOut(("a", "b"), (("x", "y"), ("u", "v"))),
            (Gate("not-gate.man", ("x",), "out"),),
            (),
            "Gate 0 must consume complete FanOut branch ('x', 'y')",
        ),
        (
            ("a",),
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
            ("a",),
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
    inputs: tuple[str, ...],
    fanout: FanOut,
    gates: tuple[Gate, ...],
    outputs: tuple[str, ...],
    message: str,
) -> None:
    with pytest.raises(ValueError, match=re.escape(message)):
        Netlist(inputs=inputs, fanouts=(fanout,), gates=gates, outputs=outputs)
