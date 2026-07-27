#!/usr/bin/env python3
"""Render `writeup.html` — the contest retrospective, as one self-contained file.

Three figures, all inline SVG so the page needs no network and no build step:

* a **timeline** of the three tracks (infrastructure, the LM-1 compiler, hand-built
  grids), with the pre-contest days shown as their own strip rather than as a long
  empty stretch of the same axis;
* one **decision tree per problem** — every idea we tried, marked shipped / dead
  end / parked, so the failures stay visible next to the wins;
* a **bar chart** of how far each problem moved from its first judged score to its
  best, on a log axis because the range is 1x to 3,457x.

Colours are the data-viz reference palette (categorical slots 1-3 for the tracks,
the fixed status palette for outcomes), validated for both modes; outcome colour is
always paired with a glyph and a word, never carrying meaning alone.

Usage: python littleman/tools/make_writeup_html.py [--out writeup.html]
"""

# ruff: noqa: E501 -- this file is a data table plus an embedded HTML/SVG template,
# where wrapping a node's label or an SVG element string hurts more than it helps.

from __future__ import annotations

import argparse
import html
from pathlib import Path

# ── figure 1: the timeline ───────────────────────────────────────────────────
# (hours from contest start, lane, label). Contest opened 2026-07-24 12:00.
LANES = [
    ("Infrastructure", "s1"),
    ("LM-1 compiler", "s2"),
    ("Hand-built grids", "s3"),
]
PREP = [
    (0, 0, "solver entrypoint, JSON envelopes"),
    (1, 0, "split solvers by language"),
]
EVENTS = [
    (0.2, 0, "interactive-solver protocol"),
    (3.5, 0, "littleman CLI + wasm runner"),
    (4.0, 1, "emulator + Python→grid transpiler"),
    (4.5, 1, "ring store, 4-pipe CPU, Z3 router"),
    (8.5, 1, "two-man SoC: CORE + DMA over a bus"),
    (10.0, 2, "memory: rotating pipe-tape, first accepted"),
    (10.5, 1, "LM-1 ISA, assembler, emulator"),
    (10.6, 2, "bespoke triangle, 8x8"),
    (11.0, 1, "synthesise a per-program CPU"),
    (14.3, 1, "brackets + tcp compiled"),
    (16.3, 1, "plotter, gradebook, sudoku"),
    (21.6, 0, "submit tool + score-named archive"),
    (23.5, 0, "compaction passes on any .man"),
    (27.0, 1, "dataflow survey: 46 ticks vs 1"),
    (31.5, 2, "tcp as a 17-word ring: 168x"),
    (35.0, 2, "snake coprocessor: 15.9e9 → 3.4e9"),
    (38.0, 1, "pathfinder bit-parallel BFS"),
    (41.9, 1, "little-little-man interpreter on the CPU"),
    (50.0, 2, "brackets 25x25, sudoku 20x20"),
    (58.0, 1, "LLM banked store: 2.36x"),
    (59.0, 2, "little-little-little-man ring"),
    (61.5, 1, "LLM buffered ROM corridor + drain"),
    (66.0, 2, "subset-sum two-room split"),
    (70.0, 2, "sort-numbers 14x14, reverse 14x14"),
    (71.5, 2, "tcp 17x17, matmul dense"),
    (74.0, 0, "manreroute: flow-graph rerouting"),
    (78.5, 1, "LLM ROM re-fold (last)"),
]
DAY_MARKS = [
    (0, "Fri 24 Jul 12:00"),
    (12, "24th 24:00"),
    (36, "25th 24:00"),
    (60, "26th 24:00"),
    (79, "deadline"),
]

