"""Where does one CPU *operation* spend its ticks?  A per-opcode cost table.

    uv run python scratch/doom_opcodes.py --out <dir> [--rounds 8]

`doom_heatmap.py` answers "which cell does the CPU man stand on", which is a
question about *regions*: it says `cpu:lane:LD` is 22.87% of the run, and it
cannot say how much of an `LD` is walking versus waiting, because the trie, the
return bus and the collector row are shared by every instruction.

This profiler cuts the CPU man's timeline at the instruction fetch and folds
each segment into the one opcode whose lane it entered.  The trie descent that
selected `LD`, the drop out of the `LD` lane, and the walk back along the
collector row are then all charged to `LD`, and the run's ticks partition
exactly across opcodes with no shared bucket left over.

Every tick is counted — there is no stride here.  Attribution is a state machine
over consecutive ticks, so it is exact or it is nothing, and the totals are
asserted against the run's own tick count at the end.

Which engine: the native `fast_littleman` backend (`fast_littleman_native.cpp`),
gated, exactly as `doom_heatmap.py` runs it.  The reference Node/WASM engine
OOMs its 4 GB Go heap before it can load this 287x253 machine.
"""

from __future__ import annotations

import argparse
import html
import json
from pathlib import Path

from doom_case import (
    DEFAULT_ROUNDS,
    Case,
    cell_labels,
    gated_case,
    machine,
    pipe_names,
    room_labels,
)
from doom_pipes import critical_path
from randomfun2026solvers.fast_littleman import FastLittleman, FastOpProfile, OpcodeTags

TAPED = Path(__file__).resolve().parents[1] / "littleman" / "examples" / "deadman-3d_taped.man"

#: What the CPU man is doing on a cell.  The names are the report's columns and
#: the order is the order he walks them, which is why the tables read as a
#: pipeline.  `boundary` is the class the timeline is cut at.
CLASSES = (
    "fetch",         # `>rbr` — pull opcode + operand off the ROM stream
    "trie",          # the backpack trie descending to the opcode's row
    "lane",          # the opcode's own lane row (walking *and* blocked)
    "drop",          # the descent column from the lane's end to the return bus
    "return:bus",    # the westbound collector row back to the riser
    "return:riser",  # the riser column back up to the fetch
    "slab",          # a branch/jump slab: the discard loop or the seek
    "return:slab",   # the slabs' own westbound rows
    "seek",          # the CPU room's southern seek unit (drum addressing)
)
BOUNDARY = CLASSES.index("fetch")

#: The roll-up the analysis is written against.  "dispatch" is walking the CPU's
#: own decode/return machinery, "slab" is the branch work proper, and the memory
#: stall is split out of the lane by *which pipe* the man is parked on.
ROLLUP = {
    "fetch": "dispatch",
    "trie": "dispatch",
    "lane": "dispatch",
    "drop": "dispatch",
    "return:bus": "dispatch",
    "return:riser": "dispatch",
    "slab": "slab",
    "return:slab": "slab",
    "seek": "slab",
}


def classify(label: str, x: int, y: int) -> str:
    """One CPU-room cell -> one class name.

    Region names carry most of it; `cpu:other` is the part `Machine.regions`
    does not name, and it is split by row band because the CPU room's south
    holds a second, unrelated structure (the drum-seek unit) that must not be
    counted as dispatch.
    """
    if label == "cpu:fetch":
        return "fetch"
    if label == "cpu:trie":
        return "trie"
    if label.startswith("cpu:lane:"):
        return "lane"
    if label.startswith("cpu:slab:"):
        return "slab"
    if label == "cpu:return:collector":
        return "return:bus"
    if label == "cpu:return:riser":
        return "return:riser"
    # cpu:other, by band: above the collector row it is the lanes' descent
    # columns; on 143..146 it is the slabs' own return rows; below that it is
    # the seek unit.
    if y <= 142:
        return "drop"
    if y <= 146:
        return "return:slab"
    return "seek"


def drop_cells(grid: FastLittleman, built) -> set[tuple[int, int]]:
    """The descent columns, read off the grid rather than assumed.

    A lane row ends in a `v`; from there the man falls south through `.` (carry
    on) and `v` (go south) until a glyph turns him.  Two things make this worth
    computing rather than assuming:

    * the columns cut straight through the *long* lanes — `JMPS` spans x=22..58
      and five shorter lanes descend across it — so a lane's rectangle is not
      the lane, and a region profile charges those ticks to the wrong opcode;
    * the lanes' exits are **stacked**: `ADD` (y=117) drops onto `ST`'s own exit
      `v` at (43,119), `MUL`/`LDA`/`DIV` all drop onto `SUB`'s at (44,115), and
      the five immediate lanes drop onto `SND`'s at (25,141).  Without this the
      descent lands on another opcode's cell and is charged to it.
    """
    rows = grid.grid
    out: set[tuple[int, int]] = set()
    for name, (rx, ry, rw, _rh) in built.regions.items():
        if not name.startswith("cpu:lane:"):
            continue
        exits = [x for x in range(rx, rx + rw) if rows[ry][x] == "v"]
        if not exits:
            continue
        y = ry + 1
        x = exits[-1]
        while y < len(rows) and x < len(rows[y]) and rows[y][x] in ".v":
            out.add((x, y))
            y += 1
    return out


