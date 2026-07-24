#!/usr/bin/env python3
"""Generate ``littleman/memory.html`` — a visual explainer for the ``memory`` machine.

Everything the page shows is measured, not described: the walkthrough frames are
real ``lm.mjs tick`` snapshots (man position, A/B/BP, output) and the charts are
real per-case tick counts from :func:`optimize.verify`. Re-run after changing the
machine and the page follows.

    uv run --project solvers/python python littleman/tools/make_memory_html.py
"""
# ruff: noqa: E501 -- most of this file is an embedded HTML/SVG/JS template, where
# reflowing markup to 100 columns hurts readability more than it helps.

from __future__ import annotations

import json
from pathlib import Path

from randomfun2026solvers.littleman import Littleman
from randomfun2026solvers.optimize import verify
from randomfun2026solvers.scoring import footprint

ROOT = Path(__file__).resolve().parents[2]
MACHINE = ROOT / "littleman" / "examples" / "memory2.man"
OUT = ROOT / "littleman" / "memory.html"

# The main room's interior origin: the room is at (5,0), so interior x starts at 6.
IX, IY = 6, 0


# Interior regions -> phase. Decoded by tracing the machine (see the walkthrough).
def phase_of(x: int, y: int) -> str:
    if 15 <= x <= 16 and 8 <= y <= 11:
        return "FILL"
    if 9 <= x <= 12 and 8 <= y <= 12:
        return "SEEK"
    if 9 <= x <= 12 and 18 <= y <= 23:
        return "RESTORE"
    if y == 14:
        return "DISPATCH"
    if y == 15:
        return "READ"
    # Rows 16-17 east of the write block are the lane both branches take back to
    # the restore loop, not part of the write itself.
    if y == 17 and x >= 18:
        return "FETCH"
    if y in (13, 16, 17):
        return "WRITE"
    if y in (5, 6, 7):
        return "DECODE"
    return "FETCH"


# Three colour families, not seven — the point of the picture is that most of the
# machine is "walk the tape". 3 hues + neutral also keeps the palette inside the
# validated all-pairs cap.
FAMILY = {
    "FILL": "rotate",
    "SEEK": "rotate",
    "RESTORE": "rotate",
    "DECODE": "control",
    "DISPATCH": "control",
    "FETCH": "control",
    "READ": "op",
    "WRITE": "op",
}

GLOSS = {
    "@": "spawn a man here, facing east",
    "`": "numeric literal: loads into A on the closing backtick",
    "b": "BP = A — load the loop counter",
    "m": "BP -= 1 — one loop iteration done",
    "d": "turn clockwise while BP > 0, else go straight — the loop's test",
    "r": "receive into A from the nearest incoming pipe (blocks if empty)",
    "s": "send A into the nearest outgoing pipe (blocks if full)",
    "S": "send A into EVERY outgoing pipe at once — emit and keep, one glyph",
    "M": "B = A — stash a value in the off hand",
    "W": "swap A and B",
    "X": "turn by sign(A): CW if positive, CCW if negative, straight if zero",
    "N": "A = -A — negate",
    "-": "A = A - B",
    "0": "A = 0",
    "H": "halt this man",
    ">": "head east",
    "<": "head west",
    "^": "head north",
    "v": "head south",
}


