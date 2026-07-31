"""Deliverable 1: the seek subsystem's ECONOMICS, from the emulator alone.

No machine is built. The emulator executes the same 880,332-instruction tour the
grid does, so its per-jump *dynamic* taken counts are exactly the grid's. Cross
that with `seek_split`'s *static* threshold decision and every SEEK_THRESHOLD /
SEEK_OPS variant is priced without a 70s build.

The cost model has two constants only:

  seek(taken)     = SEEK_FIX ticks, flat (drum row lookup; independent of skip)
  classic(taken)  = CLASSIC_FIX + WORD * skip  (the counted discard walks skip
                    ring words at ~4 ticks a word)

Both are calibrated against the profile in `--calibrate`.
"""
from __future__ import annotations
import sys, collections, json
sys.path.insert(0, "/tmp/seekecon")
from common import setup, SLUG


def trace(prog, hires, cmds):
    """Execute the tour, returning per-jump-instruction dynamic counts.

    key: word position of the opcode. value: dict(mnemonic, skip, execs, taken).
    """
    from randomfun2026solvers.lm1.emulator import Emulator, Round
    from randomfun2026solvers.lm1.isa import TARGET_SEMS

    em = Emulator(prog)
    stat: dict[int, dict] = {}
    P = len(prog.words)
    # word position -> instruction, for the jump family only
    by_pos = {i.pos: i for i in prog.instrs}

    orig_step = em.step
    isa = prog.isa

    def step():
        pos = em.phase                 # the opcode's ring index, pre-fetch
        before = em.phase
        op = orig_step()
        if op.sem in TARGET_SEMS:
            ins = by_pos.get(pos)
            mn = ins.mnemonic if ins is not None else op.mnemonic
            # operand is the static forward-skip in ring words
            skip = prog.words[(pos + 1) % P]
            d = stat.get(pos)
            if d is None:
                d = stat[pos] = {"mnemonic": mn, "skip": skip, "execs": 0, "taken": 0}
            d["execs"] += 1
            # taken iff the phase advanced by more than the 2 fetched words
            if (em.phase - before) % P != 2:
                d["taken"] += 1
        return op

    em.step = step
    res = em.run([Round(input=tuple(hires.input_words(cmds)))],
                 max_instructions=50_000_000)
    return stat, res


def main():
    d3, hires, M, prog = setup()
    cmds = list(hires.WALK[:20])
    stat, res = trace(prog, hires, cmds)
    print(f"emulator: instructions={res.instructions:,} reason={res.reason}", flush=True)

    rows = sorted(stat.values(), key=lambda d: -d["taken"] * d["skip"])
    fams = collections.Counter()
    for d in rows:
        fams[d["mnemonic"]] += d["taken"]
    print("\n== taken jumps by family (21-round tour) ==")
    for mn, n in fams.most_common():
        words = sum(d["taken"] * d["skip"] for d in rows if d["mnemonic"] == mn)
        print(f"  {mn:6s} taken={n:9,}  discard words={words:12,}")
    tot_words = sum(d["taken"] * d["skip"] for d in rows)
    tot_taken = sum(d["taken"] for d in rows)
    print(f"  {'ALL':6s} taken={tot_taken:9,}  discard words={tot_words:12,}")

    # ---- the threshold sweep, per family set -------------------------------
    print("\n== SEEK_THRESHOLD sweep: what actually gets seeked ==")
    THR = [0, 64, 128, 192, 256, 320, 384, 448, 512, 600, 700, 800, 1000,
           1200, 1500, 2000, 3000, 5000, 10**9]
    OPSETS = [("JMPF",), ("JMPF", "BRZ"), ("JMPF", "BRN"), ("JMPF", "BRZ", "BRN")]
    out = {}
    for ops in OPSETS:
        allowed = set(ops)
        print(f"\n  ops={'+'.join(ops)}")
        print(f"    {'thr':>8} {'seek instrs':>12} {'taken seeks':>12} "
              f"{'seek words':>13} {'classic words':>14}")
        for t in THR:
            si = sk = sw = cw = 0
            for d in stat.values():
                is_seek = d["mnemonic"] in allowed and d["skip"] >= t
                if is_seek:
                    si += 1
                    sk += d["taken"]
                    sw += d["taken"] * d["skip"]
                else:
                    cw += d["taken"] * d["skip"]
            out[(ops, t)] = (si, sk, sw, cw)
            print(f"    {t:>8} {si:>12,} {sk:>12,} {sw:>13,} {cw:>14,}")

    with open("/tmp/seekecon/stat.json", "w") as fh:
        json.dump({str(k): v for k, v in stat.items()}, fh)
    print("\nwrote /tmp/seekecon/stat.json")

    # ---- the top individual jumps, so the shape is visible -----------------
    print("\n== the 25 costliest jump sites (taken x skip) ==")
    print(f"  {'pos':>7} {'op':6} {'skip':>7} {'execs':>9} {'taken':>9} {'words':>13}")
    for d in rows[:25]:
        pos = next(p for p, v in stat.items() if v is d)
        print(f"  {pos:>7} {d['mnemonic']:6} {d['skip']:>7,} {d['execs']:>9,} "
              f"{d['taken']:>9,} {d['taken']*d['skip']:>13,}")


if __name__ == "__main__":
    main()