# ── figure 2: one tree per problem ───────────────────────────────────────────
# node = (text, outcome, [children]); outcome: win | dead | step
TREES: list[tuple[str, str, tuple]] = [
    (
        "little-little-man",
        "163,823,101,714 · 180x179 · 28/28",
        (
            "Interpret a littleman program — needs a real store",
            "step",
            [
                (
                    "Write an interpreter for the LM-1 CPU",
                    "win",
                    [
                        ("Tape ring capped at 107 slots — needs 427", "dead", []),
                        (
                            "Serpentine tape ring, zero width cost",
                            "win",
                            [
                                ("Pack 4 cells/word: read-modify-write costs more", "dead", []),
                                (
                                    "Bank the store: hot man-memory + tape, 2.36x",
                                    "win",
                                    [
                                        ("Four-word tape skips", "win", []),
                                        (
                                            "Roll the three men into a loop — score-neutral",
                                            "dead",
                                            [],
                                        ),
                                    ],
                                ),
                            ],
                        ),
                        (
                            "56% of ticks is the CPU waiting out a ROM lap",
                            "step",
                            [
                                ("Faster discard drain alone: +0.18%", "dead", []),
                                ("Buffer the ROM corridor (a pipe is a FIFO)", "win", []),
                                ("Re-fold the ROM to the real bounding box: -13%", "win", []),
                                ("Two-bank ring repeater at 1 word/tick", "step", []),
                            ],
                        ),
                    ],
                )
            ],
        ),
    ),
    (
        "tcp",
        "535,084 · 17x17 · 3,457x",
        (
            "Reassemble a byte stream in order",
            "step",
            [
                (
                    "LM-1 program: 112x78, 1.85e9",
                    "step",
                    [
                        ("Compaction + ROM packing: -21%", "win", []),
                        (
                            "Rebuild as a 17-word ring machine: 168x",
                            "win",
                            [("Two-tape queue, 17x17", "win", [])],
                        ),
                    ],
                )
            ],
        ),
    ),
    (
        "brackets",
        "330,456 · 25x25 · 3,096x",
        (
            "Match nested brackets — a stack problem",
            "step",
            [
                ("A pipe cannot be a stack: pop-newest is O(depth)", "dead", []),
                (
                    "LM-1 program with the stack in the tape",
                    "step",
                    [
                        ("Hand ring, stack threaded through data cells", "step", []),
                        ("The whole stack in one register via /: 25x25", "win", []),
                        ("25x14 attempt: 16/26 cases", "dead", []),
                    ],
                ),
            ],
        ),
    ),
    (
        "sudoku-validity",
        "2,815,180 · 20x20 · 1,446x",
        (
            "Validate 27 units of a grid",
            "step",
            [
                ("LM-1: 27 unit masks, one lap per cell", "step", []),
                (
                    "Hand-built 20x20 ring, one mask per unit",
                    "win",
                    [("Bit-packing and a systolic variant — both lose", "dead", [])],
                ),
            ],
        ),
    ),
    (
        "plotter",
        "22,774,730 · 44x56 · 341x",
        (
            "Draw line segments on the LM-75 panel",
            "step",
            [
                ("LM-1 + display block: 6% over the step cap", "dead", []),
                ("Cut tape accesses per pixel — back under the cap", "win", []),
                ("Replace the CPU with a line-drawing block", "win", []),
            ],
        ),
    ),
    (
        "gradebook",
        "194,662,790 · 70x70 · 69x",
        (
            "Per-student aggregates over a stream",
            "step",
            [
                ("Tape cost modelled per slot — it is per access", "dead", []),
                ("Order lanes by opcode frequency", "win", []),
                ("Move the output room off the binding axis", "win", []),
                ("One-pass ring worker: 70x70", "win", []),
            ],
        ),
    ),
    (
        "matmul",
        "232,294,501 · 72x81 · 6.3x",
        (
            "Multiply two matrices — the STORE wall",
            "step",
            [
                ("LM-1 program: correct, but every element is a tape access", "step", []),
                ("STREAM: a third tier of rotate-only rings + fused MAC", "win", []),
                (
                    "Dense hand-built band machine",
                    "win",
                    [
                        ("MAC rectangle and one-row-per-pipe band are exclusive", "dead", []),
                        ("Phantom pipe mouths stalled it — four causes ruled out", "dead", []),
                        ("Systolic MAC chain, validated one stage deep", "step", []),
                    ],
                ),
            ],
        ),
    ),
    (
        "snake",
        "108,396,066 · 75x69 · 154x",
        (
            "Run a snake and paint every frame",
            "step",
            [
                ("LM-1 display machine: 123x129, 1.66e10", "step", []),
                (
                    "Move the body into a write-only coprocessor ring: 4.7x",
                    "win",
                    [("A coprocessor may own the display", "win", [])],
                ),
                ("Hand-built 75x69, fused ring laps", "win", []),
                ("8.8e7 built locally after the last submission window", "step", []),
            ],
        ),
    ),
    (
        "pathfinder",
        "10,636,538,807 · 82x173",
        (
            "BFS a maze and paint the path",
            "step",
            [
                ("Bitplane BFS, one word per row: 17/18", "dead", []),
                (
                    "Bit-parallel BFS on four 64-bit words: 18/18",
                    "win",
                    [
                        ("Cross-band placer wedges on real floorplans", "dead", []),
                        ("Band-grid layout with anchors from the loop order", "win", []),
                    ],
                ),
                ("Paint-only coprocessor variant — scaffolded, never finished", "step", []),
            ],
        ),
    ),
    (
        "subset-sum",
        "5,218,553,037 · 80x84 · 9.8x",
        (
            "Does a subset hit the target?",
            "step",
            [
                ("Dataflow survey said it was unlockable with two registers", "win", []),
                (
                    "One-room ring: 81x253 — rows are the scarce dimension",
                    "step",
                    [
                        ("Turn every ring rotation around: 92x153 → 92x128", "win", []),
                        ("Split into two rooms on one ring: 80x84", "win", []),
                    ],
                ),
            ],
        ),
    ),
    (
        "memory",
        "19,973,628 · 108x107 · 9.7x",
        (
            "A read/write store with N addresses",
            "step",
            [
                ("Rotating pipe-tape — first accepted solution of the contest", "win", []),
                ("31x31 tight ring: small but 84k ticks", "step", []),
                ("55x308 one-pass: 2.3k ticks but a huge box", "dead", []),
                ("Fold the one-pass worker into a 108x107 box", "win", []),
            ],
        ),
    ),
    (
        "sort-numbers + reverse-a-list",
        "413,066 · 34,535 · both 14x14",
        (
            "Two small list problems, one shape",
            "step",
            [
                ("25x25 / 19x18 rings straight off the primitives", "step", []),
                ("Feed the ring from the relay: 17x17 → 14x14", "win", []),
                ("Pair ring — two values per lap, 13x13 at 169", "win", []),
                ("Counterpipe variant: 2x worse, archived anyway", "dead", []),
                ("One loop test per lane, not per value", "win", []),
            ],
        ),
    ),
    (
        "little-little-little-man",
        "8,037,334,868 · 144x202 · 21/21",
        (
            "Interpret an LLM program — an interpreter for our interpreter",
            "step",
            [
                ("Pack 8 classes per word: needs three live values, we have two hands", "dead", []),
                ("One class per ring word — the decode body holds nothing", "win", []),
                ("Hold the word, not the lap: 5.6e10 → 7.4e9", "win", []),
            ],
        ),
    ),
    (
        "history-lesson",
        "8,100 · 90x90 · footprint-only",
        (
            "Emit 2,810 fixed tokens — scored on footprint alone",
            "step",
            [
                ("Rule-respecting ROM snake packer, base-128 words", "win", []),
                ("Speed is irrelevant here — check `scoring` before optimising", "win", []),
            ],
        ),
    ),
    (
        "triangle",
        "960 · 8x8 · 19/19",
        (
            "The smallest problem — hand-drawn on day one",
            "win",
            [("Proof that bespoke beats generated, before we could measure it", "win", [])],
        ),
    ),
]