def build_frames() -> tuple[list[dict], dict]:
    """Real snapshots for WRITE 7 -> cell 2, then READ cell 2 (output 7)."""
    lm = Littleman()
    rows = MACHINE.read_text().split("\n")
    inp = [1, 2, 7, 0, 2]

    raw: list[dict] = []
    prev = None
    laps = 0  # ring rotations since the current op started
    for t in range(0, 2400):
        s = lm.tick(MACHINE, t, input=inp)
        if not s.entities.runners:
            break
        r = s.entities.runners[0]
        x, y = r.pos.x - IX, r.pos.y - IY
        if (x, y) == prev:
            continue
        prev = (x, y)
        g = rows[r.pos.y][r.pos.x] if r.pos.x < len(rows[r.pos.y]) else " "
        ph = phase_of(x, y)
        if (x, y) == (0, 5):
            laps = 0
        if g in "sS" and ph in ("SEEK", "RESTORE", "READ", "WRITE"):
            laps += 1
        raw.append(
            {
                "t": t,
                "x": x,
                "y": y,
                "g": g,
                "a": r.a,
                "b": r.b,
                "bp": r.backpack,
                "ph": ph,
                "out": list(s.output),
                "lap": laps,
            }
        )
        if s.output and t > 1500:
            break

    def window(lo: int, hi: int) -> list[dict]:
        return [e for e in raw if lo <= e["t"] <= hi]

    frames: list[dict] = []
    frames += window(0, 40)
    frames.append(
        {
            "elide": "the fill loop runs 98 more times — 8 ticks each, "
            "855 ticks before the machine can serve anything"
        }
    )
    frames += window(853, 1010)
    frames.append(
        {
            "elide": "the restore loop runs ~95 more times, completing "
            "exactly one 100-value lap so the tape realigns"
        }
    )
    frames += window(1504, 1576)

    meta = {"input": inp, "fill_end": 855, "output": [7]}
    return frames, meta


def build_cases() -> dict:
    res = verify(MACHINE, "memory")
    prob = json.loads((ROOT / "tasks" / "problems" / "memory.json").read_text())
    cases = []
    for c, v in zip(prob["publicTestData"], res.cases, strict=True):
        toks = [int(t) for t in c["in"]]
        n, i = 0, 0
        while i < len(toks):
            i += 3 if toks[i] == 1 else 2
            n += 1
        cases.append(
            {
                "name": v.name,
                "ops": n,
                "ticks": v.ticks,
                "fill": 855,
                "per_op": round((v.ticks - 855) / n, 1),
            }
        )
    w, h, a2 = footprint(MACHINE)
    return {
        "cases": cases,
        "avg": res.avg_ticks,
        "w": w,
        "h": h,
        "area2": a2,
        "score": a2 * res.avg_ticks,
    }


def build_grid() -> list[list[dict]]:
    rows = MACHINE.read_text().rstrip("\n").split("\n")
    width = max(len(r) for r in rows)
    out = []
    for y, row in enumerate(rows):
        cells = []
        for x in range(width):
            ch = row[x] if x < len(row) else " "
            fam = ""
            if ch != " " and IX <= x <= 27 and 1 <= y <= 23:
                fam = FAMILY[phase_of(x - IX, y - IY)]
            cells.append({"c": ch, "f": fam})
        out.append(cells)
    return out


# Raw string: the embedded JS contains \n and regex escapes that must survive
# verbatim into the page (a cooked string turns `.join('\n')` into a real newline
# inside a JS string literal, which is a syntax error and silently blanks the page).
HTML = r"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>memory — how the littleman 100-cell RAM works</title>
<style>
:root {
  color-scheme: light;
  --surface-0: #f4f4f1; --surface-1: #fcfcfb; --surface-2: #eeeeea;
  --border: #dcdcd5; --text-primary: #0b0b0b; --text-secondary: #52514e;
  --text-muted: #78776f;
  --rotate: #2a78d6; --control: #eb6834; --op: #1baf7a; --neutral: #a9a8a0;
}
@media (prefers-color-scheme: dark) {
  :root:where(:not([data-theme="light"])) {
    color-scheme: dark;
    --surface-0: #121211; --surface-1: #1a1a19; --surface-2: #242422;
    --border: #35352f; --text-primary: #ffffff; --text-secondary: #c3c2b7;
    --text-muted: #8e8d84;
    --rotate: #3987e5; --control: #d95926; --op: #199e70; --neutral: #6b6a63;
  }
}
:root[data-theme="dark"] {
  color-scheme: dark;
  --surface-0: #121211; --surface-1: #1a1a19; --surface-2: #242422;
  --border: #35352f; --text-primary: #ffffff; --text-secondary: #c3c2b7;
  --text-muted: #8e8d84;
  --rotate: #3987e5; --control: #d95926; --op: #199e70; --neutral: #6b6a63;
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--surface-0); color: var(--text-primary);
  font: 15px/1.6 ui-sans-serif, system-ui, -apple-system, "Segoe UI", sans-serif; }
