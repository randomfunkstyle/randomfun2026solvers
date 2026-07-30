# MNIST on LM-1 — a machine that learns

A littleman grid that trains a convolutional network and draws its own learning
curves. 437×477, 13 rooms, **two** LM-75 panels, 26 pipes, 11 live men.

Loss on one panel, accuracy on the other, train in red and validation in cyan. The
curves are painted by the program: the pixel positions are values the machine
computed, not something Python drew on its behalf.

This is a demo in the shape of [`DEADMAN-3D.md`](DEADMAN-3D.md), not a contest
entry — MNIST is not a released problem, so there is no score to protect and no tick
cap to respect. What constrains it is wall clock.

Design: [`../docs/superpowers/specs/2026-07-29-mnist-cnn-design.md`](../docs/superpowers/specs/2026-07-29-mnist-cnn-design.md).

## Quick start

The curves, in minutes, on the emulator:

```sh
cd solvers/python
PYTHONPATH=$PWD uv run python -c "
from randomfun2026solvers import mnist_cnn
from randomfun2026solvers.deadman3d import _png_bytes
rep = mnist_cnn.run_emulator(epochs=60, samples=300, frames=True)
loss, acc = rep.frames
open('/tmp/panels.png','wb').write(_png_bytes(loss[-1] + ['0'*64]*2 + acc[-1], 16))
"
```

60 epochs over 300 images a split is ~152M instructions and about five minutes. The
full 2000/1000 set is ~11.5 minutes for 30 epochs.

The real machine, on the native engine:

```sh
PYTHONPATH=$PWD/solvers/python uv run python -c "
from randomfun2026solvers import mnist_cnn
from randomfun2026solvers.fast_littleman import FastLittleman
kw = dict(epochs=3, samples=8)                      # <- ONE place; see the traps below
m = mnist_cnn.build_machine(**kw)
rows = (m.text() if hasattr(m,'text') else '\n'.join(m.rows)).split('\n')
r = FastLittleman(rows).run(input=mnist_cnn.machine_input(**kw), max_ticks=20_000_000_000)
print(r.step, [len(f) for f in r.frames_per_display()])
"
```

The checked-in grid is `../tasks/solutions/mnist-cnn.man`, built at the generator's
defaults. Regenerate it, with its debug sidecars, in one invocation:

```sh
cd solvers/python
uv run python -m randomfun2026solvers.mnist_cnn \
    --man ../../tasks/solutions/mnist-cnn.man --html /tmp/m.html --json /tmp/m.json
```

## Three things that will look like a hung machine

Every one of these cost real debugging time, twice over in one case — a false
deadlock was reported against a perfectly healthy machine, and a *correct* diagnosis
of a real bug elsewhere was wrongly retracted on the strength of it.

### 1. There are two input protocols, and they are not interchangeable

The **grid** reads what `mnist_cnn.machine_input(**kw)` returns: ring B's initial
contents — the model's starting weights, biased by `SCALE` so they are non-negative,
because ROM literals cannot be negative — followed by one lap of train-then-validation
words per epoch. **There is no leading epoch count.** The grid bakes its epoch count,
because the STREAM block already owns the machine's one legal `I` room and `SPEC.md`
makes a second one a load error.

The **emulator**'s stream *does* begin with an epoch count.

Feed either one the other's stream and it starves.

### 2. The machine and its input are a matched pair

The body is fully unrolled, so it reads *exactly* as many words as it was built to
read. `build_machine` and `machine_input` must be called with identical
`epochs`/`samples`. Derive both from one dict so they cannot drift.

A mismatch starves the machine: every man blocks on `r` waiting for words that never
arrive, pipes carry nothing, and it is **indistinguishable from a deadlock** — same
symptom, same profile, no error.

### 3. `halted` is never the signal. Frames are.

**This machine never ends.** The STREAM rings and the tape worker never halt, so the
engine's "every runner stopped with nothing in flight" condition is never met.
`reason` is always `tick-cap` and `step` is always just the cap you passed, whatever
the machine actually did. A 40-billion-tick run that reports `tick-cap` may have
finished six epochs or none.

The health check is **`frames == epochs + 1` per panel** — one for the axes painted at
boot, plus one per epoch. Read `FastResult.frames_per_display()`.

