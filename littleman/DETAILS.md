# Littleman contest — setup & orientation

Cold-start handoff for the ICFP Contest 2026 **littleman** track. Read this once
to understand the repo, the language, the engine, the scoring rules, and the
develop→test→submit loop — enough to pick up any task here (write a solver,
build tooling, analyse a problem, submit) without re-deriving the setup.

Deep references (this doc summarises, they are the source of truth):

- [`SPEC.md`](SPEC.md) — the language, glyph-by-glyph, verified against the interpreter.
- [`GRADING.md`](GRADING.md) — scoring, points/ranking, rounds, limits, submission API.
- [`../tasks/problems/README.md`](../tasks/problems/README.md) — the 16-problem table.
- [`reference/`](reference/) — raw verbatim extracts from the contest site (`grading.txt`, `language-reference.txt`, `textbook.txt`, `api.txt`, `contest-rules.txt`, `interpreter-probe.txt`).

---

## 1. What this is

The contest asks you to write programs for **littleman**, an esolang where a
program is a **2-D grid of ASCII** walked by one or more **"little men"**. You
solve small algorithmic problems (sort, matmul, sudoku-check, draw lines, …).
Programs are scored on **size** and **speed** — smaller and faster is better —
so the game is writing tight, looping grids, not clever one-liners.

We have the **exact reference interpreter** locally (`littleman.wasm`, the same
engine the online editor runs) wrapped in a Node CLI and a Python front-end, plus
all 20 released problems fetched from the API. Everything needed to develop and
score offline is in this repo; the live site is only needed to submit.

## 2. Repo layout

| Path | Role |
|---|---|
| `littleman/littleman.wasm` | Reference interpreter (Go 1.25.7, `GOOS=js GOARCH=wasm`). Source of truth. |
| `littleman/wasm_exec.js` | Go's standard wasm runtime shim (boots the wasm). |
| `littleman/lm.mjs` | Node CLI driving the wasm: `run` / `tick`. **See §3.** |
| `littleman/SPEC.md` | Language reference (cleaned + interpreter-verified). |
| `littleman/GRADING.md` | Scoring / rules / submission API. |
| `littleman/details.md` | This file. |
| `littleman/examples/*.man` | Tiny worked programs: `walk`, `io`, `echo`, `atoi`. |
| `littleman/reference/*.txt` | Verbatim site extracts (fallback when docs disagree). |
| `tasks/problems/_index.json` | Listing of all 20 problems (carries `id` for submission). |
| `tasks/problems/<slug>.json` | Full problem: description, `io`, `scoring`, `publicTestData`, … |
| `tasks/problems/README.md` | Problem table (set / scoring / case counts / summaries). |
| `solvers/python/randomfun2026solvers/littleman.py` | Typed Python wrapper over `lm.mjs` (pydantic models). |
| `solvers/python/randomfun2026solvers/scoring.py` | Offline scorer: `area2 × avg ticks` for a `.man` + problem (§6). |
| `solvers/python/randomfun2026solvers/manparse.py` | Grid → structural `Program` blocks (inverse of `layout.py`); `to_grid`/`to_graph`. |
| `solvers/python/randomfun2026solvers/optimize.py` | Score optimizer: verified passes shrink `area2 × ticks` (§6). |

A `.man` file **is** the program — the grid, newlines and all. No syntax around it.

## 3. Running programs

### Node CLI (`lm.mjs`)

```sh
node littleman/lm.mjs run  <file.man> [--input "1 2 3"] [--json] [--max-ticks N]
node littleman/lm.mjs tick <file.man> [n]  [--input "1 2 3"] [--json]
```

