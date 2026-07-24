# Validated primitive blocks

Each `.man` here is checked byte-for-byte against the reference interpreter
(`littleman.wasm` via `lm.mjs`) by `tests/test_blocks.py`. They are the building
blocks for memory, composed from small single-purpose rooms wired by pipes
rather than one large room — the substrate is CSP-native (men = processes,
pipes = blocking channels), so composition is easier to lay out and verify than
cramming loops into a single man's trail.

## `forwarder.man` — a looping man
One man circles a rectangle doing `r` (receive) then `s` (send), forever. Echoes
its whole input stream. Key lesson: re-entry into the loop must land on a
direction glyph (`>`), **not** on `@` — `@` is a nop and does not reset heading,
so the man keeps his current direction and walks into a wall.

## `chain.man` — value through connected blocks
`I -> M1 -> M2 -> O`, each `Mi` a one-shot forwarder `@rsH`. Proves a value flows
through a chain of independent man-blocks over pipes, no pipe-selection ambiguity
(each room has exactly one in and one out).

## `roundtrip.man` — a 1-word circulating memory
CPU (bottom) and BUF (top) joined by **two vertical pipes** (up = CPU->BUF,
down = BUF->CPU). CPU loads `42`, sends it up, BUF bounces it straight back down,
CPU receives it (A=42) and halts (10 ticks). This is the atom of a circulating
store: a value the CPU can send out and get back. A value kept cycling and not
consumed is persistent state — i.e. one word of memory.

## `ring.man` — an N-word circulating store
CPU seeds three literals (10, 20, 30) up into the loop, then runs a tight
`r`/`s` loop: receive the head value, re-send it back up. The three values
persist and **cycle past the CPU tap** — A reads 10, 20, 30, 10, 20, 30, … on a
steady 8-tick period. This is addressable memory: to read index i, rotate i
times and use the value in hand; sequential access is O(1), random access O(N).
BUF is the forwarder loop; CPU + BUF share paired vertical pipes. FIFO order is
preserved (the reference guarantees pipe order), so the ring never scrambles.

# Memory plan (next)

- **Parametrise the ring**: generate seed + loop for any N; size the loop pipes
  so capacity >= N. `q` gives the live count for length checks.
- **Addressing / multi-pipe rooms**: a real CPU touches I, O, and memory at once,
  so `s`/`r` must pick the right pipe by nearest-Manhattan-distance. Lay I on one
  side, O on another, memory on a third; place each `r`/`s` nearest its target.
- **Integration**: expose `list`/`[i]` in the Python frontend, lowering array
  access to ring rotate + load/store, and use a memory slot for expression
  spills so the frontend stops needing the A/B-only register gymnastics.
