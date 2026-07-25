# Explicit Frame Fanout and Circuit Source Language Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** Add explicit ordered-frame fanout to the Python netlist IR, lower it through one S room without scalar adapters, and expose the IR through an .lmc language and littleman-compile CLI.

**Architecture:** FanOut extends immutable Netlist with named branches of a declared-input frame. Lowering registers each branch as a frame port, so a gate consuming its complete ordered branch connects directly to the primitive. A line-oriented parser creates the same IR; the CLI only parses and writes.

**Tech Stack:** Python 3.12 dataclasses; stdlib argparse and re; existing Little Man runtime wrapper; pytest and Ruff.

## Global Constraints

- Keep every checked-in primitive .man artifact and primitive contract unchanged.
- V1 accepts at most one explicit FanOut, whose source must exactly equal the complete ordered Netlist.inputs tuple; proper subsets and intermediate gate-result fanout are out of scope.
- Inputs participating in explicit fanout cannot also be consumed directly by a gate or selected directly as outputs; scalar fallback is out of scope.
- Each V1 branch is consumed exactly once as one later gate complete ordered input tuple. Reject partial branch use and direct output selection.
- Existing scalar-only Netlist(inputs, gates, outputs) behavior remains unchanged.
- A complete branch lowers through one existing S fanout room, with no scalar fanout or per-gate packer room for that branch.
- .lmc supports inputs, one tuple-of-tuples fanout assignment, primitive assignments, and outputs.
- littleman-compile INPUT.lmc -o OUTPUT.man writes only the requested output path. Do not add a solver, submission client, network behavior, or archive.
- Mark only reference-runtime output tests slow; default uv run pytest remains under 30 seconds.

---

## File Structure

- Modify littleman_tools/composer.py: FanOut, validation, and direct frame lowering.
- Create littleman_tools/language.py: parser and source-location errors.
- Create littleman_tools/compiler_cli.py: compiler command entrypoint.
- Modify littleman_tools/__init__.py, pyproject.toml, and README.md.
- Create tests/test_fanout.py and tests/test_language.py.

### Task 1: Add explicit frame fanout to the immutable netlist IR

**Files:**

- Modify: littleman_tools/composer.py
- Modify: littleman_tools/__init__.py
- Create: tests/test_fanout.py

**Interfaces:**

~~~python
@dataclass(frozen=True)
class FanOut:
    source: tuple[str, ...]
    branches: tuple[tuple[str, ...], ...]

@dataclass(frozen=True)
class Netlist:
    inputs: tuple[str, ...]
    gates: tuple[Gate, ...]
    outputs: tuple[str, ...]
    fanouts: tuple[FanOut, ...] = ()
~~~

FanOut and all Netlist sequence fields normalize caller sequences to tuples. Branch signals appear in immutable producers, consumers, and levels mappings; no-fanout mapping behavior does not change.

- [ ] **Step 1: Write failing model tests**

~~~python
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
~~~

Add parametrized invalid cases for a non-input source, a branch width mismatch, duplicate branch names, collision with an input or gate output, partial branch use, duplicate branch consumption, and output selection.

- [ ] **Step 2: Verify RED**

Run: uv run pytest -n 0 tests/test_fanout.py::test_netlist_records_explicit_input_frame_fanout -v

Expected: collection FAIL because FanOut is not exported.

- [ ] **Step 3: Implement the model and validation**

~~~python
@dataclass(frozen=True)
class FanOut:
    source: tuple[str, ...]
    branches: tuple[tuple[str, ...], ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "source", tuple(self.source))
        object.__setattr__(self, "branches", tuple(tuple(branch) for branch in self.branches))
~~~

Normalize Netlist.fanouts before graph construction. Build:

~~~python
branch_owner = {
    name: (fanout, branch)
    for fanout in fanouts
    for branch in fanout.branches
    for name in branch
}
~~~

Require at most one FanOut, a non-empty source tuple exactly equal to Netlist.inputs in order, at least two branches, matching branch width, and globally unique branch names. Add branch aliases as FanOut producers at level zero. If a gate references any alias, require gate.inputs == branch, record the one consumer, and reject unconsumed branches after gate validation. Reject any direct gate consumer or selected output of an original input when explicit fanout is present. Export FanOut from composer and package init.

