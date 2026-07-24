# littleman — headless `.man` runner

Runs ICFP Contest 2026 "littleman" programs (`.man` ASCII grids) from the command
line using the **exact interpreter the online editor uses** — `littleman.wasm`
(Go 1.25.7, `GOOS=js GOARCH=wasm`), driven through Go's standard `wasm_exec.js`.
No reimplementation: this is the reference engine run 1:1, so output matches the
editor byte-for-byte.

## Requirements
- Node.js 24+ (uses only the stdlib: `fs`, `vm`, `path`, `url`, `WebAssembly`).
- The wasm engine + Go runtime, bundled in this folder (self-contained):
  - `littleman.wasm` — the interpreter (Go→wasm)
  - `wasm_exec.js` — Go's js/wasm glue (from `icfpcontest2026.com/wasm_exec.js`)

## Usage
```
./lm.mjs run  <file.man> [--input "1 2 3"] [--json] [--max-ticks N]
./lm.mjs tick <file.man> [n] [--input "1 2 3"] [--json]
```

### `run` — execute to completion
Prints the program output (space-joined integers) to stdout; a
`# halted after N tick(s) (reason)` line goes to stderr, so stdout stays pure output.
```
$ ./lm.mjs run examples/io.man
123
# halted after 9 tick(s) (done)      ← stderr
$ ./lm.mjs run examples/echo.man --input "42"
42
$ echo 7 | ./lm.mjs run examples/echo.man
7
```
- `--input "…"` supplies whitespace-separated integers to the program's input
  room. If omitted, piped stdin is used. If neither, input is empty.
- `--json` prints the full final snapshot JSON instead of just output.
- `--max-ticks N` safety cap (default 1,000,000) — a non-halting program errors out.
- Exit code is non-zero on a fatal runtime error (e.g. `wall`) or a load error.

### `tick` — step and inspect state (side quest)
Advances `n` ticks (default 1) from the start and shows the next state: the ASCII
map with live runners overlaid as `@`, plus a per-runner summary.
```
$ ./lm.mjs tick examples/walk.man 2
+------+
|..@.H |
+------+

tick 2  halted:false
runner0  A=0 B=0 BP=0 dir=> pos=(3,1)
output:
```
- `A`/`B` = the runner's two hands, `BP` = backpack, `dir` = heading.
- `--json` emits the raw wasm snapshot (all entities: runners, pipes, rooms,
  displays, plus `output`, `step`, `halted`, `fatal`).

## Snapshot JSON shape (`--json`)
```jsonc
{
  "step": 9, "halted": true, "reason": "done", "fatal": null,
  "output": [123],
  "inputReleased": 0, "inputRead": 0, "outputSettled": true,
  "entities": {
    "runners":  [{"id":0,"pos":[x,y],"dir":[dx,dy],"halted":false,"a":0,"b":0,"backpack":0}],
    "pipes":    [{"id","path":[[x,y]...],"values":[...]}],
    "rooms":    [{"id","min":[x,y],"max":[x,y],"runners":[...]}],
    "displays": [{"id","min","max","w","h","front":[...],"back":[...],"cursor"}]
  }
}
```

## How it works
`lm.mjs` evaluates `wasm_exec.js` to define `globalThis.Go`, instantiates
`littleman.wasm` with `go.importObject`, calls `go.run(instance)` (not awaited —
Go's `main()` registers `globalThis.littlemanWasm` then blocks forever), then polls
for that global. It then drives the same API the editor uses:
`newSession()` → `load(session, rows, input, "", "")` → `step`/`stepN(session, n, false)`.
Every call returns a JSON string; `{type:"error"}` results are thrown.

## Examples
- `examples/walk.man` — a runner walks right into `H` and halts.
- `examples/io.man` — a `` `123` `` literal is sent to the output room.
- `examples/echo.man` — reads one input value and echoes it to output.

## Python wrapper
A typed Python front-end lives at
`solvers/python/randomfun2026solvers/littleman.py`. It shells out to this CLI with
`--json` and parses the snapshot into pydantic v2 models.

```python
from pathlib import Path
from randomfun2026solvers.littleman import Littleman

lm = Littleman()                                   # finds ../littleman/lm.mjs by default
snap = lm.run(Path("littleman/examples/io.man"))   # Path => run a .man file
snap.output                                        # [123]
snap.step, snap.ok, snap.reason                    # 9, True, "done"

snap = lm.run(Path("littleman/examples/echo.man"), input=[42])   # int list or "42" string
snap = lm.run("+---+\n|@H |\n+---+\n")              # str => inline program source

snap = lm.tick(Path("littleman/examples/walk.man"), 2)           # step 2 ticks from start
r = snap.entities.runners[0]
r.pos.x, r.pos.y, r.a, r.b, r.backpack             # Vec2 coords + hands + backpack
```

- Coordinates are `Vec2(x, y)` models (`.as_tuple()` for a plain tuple).
- Program `str` = inline source; `Path`/`os.PathLike` = an existing `.man` file.
- `input` accepts a whitespace `str` or a sequence of ints.
- A **fatal** runtime error is reported on `snap.fatal` (a `Fatal` model); a
  **load/usage** error raises `LittlemanError` (with `.pos`).
- Override the engine via `Littleman(script=..., node=...)` or env `LM_SCRIPT`/`LM_NODE`.

Mirrored Python CLI (full parity with `lm.mjs`):
```
uv run python -m randomfun2026solvers.littleman run  <file.man> [--input STR] [--json] [--max-ticks N]
uv run python -m randomfun2026solvers.littleman tick <file.man> [n] [--input STR] [--json]
```