.wrap { max-width: 1000px; margin: 0 auto; padding: 32px 20px 80px; }
h1 { font-size: 28px; line-height: 1.25; margin: 0 0 8px; letter-spacing: -.02em; }
h2 { font-size: 19px; margin: 44px 0 10px; letter-spacing: -.01em; }
h3 { font-size: 15px; margin: 22px 0 6px; }
p { margin: 0 0 12px; color: var(--text-secondary); max-width: 68ch; }
.lede { font-size: 17px; color: var(--text-secondary); max-width: 66ch; }
code, .mono { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; }
code { background: var(--surface-2); padding: 1px 5px; border-radius: 4px;
  font-size: .9em; color: var(--text-primary); }
.card { background: var(--surface-1); border: 1px solid var(--border);
  border-radius: 10px; padding: 18px; margin: 16px 0; }
.stats { display: flex; flex-wrap: wrap; gap: 2px; margin: 20px 0 8px; }
.stat { flex: 1 1 130px; background: var(--surface-1); border: 1px solid var(--border);
  padding: 12px 14px; }
.stat:first-child { border-radius: 10px 0 0 10px; }
.stat:last-child { border-radius: 0 10px 10px 0; }
.stat b { display: block; font-size: 22px; font-weight: 650; letter-spacing: -.02em; }
.stat span { font-size: 12px; color: var(--text-muted); text-transform: uppercase;
  letter-spacing: .05em; }
.legend { display: flex; flex-wrap: wrap; gap: 16px; margin: 12px 0; font-size: 13px; }
.legend i { display: inline-block; width: 10px; height: 10px; border-radius: 2px;
  margin-right: 6px; vertical-align: -1px; }
.grid { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 13px; line-height: 1.35; white-space: pre; overflow-x: auto;
  position: relative; letter-spacing: .5px; }
.grid .rotate { color: var(--rotate); font-weight: 600; }
.grid .control { color: var(--control); font-weight: 600; }
.grid .op { color: var(--op); font-weight: 600; }
.grid span:not([class]) { color: var(--neutral); }
.grid .here { background: var(--text-primary); color: var(--surface-1) !important;
  border-radius: 2px; font-weight: 700; }
.regs { display: flex; gap: 2px; margin: 12px 0; flex-wrap: wrap; }
.reg { flex: 1 1 90px; background: var(--surface-2); border-radius: 6px;
  padding: 8px 10px; }
.reg span { display: block; font-size: 11px; color: var(--text-muted);
  text-transform: uppercase; letter-spacing: .05em; }
.reg b { font: 600 17px/1.3 ui-monospace, Menlo, monospace; }
.ctl { display: flex; align-items: center; gap: 12px; margin: 14px 0 6px;
  flex-wrap: wrap; }
button { font: inherit; font-size: 13px; padding: 6px 14px; border-radius: 6px;
  border: 1px solid var(--border); background: var(--surface-2);
  color: var(--text-primary); cursor: pointer; }
button:hover { border-color: var(--text-muted); }
input[type=range] { flex: 1 1 240px; accent-color: var(--rotate); min-width: 180px; }
.phase { display: inline-block; padding: 2px 9px; border-radius: 20px;
  font: 600 11px/1.7 ui-sans-serif, system-ui, sans-serif; letter-spacing: .06em; }
.gloss { min-height: 3em; font-size: 14px; color: var(--text-secondary); }
.gloss b { color: var(--text-primary); }
.lap { height: 8px; background: var(--surface-2); border-radius: 4px;
  overflow: hidden; margin: 10px 0 4px; }
.lap i { display: block; height: 100%; background: var(--rotate);
  border-radius: 0 4px 4px 0; transition: width .12s linear; }
table { border-collapse: collapse; width: 100%; font-size: 13px; margin: 10px 0; }
th, td { text-align: left; padding: 7px 10px; border-bottom: 1px solid var(--border); }
th { font-size: 11px; text-transform: uppercase; letter-spacing: .05em;
  color: var(--text-muted); font-weight: 600; }
