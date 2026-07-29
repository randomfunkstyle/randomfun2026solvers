"""Census the DOOM unit's command-word stream, and what the CPU pays to make it.

Answers the "would a run/plotter primitive help?" question with counts rather
than intuition:

* how many command words a frame is, split by the unit's own opcode
  (`COL`/`RUN`/`CURS`/`GUN`/`GUNF`/`COMMIT`);
* how many *pixels* each of those words paints, so the pixels-per-word ratio is
  visible per class;
* which region of the program emitted each word, and how many CPU instructions
  (and, priced at `scratch/DOOM-OPCODES.md`'s measured per-opcode ticks, how
  many CPU ticks) that region spends to produce it.

Runs on the Python emulator, which is exact for the *program* (the same words
in the same order the native engine sends) and free of any grid simulation.

    uv run python scratch/doom_words.py [--rounds 8]
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter, defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "solvers" / "python"))

from randomfun2026solvers import deadman3d as d3  # noqa: E402
from randomfun2026solvers.lm1 import emulator as emu_mod  # noqa: E402
from randomfun2026solvers.lm1.emulator import Emulator, Round  # noqa: E402
from randomfun2026solvers.lm1.store import DoomUnit  # noqa: E402

CODE_NAME = {v: k for k, v in DoomUnit.CODES.items()}

#: Measured mean ticks per execution on the taped machine, `scratch/DOOM-OPCODES.md`
#: §2.  Used only to price an instruction census; the tick totals it produces are
#: therefore estimates carrying that table's own error, and are labelled so.
TICKS = {
    "LDA": 801.0, "INCM": 579.2, "LD": 470.9, "ADD": 421.8, "MOVA": 419.1,
    "SUB": 412.9, "MUL": 409.9, "DIV": 358.2, "JMPS": 1168.7, "BRN": 447.3,
    "BRZ": 348.7, "JMPF": 210.1, "SND": 272.6, "ST": 132.0, "MODI": 92.0,
    "DIVI": 94.0, "SUBI": 92.0, "MULI": 88.0, "LDI": 86.1, "ADDI": 144.0,
    "IN": 202.7, "NEG": 160.0,
}


def region_map(prog) -> list[str]:
    """word index -> the nearest preceding label."""
    at = sorted((pos, name) for name, pos in prog.labels.items())
    out, k, cur = [], 0, "(head)"
    for i in range(len(prog.words)):
        while k < len(at) and at[k][0] <= i:
            cur = at[k][1]
            k += 1
        out.append(cur)
    return out


#: Coarse buckets over the program's labels: which part of a frame a region is.
def bucket(label: str) -> str:
    if label.startswith(("send", "colnxt", "nuk")):
        return "wall+floor column (COL)"
    # `hity*`/`hitx*` are the DDA's own arm joins, not the sprite painter — the
    # first cut of this bucket caught them on a "hit" prefix and reported the
    # raycaster's y-arm as 13.4% of "monster painting".
    if label.startswith(("chain", "csk", "ccr", "cdn", "mc", "mon", "spr", "mb",
                         "msx", "mslot", "mband", "mnext", "mstrip", "mpaint",
                         "mdead", "msel")):
        return "sprite paint chain (CURS+RUN per pixel)"
    if label.startswith(("hud", "bar", "face", "ammo", "hp", "num")):
        return "HUD (CURS+RUN)"
    if label.startswith(("gun", "gid")):
        return "gun sprite"
    if label.startswith(("title", "tt")):
        return "title screen"
    if label.startswith("cmit"):
        return "COMMIT"
    return f"other:{label}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rounds", type=int, default=8)
    ap.add_argument("--tour", action="store_true",
                    help="the checked-in 116-round tour instead of WALK[:rounds]")
    args = ap.parse_args()

    prog = d3.taped_program()
    regions = region_map(prog)

    words: list[tuple[int, str]] = []          # (command word, emitting label)
    per_op = Counter()                         # mnemonic -> executions
    per_region_ops: dict[str, Counter] = defaultdict(Counter)

    em = Emulator(prog)

    # Record the label the SND was fetched from.  `step` has already advanced the
    # phase past the opcode when the handler runs, so back off by one word.
    real_send = emu_mod._HANDLERS[emu_mod.Sem.STREAM_SEND]

    def spy_send(e, operand):
        words.append((e.b % (1 << 32), regions[(e.phase - 1) % e.P]))
        return real_send(e, operand)

    emu_mod._HANDLERS[emu_mod.Sem.STREAM_SEND] = spy_send

    real_step = Emulator.step

    def spy_step(self):
        here = regions[self.phase]
        op = real_step(self)
        per_op[op.mnemonic] += 1
        per_region_ops[here][op.mnemonic] += 1
        return op

    Emulator.step = spy_step  # type: ignore[method-assign]
    try:
        if args.tour:
            ex = REPO / "littleman" / "examples" / "deadman-3d_tour.input.txt"
            all_words = [int(w) for w in ex.read_text().split()]
            boot = d3.preamble_words() + d3.title_words()
            assert all_words[: len(boot)] == boot, "tour input does not start with boot"
            cmds = all_words[len(boot) :]
        else:
            cmds = d3.WALK[: args.rounds]
        args.rounds = len(cmds)
        res = em.run([Round(input=tuple(d3.input_words(cmds)))], max_instructions=40_000_000)
    finally:
        Emulator.step = real_step  # type: ignore[method-assign]
        emu_mod._HANDLERS[emu_mod.Sem.STREAM_SEND] = real_send

    frames = 1 + args.rounds  # the title frame plus one per command
    print(f"reason={res.reason}  instructions={res.instructions:,}  frames={frames}")

    # ── the command-word census ─────────────────────────────────────────────
    # Words after the title screen only: the title is round 0 and is not a
    # raycast frame.  Split on the first COMMIT.
    first_commit = next(
        i for i, (w, _) in enumerate(words) if w % 8 == DoomUnit.CODES["COMMIT"]
    )
    title, play = words[: first_commit + 1], words[first_commit + 1 :]
    play_frames = frames - 1

    def census(stream, n_frames, name):
        by_code = Counter()
        px = Counter()
        by_region = Counter()
        for w, label in stream:
            code = w % 8
            arg = w // 8
            cn = CODE_NAME.get(code, f"?{code}")
            by_code[cn] += 1
            by_region[(cn, bucket(label))] += 1
            if cn == "COL":
                n = arg % 64
                px[cn] += n  # the wall run; the floor lap is the unit's own
            elif cn == "RUN":
                px[cn] += arg // 16
            elif cn in ("GUN", "GUNF"):
                px[cn] += 0  # a baked sprite: its pixels are the unit's, not a word's
        total = sum(by_code.values())
        print(f"\n── {name}: {total:,} words over {n_frames} frame(s) "
              f"= {total / n_frames:.1f}/frame")
        print(f"{'code':<8}{'words':>9}{'/frame':>9}{'% words':>9}{'wall/run px':>13}")
        for cn, c in by_code.most_common():
            print(f"{cn:<8}{c:>9,}{c / n_frames:>9.1f}{100 * c / total:>8.1f}%"
                  f"{px[cn]:>13,}")
        print(f"\n  by emitting region:")
        for (cn, b), c in sorted(by_region.items(), key=lambda kv: -kv[1]):
            print(f"    {cn:<8}{b:<34}{c:>8,}{c / n_frames:>9.1f}/frame")
        return by_code

    census(title, 1, "title frame")
    census(play, play_frames, "raycast frames")

    # ── what the CPU pays to produce them ───────────────────────────────────
    print(f"\n── CPU instruction census by region bucket "
          f"(all {frames} frames, priced at DOOM-OPCODES.md §2 means) ──")
    buckets: dict[str, Counter] = defaultdict(Counter)
    for label, ops in per_region_ops.items():
        buckets[bucket(label)] += ops
    rows = []
    for b, ops in buckets.items():
        n = sum(ops.values())
        ticks = sum(TICKS.get(m, 100.0) * c for m, c in ops.items())
        rows.append((ticks, n, b, ops))
    rows.sort(reverse=True)
    grand = sum(r[0] for r in rows)
    print(f"{'bucket':<36}{'instrs':>10}{'/frame':>9}{'est ticks':>13}{'% est':>8}")
    for ticks, n, b, _ops in rows:
        print(f"{b:<36}{n:>10,}{n / frames:>9.1f}{ticks:>13,.0f}{100 * ticks / grand:>7.1f}%")
    print(f"{'TOTAL':<36}{sum(r[1] for r in rows):>10,}"
          f"{sum(r[1] for r in rows) / frames:>9.1f}{grand:>13,.0f}{100:>7.1f}%")

    print(f"\n── inside the monster chain: where its instructions go ──")
    mon = [(sum(c.values()), sum(TICKS.get(m, 100.0) * n for m, n in c.items()), lab)
           for lab, c in per_region_ops.items()
           if bucket(lab) == "sprite paint chain (CURS+RUN per pixel)"]
    mon.sort(key=lambda r: -r[1])
    tot_i, tot_t = sum(r[0] for r in mon), sum(r[1] for r in mon)
    for n, t, lab in mon:
        print(f"  {lab:<12}{n:>9,}{n / frames:>9.1f}/frame{t:>13,.0f} est ticks")
    print(f"  {'(chain total)':<12}{tot_i:>9,}{tot_i / frames:>9.1f}/frame"
          f"{tot_t:>13,.0f} est ticks over {len(mon)} labels")

    print(f"\n── SND-adjacent detail: the `send:` column block ──")
    for label in sorted(per_region_ops):
        if bucket(label) == "wall+floor column (COL)":
            ops = per_region_ops[label]
            n = sum(ops.values())
            ticks = sum(TICKS.get(m, 100.0) * c for m, c in ops.items())
            print(f"  {label:<12}{n:>8,} instrs  {n / frames:>7.1f}/frame  "
                  f"{ticks:>11,.0f} est ticks   {dict(ops)}")

    print(f"\ntotal SND executions: {per_op['SND']:,} "
          f"({per_op['SND'] / frames:.1f}/frame)")

    # ── could a *horizontal* run merge adjacent columns? ────────────────────
    # A COL word is 8*arg, arg = seed*64 + n, seed = (top*64 + x)*16 + c - 1024.
    # Decode it back and ask how often neighbouring columns are pixel-identical
    # in (top, n, colour) — the only case a "paint this run across k columns"
    # primitive could collapse without changing a pixel.
    cols: list[tuple[int, int, int, int]] = []
    for w, label in play:
        if w % 8 != DoomUnit.CODES["COL"] or bucket(label) != "wall+floor column (COL)":
            continue
        arg = w // 8
        n, seed = arg % 64, arg // 64
        c = (seed + 1024) % 16
        x = ((seed + 1024) // 16) % 64
        top = ((seed + 1024) // 16) // 64
        cols.append((x, top, n, c))
    runs, merged = 0, 0
    prev = None
    for x, top, n, c in cols:
        same = prev is not None and prev[0] + 1 == x and prev[1:] == (top, n, c)
        if same:
            merged += 1
        else:
            runs += 1
        prev = (x, top, n, c)
    if cols:
        print(f"\n── adjacent-column identity (the only merge a horizontal run buys) ──")
        print(f"  {len(cols):,} COL words, {runs:,} maximal runs of identical "
              f"neighbours, {merged:,} words a perfect merge would delete "
              f"({100 * merged / len(cols):.1f}%)")
        print(f"  mean run length {len(cols) / runs:.2f} columns")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
