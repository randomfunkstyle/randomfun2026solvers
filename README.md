# Little Man solutions

This repository contains Little Man task definitions, checked-in `.man`
solutions, and the local tools needed to run and score them. It intentionally
contains no solver framework, generator, optimizer, submission client, or
solution archive.

## Layout

- `tasks/problems/` — one JSON file per task. Each file carries the task
  description, I/O schema, public I/O examples, and scoring metadata.
- `tasks/solutions/` — checked-in `.man` solution artifacts. The redesign
  baseline keeps the Triangle solution only.
- `littleman/` — the reference Node/WebAssembly runtime and stable language and
  scoring documentation.
- `littleman_tools/` — Python wrapper and offline scorer for existing `.man`
  files.

## Run and judge a solution

Run a program directly with the reference runtime:

```sh
node littleman/lm.mjs run tasks/solutions/triangle.man --input "4"
```

Judge it against a known I/O example:

```sh
node littleman/lm.mjs judge tasks/solutions/triangle.man \
  --input "4" --expected "10" --json
```

The Python wrapper exposes the same runtime:

```sh
uv run python -m littleman_tools.runner run \
  tasks/solutions/triangle.man --input "4" --json
```

## Compile a circuit

```text
inputs a, b
(xor_a, xor_b), (and_a, and_b) = fanout(a, b)
sum = xor(xor_a, xor_b)
carry = and(and_a, and_b)
outputs sum, carry
```

```sh
littleman-compile half_adder.lmc -o half_adder.man
```

`fanout` copies the complete ordered input frame through `S`. V1 supports at
most 13 direct branches; larger fanout needs a future cascading lowering. The
compiler CLI writes only the path explicitly requested with `-o`.

## Score a solution

Score a `.man` program against the public cases in a task definition:

```sh
uv run python -m littleman_tools.scoring \
  tasks/solutions/triangle.man triangle --json
```

See [`littleman/README.md`](littleman/README.md) for runner details,
[`littleman/SPEC.md`](littleman/SPEC.md) for the language, and
[`littleman/GRADING.md`](littleman/GRADING.md) for scoring rules.