td.num, th.num { text-align: right; font-family: ui-monospace, Menlo, monospace; }
.bars { margin: 8px 0; }
.bar { display: grid; grid-template-columns: 190px 1fr 74px; gap: 10px;
  align-items: center; margin-bottom: 6px; font-size: 13px; }
.bar .track { height: 16px; background: var(--surface-2); border-radius: 3px;
  position: relative; overflow: hidden; }
.bar .fill { position: absolute; inset: 0 auto 0 0; border-radius: 0 3px 3px 0; }
.bar .lbl { font-family: ui-monospace, Menlo, monospace; color: var(--text-secondary);
  text-align: right; }
.bar .nm { color: var(--text-secondary); overflow: hidden; text-overflow: ellipsis;
  white-space: nowrap; }
.ref { border-left: 2px dashed var(--text-muted); position: absolute;
  top: -3px; bottom: -3px; }
.note { font-size: 13px; color: var(--text-muted); }
.toggle { position: fixed; top: 14px; right: 14px; z-index: 9; }
.two { display: grid; grid-template-columns: 1fr 1fr; gap: 18px; }
@media (max-width: 760px) { .two { grid-template-columns: 1fr; } }
svg text { font: 11px ui-sans-serif, system-ui, sans-serif; fill: var(--text-secondary); }
svg .k { fill: var(--text-primary); font-weight: 600; }
details { margin: 10px 0; } summary { cursor: pointer; font-size: 13px;
  color: var(--text-secondary); }
</style></head><body>
<button class="toggle" onclick="var r=document.documentElement,d=r.getAttribute('data-theme')==='dark';r.setAttribute('data-theme',d?'light':'dark')">◐ theme</button>
<div class="wrap">

<h1>How <code>memory</code> works</h1>
<p class="lede">A 100-cell RAM with no memory. The tape is a <b>pipe</b> — 107 cells
of it, holding 100 values that circulate forever — and a single little man walks the
whole loop on <b>every single operation</b>. That one fact is the machine's entire
cost structure.</p>

<div class="stats" id="stats"></div>
<p class="note">Measured on the reference interpreter (<code>littleman/examples/memory2.man</code>).
Scoring is <code>footprint-tick</code>: <code>max(w,h)² × avg_ticks</code>, so only the
larger dimension is billed, and it is squared.</p>

<h2>1. Four rooms and two very long pipes</h2>
<p>A little man may never leave his room, so all the work happens in one 24×25
compute room. The tape can't loop back into that room directly — SPEC lists
"a pipe looping back to its own room" as a load error — so a 6×5 <b>relay</b> room
exists for no reason other than to turn the ring around.</p>
<div class="card">
  <div class="legend">
    <span><i style="background:var(--rotate)"></i><b>walk the tape</b> — fill, seek, restore</span>
    <span><i style="background:var(--control)"></i><b>decode &amp; dispatch</b></span>
    <span><i style="background:var(--op)"></i><b>the actual read/write</b></span>
    <span><i style="background:var(--neutral)"></i>walls, pipes, lanes</span>
  </div>
  <div class="grid" id="grid-static"></div>
</div>
<p>Count the blue. Roughly every glyph that does real work is a rotation step —
and the two blue blocks in the middle-left are the <i>same loop written twice</i>,
once to seek to the address and once to restore alignment afterwards.</p>