- [ ] **Step 4: Verify GREEN and compatibility**

Run: uv run pytest -n 0 tests/test_fanout.py tests/test_composer.py -v

Expected: PASS, including all existing scalar-netlist tests.

- [ ] **Step 5: Commit**

~~~sh
git add littleman_tools/composer.py littleman_tools/__init__.py tests/test_fanout.py
git commit -m "Add explicit frame fanout IR"
~~~

### Task 2: Lower complete branch frames directly through S

**Files:**

- Modify: littleman_tools/composer.py
- Modify: tests/test_fanout.py

**Interfaces:**

- Consumes validated Netlist.fanouts.
- Keeps private _lower_layout_rooms(netlist, primitive_root) return type unchanged.
- Produces one existing FANOUT room per explicit FanOut and a direct frame connection from copy[index] to the consuming primitive input.

- [ ] **Step 1: Write failing room-reduction tests**

~~~python
def test_explicit_input_frame_fanout_avoids_scalar_adapters() -> None:
    source = compose(_fanout_half_adder())
    analysis = Littleman().analyze(source)

    assert len(analysis.rooms) == 6  # I, S, XOR, AND, joiner, O
    assert source.count("@") == 4    # S, XOR, AND, output joiner
~~~

Also test private lowering specs contain one FANOUT, no INPUT_DEMULTIPLEXER, and no PACKER.

- [ ] **Step 2: Verify RED**

Run: uv run pytest -n 0 tests/test_fanout.py::test_explicit_input_frame_fanout_avoids_scalar_adapters -v

Expected: FAIL because current lowering creates input demux and two-field packers.

- [ ] **Step 3: Register input and branch frame ports**

At _lower_layout_rooms start, add:

~~~python
explicit_branch_sources: dict[tuple[str, ...], _PortRef] = {}
~~~

Connect input.io.frame directly to _make_scalar_fanout(len(fanout.branches)).input, omit the input demultiplexer, and register each branch tuple to the corresponding copy[index] port. Validation rejects proper input subsets and scalar fallback, so lowering has no demultiplexer/repacker fallback path for explicit fanout.

- [ ] **Step 4: Bypass scalar packing for registered branches**

~~~python
frame_source = explicit_branch_sources.get(gate.inputs)
if frame_source is not None:
    connections.append(
        _Connection(
            f"{gate.output}:input-frame",
            frame_source,
            _PortRef(gate_name, "input"),
        )
    )
elif len(gate.inputs) == 1:
    signal_targets[gate.inputs[0]].append(_PortRef(gate_name, "input"))
else:
    # retain existing packer/output-joiner lowering
~~~

Do not pass registered branch aliases into _connect_signal_with_fanout. Preserve original scalar lowering when fanouts is empty.

Add fast validation regressions proving that a proper-subset source and a direct scalar consumer/output of an explicitly fanned-out input are rejected before placement.

- [ ] **Step 5: Add the slow runtime regression**

~~~python
@pytest.mark.slow
def test_explicit_fanout_half_adder_preserves_ordered_sum_and_carry() -> None:
    snapshot = Littleman().judge(
        compose(_fanout_half_adder()),
        input=[0, 0, 0, 1, 1, 0, 1, 1],
        expected=[0, 0, 1, 0, 1, 0, 0, 1],
        max_ticks=1_000,
    )
    assert snapshot.output_settled is True
    assert snapshot.fatal is None
~~~

- [ ] **Step 6: Verify fast, slow, and scalar compatibility**

Run: uv run pytest -n 0 tests/test_fanout.py tests/test_composed_circuits.py -v

Expected: fast tests PASS and slow tests deselected.

Run: uv run pytest -n 0 -m slow tests/test_fanout.py tests/test_composed_circuits.py -v

Expected: PASS; scalar and explicit fanout half-adders emit [0, 0, 1, 0, 1, 0, 0, 1].

- [ ] **Step 7: Commit**

~~~sh
git add littleman_tools/composer.py tests/test_fanout.py
git commit -m "Lower explicit input frame fanout"
~~~

### Task 3: Parse .lmc programs into the shared IR

**Files:**

- Create: littleman_tools/language.py
- Modify: littleman_tools/__init__.py
- Create: tests/test_language.py

