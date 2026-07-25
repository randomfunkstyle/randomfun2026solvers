from littleman_tools.primitive_contracts import (
    Direction,
    Side,
    contracts_by_artifact,
)


def test_primitive_contracts_describe_stream_shape_and_placement_rules() -> None:
    contracts = contracts_by_artifact()

    assert set(contracts) == {
        "and-gate.man",
        "and-gate-ten-tick.man",
        "bit-register.man",
        "mux-gate.man",
        "mux-gate-select-first-user-u-compact.man",
        "nand-gate.man",
        "nand-gate-narrow.man",
        "not-gate.man",
        "or-gate.man",
        "transistor.man",
        "transistor-compact.man",
        "transistor-compact-narrow.man",
        "xor-gate.man",
    }

    assert contracts["and-gate.man"].input_order == ("a", "b")
    assert contracts["and-gate.man"].operation == "and"
    assert contracts["xor-gate.man"].input_order == ("a", "b")
    assert contracts["xor-gate.man"].operation == "xor"

    for name in ("and-gate.man", "xor-gate.man"):
        contract = contracts[name]
        assert contract.inputs == 1
        assert contract.outputs == 1
        assert contract.input_port.allowed_sides == frozenset({Side.NORTH})
        assert contract.control_flow is Direction.AWAY_FROM_INPUT
        assert contract.output_port.fanout_safe is True

    assert contracts["or-gate.man"].operation == "or"
    assert contracts["not-gate.man"].input_order == ("a",)
    assert contracts["nand-gate.man"].operation == "nand"
    assert contracts["bit-register.man"].operation == "previous_bit_with_zero_reset"

    for name in (
        "transistor.man",
        "transistor-compact.man",
        "transistor-compact-narrow.man",
    ):
        contract = contracts[name]
        assert contract.input_order == ("data", "enable")
        assert contract.operation == "controlled_forwarding"
        assert contract.input_port.allowed_sides == frozenset(Side)

    for name, required_side in (
        ("mux-gate.man", Side.WEST),
        ("mux-gate-select-first-user-u-compact.man", Side.NORTH),
    ):
        contract = contracts[name]
        assert contract.input_order == ("select", "i1", "i2")
        assert contract.operation == "select_first_mux"
        assert contract.input_port.allowed_sides == frozenset({required_side})
        assert contract.control_flow is Direction.AWAY_FROM_INPUT

    for contract in contracts.values():
        assert contract.input_port.required is True
        assert contract.output_port.required is True
        assert contract.input_port.multiplicity == 1
        assert contract.output_port.multiplicity == 1
        assert contract.input_port.corner_allowed is False
        assert contract.output_port.corner_allowed is False
        assert contract.minimum_clearance == 2