Passing `frames=` (expected content) makes the run settle at `output-settled` the
moment the last frame matches, instead of burning to the cap, and stamps
`frame_ticks`. Per `AGENTS.md`'s deadman-3d rule, that is the only tick number here
that means anything.

## The panels

Two 64×32 panels. Rows 0–29 are the plot, row 30 the x-axis, columns 0–1 the y-axis,
columns 2–63 one column per epoch — so **62 epochs is the most that will draw**.
Beyond that the curve stops advancing horizontally, which looks like a stall and is
not one.

| | |
|---|---|
| background | 0 |
| axes | 8 |
| **train** | **9** (bright red) |
| **validation** | **14** (bright cyan) |

Loss maps to 30 rows over the measured range (train 2,366–7,387, validation
2,702–4,883 in Q12); accuracy is linear 0–100%. Values above the ceiling clip to row 0
**silently** — expect both series pinned to the top for the first epoch or two.

`SWAP ← 1` preserves `next` and the cursor, so the axes are painted once at boot and
each epoch writes four pixels and commits. The panel is a persistent framebuffer.

### Two panels need two rooms

`SPEC.md`'s "exactly one display" is a *judging* rule, not an engine limit. The engine
accepts more, under two rules found by bisection:

* **R1 — a room may feed at most one display.** One room piped to two panels fails at
  load with `runtime error: index out of range [1] with length 1`.
* **R2 — the wired displays must be a prefix of the displays in reading order.**
  Wiring only the second of two fails the same way.

R1 is the load-bearing one: **the CPU is one room, so the CPU cannot drive both
panels.** Each panel gets its own relay room, in the shape `dsprelay.py` was built
for — one CPU pipe in, three LM-75 ports out, the port chosen behind the seam where
each `s` sits statically beside its own outlet. A new opcode buys a lane, hence a
pipe, hence a room; it cannot buy a panel.

## The network

```
x  : 8x8x1, 4-bit            -> Q12 fixed point (SCALE 4096)
c1 : Conv2D(2, 3x3, valid)   -> 6x6x2                648 MAC
b1 : + per-channel learnable bias
a1 : ReLU
p1 : MaxPool2                -> 3x3x2 = 18
z  : Dense(10) + bias                                180 MAC
p  : softmax, exp via a 32-entry LUT
L  : cross-entropy
```

210 parameters. 828 MACs a forward pass, ~1,008 backward. SGD at batch 1, learning
rate a power of two so an update is a shift.

**Achieved: 80–82% validation accuracy.** Validation loss bottoms around epoch 8–10
and then climbs — a fixed learning rate overfitting a small set. That is a property of
the model, faithfully reproduced, not a fault in the machine.

### Why it is this small

The architecture originally asked for — two 32-filter convolutions with batch norm and
a 100-unit dense layer on 32×32×3 — is ~9.9M MACs a forward pass. At the measured cost
of an LM-1 instruction that is **208 days an epoch**. The model had to come down by
about four orders of magnitude, and 8×8 with two filters is where it landed.

### Why there is no BatchNorm

At batch size 1 the batch mean *is* the sample, so the normalised output is a constant
independent of the input. The layer cannot function. Its learnable β survives as the
per-channel bias above; γ folds into the next layer's weights. Making BN meaningful
needs minibatches, which is a different and much more expensive machine.

## How it is built, and why

**Fully unrolled.** A taken backward jump discards `(target − pc − 1) mod P` ring words
at 4.8 ticks each — 36–52% of six shipped machines. Rolled up, this program's
~1,836-MAC inner loop would spend roughly three quarters of its life throwing
instructions away. So forward and backward are straight-line code, and the only
backward jumps are the sample and epoch loops, whose wrap is nearly free.

**The multiply lives in the STREAM unit.** `MAC n` is `r s * s` in a counted loop
inside the block — about 12 ticks a multiply-accumulate against ~650 for four CPU
instructions. Four arms were added for this machine: `PUSHA` (the scalars are
CPU-computed, where `FILLA` fills from the input room), `ROTB` (conv needs ring B at a
tap offset and popping is the only way to rotate), `RDP` (`EMIT` goes to the `O` room;
we need the CPU), and `UPDB` (routing 190 weight updates through the CPU would cost
more than the whole step). They sit on a depth-4 trie; `matmul` keeps the depth-3 unit
and its grid is byte-identical.