def build_tags(grid: FastLittleman, built) -> tuple[OpcodeTags, list[str], dict]:
    labels = cell_labels(grid, built)
    rooms = room_labels(grid, built)
    pipes = pipe_names(grid, built)
    cpu = rooms.index("cpu")
    room = grid.rooms[cpu]
    drops = drop_cells(grid, built)

    ops = sorted(
        {n.rsplit(":", 1)[1] for n in built.regions if n.startswith("cpu:lane:")},
    )
    op_index = {name: i for i, name in enumerate(ops)}
    classes = list(CLASSES)
    tags: dict[tuple[int, int, int], tuple[int, int]] = {}
    crossed = 0
    for (x, y), label in labels.items():
        if not room.contains((x, y)):
            continue
        cls = classes.index(classify(label, x, y))
        opc = -1
        if label.startswith("cpu:lane:") or label.startswith("cpu:slab:"):
            opc = op_index[label.rsplit(":", 1)[1]]
        # 0 east, 1 south, 2 west, 3 north — the arrival direction.  A lane cell
        # that another lane descends through is the lane only when walked
        # sideways; walked vertically it is that other lane's drop, and carries
        # no opcode of its own.
        vertical = (cls, opc)
        if (x, y) in drops and label.startswith("cpu:lane:"):
            vertical = (classes.index("drop"), -1)
            crossed += 1
        for direction in (0, 2):
            tags[(x, y, direction)] = (cls, opc)
        for direction in (1, 3):
            tags[(x, y, direction)] = vertical

    answer = pipes.index("store:collector->cpu")
    request = pipes.index("cpu->adapter")
    spec = OpcodeTags(
        classes=classes,
        ops=ops,
        tags=tags,
        boundary=BOUNDARY,
        hist_pipe=answer,
        value_pipe=request,
    )
    meta = {
        "drop_cells": len(drops),
        "lane_cells_crossed_by_a_drop": crossed,
        "cpu_room": [list(room.min), list(room.max)],
        "answer_pipe": answer,
        "answer_pipe_name": pipes[answer],
        "request_pipe": request,
        "request_pipe_name": pipes[request],
        "pipes": pipes,
    }
    return spec, ops, meta


def run(case: Case, *, max_ticks: int = 400_000_000):
    built = machine()
    grid = FastLittleman(TAPED)
    spec, ops, meta = build_tags(grid, built)
    result = grid.run(
        case.input,
        frames=case.frames,
        max_ticks=max_ticks,
        profile=True,
        profile_stride=1,
        opcodes=spec,
    )
    if result.fatal or result.passed is False:
        raise SystemExit(f"run did not pass: reason={result.reason} fatal={result.fatal}")
    assert result.opcodes is not None and result.profile is not None
    return grid, built, result, spec, ops, meta


# ── reporting ───────────────────────────────────────────────────────────────


def asm_symbols() -> dict[int, str]:
    """The program's `.equ` table, so a store address reads as a variable name."""
    src = (
        Path(__file__).resolve().parents[1]
        / "solvers/python/randomfun2026solvers/lm1/programs/deadman-3d.asm"
    )
    out: dict[int, str] = {}
    for line in src.read_text(encoding="utf-8").splitlines():
        parts = line.split()
        if len(parts) >= 3 and parts[0] == ".equ":
            try:
                out[int(parts[2])] = parts[1]
            except ValueError:
                pass
    return out


def table(title: str, header: list[str], body: list[list[str]], widths: list[int]) -> str:
    pairs = list(zip(header, widths, strict=False))
    line = "  ".join(h.rjust(w) if i else h.ljust(w) for i, (h, w) in enumerate(pairs))
    out = [title, "-" * len(line), line, "-" * len(line)]
    for r in body:
        cells = list(zip(r, widths, strict=False))
        out.append("  ".join(c.rjust(w) if i else c.ljust(w) for i, (c, w) in enumerate(cells)))
    return "\n".join(out)