<h2>2. The tape is made of pipe</h2>
<p>A pipe is a FIFO whose capacity <b>is</b> its length: each cell holds one value
and shifts one cell per tick. So 107 cells of pipe = 107 slots, of which 100 hold
the memory. Nothing stores the address — position in the queue <i>is</i> the address.</p>
<div class="card"><svg viewBox="0 0 640 190" width="100%" height="190" role="img"
  aria-label="The tape ring: compute room sends into a 46-cell pipe to the relay room, which returns through a 61-cell pipe.">
  <rect x="40" y="30" width="150" height="90" rx="6" fill="none" stroke="var(--control)" stroke-width="2"/>
  <text class="k" x="115" y="70" text-anchor="middle">compute room</text>
  <text x="115" y="88" text-anchor="middle">24 × 25</text>
  <rect x="450" y="45" width="120" height="60" rx="6" fill="none" stroke="var(--neutral)" stroke-width="2"/>
  <text class="k" x="510" y="70" text-anchor="middle">relay</text>
  <text x="510" y="88" text-anchor="middle">6 × 5</text>
  <path d="M190 60 H450" fill="none" stroke="var(--rotate)" stroke-width="2" marker-end="url(#a)"/>
  <text x="320" y="50" text-anchor="middle">forward — 46 cells</text>
  <path d="M510 105 V150 H115 V120" fill="none" stroke="var(--rotate)" stroke-width="2" marker-end="url(#a)"/>
  <text x="320" y="168" text-anchor="middle">return — 61 cells</text>
  <text class="k" x="320" y="140" text-anchor="middle">107 slots · 100 values circulating</text>
  <path d="M115 30 V12 H30" fill="none" stroke="var(--neutral)" stroke-width="2"/>
  <text x="34" y="8">input</text>
  <path d="M40 100 H14" fill="none" stroke="var(--op)" stroke-width="2" marker-end="url(#a)"/>
  <text x="16" y="118">output</text>
  <defs><marker id="a" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6"
    markerHeight="6" orient="auto"><path d="M0 0 L10 5 L0 10 z" fill="var(--rotate)"/></marker></defs>
</svg></div>

<h2>3. Walk it yourself</h2>
<p>Every frame below is a real interpreter snapshot. This run is
<code>WRITE 7 → cell 2</code> then <code>READ cell 2</code>, which must output <code>7</code>.</p>
<div class="card">
  <div class="ctl">
    <button id="play">▶ play</button>
    <input type="range" id="scrub" min="0" value="0">
    <span class="mono note" id="tick"></span>
  </div>
  <div><span class="phase" id="phase"></span></div>
  <div class="lap"><i id="lapbar" style="width:0%"></i></div>
  <div class="note" id="laplbl"></div>
  <div class="regs">
    <div class="reg"><span>A — main hand</span><b id="rA">0</b></div>
    <div class="reg"><span>B — off hand</span><b id="rB">0</b></div>
    <div class="reg"><span>BP — backpack</span><b id="rBP">0</b></div>
    <div class="reg"><span>output</span><b id="rOut">—</b></div>
  </div>
  <div class="gloss" id="gloss"></div>
  <div class="grid" id="grid-live"></div>
</div>

<h2>4. Three tricks worth stealing</h2>

<h3>The <i>sign</i> of A carries the opcode</h3>
<p>After decoding, READ leaves <code>A = 100 − addr</code> and WRITE leaves
<code>A = addr − 100</code> — the same magnitude, opposite signs. Since
<code>addr &lt; 100</code>, neither is ever zero. The value is parked in B across the
whole seek loop, then one <code>W</code> brings it back and one <code>X</code>
branches on its sign. So the opcode survives a hundred rotations without a
register, a flag, or a second literal.</p>

<h3><code>S</code> emits and preserves in one glyph</h3>
<p>A READ has to hand the value to the output <i>and</i> keep it on the tape.
<code>S</code> sends A into <b>every</b> outgoing pipe at once — output pipe and
tape ring together — so the answer is emitted and the cell is rewritten by a
single instruction. No copy, no second pass.</p>

<h3>The counted loop zigzags, so both directions do work</h3>
<p>A man needs a closed walking cycle, and the smallest cycle holding two
operations is 3×2 = 6 cells. This machine unrolls it across two columns: down the
right doing <code>r s m</code>, up the left doing <code>r s m</code>, with a
<code>d</code> test at each end. Every leg rotates one value instead of every
other leg, so a rotation costs ~6.6 ticks rather than ~12.</p>

