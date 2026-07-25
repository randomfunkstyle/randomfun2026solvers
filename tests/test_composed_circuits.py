import pytest

from littleman_tools.composer import Gate, Netlist, compose
from littleman_tools.runner import Littleman

ORDERED_INPUT_PAIRS = ((0, 0), (0, 1), (1, 0), (1, 1))


def _dependent_chain() -> Netlist:
    return Netlist(
        inputs=("a", "b"),
        gates=(
            Gate("and-gate.man", ("a", "b"), "product"),
            Gate("xor-gate.man", ("product", "b"), "result"),
        ),
        outputs=("result",),
    )


def _half_adder() -> Netlist:
    return Netlist(
        inputs=("a", "b"),
        gates=(
            Gate("xor-gate.man", ("a", "b"), "sum"),
            Gate("and-gate.man", ("a", "b"), "carry"),
        ),
        outputs=("sum", "carry"),
    )


def test_generated_circuits_compose_deterministically() -> None:
    for netlist in (_dependent_chain(), _half_adder()):
        assert compose(netlist) == compose(netlist)


@pytest.mark.slow
def test_dependent_chain_emits_one_result_for_each_ordered_input_pair() -> None:
    input_frame = [value for pair in ORDERED_INPUT_PAIRS for value in pair]
    expected_output_frame = [0, 1, 0, 0]

    snapshot = Littleman().judge(
        compose(_dependent_chain()),
        input=input_frame,
        expected=expected_output_frame,
        max_ticks=1_000,
    )

    assert snapshot.output == expected_output_frame
    assert snapshot.output_settled is True
    assert snapshot.fatal is None


@pytest.mark.slow
def test_parallel_half_adder_emits_fields_in_declared_output_order() -> None:
    netlist = _half_adder()
    truth_table = (
        ({"a": 0, "b": 0}, {"sum": 0, "carry": 0}),
        ({"a": 0, "b": 1}, {"sum": 1, "carry": 0}),
        ({"a": 1, "b": 0}, {"sum": 1, "carry": 0}),
        ({"a": 1, "b": 1}, {"sum": 0, "carry": 1}),
    )
    input_frame = [inputs[signal] for inputs, _ in truth_table for signal in netlist.inputs]
    expected_output_frame = [
        outputs[signal] for _, outputs in truth_table for signal in netlist.outputs
    ]

    assert netlist.outputs == ("sum", "carry")
    assert expected_output_frame == [0, 0, 1, 0, 1, 0, 0, 1]

    snapshot = Littleman().judge(
        compose(netlist),
        input=input_frame,
        expected=expected_output_frame,
        max_ticks=1_000,
    )

    assert snapshot.output == expected_output_frame
    assert snapshot.output_settled is True
    assert snapshot.fatal is None
