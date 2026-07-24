# Memory & lists — the ring block

How this machine stores a list, and how every list-like operation lowers onto it.
The code is `lmc/memlib.py` (fragments), `lmc/router.py` (draws the ring), and the
worked programs in `demos.py`. `BLOCKS.md` catalogs the validated primitives;
this doc is the *model* and the **list-operation → glyph** mapping.

## Why memory is a ring

The target machine has **no RAM and no addressable store** — only three registers
(`A`, `B`, `BP`) and **pipe cells**. A pipe of length L is an L-slot FIFO: a value
sent in at the source shifts one cell per tick toward the far end, where it's
received. That FIFO is the only thing that holds more than three numbers, so a
**list is built out of pipes**: a CPU wired to a **BUF** forwarder by two pipes.

```
        +-------+
        |@>rsv  |   BUF: a forwarder man, r then s forever — it just
        |.^..<  |   bounces every value straight back around the loop
        +-------+
         ^   v
         ^   v      up  = CPU -> BUF  (enqueue / append at the tail)
         ^   v      down= BUF -> CPU  (dequeue / pop the head)
        +--------------+
        | ... CPU ...  |
        +--------------+
```

Send a value `up` and it circulates `up → BUF → down` forever. A value kept
cycling and never consumed **is** one stored word; N values cycling are an N-word
list. The BUF is a *persistent process* — it never halts, it serves memory. Ring
capacity ≈ `2 · ring_len` (the up + down pipe lengths), sized in `router.render`
to exceed the largest list a program holds (`ring_len=9` → ~18 ≥ n=16 for reverse).

## Two invariants that make it composable

Everything in `memlib` leans on these; break either and the ring corrupts.

1. **Ring `r`/`s` touch only `A`.** They never read or write `B`/`BP`. So a loop
   counter parked in `B` or `BP` survives *any number* of memory accesses. This is
   the whole reason `reverse` needs no scratch store: the remaining count `rem`
   sits in `B` across every rotate.
2. **`r` (dequeue) blocks until a value is present.** Popping an empty slot *waits*
   instead of erroring. So the CPU never races the BUF — a ring self-synchronises,
   and you may access it at any phase.

## The fragment API (`memlib.py`)

Element primitives — each is one or two trail cells, `B`/`BP` preserved:

| fragment | glyphs | meaning |
|---|---|---|
| `read_from(src)` | `r` | `A = recv(src)` — pull from an external in-pipe |
| `emit_to(out)` | `s` | `send(out, A)` — push A to an external out-pipe |
| `enqueue(up)` | `s` | `ring.append(A)` — store A at the tail |
| `dequeue(down)` | `r` | `A = ring.pop_head()` — remove & return the head |
| `rotate_once(down, up)` | `r s` | pop head, re-append it; **A = that value** — "rotate by 1" = "peek head, advance" |
| `length_to_bp(down)` | `q` | `BP = ` live count of the nearest in-pipe (see caveat) |

Counted loops — the caller sets `BP` to the trip count, body decrements it (`m`),
`d` branches while `BP>0` (all zero-trip):

| fragment | effect | list meaning |
|---|---|---|
| `load_run(src, up)` | append `BP` values read from `src` | `a += [recv() for _ in range(n)]` |
| `rotate_run(down, up)` | rotate the ring `BP` times | address `a[i]`: bring element `i` to the head |
| `drain_run(down, out)` | pop `BP` heads to `out` | `for _ in range(k): emit(a.pop_head())` |
| `pop_emit(down, out)` | pop one head to `out` | `emit(a.pop_head())` |

## List-like operations → glyphs

| Python-ish | lowering on the ring | cost |
|---|---|---|
| `a = []` | an empty ring (BUF loop, nothing circulating) | — |
| `a.append(x)` | `x` in `A`, then `enqueue(up)` (`s`) | O(1) |
| `a.pop_head()` / front | `dequeue(down)` (`r`) → `A` | O(1) |
| `len(a)` | `length_to_bp` (`q`) **at a sync point**, or carry the input's `n` | O(1) |
| `for x in a:` | `rotate_once` ×`len(a)`; each step leaves the element in `A` | O(1)/step |
| `a[i]` (read) | `BP=i`; `rotate_run` → head is `a[i]`, value left in `A` | O(i) |
| `a[i] = v` (write) | `v` in `B` (survives), `BP=i`, `rotate_run`, replace head (`r` discard, `W`, `s up`) | O(i) |
| `reversed(a)` → emit | rotate `rem-1`, `pop_emit`, `rem--`, repeat — `memlib.reverse_round` | O(n²) |
| drain `a` → output | `BP=n`; `drain_run(down, out)` | O(n) |

**Addressing detail.** The ring has no absolute index — position is *relative to the
current head*. To make `a[i]` mean the same cell twice you must return the ring to a
**canonical rotation** after each access: rotate `i` forward to reach the cell, do
the op, then rotate the remaining `len−i` to complete a full lap back to canonical.
So a *self-contained* indexed access is O(len), not O(i) — the O(i) figure assumes
you keep walking forward and never restore. Sequential iteration (`for x in a`) is
the cheap path: one `rotate_once` per element, no restore.

## The register wall — why bigger programs need a spill

Reverse fits in `A`/`B`/`BP` because `rem` is *both* the loop counter and the
remaining length — one value doing two jobs. Programs that need **two independent
persistent values** don't fit, because ring ops only free up `A`:

- **`sort`** — a bubble/selection pass carries a running max/min in `B` *and* needs
  the list length `n` to bound both the inner pass and the number of passes. `BP` is
  consumed by the inner loop, `B` holds the carry ⇒ `n` has nowhere to live.
- **`memory`** (100-cell RAM) — servicing `WRITE addr value` must carry **both**
  `value` and the count needed to restore canonical rotation across the
  rotate-to-`addr` loop ⇒ two live values, one free register.

Both want the same missing piece: a **spill word** — a second, tiny ring (a
1-word `roundtrip` cell) on another side of the CPU. The Z3 router already resolves
`r`/`s` to the nearest pipe, so placing the data ring north and a spill ring south
routes each op to the right store by geometry; what's missing is `router.render`
drawing the second BUF, plus an `if`-block in `loopgen` (branch-on-`X`, two arms
merging) for `sort`'s compare-swap and `memory`'s READ/WRITE opcode dispatch.

## Status

- ✅ **Ring built and oracle-locked.** `memlib` fragments + `reverse_list` (all 8
  public multi-round test cases pass on `littleman.wasm`). `reverse` is assembled
  entirely from `memlib`, so `tests/test_memlib.py` validates the fragments by
  construction.
- ⛏ **Next:** the spill cell (second BUF in `router.render`) and the `if`-block —
  the two pieces that unlock `sort` and `memory`. See `BLOCKS.md` §3 (reverse
  design) and §5 (per-problem state footprints).

### Caveats

- **`q` counts one pipe, not the whole ring.** Values are spread across up-pipe +
  BUF + down-pipe as they circulate, so `q` is only the true length at a sync point
  where the list is entirely in that pipe. When you know `n` from the input, carry
  it — don't re-derive with `q`.
- **The router layout is nondeterministic.** `router.solve_attachments` runs an
  unseeded Z3 solve, so the *same trail* renders to different (all valid, all
  oracle-passing) grids run to run. The trail is deterministic; only pipe placement
  varies. Seed the solver if a byte-stable `.man` is ever required.