- `run` — execute to completion; prints space-joined output ints to **stdout**, a `# halted after N tick(s) (reason)` line to **stderr**. Default `--max-ticks` = 1,000,000 (the CLI's own safety cap, **not** the grading cap).
- `tick` — advance `n` ticks (default 1); prints the ASCII map with live men overlaid as `@`, plus per-runner A/B/BP/dir/pos and current output.
- Input precedence: `--input` > piped stdin > empty. Input is normalised to keep only digits/whitespace/`-`/`/`.

Verified working:

```sh
$ node littleman/lm.mjs run littleman/examples/echo.man --input "42"
42
# halted after 4 tick(s) (done)
```

### Python wrapper (`littleman.py`)

Thin, typed front-end — no reimplementation, shells out to `lm.mjs --json` and
parses into pydantic models.

```python
from randomfun2026solvers.littleman import Littleman
lm = Littleman()                       # finds lm.mjs relative to the package; override via LM_SCRIPT
snap = lm.run("+-+  +-----+  +-+\n|I|>>|@rsH |>>|O|\n+-+  +-----+  +-+", input=[42])
snap.output      # [42]
snap.ok          # True  (halted, no fatal)
snap.step        # tick count
snap.fatal       # Fatal | None
```

`program` accepts a `.man` path or inline source string; `input` accepts a
whitespace string or a sequence of ints. `run(...)` and `tick(...)` mirror the CLI.

### Snapshot JSON fields (what `--json` / a `Snapshot` exposes)

`step` (tick count), `halted`, `reason`, `fatal` (`{reason,pos,cell,value}`),
`output` (list of ints), `outputSettled`, `frameCommitted`, `inputReleased`,
`inputRead`, `cursor`, `history`, and `entities` = `{runners, pipes, rooms,
displays}`. Runners carry `id, pos:[x,y], dir:[dx,dy], halted, a, b, backpack`.

## 4. The language at a glance

Full detail in [`SPEC.md`](SPEC.md); this is enough to read/write `.man`.

**Machine.** Each little man carries three signed-64-bit registers (wrap on
overflow), all starting 0: **A** (main hand), **B** (off hand), **BP**
(backpack). A man **cannot read BP**, only branch on it. Men live in **rooms**
(`+` corners, `-`/`|` walls), spawn at `@` (≤1 per room) **facing east**, and
die the whole program if they hit a wall.

**Tick order** (every tick, in order): 1) **pipes shift**, 2) **I/O**
(output emits, then next input enters), 3) **execution** (each man runs the
glyph *under* him; displays process pipe input), 4) **movement** (each
non-blocked man steps one cell along his heading). Snapshot subtlety: at tick
*t* the man stands on a glyph that **has not fired yet** — its effect shows at
*t+1*.