# ── figure 3: first judged score → best judged score ───────────────────────
FACTORS = [
    ("tcp", 1_849_876_224, 535_084),
    ("brackets", 1_023_149_581, 330_456),
    ("sudoku-validity", 4_070_950_637, 2_815_180),
    ("plotter", 7_760_316_749, 22_774_730),
    ("snake", 16_647_839_451, 108_396_066),
    ("gradebook", 13_517_176_008, 194_662_790),
    ("reverse-a-list", 573_485, 34_535),
    ("subset-sum", 51_103_406_206, 5_218_553_037),
    ("memory", 194_145_056, 19_973_628),
    ("sort-numbers", 3_273_525, 413_066),
    ("matmul", 1_464_201_360, 232_294_501),
    ("little-little-man", 926_292_239_445, 163_823_101_714),
    ("history-lesson", 22_801, 8_100),
    ("pathfinder", 11_096_155_486, 10_636_538_807),
    ("little-little-little-man", 8_037_334_868, 8_037_334_868),
    ("triangle", 960, 960),
]

CHAR_W = 6.15  # 11px system sans, measured wide enough to never clip


def wrap(text: str, width: int) -> list[str]:
    out, line = [], ""
    for word in text.split(" "):
        cand = f"{line} {word}".strip()
        if len(cand) > width and line:
            out.append(line)
            line = word
        else:
            line = cand
    if line:
        out.append(line)
    return out


