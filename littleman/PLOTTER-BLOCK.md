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

## Laying out the worker: the send-binding detour

`r` competes only with other **incoming** pipes and `s` only with other **outgoing**
ones, which is what makes the layout tractable at all:

* The worker's two incoming pipes (input, ring-return) are separated by grouping all
  four `RIN`s in the prologue. Ring *pushes* may interleave with them freely — an `s`
  never competes with an `r` — so the prologue costs one wall transition per round,
  not four.
* The two outgoing pipes (ring-forward, painter) are the hard case, because the hot
  loop contains four pushes **and** one send to the painter, and adjacent cells are
  ~1 apart in Manhattan distance. They cannot both bind by proximity unless the
  painter send is physically separated from the pushes.

Ring order fixes where that send falls. With `[step, den, U, U+V]` the carry lane
sends last (clean west→east run, no detour) and the no-carry lane sends third of
four; any other order just swaps which lane pays. There is no order that makes both
last, because the two lanes send *different* slots, and the slot a lane doesn't send
must still be popped and pushed to keep the ring aligned.

So the no-carry lane takes a return leg: it runs east to the painter send and comes
back west along a second row for the final push. That costs the loop band's width
once per no-carry lap — about eight ticks, or four averaged over a line.

Alternatives considered and rejected:

* **Painter does two adds** (worker sends `U` then `V`-or-`0`, ring holds
  `[step, den, U, V]`). Removes the detour and needs no `U+V` slot, but adds three
  ticks to every painter lap — the same average cost, for a second protocol to get
  right.
* **`S` instead of `s`** for the painter send. `S` writes every outgoing pipe, so it
  needs no binding at all — but it also injects the increment into the ring, and its
  ordinal differs between the two lanes, so the relay cannot know which value to
  drop.
* **`f` on the ring, `B` free.** The carry lane needs `den` before it can finalise
  `f`, while `f`'s slot must precede `den`'s; holding `step`, `den` and `f` at once
  needs three hands.

## Assembling the box

Four rooms and seven pipes. The port columns are fixed by the binding regions, so the
arrangement is mostly forced:

* **display** 34x26 at the top, **worker** 42x21 below it, **painter** 17x7 and
  **relay** 5x8 at the bottom (side by side — together 22 wide, so they cost one band
  of height, not two).
* Both pipes leaving the worker's south wall have a *forced* first cell pointing
  south, so they can only diverge on the row below: ring-forward runs west to the
  relay, the increment pipe east and then around. That is the whole reason the relay
  sits west of the painter's feed.
* The ring's return goes down and around the east side, because the input pipe needs
  the row directly above the worker's north wall and the two cannot share it.

A pipe's first cell must point **away** from the wall it leaves — the analyser reads
the source room off the cell behind the flow, so a first leg that runs *along* the
wall gets `src: -1` and no `s` in that room can bind to it. The failure is silent:
the sends go nowhere and the program simply produces no output.

Three geometric constraints fell out of the first assembly attempt, all cheap to
satisfy once known and all silent-failure modes if not:

* **The worker's south-wall pipes and the painter's north-wall pipes cannot share a
  band.** Each has a *forced* first cell pointing away from its wall, so the worker's
  two pipes necessarily occupy the row two below its south wall and the painter's
  three occupy the row two above its north wall. Leave a spare row between the rooms
  and each band gets its own row.
* **The display's top wall needs a row above it**, because a pipe entering a wall
  needs a cell on the far side — so the panel cannot sit at `dy = 0`. The same
  applies to any wall a port uses.
* **The relay cannot sit between the painter and the worker's feed.** Its own walls
  block the increment pipe's westward leg, and its return pipe's forced first cell
  takes the row below it. Putting the relay under the painter, with the ring running
  down one side and the return around the other, keeps every leg on its own row.

The three display pipes are long in the assembled box (the painter is ~50 rows below
the panel), which `timing_ok` constrains but does not forbid: only the *relative*
lengths matter, and `l_swap` may be as long as convenient.