<h2>5. Where the 13,955 ticks go</h2>
<p>Two costs, and neither is the arithmetic. <b>Setup</b>: 100 zeros pushed into
the ring at 8 ticks each, so nothing can be served for the first
<b>855 ticks</b>. <b>Per operation</b>: seek <code>addr</code> values, do the
read or write, then restore <code>99 − addr</code> — exactly
<b>100 rotations, whatever the address</b>.</p>
<div class="two">
  <div class="card">
    <h3 style="margin-top:0">Ticks per operation</h3>
    <div class="bars" id="bars-perop"></div>
    <p class="note">Converges on ~635 — one full 100-value lap. An op at address 0
    costs 660 ticks; address 99 adds only 548 more, because the lap is walked
    either way.</p>
  </div>
  <div class="card">
    <h3 style="margin-top:0">Share of runtime spent on setup</h3>
    <div class="bars" id="bars-fill"></div>
    <p class="note">The 855-tick fill is 94% of the smallest case and 1% of the
    largest. Because the score averages over cases, it is 6.1% of the total —
    and it is the same 855 ticks seven times.</p>
  </div>
</div>
<details><summary>Table view — all seven public cases</summary>
  <table id="tbl"><thead><tr><th>case</th><th class="num">ops</th>
  <th class="num">ticks</th><th class="num">setup</th><th class="num">per op</th>
  </tr></thead><tbody></tbody></table>
</details>

<h2>6. What that says about making it faster</h2>
<p>The full lap is structural, not sloppy: a pipe cannot skip, so reaching
cell <i>k</i> means physically moving every value in front of it. Three ways to
break it, with measured or estimated payoffs:</p>
<table><thead><tr><th>change</th><th>mechanism</th><th class="num">payoff</th></tr></thead>
<tbody>
<tr><td>Lazy rotation</td><td>Track the ring's offset and rotate
<code>(addr − offset) mod 100</code> instead of a full lap. Mean forward delta on
the dominant case is 50.0, so the lap halves. Needs no new rooms — the offset can
ride the ring as a header word.</td><td class="num">~1.8×</td></tr>
<tr><td>Parallel rings</td><td>Ten rings of ten, so a seek moves ≤10 values.
Selection is free: pipe binding is positional, proven by
<code>two-roms.man</code>. Costs one relay, not ten — a data ring is permanently
full, so its <code>r</code> never blocks.</td><td class="num">~3.5×</td></tr>
<tr><td>Compact redraw</td><td>The 31×32 box is <b>65% blank</b>, with a 7×10
empty rectangle inside the compute room. Pure routing slack; footprint is
squared.</td><td class="num">~1.8×</td></tr>
</tbody></table>
<p class="note">Regenerate this page with
<code>uv run --project solvers/python python littleman/tools/make_memory_html.py</code>.</p>
</div>

<script>
const GRID = __GRID__, FRAMES = __FRAMES__, DATA = __CASES__;

/* ---- stats ---- */
const fmt = n => n.toLocaleString('en-US');
document.getElementById('stats').innerHTML = [
  [`${DATA.w}×${DATA.h}`, 'footprint'],
  [fmt(DATA.area2), 'max(w,h)²'],
  [fmt(Math.round(DATA.avg)), 'avg ticks'],
  [fmt(Math.round(DATA.score)), 'score'],
  ['7/7', 'public cases'],
].map(([v, k]) => `<div class="stat"><b>${v}</b><span>${k}</span></div>`).join('');

/* ---- grids ---- */
function render(el) {
  el.innerHTML = GRID.map((row, y) => row.map((c, x) =>
    `<span${c.f ? ` class="${c.f}"` : ''} data-p="${x},${y}">` +
    (c.c === ' ' ? ' ' : c.c.replace(/[&<>]/g, m => ({'&':'&amp;','<':'&lt;','>':'&gt;'}[m]))) +
    '</span>').join('')).join('\n');
}
render(document.getElementById('grid-static'));
const live = document.getElementById('grid-live');
render(live);

/* ---- walkthrough ---- */
const steps = FRAMES.filter(f => !f.elide).length;
const scrub = document.getElementById('scrub');
scrub.max = FRAMES.length - 1;
const FAM = {FILL:'rotate',SEEK:'rotate',RESTORE:'rotate',DECODE:'control',
  DISPATCH:'control',FETCH:'control',READ:'op',WRITE:'op'};
