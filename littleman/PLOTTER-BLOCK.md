# A line-drawing block for `plotter`

`plotter.asm` runs Bresenham on the generated LM-1 CPU: every `err += dy`, every
compare and every `x += sx` pays a fetch → decode-trie → lane → return-path round
trip. That costs ~618k ticks per case inside a 112×106 footprint, and the score is
`max(w,h)² × avgTicks` — so **essentially all of both factors is CPU overhead**.
The display itself is only 34×26.

This is the dedicated replacement: two little men and a value ring, drawn around
the display instead of interpreting an ISA.

## The reformulation

Bresenham's symmetric form is equivalent to a closed form on the *display
address*, which removes `err`, both per-pixel comparisons, and `x`/`y` as separate
quantities. With `M = max(|dx|,|dy|)`, `m = min(|dx|,|dy|)`:

```
U    = major-axis step   (sx, or 32*sy)
V    = minor-axis step   (32*sy, or sx)
den  = 2M    step = 2m
addr = y0*32 + x0
f    = -M                       # f = r - den, so the carry test is a *sign* test
repeat M+1 times:
    ADDR <- addr ; DATA <- 15
    f += step
    if f >= 0:  f -= den ; addr += U + V
    else:                  addr += U
```

`f` being biased by `-den` is the point: `X` branches on the sign of a single hand,
so no second operand and no comparison constant is needed in the inner loop.

Verified by brute force against the problem statement's pseudocode on **all
589,824 legal segments** (`0≤x<32`, `0≤y<24`, both endpoints), including the
degenerate `dx=0`, `dy=0`, `dx=dy` and single-point cases. The tie direction
matters and matches: the exact-half case steps, in both octant families.

## Why two men

A man has `A`, `B` and a write-only `BP`, and **`A` is clobbered by every `r`** —
so only `B` survives a receive, giving each man exactly one stable word. The round
needs six live words (`addr`, `f`, `step`, `den`, `U`, `U+V`), so:

* **worker** — owns `f` in `B`. Reads the four constants off the ring each lap and
  sends the painter one increment per pixel.
* **painter** — owns `addr` in `B`. Emits `ADDR` then `DATA`, adds the increment,
  and writes `0` to `SWAP` when its lap counter runs out. Writing 0 both commits
  *and* clears `next`, so each round starts black and only the segment's own
  pixels are ever written.

The worker never touches the display and the painter never does arithmetic beyond
one add. Because the two run concurrently and are decoupled by the increment pipe,
the per-pixel cost is `max(worker lap, painter lap)`, not their sum.

## The ring, and why FIFO order dictates the code

The four constants circulate worker → relay → worker. A ring is a FIFO, so **a
value read early cannot be re-sent late** — the send order *is* the next lap's read
order. That single constraint shapes everything:

* `step` must be the ring's first slot, because it is the only constant that
  combines directly with `f` (`A = step`, `B = f`, `+`). The other three may be
  permuted freely, so long as `den` precedes `U+V` in the carry lane.
* Both lanes must read **all four** slots even though each ignores two — skipping a
  slot would rotate the ring and desynchronise every later lap.
* Setup groups all four `RIN`s at the top of the round (pushing two copies of `x0`
  and `y0`), so the worker's input `r`s never interleave with ring `r`s. That is a
  *pipe-binding* requirement, not a code-style one: `r` takes the **nearest**
  incoming pipe, not the nearest ready one (`SPEC.md` §"Which pipe do I talk to?").

Two tricks keep setup short despite the FIFO:

* `2D` and `2Dy` are both computable *before* the major axis is known, so the
  compare only has to **swap two values already in hands** — no re-shuffling.
  Likewise `U`/`U+V` from `sx`/`32*sy`.
* `M` is recovered as `den >> 1` in a one-lap rotation preamble, so `M` needs no
  scratch slot of its own. The same preamble sets `BP = M+1`, sends `n` to the
  painter and leaves `B = f`.

## Verification

`tests/test_plotter_block.py` re-runs the op-level simulation (`A`, `B`, `BP`, ring
and both pipes) against the statement's pseudocode over all 589,824 segments, so a
change to the op sequence fails fast and cheaply without touching the engine. The
grid itself is checked on the reference interpreter with `display-frames.mjs`, and
every `r`/`s` binding with `route-check.mjs`.
