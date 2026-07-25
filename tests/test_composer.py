from types import MappingProxyType

import pytest

from littleman_tools.composer import Gate, Netlist


def test_netlist_records_signal_graph_and_dependency_levels() -> None:
    product_gate = Gate("and-gate.man", ("a", "b"), "product")
    result_gate = Gate("xor-gate.man", ("product", "b"), "result")
    netlist = Netlist(
        inputs=("a", "b"),
        gates=(product_gate, result_gate),
        outputs=("result",),
    )

    assert netlist.producers == {
        "a": None,
        "b": None,
        "product": product_gate,
        "result": result_gate,
    }
    assert netlist.consumers == {
        "a": (product_gate,),
        "b": (product_gate, result_gate),
        "product": (result_gate,),
        "result": (),
    }
    assert netlist.levels == {"a": 0, "b": 0, "product": 1, "result": 2}
    assert isinstance(netlist.producers, MappingProxyType)
    assert isinstance(netlist.consumers, MappingProxyType)
    assert isinstance(netlist.levels, MappingProxyType)


@pytest.mark.parametrize(
    ("inputs", "gates", "outputs", "message"),
    [
        (("a", "a"), (), (), "Duplicate input signal 'a'"),
        (
            ("a",),
            (Gate("unknown.man", ("a",), "out"),),
            ("out",),
            "Unknown primitive kind 'unknown.man'",
        ),
        (
            ("a",),
            (Gate("and-gate.man", ("a",), "out"),),
            ("out",),
            "expects 2 inputs, got 1",
        ),
        (
            ("a",),
            (Gate("and-gate.man", ("later", "a"), "out"),),
            ("out",),
            "Gate 0 input 'later' is not defined by a declared input or prior gate",
        ),
        (
            ("a",),
            (
                Gate("not-gate.man", ("a",), "same"),
                Gate("not-gate.man", ("same",), "same"),
            ),
            ("same",),
            "Duplicate produced signal 'same'",
        ),
        (
            ("a",),
            (Gate("not-gate.man", ("a",), "a"),),
            ("a",),
            "Produced signal 'a' conflicts with a declared input",
        ),
        (("a",), (), ("missing",), "Selected output 'missing' is not defined"),
    ],
)
def test_netlist_rejects_invalid_ordered_scalar_dags(
    inputs: tuple[str, ...], gates: tuple[Gate, ...], outputs: tuple[str, ...], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        Netlist(inputs=inputs, gates=gates, outputs=outputs)
