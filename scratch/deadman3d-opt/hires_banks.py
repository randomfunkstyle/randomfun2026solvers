#!/usr/bin/env python3
"""Where does ``deadman-3d_hires`` actually read its tape?

:data:`machine.TAPED_BANK_ORDER` is the order the gate chain visits the banks
in, and it is worth ~7% on ``deadman-3d`` because that program's traffic is
savagely lopsided.  The order is **not** transferable: ``deadman-3d``'s
``(3, 0, 1, 2)`` was read off *its* traffic over *its* bank plan
``(256, 195, 64, 85)``, and hires has neither — it has no
:data:`machine.TAPED_BANKS` entry at all, so it takes ``taped_plan``'s uniform
quarters, and its tape is 928 slots against 828 with a completely different
middle (240 words of billboards, 66 of numerals, a 128-slot ZBUF).

So this measures hires' own distribution on the same abstract wire the
``deadman-3d`` figures came from (``0 addr`` / ``1 addr value``, ``lm1.store``),
differencing a gameplay run against the boot round alone so the numbers are per
*gameplay* frame rather than per boot.

    python scratch/deadman3d-opt/hires_banks.py [frames]

A gate peels a bank off an **end** of what it is handed, so only some
permutations exist; the script prints whether the traffic order is one of them
by handing it to :func:`memory_taped.gate_chain`, which is the same check
``build`` makes.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "solvers" / "python"))

WAD = Path.home() / "Downloads" / "doom1_0" / "DOOM1.WAD"


class Tracing:
    """A :class:`lm1.store.Store` that records every request's address."""

    def __init__(self, inner) -> None:
        self.inner = inner
        self.log: list[tuple[int, int]] = []  # (op, addr): op 0 read, 1 write
        self._pend: list[int] = []

    def send(self, word: int) -> None:
        self._pend.append(word)
        if len(self._pend) == 2 and self._pend[0] == 0:
            self.log.append((0, self._pend[1]))
            self._pend.clear()
        elif len(self._pend) == 3 and self._pend[0] == 1:
            self.log.append((1, self._pend[1]))
            self._pend.clear()
        self.inner.send(word)

    def recv(self) -> int:
        return self.inner.recv()

    def __getattr__(self, name):  # snapshot(), etc.
        return getattr(self.inner, name)


def main(argv: list[str]) -> int:
    frames = int(argv[0]) if argv else 4

    from randomfun2026solvers import deadman3d as d3
    from randomfun2026solvers import deadman3d_hires as hires
    from randomfun2026solvers import memory_taped as mt
    from randomfun2026solvers.lm1 import machine
    from randomfun2026solvers.lm1.asm import assemble
    from randomfun2026solvers.lm1.emulator import Emulator, Round
    from randomfun2026solvers.lm1.store import DictStore

    hires.install_wad(WAD)
    prog = assemble(hires.hires_source(), name="deadman-3d_hires")
    tape_n = max(d3.tape_slots(d3.GEOM128).values()) + 1
    plan = mt.taped_plan(tape_n, machine.TAPED_BANKS.get("deadman-3d_hires", 4))
    print(f"tape {tape_n} slots, bank plan {tuple(plan)}")
    bounds, lo = [], 1
    for m in plan:
        bounds.append((lo, lo + m - 1))
        lo += m
    for i, (a, b) in enumerate(bounds):
        print(f"  bank {i}: {a}..{b}")

    def run(cmds: list[int]) -> list[tuple[int, int]]:
        tr = Tracing(DictStore())
        Emulator(prog, store=tr).run(
            [Round(input=tuple(hires.input_words(cmds)))], max_instructions=400_000_000)
        return tr.log

    boot = run([])
    play = run(list(hires.WALK[:frames]))
    print(f"boot {len(boot)} accesses, {frames}-frame run {len(play)}")

    def bank_of(addr: int) -> int:
        for i, (a, b) in enumerate(bounds):
            if a <= addr <= b:
                return i
        raise ValueError(f"address {addr} is outside the plan")

    def tally(log):
        r = [0] * len(plan)
        w = [0] * len(plan)
        for op, addr in log:
            (w if op else r)[bank_of(addr)] += 1
        return r, w

    br, bw = tally(boot)
    pr, pw = tally(play)
    dr = [(p - b) / frames for p, b in zip(pr, br, strict=True)]
    dw = [(p - b) / frames for p, b in zip(pw, bw, strict=True)]
    tr_, tw = sum(dr), sum(dw)
    print(f"\nper gameplay frame: {tr_:,.0f} reads, {tw:,.0f} writes")
    print("bank   range          reads            writes")
    for i, (a, b) in enumerate(bounds):
        print(f"  {i}  {a:4d}..{b:4d}  {dr[i]:9,.0f} {100*dr[i]/tr_:6.2f}%  "
              f"{dw[i]:9,.0f} {100*dw[i]/tw:6.2f}%")

    order = tuple(sorted(range(len(plan)), key=lambda i: -(dr[i] + dw[i])))
    print(f"\ntraffic order (reads+writes, descending): {order}")
    for cand in (order, tuple(range(len(plan)))):
        try:
            mt.gate_chain(plan, order=list(cand))
            ok = "reachable"
        except Exception as exc:  # noqa: BLE001 — unreachable IS the answer
            ok = f"REJECTED — {exc}"
        walk_r = sum(dr[b] * j for j, b in enumerate(cand)) / tr_
        walk_w = sum(dw[b] * j for j, b in enumerate(cand)) / tw
        print(f"  {cand}: {ok}; mean gates walked {walk_r:.2f} a read, "
              f"{walk_w:.2f} a write")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