**Glyph set** (`validOps`): `` 0-9 ` . M W N + - * / % & | ~ { } < > ^ v V X x Y d a b m q ] s S r R U H ``

| Group | Glyphs |
|---|---|
| Constants | `0`-`9` → A=digit; `` `123` `` numeric literal (loads A on the **closing** backtick; reversed R→L) |
| Hands | `M` B=A · `W` swap A,B |
| Arithmetic | `+ - *`; `%` mod (B's sign); `/` floored div, **remainder→B** (not a fork!); `N` negate |
| Bitwise | `& \| ~`; `{` shl; `}` ashr |
| Direction | `> < ^ v`/`V`; `X` turn by sign(A) |
| Control | `.`/space nop; `H` halt this man |
| Backpack | `b` BP=A · `m` BP-=1 · `d`/`a` turn cw/ccw if BP>0 (counted loop) · `q` BP=count in nearest incoming pipe · `]` BP>>=1 · `x` turn by BP low bit (binary decomp) |
| Pipes | `s` send nearest-out · `S` send all-out · `r` recv nearest-in · `R` recv any-in · `U` like R + turn away · (all block; `no-pipe` fatal if no pipe on the needed side) |
| Split | `Y` replaces the man with right/left children (same A/B/BP), born one cell CW/CCW; they execute next tick — **only way to get >1 man in a room** |

**Pipes** carry values one-way between two rooms, FIFO, capacity = length,
min length 2; body `-`/`|`, arrowheads `> < ^ v` point with the flow.
**Nearest** = Manhattan distance, ties by reading order — and *nearest*, not
nearest-ready, so a send/recv can block behind a busy pipe while another sits idle.

**I/O rooms**: 3×3 with an `I` (one pipe out) or `O` (one pipe in). Input is a
whitespace-separated int sequence fed one value per tick when the pipe's source
is free; output values reaching the pipe end are emitted.

**LM-75 display**: room with `=`/`:` walls, ≤64×64 interior. Pipe **side** =
function: Top=**ADDR** (cursor = row·width+col), Left=**DATA** (write colour
0–15, advance cursor), Bottom=**SWAP** (0 = commit+clear next buffer, 1 =
commit+preserve). Each SWAP **commits a frame**. Display problems must emit
**zero** program output.

**Fatals** (end the whole program): `wall`, `bad-op` (stepped on a non-instruction),
`no-pipe`. Plus **load-time** rejects: malformed numeric literal, malformed pipe,
wrong/duplicate I/O room, malformed display attachment.

**Halting**: a man stops on `H` or on touching another man; the program ends when
all men have stopped, on a fatal, or at the step cap. You **pass the instant you
emit correct output in order — you need not halt.**

## 5. Grading & scoring

Full detail in [`GRADING.md`](GRADING.md). **Lower score is always better.**

| Mode | Formula |
|---|---|
| `footprint-tick` (19 of 20 problems) | **max(width, height)² × avg ticks across all test cases** |
| `footprint` (`history-lesson` only) | **max(width, height)²** — speed irrelevant |

- **width / height** = the bounding box of the **entire** program — all rooms,
  pipes, and whitespace inside the box. (⚠ Exactly how w/h are measured from
  source — raw line count × longest line, vs a minimal non-space box, and how
  trailing whitespace / a trailing newline count — is **not pinned down in these
  docs**. Confirm against the real judge before trusting a local number. See §6.)
- **ticks for a case** = ticks until your **final correct output value** is
  emitted (display problems: until the **final frame matches**). Later ticks
  don't count; the program need not halt.
- Because footprint is **squared**, narrow-and-looping beats wide-and-unrolled;
  `Y` (split) adds parallel men without adding rooms.

**Points** — up to **2 per graded problem**:
`test-case points = passed/total` (max 1) + `ranking points = (eligible teams you
beat or tie)/(other eligible teams)` (max 1). Eligibility needs at least one
**private** case passed (or any case when there are 0 private). Only your **best
submission per problem** counts. Practice problems are ungraded.

**Rounds**: a test case has one or more rounds (input/expected-output pairs) run
against a **single program run, no reset**. Round N+1's input is **withheld until
all of round N's output is received** (a no-output round unlocks immediately).
Editor input uses `/` to separate rounds.

**Limits**: source ≤ **10 MB**; step cap **5,000,000 ticks** by default
(`tickCap: null`), which is what every problem up to Semester 3 uses. Semester 4
sets it explicitly: 50,000,000 for `little-little-man`, 15,000,000 for
`little-little-little-man`, `pathfinder` and `snake`. Hitting the cap ends the
run immediately.

**Submission API** (base `https://icfpcontest2026.com/api/v1`, team bearer token
for submit): `GET /public/problems`, `GET /public/problems/<slug>`,
`POST /submissions` (body `{"problemId":"<id>","program":"<source>"}` — submit
takes the **id**, everything else the **slug**), `GET /submissions/<id>`. Full
pass returns `score = area2 × avgTicks` (`area2` alone for `footprint`). ≤5
queued submissions at once.

## 6. Working in this repo

### The develop → test → submit loop

1. **Write** a `.man` grid (the file *is* the program). Start from an
   `examples/` template or a similar solved problem.
2. **Debug** with `lm.mjs tick <file> <n>` — steps the man cell by cell, drawing
   the map with `@` and printing A/B/BP/dir/pos. This is the primary tool for
   understanding why a program misbehaves; walk it tick by tick until the fault
   shows.
3. **Run** a full case with `lm.mjs run <file> --input "…"` and compare `output`
   to the problem's `publicTestData`. Use `--json` for machine-readable state and
   `--max-ticks` to raise the local cap for long programs.
4. **Score** — footprint from the source grid, ticks from the run (see §5). Lower
   is better; iterate on making the grid smaller/faster. `scoring.py` does this
   offline against a problem's public cases (**assuming all pass**):

   ```sh
   uv run python -m randomfun2026solvers.scoring <file.man> <slug|problem.json> [--json]
   ```
   ```python
   from randomfun2026solvers.scoring import score_program
   r = score_program("solve.man", "memory")   # slug, .json path, or problem dict
   r.area2, r.avg_ticks, r.score              # e.g. 1764, 20386.57, 35961912.0
   ```

   Footprint mirrors `lm.mjs` (drop one trailing newline, split on `\n`; w =
   longest row, h = row count). Per-case ticks = the exact tick the final
   expected output value is emitted, found by exponential-then-binary search on
   `tick n` (output length is monotonic; the program need not halt). Display
   problems (`plotter`/`palette`) have no output to count — those fall back to
   the run settle/halt tick and are flagged `approx` (see §9.3).
