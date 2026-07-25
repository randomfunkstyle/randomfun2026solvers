# Debugging and profiling a `.man`

A generated grid is thousands of cells of ASCII with **no comments and no symbols**.
Everything here exists to put names and numbers back on top of it.

The house convention: **a generator emits its grid and its debug sidecars in one
invocation**, so an overlay can never drift from the ASCII it describes.

```sh
uv run python solvers/python/randomfun2026solvers/memory_onepass_v2.py --size 100 \
  --man  littleman/examples/memory-onepass-v2.man \
  --html littleman/examples/memory-onepass-v2.debug.html \
  --json littleman/examples/memory-onepass-v2.debug.json

uv run python -m randomfun2026solvers.lm1.machine plotter \
  --man  tasks/solutions/plotter_cpu.man \
  --html littleman/examples/plotter-machine.debug.html \
  --json littleman/examples/plotter-machine.debug.json

uv run python -m randomfun2026solvers.memory_men --tree 4 4 \
  --man  littleman/examples/memory-men-tree-4x4.man \
  --html littleman/examples/memory-men-tree-4x4.html \
  --json littleman/examples/memory-men-tree-4x4.json
```

The man-memory overlay is the case for the convention: its grid is 16 identical
6x6 rooms and nothing in the ASCII says which one holds address 5. The sidecar
names every cell `cell addr N` (`N = mid lane * k2 + leaf lane`, and mid lane *j*
feeds the block `k1-1-j` rows down), so the picture answers that in one hover.

Open the `.html` in a browser: the grid with every region boxed, named and
annotated. Keep the `.json` — the tools below read it.

## The sidecar is what makes the rest work

`man_debug.DebugMap` holds named boxes, circles and lanes (`region`, `lane`,
`TraceScenario`) in grid coordinates. A hand-drawn machine marks these up by hand;
`lm1/machine.py` derives them automatically from what it just laid out
(`Machine.regions` → `Machine.debug_map()`), which is why a *generated* CPU is
traceable at all.

## Which tool for which question

| Question | Tool |
|---|---|
| Does it pass? | `tools/run-cases.mjs prog.man cases.json` |
| Does it pass a **display** problem? | `tools/display-frames.mjs prog.man problem.json` |
| Which pipe does this `r`/`s` really use? | `tools/route-check.mjs prog.man` |
| What is each man doing around tick N? | `tools/debug-trace.mjs prog.man debug.json "input" from to` |
| Why is it stuck? | `tools/watch.mjs`, `tools/trace.mjs` |
| **Where does the time go?** | `tools/heatmap.mjs` + `lm1/profile.py` |

`route-check.mjs` is the one people skip and shouldn't: a mis-bound `s` is invisible
until the program silently reads the wrong pipe (`ARCH.md` §7.1). `debug-trace.mjs`
annotates each runner with the region it is standing in, so a trace reads
`[region:cpu:fetch]` rather than `(11,35)`.

## Profiling

```sh
node littleman/tools/heatmap.mjs tasks/solutions/plotter_cpu.man \
     --input "0 0 31 23" --cap 300000 --json /tmp/p.json

uv run python -m randomfun2026solvers.lm1.profile /tmp/p.json --slug plotter \
     --html littleman/examples/plotter-machine.heat.html
```

`heatmap.mjs` is a **sampling** profiler: it steps the engine and records every
runner's cell every `--stride` ticks. Sampling rather than tracing because a
snapshot carries every pipe's contents and (on a display problem) 768 pixels, so
parsing one per tick costs more than the run — and a profile needs an unbiased
sample, not every tick. `--stride 1` when exactness beats speed.

Two things it took a wrong answer to learn, and both are baked in now:

- **Read it per runner, not per cell.** A *servant* blocked on its input — the
  adapter waiting for a request, the tape waiting for the adapter — is **idle**, and
  pooling it with the CPU inverts the picture. The first run I did pointed straight
  at the adapter's `r` as 19 % of all time; that man is simply idle 89 % of his
  life. `profile.py` picks the **least-stalled** runner as the critical path and
  reports everything relative to it.
- **A blocked man still shows up.** He is sampled in the same cell every time, so a
  blocking hot spot is a tall bar — which is right, because waiting on a pipe costs
  exactly as many ticks as walking does.

The text report ends in a five-line rollup — `fetch / trie / lanes / slabs /
return` — and those are the numbers an optimisation has to move. `--html` recolours
the *same* overlay by measured time, so a profile reads like every other shot in
`littleman/examples/`.

Worked example, and the size of the prize: profiling `plotter` put the **return
path at 25 %** of the CPU's life — pure walking, no work — and it turned out to be a
placement mistake rather than a necessity. Fixing it took 11 % off `brackets` and
12.6 % off `plotter`. `ARCH.md` §2.9 has the full write-up.

## When you have a candidate

Submit it and archive it in one step — see the README's *Submitting to the contest*.
Local scores understate: the server grades private cases too (`brackets` is 9
public but 26 graded), so treat a local number as a lower bound. Resubmitting an
unchanged grid is refused by hash, so profiling and resubmitting is cheap to repeat.

### The `Y`-born cell field

```sh
uv run python -m randomfun2026solvers.memory_men_line --cells 16 \
  --man littleman/examples/memory-men-field-16.man \
  --html littleman/examples/memory-men-field-16.html \
  --json littleman/examples/memory-men-field-16.json
```

This one is the case for the convention twice over. Sixteen identical four-column
tiles share **one room**, so nothing in the ASCII says which tile holds address 5 —
and nothing says which `Y` on the spawner corridor gave birth to which resident.
The overlay names both, and the cell-to-address labels are checked against the
engine (write `1000+a` to every address, snapshot the runners, assert each holder
stands inside the region named `cell addr a`) rather than asserted from the layout.

### The broadcast-addressed field

```sh
uv run python -m randomfun2026solvers.memory_men_bcast --cells 16 \
  --man littleman/examples/memory-men-bcast-16.man \
  --html littleman/examples/memory-men-bcast-16.html \
  --json littleman/examples/memory-men-bcast-16.json
```

Sixteen cells in one room again, but here the bands differ only in the *length of a
`]` chain*, and nothing in the ASCII says why — or which band answers address 5. The
overlay names each band's address and its shift count, circles the `{` that is the
entire address decoder, circles all sixteen `Y` births, and marks the empty lower
half of the router as load-bearing (a shorter router leaves the lower bands' pipes
with no source room). Cell-to-address labels are engine-checked, not asserted.