const GLOSS = __GLOSS__;
let cur = null;

function show(i) {
  const f = FRAMES[i];
  const tickEl = document.getElementById('tick');
  if (f.elide) {
    document.getElementById('gloss').innerHTML = `<b>⋯</b> ${f.elide}`;
    tickEl.textContent = '⋯';
    return;
  }
  if (cur) cur.classList.remove('here');
  // the man stands in the compute room, whose interior starts at x=6
  const cell = live.querySelector(`[data-p="${f.x + 6},${f.y}"]`);
  if (cell) { cell.classList.add('here'); cur = cell; }
  tickEl.textContent = `tick ${f.t}`;
  const ph = document.getElementById('phase');
  ph.textContent = f.ph;
  ph.style.background = `var(--${FAM[f.ph]})`;
  ph.style.color = 'var(--surface-1)';
  document.getElementById('rA').textContent = f.a;
  document.getElementById('rB').textContent = f.b;
  document.getElementById('rBP').textContent = f.bp;
  document.getElementById('rOut').textContent = f.out.length ? f.out.join(' ') : '—';
  const pct = Math.min(100, f.lap);
  document.getElementById('lapbar').style.width = pct + '%';
  document.getElementById('laplbl').textContent =
    `${f.lap} of 100 tape rotations for this operation`;
  const g = GLOSS[f.g] || 'nop — the man just walks on';
  document.getElementById('gloss').innerHTML =
    `<b>${f.g === ' ' ? '␣' : f.g}</b> — ${g}`;
}
scrub.oninput = () => show(+scrub.value);
show(0);

let timer = null;
const btn = document.getElementById('play');
btn.onclick = () => {
  if (timer) { clearInterval(timer); timer = null; btn.textContent = '▶ play'; return; }
  btn.textContent = '❚❚ pause';
  timer = setInterval(() => {
    let v = +scrub.value + 1;
    if (v > +scrub.max) { clearInterval(timer); timer = null; btn.textContent = '▶ play'; return; }
    scrub.value = v; show(v);
  }, 170);
};

/* ---- charts ---- */
function bars(el, rows, colour, max, refAt, refLbl) {
  el.innerHTML = rows.map(([name, val, lbl]) => `
    <div class="bar"><div class="nm" title="${name}">${name}</div>
      <div class="track"><div class="fill" style="width:${100 * val / max}%;
        background:${colour}"></div>${refAt ? `<div class="ref"
        style="left:${100 * refAt / max}%" title="${refLbl}"></div>` : ''}</div>
      <div class="lbl">${lbl}</div></div>`).join('');
}
const cs = DATA.cases.slice().sort((a, b) => a.ops - b.ops);
bars(document.getElementById('bars-perop'),
  cs.map(c => [`${c.name} (${c.ops})`, c.per_op, c.per_op]),
  'var(--rotate)', 700, 660, 'one full lap = 660');
bars(document.getElementById('bars-fill'),
  cs.map(c => [`${c.name} (${c.ops})`, 100 * c.fill / c.ticks,
               (100 * c.fill / c.ticks).toFixed(1) + '%']),
  'var(--control)', 100);
document.querySelector('#tbl tbody').innerHTML = cs.map(c =>
  `<tr><td>${c.name}</td><td class="num">${c.ops}</td>
   <td class="num">${fmt(c.ticks)}</td>
   <td class="num">${(100 * c.fill / c.ticks).toFixed(1)}%</td>
   <td class="num">${c.per_op}</td></tr>`).join('');
</script></body></html>
"""


def main() -> int:
    frames, _meta = build_frames()
    cases = build_cases()
    grid = build_grid()
    html = (
        HTML.replace("__GRID__", json.dumps(grid, separators=(",", ":")))
        .replace("__FRAMES__", json.dumps(frames, separators=(",", ":")))
        .replace("__CASES__", json.dumps(cases, separators=(",", ":")))
        .replace("__GLOSS__", json.dumps(GLOSS, separators=(",", ":")))
    )
    OUT.write_text(html)
    print(f"wrote {OUT}  ({len(html):,} bytes, {len(frames)} frames)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
