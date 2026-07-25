from pathlib import Path
from types import MappingProxyType

import pytest

from littleman_tools import composer
from littleman_tools.composer import Gate, Netlist
from littleman_tools.runner import Littleman


def _not_netlist() -> Netlist:
    return Netlist(
        inputs=("value",),
        gates=(Gate("not-gate.man", ("value",), "result"),),
        outputs=("result",),
    )


def test_netlist_normalizes_mutable_sequence_fields_to_tuples() -> None:
    gate_inputs = ["a", "b"]
    netlist_inputs = ["a", "b"]
    gates = [Gate("and-gate.man", gate_inputs, "product")]
    outputs = ["product"]

    netlist = Netlist(netlist_inputs, gates, outputs)

    assert isinstance(netlist.gates[0].inputs, tuple)
    assert isinstance(netlist.inputs, tuple)
    assert isinstance(netlist.gates, tuple)
    assert isinstance(netlist.outputs, tuple)

    gate_inputs.clear()
    netlist_inputs.clear()
    gates.clear()
    outputs.clear()

    assert netlist.gates[0].inputs == ("a", "b")
    assert netlist.inputs == ("a", "b")
    assert netlist.gates == (Gate("and-gate.man", ("a", "b"), "product"),)
    assert netlist.outputs == ("product",)
    assert netlist.producers == {"a": None, "b": None, "product": netlist.gates[0]}


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


def test_compose_is_deterministic() -> None:
    assert composer.compose(_not_netlist()) == composer.compose(_not_netlist())


def test_compose_crops_every_whitespace_border() -> None:
    source = composer.compose(_not_netlist())

    rows = source.splitlines()
    assert rows[0].strip()
    assert rows[-1].strip()
    assert any(row[0].strip() for row in rows)
    assert any(row[-1].strip() for row in rows)


def test_compose_keeps_a_final_newline() -> None:
    assert composer.compose(_not_netlist()).endswith("\n")


def test_compose_does_not_write_output(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)

    source = composer.compose(_not_netlist())

    assert source
    assert list(tmp_path.iterdir()) == []


def test_write_creates_only_parents_and_writes_exact_composed_source(tmp_path: Path) -> None:
    netlist = _not_netlist()
    expected = composer.compose(netlist)
    requested = tmp_path / "nested" / "composed.man"

    result = composer.write(netlist, requested)

    assert result == requested
    assert requested.read_text(encoding="utf-8") == expected
    assert list(tmp_path.iterdir()) == [requested.parent]
    assert list(requested.parent.iterdir()) == [requested]


def test_composed_source_has_runtime_visible_io_rooms_and_connected_pipes() -> None:
    analysis = Littleman().analyze(composer.compose(_not_netlist()))

    assert len(analysis.rooms) == 5
    assert len(analysis.pipes) == 4
    assert all(pipe.src is not None and pipe.dst is not None for pipe in analysis.pipes)
    assert analysis.displays == []
