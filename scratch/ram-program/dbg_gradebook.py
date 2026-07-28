"""Trace the RAM machine's fetch stream on the Python fast engine."""

from randomfun2026solvers.fast_littleman import FastLittleman, _Machine

src = open("scratch/ram-program/gradebook_ram2.man").read()
fl = FastLittleman(src)
INP = [4, 1, 1222, 51, 2774, 23, 8603, 44, 2303, 76, 2, 1, 1222, 1, 1, 2774, 1, 2, 2, 1222, 1, 77, 1, 1222, 1]
m = _Machine(fl, [INP], None)

OPR = (192, 81)  # the fetch row's operand r
log: list[tuple[int, int, int]] = []
orig = _Machine._tick
tick_no = [0]


def tick(self):
    orig(self)
    tick_no[0] += 1
    for r in self.runners:
        if not r.halted and r.pos == OPR:
            log.append((tick_no[0], r.bp, r.a))


_Machine._tick = tick
res = m.run(40000)
print("output:", res.output)
print("fetches:", len(log))
print("first 60 (tick,opcode):", [(t, bp) for t, bp, _ in log[:60]])