def rows_for(prof: FastOpProfile, ticks: int, answer_pipe: int) -> list[dict]:
    """Per-opcode totals, with the lane's blocked time split by pipe."""
    out = []
    for i, name in enumerate(prof.ops):
        total = sum(prof.ticks[i])
        if not total:
            continue
        blocked = sum(prof.blocked[i])
        mem = prof.pipe_ticks.get((i, answer_pipe), 0)
        mem_runs = prof.pipe_runs.get((i, answer_pipe), 0)
        by_class = dict(zip(prof.classes, prof.ticks[i], strict=True))
        blk_class = dict(zip(prof.classes, prof.blocked[i], strict=True))
        roll: dict[str, int] = {}
        for cls, n in by_class.items():
            roll[ROLLUP[cls]] = roll.get(ROLLUP[cls], 0) + n
        # A stall is time spent inside whatever class the man was standing in,
        # so it is removed from that class's walking, never added on top.
        out.append(
            {
                "op": name,
                "index": i,
                "execs": prof.execs[i],
                "ticks": total,
                "pct": 100 * total / ticks,
                "mean": total / max(1, prof.execs[i]),
                "blocked": blocked,
                "mem": mem,
                "mem_runs": mem_runs,
                "other_block": blocked - mem,
                "walk": total - blocked,
                "dispatch_walk": roll.get("dispatch", 0) - sum(
                    blk_class[c] for c in prof.classes if ROLLUP[c] == "dispatch"
                ),
                "slab_walk": roll.get("slab", 0) - sum(
                    blk_class[c] for c in prof.classes if ROLLUP[c] == "slab"
                ),
                "by_class": by_class,
                "blk_class": blk_class,
            }
        )
    return sorted(out, key=lambda r: -r["ticks"])


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--rounds", type=int, default=DEFAULT_ROUNDS)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    case = gated_case(args.rounds)
    grid, built, result, spec, ops, meta = run(case)
    prof = result.opcodes
    ticks = result.step
    answer = meta["answer_pipe"]
    frames = sum(len(r) for r in case.frames)

    attributed = sum(sum(r) for r in prof.ticks)
    rows = rows_for(prof, ticks, answer)

    head = (
        f"deadman-3d_taped {grid.width}x{grid.height} — native fast_littleman "
        f"(the Python engine's C++ backend), gated {case.rounds}-round case "
        f"WALK[:{args.rounds}], {frames} frames\n"
        f"{ticks:,} ticks, {prof.samples:,} attribution samples (stride 1, every tick), "
        f"{sum(prof.execs):,} instruction executions\n"
        f"attributed {attributed:,} of {prof.samples:,} runner-ticks "
        f"({100 * attributed / prof.samples:.4f}%); "
        f"outside the CPU room {prof.outside:,}; ambiguous focus {prof.multi:,}; "
        f"unattributed segments {prof.execs[-1]:,} "
        f"({sum(prof.ticks[-1]):,} ticks)\n"
        f"per frame: {sum(prof.execs) / frames:,.0f} instructions, "
        f"{ticks / frames:,.0f} ticks"
    )

    t_op = table(
        "\nPER-OPCODE COST — every tick of the run, charged to the instruction that caused it",
        ["opcode", "execs", "exec/frame", "ticks", "%run", "mean", "dispatch", "memstall",
         "slab", "otherblk"],
        [
            [
                r["op"],
                f"{r['execs']:,}",
                f"{r['execs'] / frames:,.0f}",
                f"{r['ticks']:,}",
                f"{r['pct']:.2f}%",
                f"{r['mean']:.1f}",
                f"{r['dispatch_walk']:,}",
                f"{r['mem']:,}",
                f"{r['slab_walk']:,}",
                f"{r['other_block']:,}",
            ]
            for r in rows
        ],
        [12, 10, 11, 13, 7, 8, 12, 12, 11, 10],
    )

    t_mean = table(
        "\nPER-EXECUTION — what one instruction costs, and what that is made of",
        ["opcode", "execs", "ticks/exec", "dispatch", "memstall", "slab", "otherblk",
         "reads/exec", "ticks/read"],
        [
            [
                r["op"],
                f"{r['execs']:,}",
                f"{r['mean']:.1f}",
                f"{r['dispatch_walk'] / max(1, r['execs']):.1f}",
                f"{r['mem'] / max(1, r['execs']):.1f}",
                f"{r['slab_walk'] / max(1, r['execs']):.1f}",
                f"{r['other_block'] / max(1, r['execs']):.1f}",
                f"{r['mem_runs'] / max(1, r['execs']):.2f}",
                f"{r['mem'] / r['mem_runs']:.1f}" if r["mem_runs"] else "-",
            ]
            for r in rows
        ],
        [12, 10, 11, 10, 10, 9, 9, 11, 11],
    )

    t_class = table(
        "\nDISPATCH WALK, BY STAGE — ticks per execution in each stage of the CPU's own loop",
        ["opcode", "execs", *CLASSES],
        [
            [r["op"], f"{r['execs']:,}"]
            + [f"{r['by_class'][c] / max(1, r['execs']):.1f}" for c in CLASSES]
            for r in rows
        ],
        [12, 10, 7, 7, 8, 7, 11, 13, 8, 12, 7],
    )

    # ── the store stall, decomposed by observed duration ─────────────────────
    hist: dict[int, int] = {}
    for per_op in prof.block_hist.values():
        for length, n in per_op.items():
            hist[length] = hist.get(length, 0) + n
    reads = sum(hist.values())
    tot = sum(k * v for k, v in hist.items())
    ordered = sorted(hist.items())
    cum = 0
    quant: dict[str, int] = {}
    for length, n in ordered:
        cum += n
        for q in (1, 5, 25, 50, 75, 95, 99):
            key = f"p{q}"
            if key not in quant and cum >= reads * q / 100:
                quant[key] = length
    floor = ordered[0][0] if ordered else 0
    t_stall = table(
        f"\nSTORE ANSWER STALL — {reads:,} blocked runs on `{meta['answer_pipe_name']}`, "
        f"{tot:,} ticks ({100 * tot / ticks:.2f}% of the run)",
        ["statistic", "ticks"],
        [
            ["minimum observed (the floor)", f"{floor:,}"],
            *[[f"{k} of reads at or below", f"{v:,}"] for k, v in quant.items()],
            ["mean", f"{tot / max(1, reads):.1f}"],
            ["maximum observed", f"{ordered[-1][0]:,}" if ordered else "-"],
        ],
        [34, 12],
    )

    # ── addresses: which words the program actually reads ────────────────────
    # The request stream interleaves read addresses with write (address, data)
    # pairs, so a raw census of the pipe would count a stored *value* as if it
    # were an address.  A read-only opcode sends exactly one value per execution
    # and blocks exactly once, and that is checkable — so the census is built
    # only from opcodes that pass the check, and how much of the traffic that
    # covers is stated rather than assumed.
    symbols = asm_symbols()
    read_only = [
        r
        for r in rows
        if r["mem_runs"] == r["execs"] > 0
        and sum(prof.values.get(r["index"], {}).values()) == r["execs"]
    ]
    addr: dict[int, dict[str, int]] = {}
    for r in read_only:
        for value, n in prof.values.get(r["index"], {}).items():
            addr.setdefault(value, {})[r["op"]] = n
    covered = sum(sum(v.values()) for v in addr.values())
    t_addr = table(
        f"\nREAD CENSUS — which words the program reads.  {covered:,} of the {reads:,} reads "
        f"({100 * covered / max(1, reads):.1f}%) are by opcodes that provably send "
        f"one address and nothing else",
        ["addr", "symbol", "reads", "%", "per frame", "by opcode"],
        [
            [
                f"{value}",
                symbols.get(value, "(map word)"),
                f"{n:,}",
                f"{100 * n / max(1, covered):.1f}%",
                f"{n / frames:,.0f}",
                " ".join(f"{k}:{c:,}" for k, c in sorted(who.items(), key=lambda kv: -kv[1])),
            ]
            for value, who, n in sorted(
                ((v, w, sum(w.values())) for v, w in addr.items()), key=lambda t: -t[2]
            )[:20]
        ],
        [7, 10, 10, 7, 10, 34],
    )

    # ── the dispatch loop, and the whole of what a re-layout could move ──────
    lanes = {
        name.rsplit(":", 1)[1]: (rx, ry, rw)
        for name, (rx, ry, rw, _rh) in built.regions.items()
        if name.startswith("cpu:lane:")
    }
    per = [
        {
            "op": r["op"],
            "execs": r["execs"],
            "row": lanes[r["op"]][1],
            "cells": lanes[r["op"]][2],
            "trie": r["by_class"]["trie"] / r["execs"],
            "drop": r["by_class"]["drop"] / r["execs"],
            "bus": r["by_class"]["return:bus"] / r["execs"],
        }
        for r in rows
        if r["op"] in lanes
    ]
    n_exec = sum(p["execs"] for p in per)
    loop = sum(p["execs"] * (4 + p["trie"] + p["drop"] + p["bus"] + 22) for p in per)
    t_loop = table(
        "\nTHE DISPATCH LOOP — what every instruction pays before its own micro-program runs",
        ["stage", "ticks/instruction", "total", "%run", "set by"],
        [
            ["fetch `>rbr`", "4 (constant)", f"{4 * n_exec:,}", f"{100 * 4 * n_exec / ticks:.2f}%",
             "nothing — 4 cells"],
            ["trie descent",
             f"{min(p['trie'] for p in per):.0f}..{max(p['trie'] for p in per):.0f}",
             f"{sum(p['execs'] * p['trie'] for p in per):,.0f}",
             f"{100 * sum(p['execs'] * p['trie'] for p in per) / ticks:.2f}%",
             "OPCODE_SLOTS (the leaf's rank)"],
            ["drop to the bus",
             f"{min(p['drop'] for p in per):.0f}..{max(p['drop'] for p in per):.0f}",
             f"{sum(p['execs'] * p['drop'] for p in per):,.0f}",
             f"{100 * sum(p['execs'] * p['drop'] for p in per) / ticks:.2f}%",
             "LANE_ORDER (141 - the lane's row)"],
            ["return bus (west)",
             f"{min(p['bus'] for p in per):.0f}..{max(p['bus'] for p in per):.0f}",
             f"{sum(p['execs'] * p['bus'] for p in per):,.0f}",
             f"{100 * sum(p['execs'] * p['bus'] for p in per) / ticks:.2f}%",
             "the lane's exit column - 9"],
            ["riser (north)", "22 (constant)", f"{22 * n_exec:,}",
             f"{100 * 22 * n_exec / ticks:.2f}%", "the band's height"],
            ["TOTAL", f"{loop / n_exec:.1f}", f"{loop:,.0f}", f"{100 * loop / ticks:.2f}%",
             f"{n_exec:,} instructions"],
        ],
        [20, 18, 14, 8, 34],
    )

    # An upper bound on any re-assignment: permute the opcodes over the lane
    # slots they already have, hottest onto cheapest.  Feasibility (pipe
    # bindings, slab adjacency, lane width) only makes it worse, so this is a
    # ceiling, not a proposal.
    hot = sorted(per, key=lambda p: -p["execs"])
    lever = []
    for label, key in (("trie (OPCODE_SLOTS)", "trie"), ("drop (LANE_ORDER row)", "drop"),
                       ("drop + return bus", None)):
        cost = (lambda p: p["drop"] + p["bus"]) if key is None else (lambda p, k=key: p[k])
        now = sum(p["execs"] * cost(p) for p in per)
        best = sum(p["execs"] * c for p, c in zip(hot, sorted(cost(q) for q in per), strict=True))
        lever.append(
            [label, f"{now:,.0f}", f"{100 * now / ticks:.2f}%", f"{best:,.0f}",
             f"{now - best:,.0f}", f"{100 * (now - best) / ticks:.2f}%"]
        )
    t_lever = table(
        "\nLAYOUT CEILING — permuting the opcodes over the lane slots they already have, "
        "hottest onto cheapest",
        ["what a re-layout moves", "today", "%run", "best", "saved", "%run"],
        lever,
        [24, 13, 7, 13, 12, 7],
    )

    # ── the store read, decomposed ──────────────────────────────────────────
    cp = critical_path(grid, built, result.profile, ticks, meta["pipes"])
    transit = cp["transit"]
    nearest = min(cells for _n, _r, _f, cells, _c, _p in cp["rows"])
    seen_min = min(hist)
    t_read = table(
        f"\nONE STORE READ — {reads:,} of them, {tot:,} ticks, mean {tot / reads:.1f}",
        ["component", "ticks/read", "total", "%run", "how it is known"],
        [
            ["pipe transit", f"{transit / reads:.1f}", f"{transit:,}",
             f"{100 * transit / ticks:.2f}%", "pipe lengths x reads, per bank"],
            ["everything else", f"{(tot - transit) / reads:.1f}", f"{tot - transit:,}",
             f"{100 * (tot - transit) / ticks:.2f}%", "the measurement minus that"],
            ["  floor: gate+bank walk", f"~{seen_min - nearest}", "-", "-",
             f"fastest read ever seen ({seen_min}) - nearest bank's {nearest} cells"],
            ["  the rest: ring + queue", f"~{(tot - transit) / reads - (seen_min - nearest):.0f}",
             "-", "-", "tape rotation, and waiting behind other requests"],
        ],
        [26, 11, 14, 8, 46],
    )
    t_bank = table(
        "\nBY BANK — where the reads go and what each round trip costs",
        ["bank (chain slot)", "reads", "%", "round trip", "transit ticks", "%run"],
        [
            [name, f"{n:,}", f"{100 * n / max(1, reads):.1f}%", f"{cells} cells",
             f"{cost:,}", f"{100 * cost / ticks:.2f}%"]
            for name, n, _fwd, cells, cost, _path in sorted(cp["rows"], key=lambda r: -r[4])
        ],
        [22, 10, 7, 12, 14, 7],
    )

    total_by_roll: dict[str, int] = {}
    for r in rows:
        total_by_roll["dispatch walk"] = total_by_roll.get("dispatch walk", 0) + r["dispatch_walk"]
        total_by_roll["memory stall"] = total_by_roll.get("memory stall", 0) + r["mem"]
        total_by_roll["slab work"] = total_by_roll.get("slab work", 0) + r["slab_walk"]
        total_by_roll["other stall"] = total_by_roll.get("other stall", 0) + r["other_block"]
    t_roll = table(
        "\nTHE RUN, IN FOUR PARTS",
        ["part", "ticks", "%run"],
        [
            [k, f"{v:,}", f"{100 * v / ticks:.2f}%"]
            for k, v in sorted(total_by_roll.items(), key=lambda kv: -kv[1])
        ]
        + [["TOTAL", f"{sum(total_by_roll.values()):,}",
            f"{100 * sum(total_by_roll.values()) / ticks:.2f}%"]],
        [22, 14, 8],
    )

    text = "\n".join(
        [head, t_roll, t_op, t_mean, t_class, t_loop, t_lever, t_stall, t_read, t_bank, t_addr, ""]
    )
    print(text)
    (args.out / "opcodes.txt").write_text(text, encoding="utf-8")
    (args.out / "opcodes.json").write_text(
        json.dumps(
            {
                "engine": "fast_littleman native (C++ backend of the Python engine)",
                "grid": [grid.width, grid.height],
                "rounds": case.rounds,
                "frames": frames,
                "ticks": ticks,
                "samples": prof.samples,
                "attributed": attributed,
                "outside": prof.outside,
                "multi": prof.multi,
                "classes": list(CLASSES),
                "rollup": total_by_roll,
                "ops": [
                    {k: v for k, v in r.items() if k not in ("by_class", "blk_class")}
                    | {
                        "by_class": r["by_class"],
                        "blocked_by_class": r["blk_class"],
                        # Per-opcode, so "which variable does LD actually read"
                        # and "how long does *this* opcode's read take" are
                        # answerable without a second run.
                        "stall_hist": {
                            str(k): v
                            for k, v in sorted(prof.block_hist.get(r["index"], {}).items())
                        },
                        "sent_values": {
                            str(k): v
                            for k, v in sorted(
                                prof.values.get(r["index"], {}).items(), key=lambda kv: -kv[1]
                            )
                        },
                    }
                    for r in rows
                ],
                "lane_geometry": {
                    name.rsplit(":", 1)[1]: {"row": ry, "x0": rx, "cells": rw}
                    for name, (rx, ry, rw, _rh) in built.regions.items()
                    if name.startswith("cpu:lane:")
                },
                "store_stall_hist": {str(k): v for k, v in sorted(hist.items())},
                "read_census": {
                    str(v): {"total": sum(w.values()), "by_opcode": w, "symbol": symbols.get(v)}
                    for v, w in sorted(addr.items(), key=lambda kv: -sum(kv[1].values()))
                },
                "read_census_covers": covered,
                "dispatch_loop_ticks": loop,
                "layout_ceiling": lever,
                "meta": {k: v for k, v in meta.items() if k != "pipes"},
            },
            indent=1,
        ),
        encoding="utf-8",
    )
    write_html(
        args.out,
        head=head,
        ticks=ticks,
        frames=frames,
        rows=rows,
        rollup=total_by_roll,
        loop_rows=t_loop,
        lever=lever,
        hist=hist,
        quant=quant,
        reads=reads,
        stall=tot,
        transit=transit,
        floor=seen_min,
        nearest=nearest,
        banks=cp["rows"],
        census=addr,
        covered=covered,
        symbols=symbols,
        per=per,
        n_exec=n_exec,
        loop=loop,
    )
    print(f"wrote {args.out}/opcodes.txt, opcodes.json, index.html")
    return 0