5. **Optimize** — `optimize.py` searches for a lower-scoring grid that still
   passes every public case:

   ```sh
   uv run python -m randomfun2026solvers.optimize <file.man> <slug|json> [--out F] [--verbose]
   ```

   It parses the grid into blocks (`manparse.py`) then runs verified passes —
   **trim** (crop blank margins), **relayout** (re-place rooms + re-route pipes
   shortest, via `layout.py`'s A\* router), **relayout-cap** (same, but
   `CapacityRouter` pads every pipe back to ≥ its original length, preserving
   shift-register buffers) — keeping any candidate that lowers the real score.
   Two gates protect correctness: `verify` (engine round-gated judging, exact
   output match) and `bindings_preserved` (the `route` oracle re-checks every
   send/recv still binds to the same pipe). It never returns a grid that fails to
   verify. Footprint is *squared*, so a re-layout that shrinks `area2` 4× while
   ~2× the ticks still wins big (atoi 78×6 → 39×27, −54 %). Programs that are both
   buffer-bound *and* have a multi-pipe room (`memory`) are returned unchanged —
   the gates reject every re-layout; binding-aware attach placement is the next
   lever. Note: `scoring.py` does not round-gate and under-counts multi-round
   ticks — the optimizer's `verify` is the accurate tick source.
6. **Submit** via the API in §5 (`POST /submissions` with the problem **id**).

### Engine access beyond the CLI

The wasm exposes more than `lm.mjs` surfaces. Useful when building tooling:

- **`load(session, rows, input, expected, framesJSON)`** — the loader also
  accepts **`expected`** output and **`frames`** (display), letting the engine do
  round-gating and judging itself. `lm.mjs` currently hardcodes these empty, so
  the CLI ignores built-in judging — pass them through (extend `lm.mjs` or drive
  the wasm directly) if you want engine-side judging + precise tick counts.
- Snapshot judging signals: `outputSettled`, `frameCommitted`,
  `inputReleased`/`inputRead`, `step` (see §3).
- Static analysis: `validOps()`, `structuralGlyphs()`, `analyze(rows)` (rooms,
  pipes, displays), `flow(rows)` (per-cell reachable headings + terminals),
  `route(rows, x, y)` (which pipe a send/recv cell targets).

### Conventions

- Coordinates are `[x, y]`, origin top-left, reading order top→bottom left→right
  (used for pipe tie-breaks).
- `publicTestData` tokens are always **strings** in the JSON, even integers.
- Test-data shapes to handle: round-based, flat, frame-based (see §7).
- Python is the wrapper layer (`littleman.py`); the wasm is never reimplemented —
  it is the source of truth, so probe it rather than guessing semantics.

## 7. Problem set (20 problems)

Condensed from [`../tasks/problems/README.md`](../tasks/problems/README.md). All
have `privateTestCount: 0`. Semesters 1–3 and the practice problems have
`tickCap: null` (default 5M cap); Semester 4 raises it (50M for `little-little-man`,
15M for the other three).

| Set | Slug | Name | Scoring | Public | Display | Summary |
|---|---|---|---|---|---|---|
| Sem 1 | `triangle` | Triangle | footprint-tick | 6 | | *n*-th triangular number. |
| Sem 1 | `memory` | Memory | footprint-tick | 7 | | Simulate 100-cell memory (READ `0 addr` / WRITE `1 addr val`). |
| Sem 1 | `reverse-a-list` | Reverse a List | footprint-tick | 8 | | Print a list reversed. |
| Sem 1 | `sort-numbers` | Sort | footprint-tick | 7 | | Print a length-prefixed list ascending. |
| Sem 2 | `history-lesson` | History Lesson | **footprint** | 1 | | No input; output a fixed answer. |
| Sem 2 | `brackets` | Brackets | footprint-tick | 9 | | Is a bracket string balanced? |
| Sem 2 | `tcp` | Packet Reassembly | footprint-tick | 6 | | Reassemble a packet stream. |
| Sem 2 | `plotter` | Plotter | footprint-tick | 6 | 32×24 | Draw line segments (Bresenham) on a display. |
| Sem 3 | `gradebook` | Grade Book | footprint-tick | 7 | | Roster + GET/SET/AVG/TOP operation batches. |
| Sem 3 | `matmul` | Matrix Multiply | footprint-tick | 7 | | C = A·B, row-major, up to 16×16×16. |
| Sem 3 | `sudoku-validity` | Sudoku Auditor | footprint-tick | 6 | | Stream cells `r c v`; emit valid-so-far 1/0. |
| Sem 3 | `subset-sum` | Subset Sum | footprint-tick | 7 | | Find a subset summing to a target. |
| Sem 4 | `snake` | Snake | footprint-tick | 5 | 16×16 | Simulate Snake (fruit / turn / tick rounds) and draw it. |
| Sem 4 | `pathfinder` | Pathfinder | footprint-tick | 7 | 16×16 | BFS shortest path on a 16×16 maze; one frame per move. |
| Sem 4 | `little-little-little-man` | LLLM | footprint-tick | 10 | 16×16 | Interpret a one-room littleman subset and draw its state. |
| Sem 4 | `little-little-man` | LLM | footprint-tick | 14 | 16×16 | Interpret a multi-room + pipes littleman subset and draw its state. |
| Practice | `atoi` | atoi | footprint-tick | 2 | | Parse ASCII-digit string → integer. |
| Practice | `hello-world` | Hello World | footprint-tick | 1 | | Output 11 ASCII codes of "hello world". |
| Practice | `max-element` | Max Element | footprint-tick | 10 | | Largest number in a list. |
| Practice | `palette` | Palette | footprint-tick | 1 | 8×8 | Show all 16 palette colours. |

