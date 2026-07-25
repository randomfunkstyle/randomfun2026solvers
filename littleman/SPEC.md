# Littleman language spec (ICFP Contest 2026)

Consolidated from the official language reference (`icfpcontest2026.com/language-reference`,
mechanically extracted into `reference/language-reference.txt`) and **verified
glyph-by-glyph against the bundled reference interpreter** (`littleman.wasm` via
`lm.mjs`). Where the two disagree, the interpreter wins and it is called out below.

Companion docs: [`GRADING.md`](GRADING.md) (scoring, rounds, limits, submission
API) · [`../tasks/problems/`](../tasks/problems/) (all 20 problem specs + public
tests) · [`reference/`](reference/) (raw official text).

## The machine

A program is a grid of ASCII characters walked by one or more **little men**.
Time advances in discrete **ticks**.

Each little man carries three integers: **A** (main hand), **B** (off hand),
**BP** (backpack). All start at 0. Every value is a signed 64-bit integer;
arithmetic wraps silently on overflow. A man cannot *read* his backpack — he can
only branch on it.

Little men live in **rooms**: rectangles drawn with `+` at the corners, `-` for
horizontal walls, `|` for vertical walls. A man spawns at every `@` inside a
room and **always begins facing east**. At most one `@` per room. A man may
never leave his room — stepping on a wall ends the whole program.

### Tick order (matters constantly)

Within one tick, in this order:

1. **Pipes shift** — every value moves one cell toward its destination if the next cell is free.
2. **I/O** — a value at the end of the output pipe is emitted, then the next input value enters the input pipe if able.
3. **Execution** — every little man executes the instruction *under* him; displays consume and process their pipe input.
4. **Movement** — every non-blocked little man advances one cell along his heading.

Because pipes shift *before* execution: a value sent this tick starts moving
next tick, but a value can be moved and read on the same tick.

**Observing state:** in a snapshot at tick *t* the man is standing on a cell
whose glyph has **not yet fired**. The effect of the glyph under him shows up in
the snapshot for tick *t+1*. (Verified with `lm.mjs tick`.)

## Instruction set

Stepping on any character not listed here is an error that ends the whole
program (`bad-op`).

### Constants

| Glyph | Effect |
|---|---|
| `0`–`9` | A = the digit's value |
| `` `123` `` | Numeric literal: digits between two backticks (spaces allowed and ignored) load into A when the man walks onto the **closing** backtick. `` `123` `` is 123 walked left-to-right, **321 right-to-left**; vertical literals work the same. Non-digit, non-space between a matched pair is a load error. |

### Hands

| Glyph | Effect |
|---|---|
| `M` | B = A (A unchanged) |
| `W` | Swap A and B |

### Arithmetic

| Glyph | Effect |
|---|---|
| `+` | A = A + B |
| `-` | A = A − B |
| `*` | A = A × B |
| `%` | A = A mod B, taking **B's sign**; 0 if B = 0 |
| `/` | A = ⌊A / B⌋, **remainder goes to B**. Floored to match `%`, so (A/B)·B + rem = A always. If B = 0: A = 0 and B keeps the dividend. |
| `N` | A = −A |

`/` is **division, not a fork.** (The split instruction is `Y`, below.)

### Bitwise

Two's-complement across all 64 bits; negative operands are nothing special.

| Glyph | Effect |
|---|---|
| `&` | A = A AND B |
| `\|` | A = A OR B |
| `~` | A = A XOR B |
| `{` | A = A << B; 0 if B is outside 0–63 |
| `}` | A = A >> B, arithmetic (sign-filling); 0 if B < 0, sign-fill if B > 63 |

### Direction and branching

| Glyph | Effect |
|---|---|
| `>` `<` `^` `v` / `V` | Head east / west / north / south (`v` and `V` both work) |
| `X` | Turn by **sign(A)**: clockwise if A > 0, counter-clockwise if A < 0, straight if A = 0. A unchanged. |

### Control flow

| Glyph | Effect |
|---|---|
| `.` and space | Do nothing (nop) |
| `H` | Halt this little man |

### Backpack

| Glyph | Effect |
|---|---|
| `b` | BP = A (A unchanged) |
| `m` | BP −= 1 (no clamp; may go negative) |
| `d` | Turn **clockwise** if BP > 0, else go straight |
| `a` | Turn **counter-clockwise** if BP > 0, else go straight |
| `q` | BP = number of values in the nearest incoming pipe |
| `]` | BP >>= 1 (arithmetic shift right, sign-preserving) |
| `x` | Turn clockwise if BP's **low bit** is 1, else counter-clockwise. Unlike `d`/`a` it **always** turns, and it reads the raw bit — a negative backpack is not treated as zero. |