### The round has to be re-entrant

The bug that survived every geometric argument was not geometric. Each lap of the
pixel loop pops all four constants and pushes all four back to stay aligned, so when
`BP` runs out they are **still circulating** — and the next round's prologue pops
those in place of its own `x0`/`y0`. Every segment drawn on its own was perfect and
any sequence of them was garbage. Four `POP`s on the exit path drain the ring, which
costs the worker two rows and buys the only property that matters here: a round
leaves the ring exactly as it found it, empty.

The 589,824-segment proof never caught this because it only ever ran *one* round.
`test_the_round_is_re_entrant` and `test_consecutive_rounds_are_independent` do.

### Declaring the pipes instead of searching for them

Routing the seven pipes kept moving one collision around rather than removing it, so
the obstruction is worth stating exactly. The worker spans nearly the full width, so
every pipe that must get from the bottom half to the top half needs a column clear of
it — a *channel* — and there are only a few, on either side. Four facts then fight:

* **The worker's two south ports may only bend on the row two below its wall**, and
  ring-forward's port is west of the increment's, so the only non-crossing pair is
  ring-forward west, increment east. Whichever way the increment then travels, its
  descent is a wall across every row between the worker and the painter.
* **That descent separates the relay from the east channels.** Put the relay west of
  it and the ring's return can never reach the east side; put it east and
  *ring-forward* cannot reach the relay, since it may not cross the increment's port
  column either.
* **The input and the ring-return both end in the two-row band above the worker's
  north wall**, at its west end and its middle. From opposite sides they must cross;
  from the same side they nest, but then that side needs two channels.
* **The display's three ports need three separate bend rows** between the painter's
  stubs and the worker's south wall, and three separate channels — so one side needs
  five channels and at most four exist.

A BFS router that tries every link order and randomised tie-breaks places six of the
seven pipes and never the seventh — and that is the lesson, not a limit of the search.
A shortest-path router solves each pipe *locally* and eats the one row a later pipe
needed, so the collision only ever moves. What closes the box is **declaring** all
seven paths and allocating the shared resources up front:

    band over the worker's north wall   row 27 SWAP, row 28 ring-return, row 29 input
    west channels (painter -> display)  col 2 ADDR, col 4 DATA, col 6 SWAP
    row each display pipe bends west    56 ADDR, 55 DATA, 54 SWAP

Channels and bend rows nest opposite ways (`2<4<6` against `56>55>54`), which is what
keeps the three display pipes from crossing. Three placements do the rest:

* **The increment descends straight down.** It cannot bend before the row two below
  the worker's south wall, and any sideways leg there cuts a channel — so it goes
  straight down and round the bottom into the painter's *west* wall.
* **The relay talks east and north.** One pipe each way means neither its `s` nor its
  `r` needs a binding argument, so any wall is free: in on the east makes ring-forward
  a single drop, out on the north puts the return in the open band above the painter
  instead of the fenced-in bottom.
* **The input room sits west of the worker.** From above, the input would cross the
  ring-return in the two-row band; rising from the west each owns one row.

`build_block()` draws it and checks every path against its port's stub as it goes.

## Result

**20/20 cases, score 29,147,283** — against 7,760,316,749 for the CPU version, a
**266x improvement**. 49x59 (area² 3,481) and ~8,373 average ticks.

### The ring's length is the machine's clock rate

The single biggest win after the design itself came from measuring rather than
guessing. Fitting the public cases gives a cost model:

| | fixed | per round | per pixel |
|---|---|---|---|
| ring returning over the north wall | 43 | 541 | **112** |
| ring turning round under the worker | −14 | 288 | **78.6** |

