# LM-75 panel probes — cursor origin, commit/cursor persistence, port latency

Six hand-written grids here, all 4×4 panels. Every claim is the engine's verdict:
`lm.mjs judge <grid> --frames '[[[rows…],…]]'` returns `frameJudge {matched, total}`
on the frames quoted (hex row-strings, as in `tasks/problems/*.json`); per-frame dumps
come from `lm.mjs tick <grid> <t> --json` → `entities.displays[0]`.

## Q1 — the write cursor is 0 at power-on, before any ADDR write

`panel-cursor-poweron.man` — one room, **no ADDR pipe at all**: DATA (pipe len 17)
gets `1`,`2`,`3`; then SWAP (len 20) gets `1`.

    run --json : displays[0] = { w:4, h:4, front:[1,2,3,0, 0…], cursor:3, frames:1 }, step 34 "done"
    judge --frames [[["1230","0000","0000","0000"]]]  ->  matched 1 / total 1

The colours land in cells 0,1,2 row-major; the cursor ends at 3. So the cursor is 0
from power-on — `ADDR ← 0` / `SWAP ← 0` re-*home*, they never initialise. A pure
streaming painter can omit the ADDR pipe entirely.

## Q2 — `SWAP ← 1` preserves the cursor *and* `next`, as a sequence

`panel-cursor-interleaved-commits.man` — DATA `1`,`2`; SWAP `1`; DATA `3`,`4`;
SWAP `1`. Pipe lengths as Q1 (DATA 17, SWAP 20).

    tick 31  frame #1  cursor=2  ['1200','0000','0000','0000']
    tick 47  frame #2  cursor=4  ['1234','0000','0000','0000']
    judge --frames [[["1200",…],["1234",…]]]  ->  matched 2 / total 2

The second pair continues at cells 2,3 — not back at 0 — and frame 2 still holds the
first pair. A run of 256 DATA writes can be cut into as many `SWAP ← 1` commits as
you like with no ADDR and no re-homing; only `SWAP ← 0` resets.

## Q3 — the constraint is on *arrival ticks*, not on pipe lengths

A value sent on tick `T` into an `L`-cell pipe is consumed during tick `T+(L-1)`
(confirmed: it sits at pipe index `L-2` in the snapshot for that tick). Within a tick
the panel processes **ADDR → DATA → SWAP**. So for a DATA at send-tick `td` with its
ADDR/SWAP at `ta`/`ts`, the only real rules are

    ta + (Laddr-1)  <=  td + (Ldata-1)      ADDR must not be overtaken
    ts + (Lswap-1)  >=  td + (Ldata-1)      the commit must not overtake the pixel

and equality is safe in both, because of the same-tick ADDR→DATA→SWAP order.

**The failure, concretely.** `panel-latency-swap-overtakes.man`: DATA len 6, SWAP
len 2. DATA `1` sent tick 8 (arrives 13), SWAP `1` sent tick 10 (arrives 11).

    tick 12  frame #1  cursor=0  ['0000','0000','0000','0000']   <- pixel missing
    tick 16  frame #2  cursor=1  ['1000','0000','0000','0000']   <- a frame late

The commit is sent later and still lands first; on a streaming judge that one frame of
skew fails every later frame too.

**`swap == data` is safe** — `panel-latency-swap-equal.man`: both pipes len 6, sends
one tick apart (adjacent `s` cells, ticks 11/12), the tightest a single man can do.
Frame 1 = `1000`, matched 1/1. No strict inequality is needed.

**`swap >= data` and `addr == data` are sufficient, not minimal.**
`panel-latency-swap-shorter-but-later.man`: SWAP len 4 *shorter* than DATA len 6, sends
two ticks apart, both consumed during tick 14 → frame 1 = `1000`.
`panel-latency-addr-shorter-same-tick.man`: ADDR len 3, DATA len 11, SWAP len 20; DATA
`3` sent tick 5, ADDR `6` sent tick 13, both arrive tick 15 → frame
`0000/0030/0000/0000`, cursor 7. ADDR applied first despite being 8 cells shorter and
sent 8 ticks later. `snake_unit.py`'s asserts are a safe convention that lets the
generator ignore send ticks; they are not the machine's limit.

## Aside

A single stray `|` one cell behind a bend arrowhead (directly north of a southward `v`
bend) makes the **whole pipe silently vanish**: no load error, `analyze` just reports
one pipe fewer, `s` binds a sibling pipe, and the program runs to completion doing the
wrong thing. Two cells away, or beside a straight body cell, is harmless. Check the
`analyze` pipe count and `route` on every `s` before trusting hand-drawn wiring.
