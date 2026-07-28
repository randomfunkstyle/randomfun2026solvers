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

### Making it play at speed in the official editor

Expect the editor to crawl otherwise, and note that the engine is not at fault:
its wasm core is linear and already sparse. The cost is the editor's own habit of
snapshotting **full entity state** — every man, every pipe — once per animation
frame, and then throttling itself toward roughly one tick per frame. A frame of
this program is millions of ticks, so the demo can appear frozen.

Paste this into the browser console **before** starting the run:

```js
const api = globalThis.littlemanWasm;
const orig = api.stepN.bind(api);
api.stepN = (id, n, stop) => {
  const s = orig(id, Math.max(n, 50000), stop);
  try {
    const o = JSON.parse(s);
    if (o && o.entities) o.entities = { displays: o.entities.displays || [] };
    return JSON.stringify(o);
  } catch (e) { return s; }
};
```

It wraps `stepN` to do two things: force a floor of 50,000 ticks per UI frame
instead of a handful, and hand back a snapshot containing only the displays.
The shape stays valid, so the editor is satisfied — it just no longer receives
the hundreds of men and pipes it would otherwise re-serialise sixty times a
second. The display is all this demo renders from, so nothing visible is lost.

The snippet is deliberately comment-free. Pasting a commented version into the
console can swallow the newlines and fail with `Uncaught SyntaxError: Unexpected
identifier` — this one parses even if every newline is stripped.

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

### You do not need a WAD to run this

The machines in `littleman/examples/` are complete and standalone. Load one,
feed it `deadman-3d.input.txt`, and it plays — no WAD, no assets, nothing else
on disk. **A WAD is only needed if you want to build a machine from a different
level or different art.**

### What a WAD is for, and why it is not in this repo

A WAD is DOOM's archive format: the level geometry, the wall textures, the
palette, the title screen and every sprite live inside it. The demo needs that
data to have anything to draw — but *which* WAD supplies it is a licensing
question, not a technical one:

* **Freedoom** is a BSD-licensed, from-scratch replacement for DOOM's data. It is
  freely redistributable, so its level and art are what this repository ships,
  already baked into the committed machines.
* **id Software's IWADs** (`DOOM.WAD`, `DOOM2.WAD`, and the shareware
  `DOOM1.WAD`) are not redistributable. Owning a copy and using it yourself is
  fine; putting its data in a public repository is not. So none of it is here,
  `littleman/examples/local/` is in `.gitignore`, and no test in the suite reads
  an IWAD.

That is the entire reason for the two-mode pipeline. It is not that Freedoom is
preferred — it is that Freedoom can be published.

### From a clean clone to real DOOM, step by step

Verified end to end on a fresh clone of this repository with a retail
`DOOM1.WAD`; the numbers below are what that run printed.

**1. Get the code and its dependencies.** Python 3.12 and
[uv](https://docs.astral.sh/uv/) are all you need — there is no C toolchain
step, no WAD library, nothing to compile.

```sh
git clone <this repo> randomfun2026solvers
cd randomfun2026solvers
uv sync
```

**2. Get a WAD.** Any DOOM IWAD works. The shareware `DOOM1.WAD` is the easiest
and is enough for everything here — it contains E1M1, the title screen, the
palette, the pistol, the status-bar faces and the monsters. If you own DOOM on
Steam or GOG, the IWAD sits in that installation directory. On Windows-installer
shareware distributions the WAD may be inside the installer archive; extract it
with `7z` or similar first.

**3. Build machines from it.**

```sh
cd solvers/python
uv run python -m randomfun2026solvers.deadman3d \
    --wad /path/to/DOOM1.WAD --build
```

It prints what it found and what it wrote:

```
installed iwad:DOOM1.WAD:E1M1: spawn (27, 30) heading 4, 467 wall cells,
72 nukage cells, 3 monsters, 1120 title runs; WAD art: 11+14 pistol runs,
4 faces, 60 monster sprite words
wrote .../littleman/examples/local/deadman-3d_local.man (375x376)
wrote .../littleman/examples/local/deadman-3d_local_taped.man (395x231)
wrote .../littleman/examples/local/deadman-3d_local.cases.json, .input.txt, frames/
```

Three monsters is correct, not a bug: E1M1 "Hangar" really is that sparse on
medium skill. Freedoom's first level has sixteen, which is why the committed
demo is the busier one.

**4. Look at what it made.** `littleman/examples/local/frames/` holds a PNG per
frame; `frame-00.png` is the real `TITLEPIC` — the marine, the fire, the logo —
quantized to 64×48.

**5. Run it.**

```sh
cd ..    # back to the repository root
PYTHONPATH=$PWD/solvers/python uv run python -m randomfun2026solvers.fast_littleman \
    littleman/examples/local/deadman-3d_local.man \
    littleman/examples/local/deadman-3d_local.cases.json --tick-cap 3000000000
```

Or paste `deadman-3d_local_taped.man` plus `deadman-3d_local.input.txt` into the
web editor, with the console patch above.

Everything under `littleman/examples/local/` is gitignored, so nothing you build
this way can be committed by accident.

### Build time, not run time

The WAD is **translated first, then the machine runs**. Nothing reads a WAD while
the program executes; the machine has no filesystem and no idea DOOM exists.

```
DOOM1.WAD ──> wadimport.py ──> deadman3d.py ──> lm1/machine.py ──> .man + input.txt
   (lumps)      parse and       golden model      layout and         a standalone
                quantize        + asm generator   synthesis          machine
```

The imported level becomes ordinary numbers: the map is packed into words that
ride in over the **input pipe** as a boot preamble (451 words of map, tables,
nukage plane, monster table and sprite columns, then 429 words of title-screen
RLE — 880 in total), and the sprites the painter unit owns are baked into its
arms as run tables. After that preamble, the machine takes exactly one word per
frame, forever. That is why `input.txt` is long and why a hand-written input must
begin with the same preamble.

So `--wad` is a *compiler* invocation, not a *player* one. Run it once; the
`.man` it produces is then as self-contained as the committed ones.

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
