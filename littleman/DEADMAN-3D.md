# deadman-3d — DOOM on the LM-1 CPU

A first-person raycaster running as a program on our littleman CPU: real level
geometry, textured walls, a pistol that fires, monsters that die, damage floors
that drain a live health bar, and a status-bar face that reacts. 64×48 pixels,
one frame per input word.

It is an integer transliteration of [lodev's `raycaster_flat.cpp`][lodev],
lowered to LM-1 assembly. Every division goes through the emulator's own
floored-division semantics, and the Python golden model is pixel-exact against
the machine — that equality is the project's contract, checked on every run.

[lodev]: https://lodev.org/cgtutor/raycasting.html

---

## Quick start — run the demo

Two files: a machine and an input stream.

```sh
# in the contest web editor: paste the machine, then the input
littleman/examples/deadman-3d_taped.man        # 395x231, ~26 men — use THIS one in the editor
littleman/examples/deadman-3d.input.txt        # the 57-frame demo walk
```

Headless — run it and check every frame against the expected output:

```sh
PYTHONPATH=$PWD/solvers/python uv run python -m randomfun2026solvers.fast_littleman \
    littleman/examples/deadman-3d.man \
    littleman/examples/deadman-3d.cases.json --tick-cap 2000000000
# PASS  deadman-3d: 330339051 ticks
```

Use the **native** runner, not `littleman/tools/display-frames.mjs`. The bundled
wasm engine allocates per tick and dies (`stepN` returns `undefined`) long before
a frame of this program finishes — a frame is roughly 5.7 million ticks and the
demo walk is 330 million. The native engine is tick-exact against it, verified
over 816 differential runs.

**Which `.man`?** There are only two distinct machines:

| file | size | use it for |
|---|---|---|
| `deadman-3d.man` (= `_v2`, `_trim`) | 379×376 | headless runs — about 2× faster |
| `deadman-3d_taped.man` (= `_m6_taped`) | 395×231 | **the web editor** |

They run the same program and take the same input; they differ only in how
memory is built. The canonical machine uses man-memory (hundreds of little men);
the taped one uses a banked tape with a couple of dozen, because the editor
snapshots every man on every animation frame and grinds to a halt otherwise.

Expect the editor to be slow regardless. The bottleneck is not the engine —
its wasm core is linear and already sparse — but the editor's own habit of
snapshotting full entity state once per animation frame and then throttling
itself toward one tick per frame. A frame of this program is millions of ticks.
Wrapping `littlemanWasm.stepN` in the browser console to force a much larger
step chunk, and slimming the snapshot to displays only, makes it play at speed;
that is a local hack against the editor's internals, not something this repo
ships.

**Two input streams ship with it:**

| file | what it does |
|---|---|
| `deadman-3d.input.txt` | the 57-frame demo walk: the hall, two imps killed in the corridor, the cavern, the slime fall |
| `deadman-3d_tour.input.txt` | a 115-frame patrol: six shots on target, four kills, one deliberate wade into the nukage — health 100 → 30, the face degrading through all three states |

---

## Play it yourself, in a terminal

```sh
cd solvers/python
uv run python -m randomfun2026solvers.deadman3d --play
```

`w`/`a`/`s`/`d` move and turn, space fires, `q` quits. This runs the **real
checked-in assembly** on a persistent emulator — one input word per keypress —
so what you are playing is the machine, not a Python re-implementation. Add
`--golden` to play the golden model instead, which renders instantly if the
emulator feels slow.

Non-interactive, for scripting or screenshots:

```sh
uv run python -m randomfun2026solvers.deadman3d --play-script "wwwaad  ww"
uv run python -m randomfun2026solvers.deadman3d --walk ".wwaa d" --png /tmp/frames
```

---

## Importing your own WAD

The committed machines are built from **Freedoom** (BSD-licensed). If you own a
copy of DOOM, you can build machines from *your* WAD locally — the output is
git-ignored, and nothing IWAD-derived is ever committed.

```sh
cd solvers/python
uv run python -m randomfun2026solvers.deadman3d \
    --wad /path/to/DOOM1.WAD --build
```

That writes a complete artifact set into `littleman/examples/local/`:

```
deadman-3d_local.man            the canonical machine, your level and art
deadman-3d_local_taped.man      the editor-friendly variant
deadman-3d_local.input.txt      the demo walk for it
deadman-3d_local.cases.json     expected frames, round by round
frames/                         PNG previews
```

Useful flags:

| flag | meaning |
|---|---|
| `--wad PATH` | the IWAD or PWAD to import |
| `--wad-map NAME` | which map marker (default `E1M1`) |
| `--build` | write the full artifact set; without it, the import is just validated |

**What gets taken from your WAD:** the level geometry (`VERTEXES`, `LINEDEFS`,
`SIDEDEFS`, `SECTORS`, `THINGS`), wall textures resolved through
`TEXTURE1`/`PNAMES` and coloured by their dominant hue, the damage-floor
sectors, the title screen (`TITLEPIC`), the palette (`PLAYPAL`), the pistol
(`PISGA0`/`PISFA0`), the status-bar faces (`STFST01`/`STFST21`/`STFST41`,
`STFEVL0`), and the monster sprites (`POSSA1`, `TROOA1`, `POSSL0`).

**Shareware works.** `DOOM1.WAD` is enough — it has E1M1 and every lump above.

### Where the art actually comes from

| element | committed build | your `--wad` build |
|---|---|---|
| level geometry | Freedoom E1M1 | your WAD's map |
| wall colours | Freedoom textures | your WAD's textures |
| title screen | Freedoom `TITLEPIC` | your `TITLEPIC` |
| pistol | Freedoom | your `PISGA0`/`PISFA0` |
| HUD face | Freedoom | your `STFST*` — real DOOM art |
| monsters | Freedoom | your `POSSA1`/`TROOA1` |
| health/ammo bars | **generated** — not from any WAD | same |

At 64×48 the original status bar's numbers would be two pixels wide, so the
readouts are proportional bars rather than DOOM's digit font.

### On legality

Freedoom is BSD-licensed and safe to redistribute, which is why it is what the
repository ships. id's IWADs are not: importing your own purchased or shareware
copy for your own use is fine, redistributing its data is not. That is the whole
reason for the two-mode pipeline — `littleman/examples/local/` is in
`.gitignore`, and no test in the suite reads an IWAD.

---

## The input protocol

Round 0 is a preamble — the map, tables, sprite data and the title screen's RLE,
fed in over the input pipe rather than baked into ROM (every ROM word taxes every
backward jump forever). After that, **one word per frame**, a bitmask of the keys
held that frame:

| bit | value | key |
|---|---|---|
| 0 | 1 | W — forward |
| 1 | 2 | S — backward |
| 2 | 4 | A — turn left |
| 3 | 8 | D — turn right |
| 4 | 16 | space / click — FIRE |

`0` renders without acting. Higher bits are ignored. Keys combine: `21` is
W+A+FIRE — step forward, turn left and shoot in the same frame. Each word is
applied in lodev's order: turn first (A and D cancel), move along the *new*
heading (W and S cancel, per-axis collision), then render.

`deadman-3d.input.txt` is exactly the flattened rounds of
`deadman-3d.cases.json` — preamble, title, commands. If you hand-write an input,
it must start with that same preamble or the machine will sit at the title
painter forever.

### Planning your own route

```sh
# from the repository root, not solvers/python
PYTHONPATH=$PWD/solvers/python uv run python \
    scratch/deadman3d-opt/plan_tour.py /tmp/my.input.txt 2 5
```

The planner walks the open-cell grid (a step is two cells, a turn is 22.5°),
routes around nukage unless slime is the destination, and aims by scanning all
sixteen headings for one that actually puts a monster under the crosshair — then
fires. The two trailing arguments are which hunt to soak in slime on, and for how
many frames.

---

## Rebuilding from source

```sh
cd solvers/python
# the canonical machine, from the registry
uv run python -m randomfun2026solvers.lm1.machine deadman-3d --man out.man --report
# the editor-friendly variant
uv run python -m randomfun2026solvers.lm1.machine deadman-3d --store taped --man out_taped.man

cd ..                       # the suite runs from the repository root
uv run pytest tests/test_deadman3d.py tests/test_wadimport.py -m ""
```

The single source of truth is
`solvers/python/randomfun2026solvers/deadman3d.py`: it holds the golden model,
the imported level data, and the generator that emits the assembly. The importer
is `wadimport.py` (stdlib only — no external WAD library). The machine layout,
store tiers and per-slug tuning live in `lm1/machine.py`.

---

## Credits

Level geometry, title screen, pistol, faces and monster sprites in the committed
machines come from [Freedoom](https://freedoom.github.io/) Phase 1 (commit
`d14dbbe`, BSD licence). The raycaster is lodev's, transliterated. DOOM is
id Software's; none of it is included here.