`d`/`a` are the counted-loop primitive (`b` to load the count, `m` to
decrement, `d`/`a` to peel off); `]`/`x` are the binary-decomposition primitive.

### Pipes (send / receive)

Sends block while the target pipe's source cell is occupied. Receives block when
the target pipe(s) have no value in their destination cell. A blocked man does
not move and retries next tick. Running a pipe instruction in a room with no
pipe on the side it needs ends the whole program (`no-pipe`).

| Glyph | Effect |
|---|---|
| `s` | Send A into the **nearest outgoing** pipe. Blocks if full. |
| `S` | Send A into **every** outgoing pipe at once. Blocks unless all have a free source cell — never writes to only some. |
| `r` | Receive into A from the **nearest incoming** pipe. Blocks if nothing ready. |
| `R` | Receive into A from **any** incoming pipe with a value ready. Blocks if nothing ready. |
| `U` | Like `R`, but on success the man **turns away from the side of the room he read from**. |

### `Y` — split

The contest's supplemental [Split reference](https://icfpcontest2026.com/split)
publishes the precise semantics. The glyph is **`Y`**; `C` is not an instruction.

- The parent disappears. Two children with identical A, B, and BP are born one
  cell to its right and left, relative to its entry heading, facing away from
  `Y`.
- The children are present immediately after the split tick, but execute their
  birth cells and move starting on the following tick.
- The right child keeps the parent's creation-order slot. The left child is the
  newest runner and acts after all previously existing runners.
- Splitting is unconditional. A wall birth is a fatal `wall` error.
- A child born on another live man kills both without a fatal error. Ordinary
  same-cell arrivals, head-on swaps, and two children born on the same cell also
  kill the participants without a fatal error.
- At most 65,536 little men may be live.

Entering `Y` heading east therefore yields the order-preserving child one cell
south heading south, and the newest child one cell north heading north. `Y` is
the only way to put more than one active man in one room, which can share room
walls and duplicate register state without extra pipes.

## Pipes

A pipe carries values **one way** between two rooms. Minimum length 2 cells.
Body glyphs `-` (horizontal runs) and `|` (vertical runs); arrowheads `>` `<`
`^` `v` point **with the flow**. Each cell holds at most one value; every value
shifts one cell toward the destination each tick if the next cell is free — so a
pipe is a FIFO whose capacity equals its length. Sends put a value into the
**source** end (segment touching the sending room); receives take from the
**destination** end.

A pipe parses when all of these hold:

- It starts with an arrowhead whose **backward** cell (opposite the arrow) is on the source room's border; the arrow points away from the room.
- Body glyphs match direction (`-` horizontal, `|` vertical). A wrong body glyph is a load error, not a bend.
- Every bend is an arrowhead pointing in the **new** direction. Straight-through arrowheads are legal but redundant.
- It ends at the first arrowhead whose **forward** cell is on a room border (any room other than the source). The terminal arrowhead may itself be the final bend.

Common load errors: body glyph running into a wall (`>----|` — end with an
arrowhead instead); arrowhead pointing back along the flow (`>--<`); a one-cell
pipe (even a length-2 pipe needs arrowheads on both ends: `>>`); a pipe looping
back to its own room; a pipe originating at a display.

### Which pipe do I talk to?

`s` and `S` act over **outgoing** pipes in the current room; `r`, `R`, `U`, `q`
act over **incoming** pipes.

"Nearest" = Manhattan distance (|Δx| + |Δy|) from the instruction to the pipe
segment attached to the current room (source segment for outgoing, destination
segment for incoming). Ties break by **reading order** (top to bottom, left to
right). Nearest means nearest — *not* nearest-that-can-proceed, so `s`/`r` can
block behind a busy pipe while another sits idle.

`R`/`U` take one value from any incoming pipe that has one ready (ties by
reading order) and block only when no pipe has a value. `S` writes to all
outgoing pipes and blocks if any one of them can't be written.

## Input and output

- **Input room**: a 3×3 room (counting walls) whose single interior cell is `I`, with exactly one pipe flowing **out** of it.
- **Output room**: the same with `O` and exactly one pipe flowing **in**.

A pipe in the wrong direction, a second pipe, or a second I/O room is a load
error. A pipeless I/O room is legal.

