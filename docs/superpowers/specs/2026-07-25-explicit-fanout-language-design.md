# Explicit frame fanout and circuit source language

## Goal

Allow authors to state that an ordered scalar frame should be copied as a
unit, so the composer emits one `S` fanout room and connects each branch
directly to its consumers. Add a small textual circuit language that parses to
the same Python IR and compiles through the existing backend.

The first target is the parallel half-adder: `(a, b)` is explicitly fanned out
to `xor(a, b)` and `and(a, b)` without scalar demultiplexing, per-signal
fanout, or per-gate repacking.

## Python IR

Add an immutable `FanOut` value:

```python
FanOut(
    source=("a", "b"),
    branches=(("xor_a", "xor_b"), ("and_a", "and_b")),
)
```

`Netlist` gains an optional ordered `fanouts` tuple, defaulting to `()`.
Existing `Netlist(inputs, gates, outputs)` callers retain their current
behavior.

Validation rules:

- every source signal is a declared netlist input;
- every branch has exactly the source width and preserves source position;
- every branch signal is a unique new producer name;
- no branch name collides with an input, gate output, or another branch name;
- every branch is consumed by exactly one gate as that gate's complete ordered
  input tuple; partial branch use and direct output selection are rejected.

## Frame-aware lowering

Lowering keeps a registry from a complete ordered branch tuple to its frame
port. A `FanOut` starts from its declared-input source frame, emits one `S`
room with one outgoing branch per declared branch, and registers each branch
frame. V1 intentionally limits explicit fanout to input frames; fanout of
intermediate gate results needs a future ordered-operation IR.

When a gate's complete ordered input tuple matches a registered branch frame,
the composer connects that frame directly to the primitive input pipe. It does
not create a two-field packer and does not create scalar fanout rooms for the
signals in that complete branch. Other uses retain the existing lowering,
which keeps backward compatibility and handles arbitrary mixed consumers.

This changes no checked-in primitive artifact or primitive semantic contract.
Placement, stubs, routing, cropping, and write behavior remain unchanged.

## Textual language

The `.lmc` language is a concise frontend over the Python IR:

```text
inputs a, b

(xor_a, xor_b), (and_a, and_b) = fanout(a, b)

sum   = xor(xor_a, xor_b)
carry = and(and_a, and_b)

outputs sum, carry
```

`fanout` statements must appear after `inputs` and before the first primitive
assignment. Every fanout branch must appear, in full and in order, as the
inputs of exactly one later primitive assignment.

Supported statements are:

- `inputs` followed by a comma-separated list of signal names;
- a tuple-of-tuples assignment from `fanout(...)`;
- one output assignment from a primitive call;
- `outputs` followed by a comma-separated list of signal names.

Primitive spellings are `and`, `or`, `xor`, `not`, `nand`, `mux`,
`transistor`, and `bit_register`; each maps to its promoted checked-in
artifact. The parser rejects unknown primitive names, duplicate definitions,
wrong arity, malformed tuples, and use-before-definition errors with a source
line and column.

## CLI

Expose:

```sh
littleman-compile INPUT.lmc -o OUTPUT.man
```

The command reads one source file, parses it, calls the existing compose/write
pipeline, and writes only the explicitly requested output path. It has no
solver dispatch, optimisation service, submission client, or network behavior.

## Tests

Fast tests cover IR validation, frame-aware room reduction, parser success and
source locations for errors, CLI argument validation, and deterministic output.
Slow tests run the generated explicit-fanout half-adder through the reference
runtime and assert its ordered `sum, carry` frame. Existing scalar-netlist
regressions remain unchanged.
