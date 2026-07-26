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

## Interactive mode

Some tasks need a live conversation with the contest server rather than a single
batch answer. The worker then invokes:

```sh
./solve --solver <solver> --mode interactive --input <input.json>
```

`--mode` defaults to `batch`, so the batch contract above is unchanged. In
interactive mode there is **no `--output` file**; the solver instead speaks a
synchronous newline-delimited JSON protocol over stdio (stdout = protocol frames
only, stderr = logs). The worker proxies every message to the contest server, so
the solver never opens its own connection.

Requests the solver writes (one JSON object per line), any order, repeatable:

```json
{"t": "step",  "action_b64": "<opaque action, base64>"}
{"t": "guess", "answer_b64": "<opaque candidate answer, base64>"}
{"t": "done",  "solution_b64": "<optional explicit solution>"}
```

Responses the worker writes back:

```json
{"t": "observation", "raw_b64": "<opaque>", "query_count": N, "penalty": M, "score": X, "done": false}
{"t": "verdict",     "correct": true, "score": X, "raw": {}}
{"t": "error",       "msg": "...", "fatal": false}
```

Loop: write one request, read exactly one response, repeat; finish by sending
`done` (optionally with an explicit `solution_b64`) or by exiting. The worker
decodes `action_b64`/`answer_b64` as opaque bytes and interprets nothing — decode
and interpret them in the solver per `task.contest_key`. Full contract:
`randomfun2026claude/contracts.md §3.2`.

## Debugging a `.man`

Generated grids carry no comments. `littleman/DEBUGGING.md` covers the overlay/trace/
profile workflow and the `--man/--html/--json` convention every generator follows.

## Submitting to the contest

You need **one thing**: the team API key, in either
`ICFP_TOKEN` or an untracked `.icfp-token` at the repo root (already gitignored —
never commit it).

```sh
export ICFP_TOKEN=icfp_...                       # or: echo 'icfp_...' > .icfp-token

uv run python -m randomfun2026solvers.submit send brackets --note "what this is"
uv run python -m randomfun2026solvers.submit send brackets --dry-run   # check only
uv run python -m randomfun2026solvers.submit list                      # what is archived
uv run python -m randomfun2026solvers.submit get <submission-id>       # re-read a verdict
```

`send` guards two ways before spending a submission — only 5 may be pending at
once, so a wasted one costs real time:

- **refuses a failing grid** (`--force` overrides), and
- **refuses a grid already submitted**, matched by hash against the archive, and
  prints the verdict it got last time (`--resend` overrides).

It defaults to `tasks/solutions/<slug>_cpu.man`; use `--file` for anything else.

Every graded submission is archived so nothing is ever lost:

```
solutions/<slug>/<server-verified-score>_<slug>.man
solutions/<slug>/<server-verified-score>_<slug>.descr   # free-form note + provenance
```

The score is in the filename and zero-padded, so a listing sorts best-first and a
worse run can never overwrite a better one. A submission that does not pass every
case gets no score and is archived as `unscored_<slug>`.

Two gotchas worth knowing:

- **Cloudflare 403 `error code: 1010` is not an auth failure.** It is a
  browser-signature ban on the default `Python-urllib` User-Agent; any ordinary UA
  gets through, which is why the `curl` examples work.
- **Local scores understate.** The server runs private cases too — `brackets` is
  9 public but 26 graded, and its server `avgTicks` came out ~2x the local figure.

## Solver Layout

The root `./solve` script dispatches by solver name to language-specific
entrypoints:

- `solvers/bash/solve`: Bash solvers.
- `solvers/python/randomfun2026solvers`: Python solvers.

## Solvers

- `shell-smoke`: Bash smoke solver that writes a minimal `{"smoke": true}` solution.
- `sudoku`: Python solver for `demo_sudoku` task payloads that writes `{"grid": "<81 digits>"}`.
- `probe`: Python interactive solver for `demo_probe`; probes each digit index then guesses the reconstructed number (run with `--mode interactive`).

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
