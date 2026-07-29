# Training a CNN on the LM-1 CPU: MNIST in ROM, two live charts

**Status:** design, 2026-07-29. Branch `feat/mnist`.

A machine that *learns*. MNIST is packed into the grid as literals, an LM-1 program
runs forward and backward passes over it, and two LM-75 panels plot loss and
accuracy for train and validation as the run proceeds.

This is a demo in the shape of `deadman-3d`, not a contest submission: MNIST is not
a released problem, so there is no score to protect and no tick cap to respect. What
constrains it is wall clock, and that turns out to constrain it *hard*.

## 1. The budget, measured before anything was designed

| quantity | value | source |
|---|---|---|
| native engine, ~26 live men | **12.3M ticks/s** | 20M ticks of `deadman-3d_taped.man` in 1.62s |
| native engine, ~200 live men | **2.5M ticks/s** | 20M ticks of `deadman-3d.man` in 7.91s |
| native engine, 1,643 live men | **7.0M ticks/s** (143 ns/tick) | 1M ticks of `mnist_convnet_display.man` in 0.14s |
| cost per tick | **proportional to *awake* men, not to live men** | the three rows above; see §1.1 |
| emulator throughput | **930k instructions/s**, ~60M emulated ticks/s | `lm1.emulator` over 2,001 instructions |
| one CPU instruction | ~162 ticks | `ARCH.md` §4.1, profiled on `snake` |
| one tape read / write | 523 / 19 ticks | same |
| one man-memory read | ~31 ticks, independent of `n` | `ARCH.md` §4.1, `memory_men_addr` |
| one recirculated ROM word | 4.8 ticks | `ROM-RECIRCULATION.md` |

**Wall clock is the only clock in this project.** There is no judge, so nothing
charges ticks; what costs is our own simulator. Every sizing decision below is made
against wall clock, and several come out the opposite way to how they would on a
graded problem.

### 1.1 A live man is not a cost; an *awake* man is

This started as "~30 ns a tick plus ~1.9 ns per live man", fitted from the two
`deadman-3d` builds. **A third measurement broke it.** `mnist_convnet_display.man` —
an unrelated hand-built inference machine, 667x1328, 1,643 spawns and no `H` at all —
runs at **143 ns a tick**, where that model predicts 3,152. It is off by 22x, and in
the direction that matters: 1,643 men cost *less* per tick than `deadman-3d`'s ~200.

The mechanism is in the engine's own performance notes
(`fast_littleman_native.cpp`): *"A runner blocked on a pipe op sleeps until an event
that could change the retry outcome. A failed retry has no side effects, so sleeping
is observationally identical as long as wake-ups are never missed."* In a dataflow
grid almost every man is blocked on an `r` at any instant, so almost every man is
free. Cost tracks the men actually walking.

**What survives, and what does not.** The `deadman-3d` pair is still a real
measurement — the same program, the same 20M ticks, 1.62s taped against 7.91s on
man-memory, a genuine 4.9x. And `ARCH.md` §4.1's `little-little-man` result is real:
52 slots into a man tier raised simulator work 9.7x. So a man-memory does cost, but
**not because its words are men — because its decoders, router and collector walk,
and every access broadcasts and wakes the cells.** Cost is per *access*, not per
stored word.

So the §4.2 conclusion stands — rings hold zero men and cannot be beaten on this axis
— but **the margin claimed there is not established**, and the mechanism it gives is
wrong. Treat its table as ordering the options correctly and its numbers as
unverified. §9's slope measurement is now a requirement rather than a nicety, and it
must measure awake men per access, not live men.

The emulator is **24x faster than the grid** and runs the same assembly, which is
what makes this project tractable at all: the development loop and the accuracy
evidence live on the emulator, the machine is the artifact.

### Why the requested architecture cannot be built

