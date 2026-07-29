"""Store reads per frame, and ticks, for any deadman-3d `.man` on disk.

The quantity a program-level lever attacks is *how many reads the CPU issues*,
and ticks alone cannot tell a read that was deleted from a read that got
cheaper. `store:collector->cpu` carries exactly one value per read, so its send
count is an exact census — the same number `scratch/DOOM-OPCODES.md` §2 checks
its opcode mix against.

    uv run python scratch/deadman3d-opt/reads_gate.py <path/to.man> [walk_len]
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from doom_case import gated_case  # noqa: E402

from randomfun2026solvers.fast_littleman import FastLittleman  # noqa: E402

path = Path(sys.argv[1])
case = gated_case(int(sys.argv[2]) if len(sys.argv) > 2 else 8)
src = path.read_text(encoding="utf-8").rstrip("\n")
grid = FastLittleman(src)
t = time.time()
res = grid.run(case.input, frames=case.frames, max_ticks=6_000_000_000, profile=True)
assert res.profile is not None and not res.fatal, (res.reason, res.fatal)

# The CPU is the room the most pipes arrive at. On the taped machine each bank
# answers it directly (the collector teleport is the CPU's own inlet), so a read
# is one value on exactly one of those pipes and the census is their sum. The
# per-bank split is printed too: it is what says which bank a lever moved.
incoming: dict[int, int] = {}
for p in grid.pipes:
    incoming[p.dst] = incoming.get(p.dst, 0) + 1
cpu = max(incoming, key=lambda r: incoming[r])
answers = sorted(
    ((res.profile.send[p.id], p) for p in grid.pipes if p.dst == cpu),
    key=lambda kv: -kv[0],
)
reads = sum(n for n, _ in answers)
rows = len(src.split("\n"))
print(f"{path.name}: {max(len(r) for r in src.split(chr(10)))}x{rows} "
      f"rounds={case.rounds} ({time.time() - t:.0f}s)")
print(f"  ticks      {res.step:>12,}   {res.step / case.rounds:>12,.0f}/frame")
print(f"  reads      {reads:>12,}   {reads / case.rounds:>12,.0f}/frame")
for n, p in answers:
    src_room = grid.rooms[p.src]
    print(f"    <- room {p.src:>3} at {src_room.min}  len={len(p.path):>4}  sends={n:,}")
