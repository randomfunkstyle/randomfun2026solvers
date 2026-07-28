"""Log the words the flush loop consumes, and the fetch stream after it."""

from randomfun2026solvers.fast_littleman import FastLittleman, _Machine

src = open("scratch/ram-program/gradebook_ram2.man").read()
fl = FastLittleman(src)
INP = [4, 1, 1222, 51, 2774, 23, 8603, 44, 2303, 76, 2, 1, 1222, 1, 1, 2774, 1, 2, 2, 1222, 1, 77, 1, 1222, 1]
m = _Machine(fl, [INP], None)

FLUSH_R = (191, 108)   # flush loop's r
OPR = (193, 81)        # cell after the fetch operand r (A = operand, BP = opcode)
SEND = (188 + 46, 81 + 106 - 81)  # unused; placeholder

log: list[str] = []
orig = _Machine._tick
tick_no = [0]
prev = {}


def tick(self):
    orig(self)
    tick_no[0] += 1
    t = tick_no[0]
    if t < 33000:
        return
    for r in self.runners:
        if r.halted:
            continue
        if r.pos == FLUSH_R:
            log.append(f"t={t} FLUSH a={r.a}")
        if r.pos == OPR:
            log.append(f"t={t} FETCHED op={r.bp} operand={r.a}")


_Machine._tick = tick
res = m.run(36000)
print("output:", res.output)
seen = set()
for line in log:
    key = line.split(" ", 1)[1]
    if (key, len(seen)) not in seen:
        pass
    print(line)