**Rings, not RAM.** A permanently-full ring FIFO advances only when its consumer takes
a word, so it is a sequential tape costing ~2 instructions an access — and, crucially,
**zero live men**. A man-memory word is a little man the simulator steps every tick.
The whole machine has 11 men; an all-RAM store would have added ~351.

**`UPDB`'s shift is 18, not 6.** Nothing else writes ring B from the CPU, so `UPDB`
became the writer: with `g = 1` and `a = (b − v) << 18`, `b − ((a·g) >> 18)` is exactly
`v`. One shift serves both that and the weight update, which reaches the same place by
composition — `(g >> 12) >> 6 == g >> 18`. The program supplies it as
`.equ STREAM_LR_SHIFT`; the builder refuses a mismatch.

## What it costs

Measured on the native engine, not projected:

| | |
|---|---|
| per **train** sample | 5,478,536 ticks |
| per **validation** sample | 3,943,012 ticks |
| fixed: boot + `FILLB` + axes + one epoch report | 763,728 ticks |
| **a full epoch** (2000 + 1000) | **14.9×10⁹ ticks, ~4 min** |
| 30 epochs on the grid | ~2 hours |
| 30 full epochs on the emulator | 11.5 min |
| throughput | ~65M ticks/s at 11 live men |

**Do not estimate ticks on this machine.** Four projections during its construction
were wrong by 10×, 2.7×, 11× and 3× in turn. The reason is structural: the emulator
models **no pipe latency at all**, so nothing derived from instruction counts predicts
grid ticks. Measure it or leave it out.

## How it is verified, and what each check cannot catch

Four tiers, each required to agree with the one below it:

1. **A pure-Python Q12 reference model** (`mnist_model.py`) — the oracle. Its gradients
   are pinned against finite differences *and* against an independent
   double-precision reimplementation sharing no code (84,000 comparisons, zero sign
   disagreements above 200). *What finite differences cannot catch:* `probs` is a
   rounded Q12 integer, so the numeric gradient quantises in steps of ~320 and
   anything below ~110 is unmeasurable by that method alone.
2. **The emulator over the real `.asm`** — must reproduce the reference exactly, every
   parameter and every plotted pixel. *What it cannot catch:* anything about timing. It
   models no pipe latency, so it cannot tell you whether the grid will deadlock or how
   long it will take.
3. **The native engine over the real `.man`** — the machine itself. Its frames have
   been shown to equal the emulator's at three different sample counts.
4. **The bundled wasm** — for probe grids only, to keep R1 and R2 honest against the
   official engine. The full machine is far past what the wasm can run.

And the structural checks: every pipe binding verified **by position** against the
engine's own `route`, and pipes drawn counted against pipes `analyze` finds.

**A pipe count is necessary and not sufficient, and here is the case that proves it.**
`ARCH.md` §7.2b: a pipe may not turn in the cell it attaches by, in *either* wall
orientation. On a **horizontal** wall, drawing it wrong leaves the pipe **count
unchanged** — only `src` becomes -1. So the count cannot catch that class at all, and a
mis-bound display port produces a machine that runs to completion painting the wrong
thing with no error (§4.4). Every binding check here is paired with a deliberate
mis-bind that the check is required to complain about; a check that cannot fail is
worth nothing.

That was this machine's dominant defect class during construction — six instances in
six subsystems, each a test that exercised a construct while asserting something
adjacent to its behaviour. Worth knowing before adding a check of your own.

## Not done

* **The grid-versus-emulator differential is not a suite test.** It has been run by
  hand and passes at three sample counts; it is not yet in `tests/`.
* **No long native run has been recorded.** The measured per-sample costs come from
  short runs; nothing has trained to convergence on the grid.
* **The dataset arrives as input, not as ROM.** The original intent was MNIST baked
  into the grid as literals in recirculating rings. As built, the images are streamed
  through the input pipe once per epoch — which is why the machine is 437 columns wide
  rather than the ~700 a dataset serpentine would have needed.
