import re

import pytest

from littleman_tools.composer import FanOut, Gate, Netlist
from littleman_tools.language import LanguageError, parse_file, parse_program

HALF_ADDER = """\
inputs a, b
(xor_a, xor_b), (and_a, and_b) = fanout(a, b)
sum = xor(xor_a, xor_b)
carry = and(and_a, and_b)
outputs sum, carry
"""


def test_parse_program_builds_explicit_fanout_half_adder() -> None:
    netlist = parse_program(HALF_ADDER)

    assert netlist == Netlist(
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


@pytest.mark.parametrize(
    ("source", "message"),
    [
        (
            "inputs a\nx = unknown(a)\noutputs x\n",
            "line 2, column 5: unknown primitive 'unknown'",
        ),
        (
            "inputs a, b\n(x, y) = fanout(a, b)\noutputs x\n",
            "line 3, column 1: fanout branch",
        ),
        (
            "inputs a\nx = not(a)\n(y,), (z,) = fanout(a)\noutputs x\n",
            "line 3, column 1: fanout must appear before primitive assignments",
        ),
    ],
)
def test_parse_program_reports_source_locations(source: str, message: str) -> None:
    with pytest.raises(LanguageError, match=re.escape(message)):
        parse_program(source)


def test_parse_program_ignores_blank_lines_and_comments() -> None:
    netlist = parse_program(
        """\
# inputs arrive as one ordered frame
inputs a, b  # declaration

sum = xor(a, b)  # primitive
outputs sum
"""
    )

    assert netlist == Netlist(
        inputs=("a", "b"),
        gates=(Gate("xor-gate.man", ("a", "b"), "sum"),),
        outputs=("sum",),
    )


@pytest.mark.parametrize(
    ("spelling", "artifact", "arguments"),
    [
        ("and", "and-gate.man", "a, b"),
        ("or", "or-gate.man", "a, b"),
        ("xor", "xor-gate.man", "a, b"),
        ("not", "not-gate.man", "a"),
        ("nand", "nand-gate.man", "a, b"),
        ("mux", "mux-gate.man", "a, b, c"),
        ("transistor", "transistor.man", "a, b"),
        ("bit_register", "bit-register.man", "a"),
    ],
)
def test_parse_program_maps_supported_primitives(
    spelling: str, artifact: str, arguments: str
) -> None:
    netlist = parse_program(
        f"inputs a, b, c\nresult = {spelling}({arguments})\noutputs result\n"
    )

    assert netlist.gates == (Gate(artifact, tuple(arguments.split(", ")), "result"),)


@pytest.mark.parametrize(
    ("source", "message"),
    [
        (
            "inputs a, a\noutputs a\n",
            "line 1, column 1: duplicate input signal 'a'",
        ),
        (
            "inputs a\nx = not(a)\nx = not(a)\noutputs x\n",
            "line 3, column 1: duplicate produced signal 'x'",
        ),
        (
            "inputs a, b\n(x, y), z = fanout(a, b)\noutputs x\n",
            "line 2, column 1: malformed fanout branch tuples",
        ),
        (
            "inputs a\nx = xor(a)\noutputs x\n",
            "line 2, column 1: primitive 'xor' expects 2 inputs, got 1",
        ),
        (
            "inputs a\nx = not(later)\noutputs x\n",
            "line 2, column 1: input signal 'later' is not defined",
        ),
        (
            "inputs a\nx = not(a)\n",
            "line 3, column 1: missing final outputs statement",
        ),
        (
            "inputs a\noutputs a\nx = not(a)\n",
            "line 2, column 1: outputs statement must be final",
        ),
        (
            "inputs a, b, c\n(x, y), (u, v) = fanout(a, b)\noutputs x\n",
            "line 2, column 1: fanout source must exactly match inputs",
        ),
    ],
)
def test_parse_program_rejects_invalid_programs(source: str, message: str) -> None:
    with pytest.raises(LanguageError, match=re.escape(message)):
        parse_program(source)


def test_parse_file_reads_utf8_source(tmp_path) -> None:
    path = tmp_path / "half-adder.lmc"
    path.write_text(HALF_ADDER, encoding="utf-8")

    assert parse_file(path) == parse_program(HALF_ADDER)


def test_language_api_is_exported_from_package() -> None:
    from littleman_tools import LanguageError as ExportedLanguageError
    from littleman_tools import parse_file as exported_parse_file
    from littleman_tools import parse_program as exported_parse_program

    assert ExportedLanguageError is LanguageError
    assert exported_parse_file is parse_file
    assert exported_parse_program is parse_program
