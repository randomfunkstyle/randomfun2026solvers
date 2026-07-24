# LMC block catalog & state model

Where the little-man compiler stands: the sub-blocks we have proven on the
reference engine, what each one's **I/O and state footprint** is, and how each
maps to the **Python-ish surface** language. Every block below is validated
byte-for-byte against `littleman.wasm` (`lm.mjs`) and regression-locked in
`tests/`.

---

## 1. The machine a block runs on

- **Registers per man:** `A`, `B` (the only two you compute with; all ALU ops are
  `A = A op B`) and `BP` (write-only counter/flag: set/decrement/shift/branch, but
  never read back into A/B). All are signed 64-bit, wrapping.
- **No RAM.** State lives only in registers, **pipe cells** (a length-L pipe is an
  L-slot FIFO), or the display. Memory is therefore *built* out of pipes (§4).
- **No jumps.** Control flow is geometric — instructions turn the man; loops and
  branches are 2D routes.
- **Values** are int64. Input is a whitespace-separated int stream entering an `I`
  room; output is ints leaving an `O` room. A program passes the moment its output
  prefix is correct — it need not halt, and cannot detect end-of-input.

---

## 2. Block catalog

| block (file) | role | pipes in | pipes out | live state | ticks | status |
|---|---|---|---|---|---|---|
| **ALU / straight-line** (generated) | evaluate an int expression | 1 (I) | 1 (O) | 2 words (A,B) | ~12 | ✅ generated from Python |
| `forwarder.man` | a wire / `while True: emit(recv())` | 1 | 1 | 1 word (A, transient) | 8/lap | ✅ |
| `chain.man` | pipeline of blocks | 1 | 1 | 1 word/stage | — | ✅ |
| `roundtrip.man` | **1-word memory cell** | — | — | 1 word (persisted) | 10 | ✅ |
| `ring.man` | **N-word store** (here N=3) | — | — | N words (cycling) | 8/rotate | ✅ |
| `ring_io.man` | **memory read/write cycle** | 1 (I) | 1 (O) | N words + 2 regs | — | ✅ |