The architecture in the request — `Conv2D(32) -> BN -> Conv2D(32) -> BN -> MaxPool
-> Dense(100) -> Dense(10)` on 32x32x3 — is ~9.9M MACs per forward pass and ~30M
per training step. One epoch over 2000 train + 1000 validation images is ~7x10^10
MACs; at ~650 ticks per MAC (four instructions) that is 4.5x10^13 ticks, or
**208 days per epoch** at the measured rate. It needs to come down by ~10^4.

The chosen point on the curve, at 10-30 epochs:

| | |
|---|---|
| MACs per forward pass | 828 |
| MACs per training step | 1,836 |
| MACs per epoch | 4.5M |
| ticks per epoch, MACs on the CPU | ~6.0x10^9 |
| ticks per epoch, MACs in the STREAM unit (§4.2a) | ~4.4x10^8 |
| live men | ~25 |
| **wall clock per epoch** | **~30 s native, ~3 s emulator** |
| 10 / 20 / 30 epochs, native | 5 min / 10 min / 15 min |

Expected validation accuracy: **83-86%**.

Two decisions produce that number, and they pull in opposite directions on ticks.
The store is rings rather than random-access memory, which costs ~2x the ticks and
saves ~3.5x the wall clock (§4.2). The multiply-accumulate moves into the STREAM
unit, which saves ~14x on top (§4.2a). The model itself is unchanged from the
CPU-only plan: the STREAM win is spent entirely on wall clock.

## 2. Engine facts established by probe

Three findings, none of them in `SPEC.md` before this work. Probe grids live in
`tests/test_mnist_display.py`; the scratch versions are reproducible from the
snippets in §6.

### 2.1 Two displays are legal, under two rules

`SPEC.md` says "exactly one display at the stated resolution" — that is a *judging*
rule for display-judged problems, not an engine limit. The engine accepts more, and
the rules that govern it were found by bisection:

* **R1 — a room may feed at most one display.** One room with pipes to two panels
  fails at load with `runtime error: index out of range [1] with length 1`. Two
  rooms, one pipe each, to two panels: loads and runs.
* **R2 — the wired displays must be a prefix of the displays in reading order.**
  Two panels with only the *first* wired loads; only the *second* wired fails with
  the same panic. So an unwired panel may not precede a wired one.

A verified 2-room / 2-display / 6-pipe grid (each room driving its own panel's
ADDR, DATA and SWAP) runs to `done` on the bundled wasm — the official engine.

**R1 is load-bearing for this design: the CPU is one room, so the CPU cannot drive
two panels.** Each panel needs its own room in front of it.

### 2.2 The native engine already supports N displays

`fast_littleman_native.cpp` holds a `std::vector<Display>` and `execute_displays()`
iterates all of them. The refusal `display judging needs exactly one display, found
2` comes only from the Python frame-extraction helper at `fast_littleman.py:641`,
which assumes a single panel to compare frames against. That is our own code.

### 2.3 The palette

`deadman3d.PALETTE` is the ANSI 16: `0` black, `8` dark grey, `9` bright red,
`14` bright cyan, `15` white. Train series is **9**, validation is **14**, axes
**8**, background **0**.

## 3. Data — five words an image, in two recirculating rings

### 3.1 Preprocessing, fixed and deterministic

28x28 uint8 -> centre-crop 24x24 -> 3x3 average pool -> 8x8 -> quantise to 4 bits
(`v >> 4`, giving 0..15). 2000 train images and 1000 validation images, taken as
the first N of the official train and test splits so the selection needs no seed.

**A word holds fifteen nibbles, not sixteen.** Sixteen would be 64 bits exactly, and
every littleman value is a *signed* 64-bit integer while a ROM literal must in
addition be non-negative (`rom.digit_width` raises on a negative word) — an all-white
8x8 image packs to `2^64 - 1`, which is neither. Fifteen nibbles is 60 bits, at most
`1,152,921,504,606,846,975`: 19 digits, comfortably inside the signed range.