**Interfaces:**

~~~python
class LanguageError(ValueError):
    def __init__(self, message: str, line: int, column: int) -> None: ...

def parse_program(source: str) -> Netlist: ...
def parse_file(path: str | Path) -> Netlist: ...
~~~

LanguageError string form is exactly line N, column M: message.

- [ ] **Step 1: Write the failing parse-success test**

~~~python
HALF_ADDER = """\
inputs a, b
(xor_a, xor_b), (and_a, and_b) = fanout(a, b)
sum = xor(xor_a, xor_b)
carry = and(and_a, and_b)
outputs sum, carry
"""

def test_parse_program_builds_explicit_fanout_half_adder() -> None:
    netlist = parse_program(HALF_ADDER)
    assert netlist.fanouts == (
        FanOut(("a", "b"), (("xor_a", "xor_b"), ("and_a", "and_b"))),
    )
    assert netlist.gates[0] == Gate("xor-gate.man", ("xor_a", "xor_b"), "sum")
~~~

- [ ] **Step 2: Verify RED**

Run: uv run pytest -n 0 tests/test_language.py::test_parse_program_builds_explicit_fanout_half_adder -v

Expected: collection FAIL because littleman_tools.language does not exist.

- [ ] **Step 3: Implement grammar and primitive mapping**

Use exactly:

~~~python
_PRIMITIVES = {
    "and": "and-gate.man", "or": "or-gate.man", "xor": "xor-gate.man",
    "not": "not-gate.man", "nand": "nand-gate.man", "mux": "mux-gate.man",
    "transistor": "transistor.man", "bit_register": "bit-register.man",
}
~~~

Strip blank lines and # comments. Accept identifiers matching [A-Za-z_][A-Za-z0-9_]*. Require inputs first, at most one fanout statement before the first primitive assignment, and one final outputs statement. Parse tuple-of-tuples fanout assignment, construct FanOut and Gate values, then construct Netlist.

- [ ] **Step 4: Add failing source-location tests**

~~~python
@pytest.mark.parametrize(
    ("source", "message"),
    [
        ("inputs a\nx = unknown(a)\noutputs x\n",
         "line 2, column 5: unknown primitive 'unknown'"),
        ("inputs a, b\n(x, y) = fanout(a, b)\noutputs x\n",
         "line 3, column 1: fanout branch"),
        ("inputs a\nx = not(a)\n(y,), (z,) = fanout(a)\noutputs x\n",
         "line 3, column 1: fanout must appear before primitive assignments"),
    ],
)
def test_parse_program_reports_source_locations(source: str, message: str) -> None:
    with pytest.raises(LanguageError, match=re.escape(message)):
        parse_program(source)
~~~

- [ ] **Step 5: Implement errors and file parsing**

~~~python
def parse_file(path: str | Path) -> Netlist:
    return parse_program(Path(path).read_text(encoding="utf-8"))
~~~

Wrap model ValueError at the declaring statement first identifier. Reject duplicate definitions, malformed tuple nesting, wrong primitive arity, use-before-definition, fanout after gate assignments, missing outputs, and non-final outputs with line/column information.

- [ ] **Step 6: Verify parser integration**

Run: uv run pytest -n 0 tests/test_language.py tests/test_fanout.py -v

Expected: PASS; parsed and direct Python half-adders have equal IR.

- [ ] **Step 7: Commit**

~~~sh
git add littleman_tools/language.py littleman_tools/__init__.py tests/test_language.py
git commit -m "Parse explicit fanout circuit language"
~~~

### Task 4: Add littleman-compile and author documentation

**Files:**

- Create: littleman_tools/compiler_cli.py
- Modify: pyproject.toml
- Modify: README.md
- Modify: tests/test_language.py

**Interfaces:**

~~~python
def main(argv: Sequence[str] | None = None) -> int: ...
~~~

Register littleman-compile = "littleman_tools.compiler_cli:main". It accepts one source path and required -o/--output path.

- [ ] **Step 1: Write the failing CLI success test**