"live state" = how many int64 the block holds at once. The ring's N is bounded by
its loop pipe capacity (drawn to fit the problem's max array size).

---

## 3. Block-by-block

### ALU / straight-line (generated)
```
+-+  +------------+  +-+
|I|>>|@rW1+*W2W/sH|>>|O|      # this is `emit(n*(n+1)//2)` for `triangle`
+-+  +------------+  +-+
```
- **In:** one int per `recv()` from `I`. **Out:** one int per `emit()` to `O`.
- **State:** just `A`,`B`. No memory. Lowering keeps one variable pinned in `B`,
  accumulates in `A`, loads constants into `A`, and lets each op pull the var from
  `B` (a `W`-trick when the var dies).
- **Python-ish:**
  ```python
  n = recv()
  emit(n*(n+1)//2)
  ```

### forwarder.man — a looping man
```
+-+  +------+  +-+
|I|>>|@>rsv |>>|O|
+-+  |.^..< |  +-+
     +------+
```
- **In/Out:** 1 pipe each. **State:** 1 transient word (`A`).
- The man circles a rectangle doing `r` then `s`, forever. Re-entry must hit a
  direction glyph (`>`), never `@` (a nop that leaves heading unchanged).
- **Python-ish:** `while True: emit(recv())` — or, as a component, a channel wire.

### chain.man — connected blocks
```
+-+  +----+  +----+  +-+
|I|>>|@rsH|>>|@rsH|>>|O|
+-+  +----+  +----+  +-+
```
- A value flows `I -> M1 -> M2 -> O`. Each `Mi` has exactly one in and one out, so
  there is no pipe-selection ambiguity. This is how blocks **compose**: wire one
  block's out-pipe to the next block's in-pipe.
- **Python-ish:** function/stage composition — `emit(g(f(recv())))` split across men.

### roundtrip.man — a 1-word memory cell
```
+----+
|@rsH|        # BUF: bounce whatever arrives straight back
+----+
 ^v           # two vertical pipes: up = CPU->BUF, down = BUF->CPU
 ^v
+--------+
|@`42`srH|    # CPU: send 42 up, receive it back into A
+--------+
```
- **In/Out:** none (self-contained demo). **State:** 1 persisted word.
- A value the CPU sends out and gets back. Kept cycling and not consumed, it *is*
  one word of memory. Halts in 10 ticks with `A=42`.
- **Python-ish:** `x = 42` (a single stored variable).

### ring.man — an N-word store
```
+-----+
|@>rsv|        # BUF forwarder loop
|.^..<|
+-----+
  ^ v
  ^ v
+---------------------+
|@`10`s`20`s`30`s>rsv |   # CPU: seed 3 values, then loop { read head; re-send }
|                ^..< |
+---------------------+
```
- **State:** N words cycling (N=3 here). `A` reads `10, 20, 30, 10, 20, 30, …` on
  an 8-tick period. Sequential access O(1) (tap each lap); random access O(N)
  (rotate to index, then use value in hand). `q` gives the live count.
- **Python-ish:** `a = [0]*N` — an array. `for x in a: ...` is the natural tap.

### ring_io.man — the memory read/write cycle
```
         +----+
         |@rsH|                       # BUF
         +----+
          ^ v
          ^ v
+-+  +------------+  +-+
|I|>>|@r  s r   sH|>>|O|              # CPU wired to FOUR pipes at once
+-+  +------------+  +-+
```
- **In/Out:** `I` (left), `O` (right). **Memory:** ring on top (up + down pipes).
  **State:** N ring words + 2 regs.
- The CPU reads from `I`, pushes up (store), reads back down (load), emits to `O`.
  Which pipe an `r`/`s` hits is chosen by **nearest-Manhattan-distance**, so laying
  `I` left, ring top, `O` right and positioning each op near its target routes
  correctly. Echoes `42 -> 42`, `7 -> 7`, `-5 -> -5`.
- **Python-ish:** the skeleton of every array program —
  ```python
  a[i] = recv()
  emit(a[j])
  ```

---

## 4. Python-ish → block mapping

| Python-ish | lowers to |
|---|---|
| `x = recv()` / `emit(e)` | ALU block: `r` / eval-into-A + `s` |
| `a + b`, `a*b`, `a//b`, `a % b`, `a<<b`, `a&b`, `-a` | native `+ - * / % { & N` on A/B |
| `if a < b:` / 3-way | `X` (turn by sign of `A-B`) |
| `for i in range(n):` / `while` | `BP` counter (`b`,`m`,`d`) + a 2D loop route |
| `x = c` (one variable) | `roundtrip` cell (1 word) |
| `a = [0]*N` | `ring` (N-word store) |
| `a[i]` (read) | rotate ring i times, tap head |
| `a[i] = v` (write) | rotate ring i times, replace head |
| `recv()` + `emit()` + array together | `ring_io` 4-pipe CPU |
| function composition / stages | `chain` of man-blocks over pipes |

---

## 5. State footprint per target problem

How much live memory each problem needs — i.e. the ring size to draw:

| problem | live state | needs |
|---|---|---|
| `triangle` | 0 (regs only) | ALU block only ✅ |
| `history_lesson` | 0 | constant output (footprint-scored) |
| `sudoku_auditor` | 27 words (row/col/box masks) | ring(27) + bitops + `/` + interactive loop |
| `reverse_list` | ≤16 words | ring(16) + rotate |
| `sort` | ≤16 words | ring(16) + compare + swap |
| `brackets` | stack depth ≤32 | ring(32) as a stack |
| `grade_book` | ≤64 words (N×K) | ring(64) + per-op dispatch |
| `packet_reassembly` | ≤48 words | ring(48) + gap tracking |
| `subset_sum` | ≤20 words + best | ring(20) + nested loops + bitops |
| `memory` | 100 words | ring(100) |
| `matrix_multiply` | ≤512 words | ring(512) or nested rings |
| `plotter` | display | LM-75 block (not yet built) |

---

## 6. Status

- **Generated end-to-end today:** straight-line arithmetic (`triangle`, echo,
  polynomials) — Python source → grid → correct on the reference.
- **Proven as hand-built, oracle-validated blocks:** forwarder, chain, 1-word
  cell, N-word ring, 4-pipe read/write cycle. Every primitive an array program
  needs exists.
- **Not yet generated:** array programs (`reverse`, `sort`, …). The missing piece
  is the **layout emitter** that weaves a single man's trail across the 4-pipe
  field (visiting I / ring / O out of left-to-right order) plus a rotate-to-index
  routine. No new *primitive* is needed — this is codegen/routing.
- **Not built:** the LM-75 display block (for `plotter`) and CSP multi-man
  compute (systolic `matrix_multiply`).