# ── tree layout: left-to-right, parents centred on their subtree ─────────────
NODE_W = [246, 250, 250, 236]
GAP_X, GAP_Y, PAD = 34, 9, 7
LINE_H = 14


class N:
    def __init__(self, spec, depth):
        text, outcome, kids = spec
        self.text, self.outcome, self.depth = text, outcome, depth
        self.w = NODE_W[min(depth, len(NODE_W) - 1)]
        self.lines = wrap(text, int((self.w - 2 * PAD - 16) / CHAR_W))
        self.h = len(self.lines) * LINE_H + 2 * PAD
        self.kids = [N(k, depth + 1) for k in kids]
        self.x = sum(NODE_W[min(d, len(NODE_W) - 1)] + GAP_X for d in range(depth))
        self.y = 0.0

    def place(self, top: float) -> float:
        if not self.kids:
            self.y = top
            return top + self.h + GAP_Y
        cur = top
        for k in self.kids:
            cur = k.place(cur)
        span = (self.kids[0].y, self.kids[-1].y + self.kids[-1].h)
        self.y = max(top, (span[0] + span[1]) / 2 - self.h / 2)
        return max(cur, self.y + self.h + GAP_Y)


def tree_svg(spec, idx: int) -> str:
    root = N(spec, 0)
    height = root.place(4)
    width = max(_right(root)) + 8
    parts = [
        f'<svg class="fig" viewBox="0 0 {width:.0f} {height:.0f}" width="100%" '
        f'style="max-width:{width:.0f}px" role="img" aria-labelledby="t{idx}">'
    ]
    parts.append(f'<title id="t{idx}">Decision tree</title>')
    parts += _edges(root)
    parts += _nodes(root)
    parts.append("</svg>")
    return "".join(parts)


def _right(n: N) -> list[float]:
    out = [n.x + n.w]
    for k in n.kids:
        out += _right(k)
    return out


def _edges(n: N) -> list[str]:
    out = []
    for k in n.kids:
        mid = n.x + n.w + GAP_X / 2
        out.append(
            f'<path class="edge" d="M{n.x + n.w:.0f} {n.y + n.h / 2:.0f} H{mid:.0f} '
            f'V{k.y + k.h / 2:.0f} H{k.x:.0f}"/>'
        )
        out += _edges(k)
    return out


GLYPH = {"win": "✓", "dead": "✗", "step": "·"}


def _nodes(n: N) -> list[str]:
    out = [
        f'<g class="node {n.outcome}">'
        f'<rect x="{n.x:.0f}" y="{n.y:.0f}" width="{n.w}" height="{n.h}" rx="5"/>'
        f'<rect class="flag" x="{n.x:.0f}" y="{n.y:.0f}" width="3" height="{n.h}" rx="1.5"/>'
        f'<text class="glyph" x="{n.x + PAD + 3:.0f}" y="{n.y + PAD + 11:.0f}">{GLYPH[n.outcome]}</text>'
    ]
    for i, line in enumerate(n.lines):
        out.append(
            f'<text x="{n.x + PAD + 16:.0f}" y="{n.y + PAD + 11 + i * LINE_H:.0f}">{html.escape(line)}</text>'
        )
    out.append("</g>")
    for k in n.kids:
        out += _nodes(k)
    return out


