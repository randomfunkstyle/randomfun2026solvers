"""Watch the fetcher's pc (B) and service-arm visits around the failure."""

from randomfun2026solvers.fast_littleman import FastLittleman, _Machine

src = open("scratch/ram-program/gradebook_ram2.man").read()
fl = FastLittleman(src)
INP = [4, 1, 1222, 51, 2774, 23, 8603, 44, 2303, 76, 2, 1, 1222, 1, 1, 2774, 1, 2, 2, 1222, 1, 77, 1, 1222, 1]
m = _Machine(fl, [INP], None)

FX, FY = 6, 61
RING_Y = FY + 7      # streaming ring main row
SVC = (FX + 15, FY + 8)  # service arm r

orig = _Machine._tick
tick_no = [0]
last_b = [None]


def tick(self):
    orig(self)
    tick_no[0] += 1
    t = tick_no[0]
    if not (33100 <= t <= 33900):
        return
    for r in self.runners:
        if r.halted:
            continue
        x, y = r.pos
        if FX - 1 <= x <= FX + 20 and FY - 1 <= y <= FY + 12:
            if r.pos == SVC:
                print(f"t={t} SERVICE r a={r.a} b={r.b} bp={r.bp}")
            if y == RING_Y and r.b != last_b[0]:
                print(f"t={t} pc(B)={r.b} at x={x} bp={r.bp}")
                last_b[0] = r.b


_Machine._tick = tick
res = m.run(34000)
print("output:", res.output)