~~~python
def test_compile_cli_writes_requested_man_file(tmp_path: Path) -> None:
    source = tmp_path / "half_adder.lmc"
    output = tmp_path / "nested" / "half_adder.man"
    source.write_text(HALF_ADDER, encoding="utf-8")

    assert compiler_main([str(source), "-o", str(output)]) == 0
    assert output.read_text(encoding="utf-8") == compose(parse_program(HALF_ADDER))
~~~

- [ ] **Step 2: Verify RED**

Run: uv run pytest -n 0 tests/test_language.py::test_compile_cli_writes_requested_man_file -v

Expected: collection FAIL because littleman_tools.compiler_cli does not exist.

- [ ] **Step 3: Implement the thin argparse wrapper**

~~~python
def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="littleman-compile")
    parser.add_argument("source", type=Path, metavar="INPUT.lmc")
    parser.add_argument("-o", "--output", type=Path, required=True, metavar="OUTPUT.man")
    args = parser.parse_args(argv)
    try:
        write(parse_file(args.source), args.output)
    except (LanguageError, OSError, ValueError) as exc:
        parser.error(str(exc))
    return 0
~~~

Add the project script below littleman-score. Do not create any solver, optimizer, or network command.

- [ ] **Step 4: Add CLI error tests**

~~~python
def test_compile_cli_requires_an_output_path(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit, match="2"):
        compiler_main(["program.lmc"])
    assert "the following arguments are required: -o/--output" in capsys.readouterr().err
~~~

Add malformed-source test that asserts exit code 2 and line N, column M in stderr. Confirm only write, not parse_program or compose, creates output directories.

- [ ] **Step 5: Document the language**

Add this exact README section after existing run/judge examples:

~~~markdown
## Compile a circuit

~~~text
inputs a, b
(xor_a, xor_b), (and_a, and_b) = fanout(a, b)
sum = xor(xor_a, xor_b)
carry = and(and_a, and_b)
outputs sum, carry
~~~

~~~sh
littleman-compile half_adder.lmc -o half_adder.man
~~~
~~~

State that fanout copies the complete ordered input frame through S and the CLI writes only the requested -o path.

- [ ] **Step 6: Verify CLI and format**

Run: uv run pytest -n 0 tests/test_language.py -v

Expected: PASS.

Run: uv run ruff check littleman_tools/language.py littleman_tools/compiler_cli.py tests/test_language.py

Expected: All checks passed.

Run: uv run ruff format --check littleman_tools/language.py littleman_tools/compiler_cli.py tests/test_language.py

Expected: files already formatted.

- [ ] **Step 7: Commit**

~~~sh
git add littleman_tools/compiler_cli.py pyproject.toml README.md tests/test_language.py
git commit -m "Add circuit language compile CLI"
~~~

### Task 5: Run complete regression verification

**Files:** Verify only files above.

- [ ] **Step 1: Run fast tier**

Run: uv run pytest

Expected: PASS below 30 seconds; runtime cases deselected.

- [ ] **Step 2: Run runtime checks**

Run: uv run pytest -n 0 -m slow tests/test_fanout.py tests/test_composed_circuits.py -v

Expected: PASS. Dependent chain emits [0, 1, 0, 0]; scalar and explicit fanout half-adders emit [0, 0, 1, 0, 1, 0, 0, 1].

- [ ] **Step 3: Run all tests and inspect diff**

Run: uv run pytest -m ""

Expected: PASS.

Run: git diff --check HEAD~3..HEAD && git status --short

Expected: no whitespace errors and only planned changes.

- [ ] **Step 4: Commit only verification-exposed correction**

If verification needs a correction, make the smallest tested change and commit:

~~~sh
git add <corrected-files>
git commit -m "Fix explicit fanout compiler regression"
~~~

If verification causes no edit, do not create an empty commit.

## Plan Self-Review

- Spec coverage: Task 1 implements immutable IR and validation; Task 2 direct S lowering and runtime behavior; Task 3 .lmc grammar and source errors; Task 4 CLI and docs; Task 5 fast, slow, and all-test verification.
- Scope coverage: no task changes a primitive, adds solver/submission behavior, or expands V1 beyond declared-input fanout.
- Type consistency: FanOut, Netlist.fanouts, parse_program, parse_file, and compiler_cli.main are defined before later tasks consume them.
- Placeholder scan: no task contains deferred or unspecified implementation steps.
