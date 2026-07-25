"""Parser for the small textual Little Man circuit language."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .composer import FanOut, Gate, Netlist
from .primitive_contracts import contract_for

__all__ = ["LanguageError", "parse_file", "parse_program"]

_IDENTIFIER = r"[A-Za-z_][A-Za-z0-9_]*"
_PRIMITIVES = {
    "and": "and-gate.man",
    "or": "or-gate.man",
    "xor": "xor-gate.man",
    "not": "not-gate.man",
    "nand": "nand-gate.man",
    "mux": "mux-gate.man",
    "transistor": "transistor.man",
    "bit_register": "bit-register.man",
}


class LanguageError(ValueError):
    """A source error with a one-based line and column."""

    def __init__(self, message: str, line: int, column: int) -> None:
        self.message = message
        self.line = line
        self.column = column
        super().__init__(f"line {line}, column {column}: {message}")


@dataclass(frozen=True)
class _Statement:
    text: str
    line: int
    column: int


def _statements(source: str) -> tuple[_Statement, ...]:
    statements: list[_Statement] = []
    for line_number, raw_line in enumerate(source.splitlines(), start=1):
        code = raw_line.split("#", 1)[0].rstrip()
        if not code.strip():
            continue
        text = code.lstrip()
        statements.append(
            _Statement(
                text=text,
                line=line_number,
                column=len(code) - len(text) + 1,
            )
        )
    return tuple(statements)


def _parse_names(
    source: str,
    statement: _Statement,
    *,
    description: str = "signal list",
    allow_trailing_comma: bool = False,
) -> tuple[str, ...]:
    trailing_comma = r",?" if allow_trailing_comma else ""
    pattern = rf"\s*({_IDENTIFIER}(?:\s*,\s*{_IDENTIFIER})*)\s*{trailing_comma}\s*"
    match = re.fullmatch(pattern, source)
    if match is None:
        raise LanguageError(f"malformed {description}", statement.line, statement.column)
    return tuple(part.strip() for part in match.group(1).split(","))


def _parse_branches(source: str, statement: _Statement) -> tuple[tuple[str, ...], ...]:
    branch_pattern = (
        rf"\(\s*({_IDENTIFIER}(?:\s*,\s*{_IDENTIFIER})*)\s*,?\s*\)"
    )
    cursor = 0
    branches: list[tuple[str, ...]] = []
    while cursor < len(source):
        branch = re.match(branch_pattern, source[cursor:])
        if branch is None:
            raise LanguageError(
                "malformed fanout branch tuples", statement.line, statement.column
            )
        branches.append(tuple(part.strip() for part in branch.group(1).split(",")))
        cursor += branch.end()
        separator = re.match(r"\s*,\s*", source[cursor:])
        if separator is None:
            if source[cursor:].strip():
                raise LanguageError(
                    "malformed fanout branch tuples", statement.line, statement.column
                )
            break
        cursor += separator.end()
        if not source[cursor:].strip():
            raise LanguageError(
                "malformed fanout branch tuples", statement.line, statement.column
            )
    if not branches:
        raise LanguageError("malformed fanout branch tuples", statement.line, statement.column)
    return tuple(branches)


def _parse_inputs(statement: _Statement) -> tuple[str, ...]:
    match = re.fullmatch(r"inputs\s+(.+)", statement.text)
    if match is None:
        raise LanguageError(
            "inputs statement must appear first", statement.line, statement.column
        )
    return _parse_names(match.group(1), statement, description="inputs statement")


def _parse_outputs(statement: _Statement) -> tuple[str, ...]:
    match = re.fullmatch(r"outputs\s+(.+)", statement.text)
    if match is None:
        raise LanguageError("malformed outputs statement", statement.line, statement.column)
    return _parse_names(match.group(1), statement, description="outputs statement")


def parse_program(source: str) -> Netlist:
    """Parse one textual circuit into the shared Python netlist IR."""

    statements = _statements(source)
    if not statements:
        raise LanguageError("inputs statement must appear first", 1, 1)

    inputs_statement = statements[0]
    inputs = _parse_inputs(inputs_statement)
    duplicate_input = next(
        (name for index, name in enumerate(inputs) if name in inputs[:index]),
        None,
    )
    if duplicate_input is not None:
        raise LanguageError(
            f"duplicate input signal {duplicate_input!r}",
            inputs_statement.line,
            inputs_statement.column,
        )

    fanout: FanOut | None = None
    fanout_statement: _Statement | None = None
    gates: list[Gate] = []
    definitions = set(inputs)
    branch_by_signal: dict[str, tuple[str, ...]] = {}
    consumed_branches: set[tuple[str, ...]] = set()
    outputs: tuple[str, ...] | None = None
    outputs_statement: _Statement | None = None

    for index, statement in enumerate(statements[1:], start=1):
        left, separator, right = statement.text.partition("=")
        if not separator:
            if re.match(r"outputs(?:\s|$)", statement.text):
                if index != len(statements) - 1:
                    raise LanguageError(
                        "outputs statement must be final", statement.line, statement.column
                    )
                outputs = _parse_outputs(statement)
                outputs_statement = statement
                break

            if re.match(r"inputs(?:\s|$)", statement.text):
                raise LanguageError(
                    "inputs statement must appear first", statement.line, statement.column
                )

            raise LanguageError("expected assignment", statement.line, statement.column)

        if "=" in right:
            raise LanguageError("expected assignment", statement.line, statement.column)
        left = left.strip()
        right_leading = len(right) - len(right.lstrip())
        right = right.strip()
        if left in {"inputs", "outputs"}:
            raise LanguageError(
                f"reserved assignment target {left!r}", statement.line, statement.column
            )

        fanout_call = re.fullmatch(r"fanout\s*\((.*)\)", right)
        if fanout_call is not None:
            if gates:
                raise LanguageError(
                    "fanout must appear before primitive assignments",
                    statement.line,
                    statement.column,
                )
            if fanout is not None:
                raise LanguageError(
                    "V1 supports at most one fanout statement",
                    statement.line,
                    statement.column,
                )
            branches = _parse_branches(left, statement)
            fanout_source = _parse_names(
                fanout_call.group(1), statement, description="fanout source"
            )
            if fanout_source != inputs:
                raise LanguageError(
                    "fanout source must exactly match inputs",
                    statement.line,
                    statement.column,
                )
            for branch_index, branch in enumerate(branches):
                if len(branch) != len(fanout_source):
                    raise LanguageError(
                        f"fanout branch {branch_index} has width {len(branch)}, "
                        f"expected {len(fanout_source)}",
                        statement.line,
                        statement.column,
                    )
                for signal in branch:
                    if signal in definitions:
                        raise LanguageError(
                            f"duplicate definition of signal {signal!r}",
                            statement.line,
                            statement.column,
                        )
                    definitions.add(signal)
                    branch_by_signal[signal] = branch
            fanout = FanOut(source=fanout_source, branches=branches)
            fanout_statement = statement
            continue

        assignment = re.fullmatch(
            rf"({_IDENTIFIER})\s*\((.*)\)",
            right,
        )
        if assignment is None or re.fullmatch(_IDENTIFIER, left) is None:
            raise LanguageError(
                "malformed primitive assignment", statement.line, statement.column
            )
        primitive, argument_source = assignment.groups()
        try:
            artifact = _PRIMITIVES[primitive]
        except KeyError as error:
            column = (
                statement.column
                + statement.text.index("=")
                + 1
                + right_leading
                + assignment.start(1)
            )
            raise LanguageError(
                f"unknown primitive {primitive!r}", statement.line, column
            ) from error

        arguments = (
            ()
            if not argument_source.strip()
            else _parse_names(
                argument_source, statement, description="primitive argument list"
            )
        )
        expected_arity = len(contract_for(artifact).input_order)
        if len(arguments) != expected_arity:
            raise LanguageError(
                f"primitive {primitive!r} expects {expected_arity} inputs, "
                f"got {len(arguments)}",
                statement.line,
                statement.column,
            )
        if left in definitions:
            message = (
                f"duplicate produced signal {left!r}"
                if left not in inputs and left not in branch_by_signal
                else f"duplicate definition of signal {left!r}"
            )
            raise LanguageError(message, statement.line, statement.column)
        for signal in arguments:
            if signal not in definitions:
                raise LanguageError(
                    f"input signal {signal!r} is not defined",
                    statement.line,
                    statement.column,
                )

        referenced_branches = {
            branch_by_signal[signal] for signal in arguments if signal in branch_by_signal
        }
        if referenced_branches:
            branch = next(iter(referenced_branches))
            if len(referenced_branches) != 1 or arguments != branch:
                raise LanguageError(
                    f"primitive must consume complete fanout branch {branch!r}",
                    statement.line,
                    statement.column,
                )
            if branch in consumed_branches:
                raise LanguageError(
                    f"fanout branch {branch!r} is consumed more than once",
                    statement.line,
                    statement.column,
                )
            consumed_branches.add(branch)
        if fanout is not None and any(signal in fanout.source for signal in arguments):
            raise LanguageError(
                "fanned-out inputs cannot be consumed directly",
                statement.line,
                statement.column,
            )

        gates.append(Gate(artifact, arguments, left))
        definitions.add(left)

    if outputs is None or outputs_statement is None:
        eof_line = source.count("\n") + 1
        raise LanguageError("missing final outputs statement", eof_line, 1)

    for signal in outputs:
        if signal in branch_by_signal:
            raise LanguageError(
                f"fanout branch signal {signal!r} cannot be selected directly",
                outputs_statement.line,
                outputs_statement.column,
            )
        if signal not in definitions:
            raise LanguageError(
                f"output signal {signal!r} is not defined",
                outputs_statement.line,
                outputs_statement.column,
            )
        if fanout is not None and signal in fanout.source:
            raise LanguageError(
                f"fanned-out input signal {signal!r} cannot be selected directly",
                outputs_statement.line,
                outputs_statement.column,
            )

    if fanout is not None:
        for branch in fanout.branches:
            if branch not in consumed_branches:
                raise LanguageError(
                    f"fanout branch {branch!r} is not consumed by a gate",
                    outputs_statement.line,
                    outputs_statement.column,
                )

    try:
        return Netlist(
            inputs=inputs,
            fanouts=(() if fanout is None else (fanout,)),
            gates=tuple(gates),
            outputs=outputs,
        )
    except ValueError as error:
        statement = fanout_statement or outputs_statement
        raise LanguageError(
            str(error),
            statement.line,
            statement.column,
        ) from error


def parse_file(path: str | Path) -> Netlist:
    """Read and parse one UTF-8 encoded circuit source file."""

    return parse_program(Path(path).read_text(encoding="utf-8"))
