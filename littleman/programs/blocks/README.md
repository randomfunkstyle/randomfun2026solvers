# Verified blocks

Small programs that each prove one mechanism against the reference engine, kept
so the mechanism can be reused (and re-verified) without re-deriving it. Run any
of them with the matching `-cases.json`:

```sh
node littleman/tools/run-cases.mjs littleman/programs/blocks/<x>.man \
     littleman/programs/blocks/<x>-cases.json 20000 1
```

## `scratch-register.man` — a value parked outside A/B/BP

A 2-room circulating ring. `s` parks a value, `r` takes it back; a relay keeps it
moving in between. Round trip ~10 ticks, and the parked value survives A and B
being overwritten — which is the point, since the worker has only two hands and a
backpack it cannot read.

Needed by anything that must remember something across a pass-through loop: the
tape phase (relative rotation) or a division remainder.

**The catch is geometry, not logic.** Adding a register gives the worker two
incoming pipes (input, register) and two outgoing (output, register), and `r`/`s`
pick the *nearest* one — so every op's column decides which pipe it talks to.
With anchors ~6 cells apart the windows are tight; in this block `r(in)` must sit
left of column 12 and `r(reg)` right of it. Always confirm with
`route-check.mjs` rather than by eye: the first draft of this block silently read
the empty register instead of the input and blocked forever.

## `packed-field-unpack.man` — 3 memory cells in one 64-bit word

A value needs 21 bits (±10⁶ biased by 10⁶), so three fit in 63:

    value = ((word >> 21*slot) & 2097151) - 1000000

as `r M `21` * M r } M `2097151` & M `1000000` - N s`. 36 ticks, fixed. Verified
for ±10⁶, ±1 and 0 in every slot, with the neighbouring fields left intact.

Packing would cut the `memory` tape from 100 values to 34 words. The write path
is harder than this read path: read-modify-write needs `word`, `21*slot` and
`value` live at once, which is three values against two hands — hence the
register above. `newword = word + ((value_biased - old_field) << 21*slot)` is the
cheapest form found so far.