# ── timeline ─────────────────────────────────────────────────────────────────
def timeline_svg() -> str:
    prep_w, gap, main_w, right_pad = 186, 46, 980, 150
    x0, top, row_h = 126, 58, 36
    total = x0 + prep_w + gap + main_w + right_pad

    def mx(hours: float) -> float:
        return x0 + prep_w + gap + (hours / 80.0) * main_w

    # pass 1: assign every event a row inside its lane, treating a label as the
    # box it actually occupies (flipped labels extend left), then size each lane
    # to the rows it needs — otherwise a busy hour spills into the lane below.
    flip_at = x0 + prep_w + gap + main_w - 40
    label_w = 162.0
    placed: list[tuple[float, int, int, list[str], bool]] = []
    used: dict[int, list[tuple[float, float, int]]] = {i: [] for i in range(len(LANES))}
    for hours, lane, label in EVENTS:
        x, lines = mx(hours), wrap(label, 24)
        end = x > flip_at
        lo, hi = (x - label_w, x) if end else (x, x + label_w)
        row = 0
        while any(pr == row and plo < hi and lo < phi for plo, phi, pr in used[lane]):
            row += 1
        used[lane].append((lo, hi, row))
        placed.append((x, lane, row, lines, end))
    rows_in = {
        i: max([r for _, ln, r, _, _ in placed if ln == i] + [0]) + 1 for i in range(len(LANES))
    }
    rows_in[0] = max(rows_in[0], len(PREP))
    lane_y, y_cur = {}, top
    for i in range(len(LANES)):
        lane_y[i] = y_cur
        y_cur += 22 + rows_in[i] * row_h + 8
    height = y_cur + 26

    p = [
        f'<svg class="fig" viewBox="0 0 {total} {height}" width="100%" style="max-width:{total}px" '
        'role="img" aria-labelledby="tl-t"><title id="tl-t">Timeline of the three tracks</title>'
    ]
    # lane bands + labels (labels are the direct-label relief for the light aqua)
    for i, (name, cls) in enumerate(LANES):
        y = lane_y[i]
        p.append(
            f'<rect class="lane" x="{x0}" y="{y}" width="{total - x0 - 16}" '
            f'height="{22 + rows_in[i] * row_h}" rx="6"/>'
        )
        p.append(
            f'<text class="lane-label {cls}" x="{x0 - 12}" y="{y + 20}" text-anchor="end">{name}</text>'
        )
    # the pre-contest strip, deliberately its own scale
    p.append(
        f'<rect class="prep" x="{x0}" y="{top - 26}" width="{prep_w}" height="{height - top - 4}" rx="6"/>'
    )
    p.append(
        f'<text class="axis" x="{x0 + prep_w / 2}" y="{top - 44}" text-anchor="middle">20–22 Jul · pre-contest</text>'
    )
    midy = (top + height) / 2
    p.append(
        f'<text class="axis" x="{x0 + prep_w + gap / 2}" y="{midy:.0f}" text-anchor="middle" '
        f'transform="rotate(-90 {x0 + prep_w + gap / 2} {midy:.0f})">scale break</text>'
    )
    for i, (_, lane, label) in enumerate(PREP):
        y = lane_y[lane] + 22 + i * row_h
        cx = x0 + 14
        p.append(f'<circle class="dot {LANES[lane][1]}" cx="{cx}" cy="{y}" r="4.5"/>')
        for j, line in enumerate(wrap(label, 22)):
            p.append(
                f'<text class="ev" x="{cx + 9}" y="{y + 4 + j * 12}">{html.escape(line)}</text>'
            )
    # contest axis
    for hours, label in DAY_MARKS:  # noqa: B007
        x = mx(hours)
        p.append(
            f'<line class="grid" x1="{x:.0f}" y1="{top - 22}" x2="{x:.0f}" y2="{height - 30}"/>'
        )
        p.append(
            f'<text class="axis" x="{x:.0f}" y="{top - 30}" text-anchor="middle">{label}</text>'
        )
    # pass 2: draw the events where pass 1 put them
    for x, lane, row, lines, end in placed:
        y = lane_y[lane] + 22 + row * row_h
        title = html.escape(" ".join(lines))
        p.append(
            f'<circle class="dot {LANES[lane][1]}" cx="{x:.0f}" cy="{y}" r="4.5"><title>{title}</title></circle>'
        )
        for j, line in enumerate(lines):
            anchor = ' text-anchor="end"' if end else ""
            tx = x - 9 if end else x + 9
            p.append(
                f'<text class="ev" x="{tx:.0f}" y="{y + 4 + j * 12}"{anchor}>{html.escape(line)}</text>'
            )
    p.append("</svg>")
    return "".join(p)