64 pixels plus a label is 65 nibbles, so an image is **5 words** with ten slots
spare — the count the rest of this document assumes, unchanged. The train set is
10,000 words and the validation set 5,000.

### 3.2 Blocked rings, not a tape

Both sets live in **permanently-full ring FIFOs**, and this is the single most
important structural decision in the design.

`ARCH.md` §2.1: a ring holding `P` words in `P + slack` cells advances only when its
consumer takes a word. So the dataset ring is a sequential tape with:

* **a read cost of one `r` plus one `s` write-back** — ~2 instructions, ~320 ticks;
* **perfect order preservation**, because men on a cycle cannot overtake (§"four
  measured facts", fact 3);
* **one lap per epoch, exactly** — 10,000 reads returns the ring to its start
  position, so epoch boundaries need no counter and no addressing;
* **zero live men, and zero per-tick cost.** Values move through pipes without a
  runner, and `fast_littleman_native.cpp`'s performance notes make the stronger
  claim: a pipe is a deque plus a run-length list of occupied cells, "a busy pipe
  costs O(runs), an empty pipe costs nothing", and every run advances unless jammed
  against the destination. A **permanently-full ring is exactly one jammed run**, so
  a 10,000-word ring costs O(1) a tick no matter how long it is. This is the
  mechanism behind §4.1's `runners x ticks` rule — the one that got a
  `little-little-man` submission rejected with `time-cap` despite being 2.36x faster
  in ticks.

The alternative was a tape, and it is not close: at `105 + 8.3n` ticks per access a
15,000-word tape costs ~125,000 ticks **per read**.

### 3.3 Getting the data in

`mnist_data.py` fetches the four idx.gz files from the CVDF mirror with stdlib
`urllib` + `gzip` (no new dependencies; verified reachable), preprocesses, packs,
and writes a checked-in artifact of ~100KB holding the 3000 packed images plus a
SHA-256 of the source files. Builds and tests are then **offline and
byte-reproducible**, which `AGENTS.md`'s "never touch production from a test" rule
wants. The fetch is a separate CLI subcommand, never a build step.

ROM cost: 15,000 words as 19-digit literals in ~21 cells each is ~315,000 cells of
serpentine. With the program's own ROM and code ring (§5) the whole grid lands near
700x700 — irrelevant
against the 10MB program limit, and there is no footprint term to pay.

## 4. The model

```
x  : 8x8x1, 4-bit 0..15        -> Q12 fixed point (scale 4096)
c1 : Conv2D(2, 3x3, valid)     -> 6x6x2              648 MAC
b1 : + per-channel bias (learned)
a1 : ReLU
p1 : MaxPool2                  -> 3x3x2 = 18
f  : Flatten                   -> 18
z  : Dense(10) + bias                                 180 MAC
p  : softmax, exp via a 32-entry LUT (`>> 10` index, interpolated)
L  : cross-entropy
```

828 MAC forward, ~1008 backward (dense weight grads 180, dense input grads 180,
conv weight grads 648), **1,836 per training step**. 210 parameters: 18 conv
weights, 2 conv biases, 180 dense weights, 10 dense biases.

**Arithmetic.** Q12 throughout; a MAC is `a*w` then `DIVI 4096` (floored, matching
`%` and the `}` shift). With activations under 8.0 and weights under 1.0 a product
is under 2^30 and a 9-term accumulation under 2^34 — comfortably inside signed 64
bits, which is what makes fixed point safe here without saturation logic.

**SGD, batch 1.** The learning rate is a power of two so an update is a shift.
`lr_shift = 6`, measured: at 8 the model reaches only 70%.

### 4.1a The bit-exactness contract

Two tiers have to reproduce this model *exactly*, so every approximation in it is a
load-bearing interface, not an implementation detail. Established while building it
(`mnist_model.py`, and §10 of the task-2 report):

* **Accumulate then shift** — `sum(a*b) >> SHIFT`, never a shift per product, in conv,
  in dense, and in the conv weight gradient. Python's `+` binds tighter than `>>`, so
  the obvious formula is ambiguous and the grouping has to be stated.
* **The exp LUT is indexed `(-z) >> 10`**, clamped at entry 31, linearly interpolated
  on the low bits below the clamp. `>> 10` covers `z` in (-8, 0], the whole range
  before a Q12 exp underflows. **`>> 7` — the first guess — cannot work**: it confines
  all 32 entries to (-1, 0], logit gaps pass 1.0 inside one epoch, and every trailing
  class then shares one probability with the label's `dz` pinned near -3169. Thirty
  configurations of learning rate against init scale all stayed under 53%, against 80%
  for a double-precision exp, so it was not a tuning problem.
* **A second 33-endpoint log table** (`_LN_MANT`, interpolated) backs
  `cross_entropy`, so the reported losses can compare equal across tiers.
* **Pool ties break to the lowest index**; the ReLU mask is `pre > 0`.
* **`predict` reads `logits`, not `probs`** — the softmax is monotone, so this is free
  and avoids a rounding disagreement.
* **Metrics:** train metrics online, validation as a separate sweep, file order, no
  shuffling.
* **`w -= g >> lr_shift`** with floor semantics, sign asymmetry included.

Gradients were additionally checked against an independent double-precision
reimplementation sharing no code: 84,000 comparisons at init and after three epochs,
maximum deviation 269 in Q12 (0.066 real), **zero sign disagreements above 200**. That
matters because the finite-difference probe has an irreducible noise floor — `probs`
is a rounded Q12 integer, so the measurable step in the numeric gradient is ~320 and
anything below ~110 is unmeasurable by that method alone.

### 4.2a The MAC goes in the STREAM unit, not the CPU

`lm1/stream.py`'s STREAM block is a rotate-only ring tier with a **fused
multiply-accumulate inside the unit**: the `MAC` arm is `r s * s` in a counted loop —
read `B[j]`, push it back, multiply by the scalar still in `B`-hand, hand the product
to the ADDER — at **~12 ticks per multiply-accumulate** against ~650 for four CPU
instructions. `MAC n` is therefore a rank-1 update `C[j] += a * B[j]` for `n` terms,
issued by **one** `SND`.

It coexists with jumps. `path_unit` and `d3_unit` are write-only because "an
incoming pipe is a rival for every `r` in the CPU, the jump slab's ROM read
included" — but that is a statement about *their* geometry, not a law: `matmul` ships
with `stream=(257, 257, 17)`, four `RCV`s and two jumps, judged at 1,137,402,365.
That machine is the precedent this design builds on.

**The win is spent on wall clock, not on model size.** The model stays as in §4:
~30 s an epoch instead of ~7 min, so 30 epochs finishes in ~15 min and the
development loop is effectively interactive. (Spent on capacity instead it would
have bought a 16x16 / 8-filter / Dense(32) model at ~93-95%; that is recorded here as
the option not taken, and it remains available later without redesign.)

**What the unit does not yet have.** Eight arms exist — `RDIN`, `FILLA`, `DRAINB`,
`FILLB`, `MAC`, `ZEROC`, `FWD`, `EMIT` — and all eight trie leaves are used, so new
arms mean a depth-4 trie and a re-verified row map. Four are missing:

| new arm | why | shape |
|---|---|---|
| `PUSHA v` | `FILLA` fills from the **input room**; our scalars are CPU-computed | `s(A_fwd)` with the command's own argument |
| `ROTB n` | conv needs ring B at tap offset `t`, and the only way to rotate is to pop | `r s`, no product |
| `RDP` | `EMIT` sends to the `O` room; we need a partial sum back at the **CPU** | `r(P1) -> s(resp)` |
| `UPDB n` | the weight update must not go through the CPU (190 weights x 2 instructions = 61,560 ticks would swamp a 22,000-tick step) | `n x { b=r(B_ret), g=r(P1), s(P2), b - (a*g >> lr), s(B_fwd) }` |

`UPDB` is the one with real register pressure — two reads and a shift with only `A`
and `B` — so it gets designed as a standalone probe grid verified against a Python
model *before* it is placed, which is how `dsprelay` and the store selector were
built.

#### The trie width is a parameter, not a widening

The first plan said "add the new arms at codes 8-15, leaving the existing eight at 0-7
so `matmul` cannot shift." **That is arithmetically impossible.** The wire format is
`arg * 2^bits + code`, so:

* at the current 3 bits, `word % 8` is always 0-7 and codes 8-15 are **unreachable** —
  `PUSHA` with `arg=42` would encode to 344, which decodes as `EMIT(43)`;
* at 4 bits, every existing word with an odd `arg` **aliases**. `FILLA` with `arg=35`
  is word 283, which decodes correctly as `(35, 3)` at mod-8 and as `(17, 11)` at
  mod-16. `matmul`'s shipped public cases exercise odd `arg` already.

So the decode width cannot be widened in place. It becomes a **parameter of the built
unit**: `trie_bits=3` keeps `matmul` on the depth-3 unit with its eight arms and its
`8 * arg + code` format, byte-identical and untouched; `trie_bits=4` builds the
depth-4 unit for this machine, with sixteen leaves, twelve arms used and a
`16 * arg + code` format. Each program uses the unit its own program was written
against.

This is strictly safer than widening a shared trie: `matmul`'s grid, its `.equ`
constants and its command words are not modified at all, so the arm-renumber hazard
the plan worried about cannot arise. The cost is that `stream.py` and `StreamUnit`
both carry the width, and a test must pin that a depth-3 unit still decodes
`8 * arg + code` exactly as it did.

### 4.2 The store: rings win on wall clock, and unrolling is what makes them easy

Four tiers were priced against the model above. The tape is dead on arrival at this
size; a man-memory RAM is the fastest in ticks and loses anyway.

| tier | ticks/access | live men | ticks/epoch | throughput | **wall clock/epoch** |
|---|---|---|---|---|---|
| tape, `n` ~ 300 | `105 + 8.3n` = **2,595** | 0 | ~2.4x10^10 | 20M/s | ~20 min, and 4x the arithmetic |
| man-memory, all 300 words | **31** | ~300 | 2.5x10^9 | 1.7M/s | ~25 min |
| hybrid, ~170-word RAM | 31 | ~170 | 3.2x10^9 | 2.8M/s | ~18 min |
| **rings + <=16-word scratch RAM** | ~2 instructions | **~20** | 6.0x10^9 | 14.7M/s | **~7 min** |

So: **rings for everything with a fixed access order, a small man-memory only for
values that are genuinely random-access.** The ordering is right — a ring holds zero
men and so cannot lose on this axis — but **the wall-clock column above is computed
from the superseded per-live-man model and is not to be trusted** (§1.1). What is
measured is that a man tier costs per *access*, because a broadcast wakes its cells
and its router and collector walk: `little-little-man` cut ticks 2.36x and raised
simulator work 9.7x moving 52 slots into one. Rings remain the choice; the size of
the win is unquantified.

**Full unrolling is what makes ring order free.** A ring forces a fixed access
order, which is normally the thing that makes it hard to program. In straight-line
code it is not bookkeeping at all: the generator knows at emit time exactly which
value sits at the ring head at every instruction, so ring position is a compile-time
constant it can simply get right, and the fast tier can assert it.

Concretely:

* **Parameters** — conv (20 words) and dense (190) in their own rings. The dense
  backward pass and the weight update share **one lap**: iterating `(j, i)` in
  storage order, `df[i] += dz[j]*W[j][i]` and `W[j][i] -= lr*dz[j]*f[i]` both read
  `W[j][i]` exactly once.
* **Conv as nine offset-sequential passes.** For a fixed tap `t`, `acc[p] +=
  x[p+t]*W[f][t]` walks both `x` and `acc` in order at a constant offset, so both
  can be rings. This is ~2x the instructions of a random-access conv, and it is what
  keeps the man count near 20.
* **Activations** — the 72 conv pre-activations, 18 pooled values and pool argmax
  indices go in a ring, written forward and read backward.
* **The scratch RAM holds <=16 words**: the accumulator spills the unrolled code
  cannot keep in `A`/`B`, plus the 10 `dz` values. At 16 men that is ~30 ns/tick
  extra, i.e. free, and it removes the only genuinely awkward ordering constraint.

### 4.1 Two of the requested layers are deliberately absent

**BatchNormalization is dropped, and not as a shortcut.** At batch size 1 the batch
mean *is* the sample, so BN's normalised output is a constant independent of the
input — the layer cannot function. Its learnable `beta` survives as the per-channel
bias above; `gamma` folds into the next layer's weights. Making BN meaningful needs
minibatches, which multiplies the activation store and the tick budget; that is a
different machine and is out of scope here.

**Dropout defaults off**, behind a build flag. With 18 features at batch 1, rates of
0.25/0.5 would swamp the signal. If enabled it is an LCG, three instructions.

## 5. Program shape — straight-line, because jumps are what costs

A taken backward jump discards `(target - pc - 1) mod P` ring words at 4.8 ticks
each, and `ROM-RECIRCULATION.md` measures that as **36-52% of six shipped
machines**. Rolled up, this program is ~400 words with a ~10-word MAC body, so every
one of the 1,836 iterations would discard ~390 words: **~1,900 ticks of discarding
per MAC against ~650 of arithmetic**, i.e. the loop would spend three quarters of its
life throwing instructions away.

So forward and backward are **fully unrolled**: `train_body` ~14,700 instructions
(~21,000 ROM words) and `val_body` ~6,600 (~9,400 words), `P` ~ 30,400. With two
bodies in one ring the discards are small and asymmetric, because a loop discards
`P - body`:

| loop | discard per sample | against compute | share |
|---|---|---|---|
| train | ~9,400 words = 45k ticks | 2.4M ticks | **2%** |
| validation | ~21,000 words = 101k ticks | 1.07M ticks | **9%** |

Both acceptable. The alternative — one body with the backward section skipped
forward on validation samples — trades the 9% for a ~72k-tick forward skip, so it is
close to a wash and is settled by measurement once both exist.

Unrolling is not free on the *code* ring: at `P` ~ 30,400 words the ring pipes must
hold `P + slack` cells (§2.1 — capacity below `P` deadlocks, capacity far above it
starves), so ~30,400 cells of folded pipe plus a ~103,000-cell ROM serpentine at 3.4
cells a word. That is additive to the ~256,000 cells of dataset ROM and puts the grid
near 650x650. It costs nothing but area, and there is no area term here — but it does
cost **one** live man for the ROM, which halts after boot.

Control flow that remains:

```
epochs = IN                       ; 10..30, an input word, not a rebuild
paint both panels' axes
loop epochs:
    2000 x train_body             ; unrolled fwd + bwd, 5 words off the train ring
    1000 x val_body               ; unrolled fwd, 5 words off the val ring
    plot 2 pixels on the loss panel, 2 on the accuracy panel
    SWAP 1 on both                ; persistent framebuffer, nothing repainted
HALT
```

## 6. The two panels

Forced by R1: the CPU cannot feed two displays, so the shape is
**CPU -> relay room -> panel**, twice. This is exactly what `lm1/dsprelay.py` was
built for — one CPU pipe in, three LM-75 ports out, the port selector decoded by `X`
on `p - 1`. Each relay room feeds exactly one panel, satisfying R1; both panels are
wired, satisfying R2.

Each panel is 64x32. Layout per panel: rows 0..29 the plot area, row 30 the x-axis,
row 31 spare; columns 0..1 the y-axis, columns 2..63 up to 62 epochs at one column
each. Loss is mapped to 30 rows on a log-ish scale fixed at build time; accuracy is
linear 0-100%.

`SWAP <- 1` preserves both `next` and the cursor (`ARCH.md` §4.4, measured), so the
axes are painted once at boot and each epoch writes **two pixels per panel** and
commits. The panel costs 0 ticks beyond pipe transit.

**Generator work this requires**, called out because it is the riskiest part of the
build:

1. `machine.build`'s `display=` path assumes one panel below the CPU, with
   `_panel_x` deriving a single panel's west wall from the three port columns
   (`_display`, `machine.py:4139`). It needs to place two relay rooms and two
   panels, and `check_bindings` must confirm all six pipes bind where intended —
   `ARCH.md` §4.4 records that a mis-bound display port produces a machine that
   runs to completion doing the wrong thing, with no error.
2. `fast_littleman.py:641` must return frames per display rather than refusing
   anything but one.

## 7. Verification ladder

Four tiers, each pinned by tests, in the shape `deadman-3d` uses (its Python golden
model is pixel-exact against the machine, checked on every run):

1. **Pure-Python fixed-point reference** (`mnist_cnn.py`) — the oracle. Exact
   integer arithmetic, no numpy, so it cannot disagree with the emulator by dtype.
2. **Emulator over the real `.asm`** — must match the reference exactly: every
   parameter after every step, every plotted pixel. This is also what produces the
   published curves, in ~6 min for 20 epochs.
3. **Native grid over the real `.man`** — tick-exact against the emulator on a short
   prefix (1 epoch x 8 samples), then the full run in the background.
4. **wasm** — the 2-panel probe grids only, to keep R1/R2 honest against the
   official engine. The full machine is far past what the wasm can run (it dies on
   `deadman-3d` long before one frame).

Per `AGENTS.md`, the fast tier holds the pure things and must stay under ~10s:
nibble packing round-trips, the reference model's gradients against finite
differences, ring capacity and pipe-binding assertions, R1/R2 probe grids,
artifact-matches-generator for the checked-in `.man`. The slow tier holds the
engine runs. **No test asserts a footprint, a tick count or an accuracy** — tests
assert that the machine and the reference agree, and the achieved numbers go in the
generator report, this document and the commit message.

## 8. Files

| path | what |
|---|---|
| `solvers/python/randomfun2026solvers/mnist_data.py` | fetch, preprocess, quantise, pack; the checked-in artifact and its hash |
| `solvers/python/randomfun2026solvers/mnist_cnn.py` | fixed-point reference model, asm emitter, `machine.build` call, CLI with `--man`/`--html`/`--json` |
| `solvers/python/randomfun2026solvers/lm1/programs/mnist.asm` | generated, checked in |
| `tasks/solutions/mnist-cnn.man` | the machine |
| `tests/test_mnist_data.py` | packing, preprocessing, artifact integrity |
| `tests/test_mnist_cnn.py` | reference model, gradients, emulator equality |
| `tests/test_mnist_display.py` | R1/R2 probes, two-panel binding |
| `littleman/MNIST.md` | how to run it, what the curves show, the budget |

## 9. Open questions

* **Loss axis scaling** is fixed at build time from the reference run's range. If
  the machine's real loss leaves that range the chart clips. A log scale with a
  generous ceiling is the plan; revisit if the reference run shows otherwise.
* **Whether `val_body` is a forward skip or a second unrolled body** is a 2%
  decision, settled by measurement once both exist.
* **The cost model has been falsified once already** (§1.1) and its replacement is
  qualitative: cost tracks awake men and store cost is per access. Before sizing the
  scratch RAM, build the same trivial program at several `store="grid"` sizes and
  measure wall clock per access directly — not per stored word, which is the
  quantity the first model wrongly charged for.
* **RAM versus rings is settled** (§4.2) and does not need revisiting: rings for
  everything with a fixed access order, <=16 words of man-memory for the rest.