Input is a whitespace-separated sequence of integers: each tick, if the input
pipe's source cell is free, the next value is placed into it. Output: a value
reaching the end of the output pipe is consumed and appended to program output.

**Output flush:** if values are still in flight in the output pipe when the last
man halts, pipes and I/O rooms keep ticking until the output pipe drains (unless
the step cap hits).

**Withheld input:** on some problems the judge releases input in stages,
withholding later values until earlier output is produced. Withheld input looks
exactly like input still in flight — the pipe just runs dry.

## The LM-75 display

A special room drawn with `+` at the corners, `:` for vertical walls, `=` for
horizontal walls. Maximum interior 64×64 (so 66×66 with borders).

Controlled entirely by pipes; the **side** a pipe connects to sets its function:

| Side | Function |
|---|---|
| Top | **ADDR** |
| Left | **DATA** |
| Bottom | **SWAP** |

The display can read from all three pipes in the same tick, processing **ADDR,
then DATA, then SWAP**. Attaching two pipes to one side, attaching to the right
side, or attaching at a corner is a load error. Pipes may not originate at a
display (displays have no output ports).

State: a **current buffer** (shown), a **next buffer** (being composed), and a
**cursor**. Both buffers start filled with colour 0 (black); the cursor starts
at (0, 0).

- **ADDR** ← `row * width + column` sets the cursor to (col, row). Negative or out-of-bounds is an error.
- **DATA** ← colour `0`–`15` writes the pixel at the cursor into the *next* buffer, then advances the cursor (next column, else next row, else back to the upper-left). Any other value is an error.
- **SWAP** ← `0` copies next → current, then **clears** the next buffer and resets the cursor; `1` copies next → current and **preserves** both. Any other value is an error.

Display-judged problems: exactly one display at the stated resolution, and
emitting any program output is an error. Judging is a streaming compare — every
frame committed by a SWAP must equal the next expected frame in order.

## Judging and halting

You pass a test the moment you emit the correct output in the correct order —
**you do not need to halt**. You fail the moment you emit incorrect output, or
if the run ends before the correct output is emitted.

A run ends in exactly one of three ways: every little man has stopped, an error,
or the step cap.

A little man **stops** when he hits `H`, or when he **touches another little
man** (both stop) — that ends those men only; the program keeps running while
any man remains. Anything else that stops a man is an **error**, and errors end
the whole program on the spot:

| Error | Cause |
|---|---|
| `wall` | A man ran into a wall |
| `bad-op` | A man stepped on a character that is not an instruction |
| `no-pipe` | A pipe instruction (`s`/`S`/`r`/`R`/`U`/`q`) ran in a room with no pipe on the side it needs |

## Fine print

### Numeric literals

Backticks pair on rows and columns **independently**. Within a row they pair
left to right (1st with 2nd, 3rd with 4th, …); within a column, top to bottom. A
backtick that pairs on neither axis is a load error.

A backtick cannot opt out of an axis: one meant as a horizontal delimiter still
pairs vertically if its column holds other backticks. Literals stacked across
rows with aligned backticks can therefore form vertical pairs you did not
intend, and a non-digit between such a pair is a load error.

The value must fit in 64 bits read in **both** directions or the program is
rejected at load. A corner backtick can open a horizontal and a vertical literal
at once, so literals may overlap and cross, sharing digits. A digit walked in a
direction where it belongs to no literal is an ordinary single-digit load.
Walked along an axis it does not delimit, a backtick is a nop — as is an empty
literal (`` `` `` or spaces only).

## Full valid-glyph set

From the interpreter's `validOps`:

```
0123456789 ` . M W N + - * / % & | ~ { } < > ^ v V X x Y d a b m q ] s S r R U H
```

Structural glyphs (`structuralGlyphs`): `+ - | < > ^ v = :` — walls, pipe bodies
and arrowheads, and the display's `=`/`:` walls. `I` and `O` are room labels,
not instructions.

## Verifying against the interpreter

The bundled wasm is the same engine the online editor runs, so it is the source
of truth — probe it rather than guessing:

```sh
./lm.mjs run  <file.man> [--input "1 2 3"] [--json] [--max-ticks N]
./lm.mjs tick <file.man> [n] [--input "1 2 3"] [--json]
```

Beyond `load`/`step`/`stepN`, the wasm exposes useful static analysis:
`validOps()` (glyph set), `structuralGlyphs()`, `analyze(rows)` (rooms, pipes,
displays), `flow(rows)` (per-cell reachable headings + terminals), and
`route(rows, x, y)` (which pipe a send/receive cell targets).