# ── bar chart ────────────────────────────────────────────────────────────────
def bars_svg() -> str:
    import math

    rows = sorted(((n, a / b) for n, a, b in FACTORS), key=lambda r: -r[1])
    left, right, bar_h, gap = 176, 92, 17, 9
    plot = 560
    height = 44 + len(rows) * (bar_h + gap)
    total = left + plot + right

    def bx(f: float) -> float:
        return left + max(0.0, math.log10(max(f, 1.0)) / math.log10(4000.0)) * plot

    p = [
        f'<svg class="fig" viewBox="0 0 {total} {height}" width="100%" style="max-width:{total}px" '
        'role="img" aria-labelledby="bars-t"><title id="bars-t">Improvement factor per problem</title>'
    ]
    for tick in (1, 10, 100, 1000):
        x = bx(tick)
        p.append(f'<line class="grid" x1="{x:.0f}" y1="26" x2="{x:.0f}" y2="{height - 14}"/>')
        p.append(f'<text class="axis" x="{x:.0f}" y="18" text-anchor="middle">{tick}×</text>')
    for i, (name, f) in enumerate(rows):
        y = 32 + i * (bar_h + gap)
        w = max(bx(f) - left, 2.0)
        p.append(
            f'<text class="cat" x="{left - 12}" y="{y + 12}" text-anchor="end">{html.escape(name)}</text>'
        )
        p.append(
            f'<rect class="bar" x="{left}" y="{y}" width="{w:.1f}" height="{bar_h}" rx="4">'
            f"<title>{html.escape(name)}: {f:,.1f}x</title></rect>"
        )
        p.append(f'<text class="val" x="{left + w + 8:.0f}" y="{y + 12}">{f:,.1f}×</text>')
    p.append(f'<line class="baseline" x1="{left}" y1="26" x2="{left}" y2="{height - 14}"/>')
    p.append("</svg>")
    return "".join(p)


CSS = """
/* The roles live on :root, not on .viz-root, because the page plane and the body
   ink need them too — custom properties inherit down, never up. */
:root{
  color-scheme:light;
  --surface-1:#fcfcfb; --plane:#f9f9f7;
  --text-primary:#0b0b0b; --text-secondary:#52514e; --muted:#898781;
  --grid:#e1e0d9; --baseline:#c3c2b7; --border:rgba(11,11,11,0.10);
  --s1:#2a78d6; --s2:#eb6834; --s3:#1baf7a;
  --good:#0ca30c; --critical:#d03b3b;
}
@media (prefers-color-scheme:dark){
  :root:where(:not([data-theme="light"])){
    color-scheme:dark;
    --surface-1:#1a1a19; --plane:#0d0d0d;
    --text-primary:#fff; --text-secondary:#c3c2b7; --muted:#898781;
    --grid:#2c2c2a; --baseline:#383835; --border:rgba(255,255,255,0.10);
    --s1:#3987e5; --s2:#d95926; --s3:#199e70;
  }
}
:root[data-theme="dark"]{
  color-scheme:dark;
  --surface-1:#1a1a19; --plane:#0d0d0d;
  --text-primary:#fff; --text-secondary:#c3c2b7; --muted:#898781;
  --grid:#2c2c2a; --baseline:#383835; --border:rgba(255,255,255,0.10);
  --s1:#3987e5; --s2:#d95926; --s3:#199e70;
}
html,body{background:var(--plane);margin:0}
body{font:15px/1.55 system-ui,-apple-system,"Segoe UI",sans-serif;color:var(--text-primary)}
.viz-root{max-width:1180px;margin:0 auto;padding:34px 22px 80px}
h1{font-size:27px;margin:0 0 6px;letter-spacing:-0.01em}
h2{font-size:19px;margin:40px 0 6px;letter-spacing:-0.01em}
h3{font-size:15px;margin:26px 0 2px}
p,li{color:var(--text-secondary);max-width:78ch}
a{color:var(--s1)}
.sub{color:var(--muted);margin-top:0}
.card{background:var(--surface-1);border:1px solid var(--border);border-radius:10px;padding:14px 16px;margin:14px 0;overflow-x:auto}
.tree-head{display:flex;gap:12px;align-items:baseline;flex-wrap:wrap;margin-bottom:2px}
.tree-head b{font-size:15px}
.tree-head span{color:var(--muted);font-size:13px;font-variant-numeric:tabular-nums}
.legend{display:flex;gap:18px;flex-wrap:wrap;color:var(--text-secondary);font-size:13px;margin:8px 0 0}
.legend i{font-style:normal;font-weight:700;margin-right:5px}
.legend .g{color:var(--good)} .legend .c{color:var(--critical)} .legend .m{color:var(--muted)}
svg.fig{display:block;margin:6px 0}
svg text{font:11px system-ui,-apple-system,"Segoe UI",sans-serif;fill:var(--text-primary)}
svg .axis,svg .ev{fill:var(--muted)}
svg .cat{fill:var(--text-secondary)}
svg .val{fill:var(--text-secondary);font-variant-numeric:tabular-nums}
svg .grid{stroke:var(--grid);stroke-width:1}
svg .baseline{stroke:var(--baseline);stroke-width:1}
svg .lane{fill:var(--surface-1);stroke:var(--border)}
svg .prep{fill:none;stroke:var(--grid);stroke-dasharray:3 3}
svg .lane-label{font-weight:600;font-size:12px}
svg .lane-label.s1{fill:var(--s1)} svg .lane-label.s2{fill:var(--s2)} svg .lane-label.s3{fill:var(--s3)}
svg .dot.s1{fill:var(--s1)} svg .dot.s2{fill:var(--s2)} svg .dot.s3{fill:var(--s3)}
svg .dot{stroke:var(--surface-1);stroke-width:2}
svg .edge{fill:none;stroke:var(--baseline);stroke-width:1.5}
svg .node rect{fill:var(--surface-1);stroke:var(--border)}
svg .node .flag{stroke:none}
svg .node.win .flag{fill:var(--good)} svg .node.dead .flag{fill:var(--critical)} svg .node.step .flag{fill:var(--muted)}
svg .node.win .glyph{fill:var(--good)} svg .node.dead .glyph{fill:var(--critical)} svg .node.step .glyph{fill:var(--muted)}
svg .glyph{font-weight:700}
svg .bar{fill:var(--s1)}
svg .bar:hover{fill:var(--s2)}
table{border-collapse:collapse;font-size:13px;margin-top:8px}
th,td{text-align:left;padding:3px 14px 3px 0;border-bottom:1px solid var(--grid);color:var(--text-secondary)}
td.n{font-variant-numeric:tabular-nums;text-align:right}
details{margin-top:10px;color:var(--muted);font-size:13px}
button{font:13px system-ui;color:var(--text-secondary);background:var(--surface-1);border:1px solid var(--border);
  border-radius:7px;padding:5px 11px;cursor:pointer;float:right}
"""