The worker's pixel-loop lap is only 74–78 cells, so 112 ticks/pixel could not be its
code. It was the **ring**: every lap pops all four constants and pushes them back, so
lap *n+1* cannot start until the values pushed on lap *n* have travelled the whole
loop. The return leg was 93 cells — it entered the worker's *north* wall, so it had to
climb the west channels and cross the band to get there — and the worker spent ~35
ticks of every lap standing at an `r`. Lengthening only that pipe by 30 cells measured
**+30.0 ticks/pixel, one for one**, which is the whole diagnosis in one experiment.

The fix is a binding observation. `r` binds by Manhattan distance to *the cell where
each incoming pipe meets the room*, so two incoming pipes on **opposite walls** are
separated by the **row** term rather than by a column. The round's shape already
matches that: every input read is in the prologue at the top of the room and every
ring pop is below it. So the ring-return moved to the south wall beside ring-forward,
the relay flattened to two rows and sits directly underneath, and the ring went from
101 cells to 11. The old `RIN_MAX`/`POP_MIN` column regions are gone, replaced by a
predicate the engine's own `route` oracle is tested against, glyph by glyph.

It is not shorter still because **the ring is also its own buffer**: the prologue
pushes six values before anything pops, the peak depth is 8, and a pipe holds one
value per cell — under nine cells the machine deadlocks rather than running fast. The
ring is sized by capacity and only then minimised for latency.

By contrast the three display pipes are 74/71/83 cells and cost **~40 ticks per case,
once**: they are pure pipeline fill, and `timing_ok` forces all three to be within ~13
cells of each other anyway, so there is nothing to win there.

### Height, and what each remaining row is for

Once ticks were the worker's own lap again, the box came down 63 → 59 by finding rows
that were only clearance:

* **The increment enters the painter's east wall**, not its west — coming round the
  bottom cost a row for one westward leg. The painter has a single incoming pipe, so no
  `r` in it needs a particular side.
* **The input enters the worker's west wall**, which empties the ring-return's old band
  row. Two cells and not one: a one-cell pipe is *both* stubs at once, and the analyser
  then reports `dst: -1` and every input read silently falls through to the ring.
* **The relay's box sits inside the rows the display pipes climb and bend on** — the
  pipes are at cols 0..21, the relay at 30..36 — and the row the ring crosses to reach
  the relay is the same row SWAP bends west along. Five rows do three jobs.
* **Band B starts one row higher.** A westward `X` puts its two-cell hop on the row
  above and its solo lane below, so band B reaching up into row 2 costs nothing: that
  row had held the base row's descent, two glyphs in thirty-nine columns.

The band above the worker is still **two rows for one pipe**, and that one is not
removable: SWAP's last cell turns north into the display's bottom wall, and the loader
decides where a pipe *starts* from the cell behind the arrowhead — with the worker's
north wall directly under that turn it reads the turn as a new pipe leaving the worker
and refuses to load. The second row is clearance under an arrowhead.

Two loader rules worth stating, both found by being bitten:

* **A pipe may not bend away from a wall it hugs.** Any cell whose arrowhead points
  away from an adjacent wall is read as a pipe *starting* at that room, which turns a
  legitimate pipe into a room-to-itself loop.
* **A one-cell pipe cannot be both stubs.** `analyze` will still name its source, but
  the loader gives it no destination.

What is left is load-bearing. Row 0 exists because a pipe entering the display's top
wall needs a cell above it, and in LM-75 the top wall *is* the ADDR port. The
remaining height is the display (26 rows) and the worker (20), so the next win is
reshaping the worker itself. Width has 10 columns of headroom before it binds, so rows
can still be traded for columns for free — and the *ticks* now sit in the pixel loop's
two long horizontal runs, whose length is set by how far east the increment port's
binding boundary forces the `s@PAINT`.

## Verification

`tests/test_plotter_block.py` re-runs the op-level simulation (`A`, `B`, `BP`, ring
and both pipes) against the statement's pseudocode over all 589,824 segments, so a
change to the op sequence fails fast and cheaply without touching the engine. The
grid itself is checked on the reference interpreter with `display-frames.mjs`, and
every `r`/`s` binding with `route-check.mjs`.