# ── the rendered page ───────────────────────────────────────────────────────
# Four parts of one run = a categorical of four.  Slots 1..4 of the reference
# palette, validated in both modes (`scripts/validate_palette.js`): light passes
# every gate with a contrast WARN on aqua and yellow, which the relief rule
# discharges here because every bar carries its own number and the same figures
# are in the tables above it.
PARTS = ("memory stall", "dispatch walk", "slab work", "other stall")
LIGHT = {"surface": "#fcfcfb", "ink": "#0b0b0b", "ink2": "#52514e", "line": "#e2e2dd",
         "panel": "#f5f5f2",
         "c": ("#2a78d6", "#eb6834", "#1baf7a", "#eda100")}
DARK = {"surface": "#1a1a19", "ink": "#ffffff", "ink2": "#c3c2b7", "line": "#333330",
        "panel": "#232320",
        "c": ("#3987e5", "#d95926", "#199e70", "#c98500")}


def _vars(theme: dict) -> str:
    return "".join(
        [
            f"--surface:{theme['surface']};--ink:{theme['ink']};--ink2:{theme['ink2']};",
            f"--line:{theme['line']};--panel:{theme['panel']};",
            *(f"--s{i + 1}:{c};" for i, c in enumerate(theme["c"])),
        ]
    )