PROSE_TOP = """
<button onclick="var r=document.documentElement;r.dataset.theme=r.dataset.theme==='dark'?'light':'dark'">
theme</button>
<h1>Four days inside a 2D grid</h1>
<p class="sub">ICFP Contest 2026 &middot; team <b>randomfunkstyle</b> &middot; 16 problems solved, every public
and private case passing &middot; <a href="https://github.com/randomfunkstyle/randomfun2026solvers">the repository</a></p>
<p>The task was to write programs in <b>littleman</b>: a 2D ASCII language where little men walk a grid of rooms,
carry two values in their hands, and talk through pipes that are FIFOs whose capacity is their length. Score is
<code>max(width, height)&sup2; &times; average ticks</code>, lower better, so a program is charged for the box it
occupies and the time it runs.</p>
<p>We took two runs at it, in this order and on purpose: <b>build a CPU and compile to it</b> so that every problem
is solved and every test-case point is banked, then <b>hand-build dataflow grids</b> for the problems where the
ranking half of the points was still on the table. The compiler earned the coverage; the hand-built grids earned
up to 3,457&times; on the score.</p>
"""

PROSE_TIMELINE = """
<h2>How it went</h2>
<p>Three tracks ran in parallel for most of the contest, in separate git worktrees with agents driving each one.
The pre-contest days are on their own strip: they were spent on a batch/interactive solver harness for a contest
that turned out not to work that way at all.</p>
"""

PROSE_TREES = """
<h2>Per problem: what we tried</h2>
<p>Every branch we actually built is here, including the ones that lost — a measured negative is worth as much
as a win, and most of them looked obvious before they were measured.</p>
<div class="legend">
  <span class="g"><i>✓</i>shipped / measured win</span>
  <span class="c"><i>✗</i>dead end, measured</span>
  <span class="m"><i>·</i>step or parked idea</span>
</div>
"""

PROSE_BARS = """
<h2>How far each problem moved</h2>
<p>First judged score divided by best judged score. The axis is logarithmic — the range runs from
1&times; to 3,457&times; — so every bar carries its own value; read the numbers, not the lengths.
The three problems at 1.0&times; were submitted once and never beaten: <code>triangle</code> was already an 8&times;8
hand-drawing, and the two interpreters landed too late to iterate on.</p>
"""

