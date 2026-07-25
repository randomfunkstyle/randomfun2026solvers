# Little Man local runtime

`lm.mjs` is the reference command-line runner for Little Man `.man` programs.
It loads `littleman.wasm` through `wasm_exec.js`; keep these three files
together.

```sh
node littleman/lm.mjs run   <file.man> [--input "1 2 3"] [--json] [--max-ticks N]
node littleman/lm.mjs tick  <file.man> [N] [--input "1 2 3"] [--json]
node littleman/lm.mjs judge <file.man> [--input "…"] [--expected "…"] [--json]
node littleman/lm.mjs analyze <file.man> [--json]
```

For example:

```sh
node littleman/lm.mjs judge ../tasks/solutions/triangle.man \
  --input "4" --expected "10" --json
```

`judge` is the right command for checking a task example: it reports whether
the expected output settled. `run` is useful for inspecting the program's raw
output.

The Python wrapper and scorer live in the repository-root `littleman_tools`
package:

```sh
uv run python -m littleman_tools.runner run ../tasks/solutions/triangle.man --input "4"
uv run python -m littleman_tools.scoring ../tasks/solutions/triangle.man triangle
```

Read [`SPEC.md`](SPEC.md) for the language and [`GRADING.md`](GRADING.md) for
the scoring model. Task descriptions and public I/O examples are in
[`../tasks/problems/`](../tasks/problems/).