**Problem JSON schema** (`<slug>.json`): `id`, `slug`, `name`, `description`
(markdown), `extraNotes`, `io` (`{input, output, constraints[], …}` — a small
recursive grammar DSL: `int`/`ascii`, `{seq:[…]}`, `{repeat:X}`,
`{lengthPrefixed:…}`, `lengthPrefixedAscii`, `{count:{"*":[…]}, of:X}`),
`problemSetName`, `publicTestData`, `status` (`graded`/`practice`), `scoring`,
`tickCap`, `privateTestCount`, and optional `display:{width,height}`. Semester 4
responses add `uberStrict` (a boolean, `false` everywhere so far, undocumented in
the API reference); the sixteen files fetched before it existed do not carry it.

**`publicTestData` shapes** — each case is `{name, …}`:
- **Round-based** (most): `rounds:[{in:[…], out:[…]}]`, tokens as strings.
- **Flat** (`memory`): top-level `in`/`out`, no rounds.
- **Frame-based** (`plotter`, `palette`, and all four Semester 4 problems): `rounds:[{in, out:[], frames:[…]}]` — `out` empty, expected result is the display; each frame is an array of row strings, one hex digit (colour 0–15) per pixel.

`_index.json` is a flat array of `{id, slug, name, problemSetName,
problemSetVisible, orderInSet, status}` — the `id` is the `problemId` for submission.

## 8. Gotchas / verified quirks

- **Snapshot is pre-execution**: the glyph under a man at tick *t* fires at *t+1*.
- **`/` is floored division, remainder → B** — not a fork. The split op is **`Y`**.
- **`Y` split** is documented on the supplemental
  [`/split`](https://icfpcontest2026.com/split) page. Children execute their
  birth cells next tick; right retains order, left acts last. Birth/movement
  collisions kill men without a fatal error, while a wall birth is fatal.
- **`C` is not split.** The published glyph is `Y`.
- **Pipe "nearest" can block** behind a busy pipe even if another is idle — use
  `R`/`U` (any-ready) or lay pipes carefully.
- **ASCII problems** are still plain decimal ints on the wire (`"hi"` = `104 105`).
- **Display problems**: emitting *any* program output is an error; you're judged
  only on committed frames.
- **Withheld input** looks identical to input still in flight — the pipe just
  runs dry; don't assume EOF.
- The CLI's default `--max-ticks` (1M) is smaller than the grading cap (5M) — raise
  it for long programs so you don't false-timeout locally.

## 9. Open questions

Unresolved in these docs — pin down before relying on them:

1. **Exact footprint measurement** — how w/h are read from source (raw line
   count × longest line vs a minimal non-space box, trailing whitespace / newline
   handling). Validate a local `area2` against a real submission's returned
   `area2`.
2. **Round feeding / judging** — the engine can gate rounds and judge if given
   `expected`/`frames` to `load`, but `lm.mjs` doesn't pass them (§6). Any tool
   that judges locally must either wire these through or gate rounds manually.
3. **Display-frame judging** — how committed frames map to the JSON `frames` hex
   grids for `plotter` / `palette`.
4. **Private cases** — all current problems have `privateTestCount: 0`; behaviour
   when private cases exist (eligibility) is documented but untested locally.
