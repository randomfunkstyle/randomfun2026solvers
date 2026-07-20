# randomfun2026solvers

External batch solver entrypoint repo for `randomfun2026claude`.

The infrastructure worker clones this repo, checks out the requested ref, and
runs one entrypoint in an isolated per-run worktree. Solver code does not talk to
Temporal or the app database. It reads one input JSON file and writes one output
JSON file.

## Command contract

The worker invokes the configured entrypoint from the checked-out solver repo:

```sh
./solve --solver <solver> --input <input.json> --output <output.json>
```

`./solve` is the default `SOLVER_ENTRYPOINT`. The `--solver` value is an opaque
name chosen by the caller, so one entrypoint can dispatch to multiple solver
implementations.

## Input envelope

The input file is JSON:

```json
{
  "solver": "shell-smoke",
  "run_id": 123,
  "solver_commit": {
    "ref": "main",
    "hash": "abc123"
  },
  "task": {
    "contest_key": "demo_sudoku",
    "external_id": "sudoku-000",
    "payload_b64": "...",
    "content_type": "application/json"
  }
}
```

`task.payload_b64` is opaque task bytes encoded as base64. Decode it in the
solver and interpret it according to `task.contest_key` and `task.content_type`.

## Output envelope

The solver must create the output file as JSON:

```json
{
  "solution_b64": "...",
  "meta": {
    "optional": "metadata"
  },
  "logs": "optional short log text"
}
```

`solution_b64` is required and must decode to the exact solution bytes the worker
should submit. `meta` and `logs` are optional.

The worker treats these as run failures:

- nonzero exit code
- timeout
- missing output file
- invalid output JSON
- missing or invalid `solution_b64`

## Solvers

- `shell-smoke`: writes a minimal `{"smoke": true}` solution for entrypoint checks.
- `sudoku`: solves `demo_sudoku` task payloads and writes `{"grid": "<81 digits>"}`.

## Local smoke

Create a tiny input file and run the default smoke solver:

```sh
cat > /tmp/lq-input.json <<'JSON'
{
  "solver": "shell-smoke",
  "run_id": 1,
  "solver_commit": { "ref": "main", "hash": "local" },
  "task": {
    "contest_key": "demo_sudoku",
    "external_id": "sudoku-000",
    "payload_b64": "eyJzaXplIjo5LCJnaXZlbnMiOiIwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwIn0=",
    "content_type": "application/json"
  }
}
JSON

./solve --solver shell-smoke --input /tmp/lq-input.json --output /tmp/lq-output.json
cat /tmp/lq-output.json
```