PROSE_GENERAL = """
<h2>Ideas that generalise</h2>
<ol>
<li><b>A walked glyph costs 1 tick; an issued CPU instruction costs 46.</b> That single ratio, measured on day two
(<a href="https://github.com/randomfunkstyle/randomfun2026solvers/blob/main/littleman/DATAFLOW-SURVEY.md">DATAFLOW-SURVEY.md</a>),
decided the second half of the contest. A compiler buys correctness; only hand-built dataflow buys rank.</li>
<li><b>The short side of the box is free.</b> Score charges <code>max(w,h)&sup2;</code>, so narrowing an already-narrow
machine is worth exactly zero, and a row of the binding dimension is never worth spending to shorten a pipe.</li>
<li><b>Pipes are the memory.</b> Capacity equals length, so a corridor is a queue you already paid for; a ring needs
<code>payload + 1</code> cells and deadlocks <i>silently</i> below that. A pipe cannot be a stack — pack the stack
into an integer instead, which is what took <code>brackets</code> from 9,604 to 625 area.</li>
<li><b>Every ring costs a turnaround room</b> — about 6 ticks a word, whatever the worker does. Measure the
producer before optimising the consumer: on <code>little-little-man</code> a 1.6&times; faster drain bought 0.18%,
because the ROM man was the one setting the pace.</li>
<li><b>Nearest-pipe binding is geometry, not readiness.</b> A misplaced room reads the wrong pipe with no error at
all, so every layout move is followed by a binding check.</li>
<li><b>Re-measure every inherited constant.</b> Ring rotation cost was 1.9&times; optimistic, the tape's cost was
per-access and not per-slot, and <code>plotter</code> shipped 6% over the step cap on a number nobody had run.
Three of our biggest wins were just correcting a number we had assumed.</li>
<li><b>Build the verifier you can afford to run.</b> The bundled wasm engine OOMs on our largest machines, so a
native tick loop was worth more than any single optimisation — it made a 90-second sweep of 14 cases routine.</li>
<li><b>Make the submission tool unable to lose work.</b> Every graded grid is archived under its server-verified
score, so a listing sorts best-first and a worse run can never overwrite a better one.</li>
</ol>
<h2>What we would do differently</h2>
<ul>
<li>Start the hand-built track on day one for the small problems. <code>triangle</code> was hand-drawn in the
first hours and never beaten; the same instinct applied to <code>tcp</code> a day earlier would have been worth
thousands of times its score.</li>
<li>Keep a single source of truth for each machine's <i>real</i> bounding box. The last win of the contest
(&minus;13% on <code>little-little-man</code>) was entirely a stale constant that had priced the height of a
machine that had since gotten shorter.</li>
<li>Submit earlier and more often. The judge's tick average ran a consistent 1.098&times; our local one; we only
established that on the final day, and until then every decision carried an unnecessary error bar.</li>
</ul>
"""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", type=Path, default=Path("writeup.html"))
    args = ap.parse_args()

    body = [PROSE_TOP, PROSE_TIMELINE, f'<div class="card">{timeline_svg()}</div>', PROSE_TREES]
    for i, (name, headline, spec) in enumerate(TREES):
        body.append(
            f'<div class="card"><div class="tree-head"><b>{html.escape(name)}</b>'
            f"<span>{html.escape(headline)}</span></div>{tree_svg(spec, i)}</div>"
        )
    rows = sorted(((n, a, b, a / b) for n, a, b in FACTORS), key=lambda r: -r[3])
    table = "".join(
        f"<tr><td>{html.escape(n)}</td><td class='n'>{a:,}</td><td class='n'>{b:,}</td>"
        f"<td class='n'>{f:,.1f}&times;</td></tr>"
        for n, a, b, f in rows
    )
    body += [
        PROSE_BARS,
        f'<div class="card">{bars_svg()}</div>',
        "<details><summary>the same numbers as a table</summary>"
        "<table><tr><th>problem</th><th>first judged</th><th>best judged</th><th>factor</th></tr>"
        f"{table}</table></details>",
        PROSE_GENERAL,
        '<p class="sub">Generated by <code>littleman/tools/make_writeup_html.py</code> · prose in '
        '<a href="https://github.com/randomfunkstyle/randomfun2026solvers/blob/main/WRITEUP.md">WRITEUP.md</a></p>',
    ]
    doc = (
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        "<title>ICFP Contest 2026 — randomfunkstyle writeup</title>"
        f"<style>{CSS}</style></head><body><div class='viz-root'>{''.join(body)}</div></body></html>"
    )
    args.out.write_text(doc, encoding="utf-8")
    print(
        f"{args.out}: {len(doc):,} bytes, {len(TREES)} trees, {len(EVENTS) + len(PREP)} timeline events"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