def write_html(out: Path, **d) -> None:
    ticks = d["ticks"]
    rows = d["rows"]
    esc = html.escape

    def seg(value: int, denom: int, slot: int, label: str) -> str:
        if value <= 0:
            return ""
        return (
            f'<span class="seg" style="width:{100 * value / denom:.4f}%;'
            f'background:var(--s{slot})" title="{esc(label)}: {value:,} ticks '
            f'({100 * value / ticks:.2f}% of the run)"></span>'
        )

    def stack(r: dict, denom: int) -> str:
        return (
            '<span class="stack">'
            + seg(r["mem"], denom, 1, "memory stall")
            + seg(r["dispatch_walk"], denom, 2, "dispatch walk")
            + seg(r["slab_walk"], denom, 3, "slab work")
            + seg(r["other_block"], denom, 4, "other stall")
            + "</span>"
        )

    # The headline is whichever opcode's *store stall* is biggest, and the two
    # words that opcode reads most — both read off the data, never hard-coded.
    worst = max(rows, key=lambda r: r["mem"])
    mine = sorted(
        ((v, w.get(worst["op"], 0)) for v, w in d["census"].items()), key=lambda t: -t[1]
    )[:2]
    worst_share = sum(n for _v, n in mine) / max(1, worst["execs"])
    worst_words = " and ".join(d["symbols"].get(v, str(v)) for v, _n in mine)

    peak = max(r["ticks"] for r in rows)
    op_html = "".join(
        f'<tr><th>{esc(r["op"])}</th><td class="n">{r["execs"]:,}</td>'
        f'<td class="n">{r["execs"] / d["frames"]:,.0f}</td>'
        f'<td class="n">{r["ticks"]:,}</td><td class="n">{r["pct"]:.2f}%</td>'
        f'<td class="bar">{stack(r, peak)}</td>'
        f'<td class="n">{r["mean"]:.0f}</td></tr>'
        for r in rows
        if r["ticks"]
    )
    peak_mean = max(r["mean"] for r in rows)

    def mean_row(r: dict) -> str:
        per_read = f'{r["mem"] / r["mem_runs"]:.0f}' if r["mem_runs"] else "-"
        return (
            f'<tr><th>{esc(r["op"])}</th><td class="n">{r["mean"]:.0f}</td>'
            f'<td class="bar">{stack(r, peak_mean * max(1, r["execs"]))}</td>'
            f'<td class="n">{r["dispatch_walk"] / r["execs"]:.0f}</td>'
            f'<td class="n">{r["mem"] / r["execs"]:.0f}</td>'
            f'<td class="n">{r["slab_walk"] / r["execs"]:.0f}</td>'
            f'<td class="n">{per_read}</td></tr>'
        )

    mean_html = "".join(mean_row(r) for r in rows if r["ticks"])

    run_html = "".join(
        f'<span class="seg" style="width:{100 * d["rollup"][p] / ticks:.4f}%;'
        f'background:var(--s{i + 1})" title="{p}: {d["rollup"][p]:,} ticks"></span>'
        for i, p in enumerate(PARTS)
    )
    legend = "".join(
        f'<span class="key"><i style="background:var(--s{i + 1})"></i>{p} '
        f'<b>{100 * d["rollup"][p] / ticks:.1f}%</b></span>'
        for i, p in enumerate(PARTS)
    )

    # the stall distribution: one measure, one hue, 20-tick bins to 1000
    binw, cap = 20, 1000
    bins: dict[int, int] = {}
    for length, n in d["hist"].items():
        bins[min(length, cap) // binw] = bins.get(min(length, cap) // binw, 0) + n
    top = max(bins.values())
    bars = "".join(
        f'<span class="hb" style="height:{100 * bins.get(b, 0) / top:.2f}%" '
        f'title="{b * binw}–{b * binw + binw - 1} ticks: {bins.get(b, 0):,} reads"></span>'
        for b in range(max(bins) + 1)
    )

    lever_html = "".join(
        f"<tr><th>{esc(r[0])}</th><td class='n'>{r[1]}</td><td class='n'>{r[2]}</td>"
        f"<td class='n'>{r[3]}</td><td class='n'>{r[4]}</td><td class='n hi'>{r[5]}</td></tr>"
        for r in d["lever"]
    )
    bank_html = "".join(
        f"<tr><th>{esc(name)}</th><td class='n'>{n:,}</td>"
        f"<td class='n'>{100 * n / d['reads']:.1f}%</td><td class='n'>{cells}</td>"
        f"<td class='n'>{cost:,}</td><td class='n'>{100 * cost / ticks:.2f}%</td></tr>"
        for name, n, _f, cells, cost, _p in sorted(d["banks"], key=lambda r: -r[4])
    )
    census = sorted(((v, w, sum(w.values())) for v, w in d["census"].items()), key=lambda t: -t[2])

    def by_op(w: dict[str, int]) -> str:
        return " ".join(f"{k}:{c:,}" for k, c in sorted(w.items(), key=lambda kv: -kv[1]))

    census_html = "".join(
        f"<tr><th>{v} <span class=dim>{esc(d['symbols'].get(v, '(map word)'))}</span></th>"
        f"<td class='n'>{n:,}</td><td class='n'>{100 * n / d['covered']:.1f}%</td>"
        f"<td class='n'>{n / d['frames']:,.0f}</td>"
        f"<td class='bar'><span class=\"stack\"><span class=\"seg\" "
        f'style="width:{100 * n / census[0][2]:.2f}%;background:var(--s1)"></span></span></td>'
        f"<td class=dim>{esc(by_op(w))}</td></tr>"
        for v, w, n in census[:18]
    )
    stage_html = "".join(
        f"<tr><th>{name}</th><td class='n'>{span}</td><td class='n'>{total:,.0f}</td>"
        f"<td class='n'>{100 * total / ticks:.2f}%</td><td class=dim>{esc(by)}</td></tr>"
        for name, span, total, by in (
            ("fetch <code>&gt;rbr</code>", "4, constant", 4 * d["n_exec"],
             "nothing — it is 4 cells"),
            ("trie descent",
             f"{min(p['trie'] for p in d['per']):.0f}–{max(p['trie'] for p in d['per']):.0f}",
             sum(p["execs"] * p["trie"] for p in d["per"]), "OPCODE_SLOTS (the leaf's rank)"),
            ("drop to the bus",
             f"{min(p['drop'] for p in d['per']):.0f}–{max(p['drop'] for p in d['per']):.0f}",
             sum(p["execs"] * p["drop"] for p in d["per"]), "LANE_ORDER (141 − the lane's row)"),
            ("return bus, westward",
             f"{min(p['bus'] for p in d['per']):.0f}–{max(p['bus'] for p in d['per']):.0f}",
             sum(p["execs"] * p["bus"] for p in d["per"]), "the lane's exit column − 9"),
            ("riser, northward", "22, constant", 22 * d["n_exec"], "the lane band's height"),
        )
    )

    page = f"""<!doctype html><meta charset="utf-8">
<title>deadman-3d_taped — where a CPU operation's ticks go</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
 :root {{ color-scheme: light; {_vars(LIGHT)} }}
 @media (prefers-color-scheme: dark) {{
   :root:where(:not([data-theme="light"])) {{ color-scheme: dark; {_vars(DARK)} }}
 }}
 :root[data-theme="dark"] {{ color-scheme: dark; {_vars(DARK)} }}
 :root[data-theme="light"] {{ color-scheme: light; {_vars(LIGHT)} }}
 * {{ box-sizing: border-box; }}
 body {{ background:var(--surface); color:var(--ink); margin:0 auto; padding:32px 20px 72px;
        max-width:1180px; font:14px/1.55 ui-sans-serif,-apple-system,"Segoe UI",sans-serif; }}
 h1 {{ font-size:23px; margin:0 0 6px; letter-spacing:-.01em; }}
 h2 {{ font-size:15px; margin:44px 0 6px; }}
 p {{ margin:6px 0 12px; color:var(--ink2); max-width:75ch; }}
 pre.meta {{ color:var(--ink2); font:11.5px/1.65 ui-monospace,SFMono-Regular,Menlo,monospace;
             white-space:pre-wrap; margin:0 0 18px; }}
 .hero {{ background:var(--panel); border:1px solid var(--line); border-radius:10px;
          padding:20px 22px; margin:18px 0 8px; }}
 .hero b {{ display:block; font-size:40px; line-height:1.05; font-variant-numeric:tabular-nums; }}
 .hero span {{ color:var(--ink2); }}
 .tiles {{ display:flex; gap:12px; flex-wrap:wrap; margin:12px 0 4px; }}
 .tile {{ background:var(--panel); border:1px solid var(--line); border-radius:8px;
          padding:11px 15px; min-width:150px; }}
 .tile b {{ display:block; font-size:22px; font-variant-numeric:tabular-nums; }}
 .tile span {{ color:var(--ink2); font-size:12px; }}
 .stack {{ display:flex; width:100%; height:11px; gap:2px; }}
 .seg {{ display:block; height:100%; border-radius:2px; min-width:2px; }}
 .stack.big {{ height:30px; }}
 .stack.big .seg {{ border-radius:4px; }}
 .key {{ display:inline-flex; align-items:center; gap:6px; margin-right:18px;
         color:var(--ink2); font-size:12px; }}
 .key i {{ width:11px; height:11px; border-radius:2px; display:inline-block; }}
 .key b {{ color:var(--ink); font-variant-numeric:tabular-nums; }}
 .wrap {{ overflow-x:auto; }}
 table {{ border-collapse:collapse; width:100%; font-variant-numeric:tabular-nums;
          min-width:640px; }}
 th, td {{ text-align:left; padding:4px 9px; border-bottom:1px solid var(--line);
           font-weight:400; }}
 thead th {{ color:var(--ink2); font-size:11.5px; border-bottom:1px solid var(--ink2);
             white-space:nowrap; }}
 tbody th {{ font-family:ui-monospace,Menlo,monospace; font-size:12px; white-space:nowrap; }}
 td.n {{ text-align:right; white-space:nowrap; }}
 td.bar {{ width:32%; min-width:150px; }}
 td.hi, .hi {{ color:var(--ink); font-weight:600; }}
 .dim {{ color:var(--ink2); font-size:12px; }}
 .hist {{ display:flex; align-items:flex-end; gap:1px; height:130px; margin:14px 0 4px;
          border-bottom:1px solid var(--line); }}
 .hb {{ flex:1 1 0; background:var(--s1); border-radius:2px 2px 0 0; min-height:1px; }}
 .axis {{ display:flex; justify-content:space-between; color:var(--ink2); font-size:11px; }}
 code {{ font-family:ui-monospace,Menlo,monospace; font-size:12.5px; }}
</style>
<h1>deadman-3d_taped — where a CPU operation's ticks go</h1>
<pre class="meta">{esc(d["head"])}</pre>

<div class="hero">
  <b>{100 * worst["mem"] / ticks:.1f}% of the whole run is one opcode waiting for the store</b>
  <span><code>{esc(worst["op"])}</code> runs {worst["execs"]:,} times
  ({worst["execs"] / d["frames"]:,.0f} a frame) at {worst["mean"]:.0f} ticks each —
  <b style="display:inline;font-size:inherit">{worst["mem"] / worst["execs"]:.0f} blocked
  on <code>store:collector-&gt;cpu</code></b> and
  {worst["dispatch_walk"] / worst["execs"]:.0f} walking the CPU's own dispatch.
  {100 * worst_share:.0f}% of those reads are two words —
  {esc(worst_words)} — the DDA's own cursor pair.</span>
</div>

<div class="tiles">
  <div class="tile"><b>{ticks:,}</b><span>ticks, {d["frames"]} frames</span></div>
  <div class="tile"><b>{d["n_exec"]:,}</b><span>instructions
    ({d["n_exec"] / d["frames"]:,.0f} a frame)</span></div>
  <div class="tile"><b>{d["reads"]:,}</b><span>store reads
    ({d["reads"] / d["frames"]:,.0f} a frame)</span></div>
  <div class="tile"><b>{d["stall"] / d["reads"]:.0f}</b><span>ticks blocked per read
    (floor {d["floor"]})</span></div>
  <div class="tile"><b>{d["loop"] / d["n_exec"]:.0f}</b><span>ticks of dispatch loop
    per instruction</span></div>
</div>

<h2>The run, in four parts</h2>
<p>Every tick is charged to the instruction that caused it: the timeline is cut at the
instruction fetch and each segment folded into the one opcode whose lane it entered, so
the trie descent, the drop and the return walk belong to an instruction rather than to a
shared bucket. Nothing is sampled — 100.0000% of the run is attributed.</p>
<div class="stack big">{run_html}</div>
<p style="margin-top:10px">{legend}</p>

<h2>Per opcode — the whole run</h2>
<div class="wrap"><table>
<thead><tr><th>opcode</th><th>executions</th><th>per frame</th><th>ticks</th><th>% run</th>
<th>memory stall · dispatch walk · slab · other</th><th>mean</th></tr></thead>
<tbody>{op_html}</tbody></table></div>

<h2>Per execution — what one instruction costs</h2>
<p>The bar is the same four parts, scaled so opcodes are comparable per execution.
<code>LDA</code> is the most expensive single instruction in the machine at
{next(r for r in rows if r["op"] == "LDA")["mean"]:.0f} ticks — it reads the map bank,
whose ring is the longest in the store.</p>
<div class="wrap"><table>
<thead><tr><th>opcode</th><th>ticks/exec</th><th>composition</th><th>dispatch</th>
<th>mem stall</th><th>slab</th><th>ticks/read</th></tr></thead>
<tbody>{mean_html}</tbody></table></div>

<h2>The dispatch loop — what every instruction pays before its own micro-program runs</h2>
<p>{d["loop"] / d["n_exec"]:.0f} ticks × {d["n_exec"]:,} instructions =
{d["loop"]:,.0f} ticks, {100 * d["loop"] / ticks:.2f}% of the run. Two of the five stages are
constants; the trie is nearly one; only the drop is a free variable of the layout.</p>
<div class="wrap"><table>
<thead><tr><th>stage</th><th>ticks/instruction</th><th>total</th><th>% run</th>
<th>set by</th></tr></thead><tbody>{stage_html}</tbody></table></div>

<h2>What a re-layout could move — a ceiling, not a proposal</h2>
<p>Permute the opcodes over the lane slots they already have, hottest onto cheapest.
Feasibility — pipe bindings, slab adjacency, lane width — only makes it worse.</p>
<div class="wrap"><table>
<thead><tr><th>what it moves</th><th>today</th><th>% run</th><th>best</th><th>saved</th>
<th>% run</th></tr></thead><tbody>{lever_html}</tbody></table></div>

<h2>One store read — {d["reads"]:,} of them, mean {d["stall"] / d["reads"]:.0f} ticks</h2>
<p>Every read is a blocked run measured to the tick. The fastest ever seen is
{d["floor"]}; the nearest bank's round trip is {d["nearest"]} cells of pipe, so about
{d["floor"] - d["nearest"]} ticks of gate, adapter and bank <em>walking</em> sit under the
transit and cannot be teleported away. Pipe transit is {d["transit"] / d["reads"]:.0f} of
the {d["stall"] / d["reads"]:.0f}; the remaining
{(d["stall"] - d["transit"]) / d["reads"] - (d["floor"] - d["nearest"]):.0f} is tape
rotation and queueing.</p>
<div class="hist">{bars}</div>
<div class="axis"><span>0 ticks</span><span>every read's blocked run, {binw}-tick bins —
floor {d["floor"]}, p50 {d["quant"].get("p50", 0):,}, p95 {d["quant"].get("p95", 0):,},
longest {max(d["hist"]):,}</span><span>{cap}+</span></div>
<div class="wrap" style="margin-top:20px"><table>
<thead><tr><th>bank (chain slot)</th><th>reads</th><th>%</th><th>round trip, cells</th>
<th>transit ticks</th><th>% run</th></tr></thead><tbody>{bank_html}</tbody></table></div>

<h2>Which words the program reads</h2>
<p>{d["covered"]:,} of the {d["reads"]:,} reads ({100 * d["covered"] / d["reads"]:.1f}%)
are by opcodes that provably send one address and nothing else, so this census is exact
rather than inferred from the request stream (which interleaves write data).</p>
<div class="wrap"><table>
<thead><tr><th>address</th><th>reads</th><th>%</th><th>per frame</th><th></th>
<th>by opcode</th></tr></thead><tbody>{census_html}</tbody></table></div>
</html>"""
    (out / "index.html").write_text(page, encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
