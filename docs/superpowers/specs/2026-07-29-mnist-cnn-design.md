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
| fitted cost per tick | **~30 ns fixed + ~1.9 ns per live man** | the two rows above |
| emulator throughput | **930k instructions/s**, ~60M emulated ticks/s | `lm1.emulator` over 2,001 instructions |
| one CPU instruction | ~162 ticks | `ARCH.md` §4.1, profiled on `snake` |
| one tape read / write | 523 / 19 ticks | same |
| one man-memory read | ~31 ticks, independent of `n` | `ARCH.md` §4.1, `memory_men_addr` |
| one recirculated ROM word | 4.8 ticks | `ROM-RECIRCULATION.md` |

**Wall clock is the only clock in this project.** There is no judge, so nothing
charges ticks; what costs is our own simulator, and it charges per live man per
tick. Every sizing decision below is made against wall clock, and several of them
come out the opposite way to how they would on a graded problem.

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
| ticks per epoch | ~6.0x10^9 |
| live men | ~20 |
| **wall clock per epoch** | **~7 min native, ~40 s emulator** |
| 10 / 20 / 30 epochs, native | 1.1 h / 2.3 h / 3.4 h |
| 10 / 20 / 30 epochs, emulator | 7 min / 13 min / 20 min |

Expected validation accuracy: **83-86%**.

The tick count is ~2x what the MAC count alone implies, because the store is rings
rather than random-access memory (§4.2) — and the wall clock is nevertheless ~3.5x
better, because the ring costs no men.

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

64 nibbles is **exactly four 64-bit words**. A fifth word carries the label. So an
image is 5 words, the train set is 10,000 words and the validation set is 5,000.

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

ROM cost: 192,000 nibbles at ~3 bits per grid cell (a 19-digit literal in 21 cells)
is ~256,000 cells of serpentine. With the program's own ROM and code ring (§5) the
whole grid lands near 650x650 — irrelevant
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
p  : softmax, exp via a 32-entry LUT
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
values that are genuinely random-access.** RAM is faster per access and costs ~3.5x
more wall clock, which is the `little-little-man` trade again — 52 slots in a man
tier cut ticks 2.36x and raised simulator work 9.7x.

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
* **The live-man cost model is fitted from two points**, both on `deadman-3d`, whose
  man counts are themselves estimates from the docs rather than counted. `~30 ns +
  1.9 ns per man` is good enough to choose rings over RAM by 3.5x, and not good
  enough to size the scratch RAM precisely. First implementation task after the
  data path: build the same trivial program at several `store="grid"` sizes and
  measure the slope directly.
* **RAM versus rings is settled** (§4.2) and does not need revisiting: rings for
  everything with a fixed access order, <=16 words of man-memory for the rest.
