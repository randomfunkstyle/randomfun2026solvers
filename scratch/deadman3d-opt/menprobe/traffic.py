"""Fresh per-address store traffic census over the real 21-round tour.

Writes /tmp/menprobe/traffic21.json (temp dir; nothing WAD-derived committed).
"""
import json
import sys
sys.path.insert(0, "/tmp/menprobe")
from common import setup, SLUG, WT

sys.path.insert(0, str(WT / "scratch" / "deadman3d-opt"))

d3, hires, M, prog = setup()
from randomfun2026solvers.lm1.emulator import Emulator, Round
from randomfun2026solvers.lm1.store import DictStore
from hires_banks import Tracing


def run(cmds):
    tr = Tracing(DictStore())
    Emulator(prog, store=tr).run([Round(input=tuple(hires.input_words(cmds)))],
                                 max_instructions=400_000_000)
    return tr.log


boot = run([])
print(f"boot accesses {len(boot):,}", flush=True)
cmds = list(hires.WALK[:20])
play = run(cmds)
print(f"tour(20 cmds) accesses {len(play):,}", flush=True)

def tally(log, key):
    out = {}
    for op, addr in log:
        if op == key:
            out[addr] = out.get(addr, 0) + 1
    return out

data = {"tape_n": M.TAPE_SIZE[SLUG], "frames": 20,
        "boot_reads": tally(boot, 0), "boot_writes": tally(boot, 1),
        "tour_reads": tally(play, 0), "tour_writes": tally(play, 1),
        "boot_n": len(boot), "tour_n": len(play)}

R = data["tour_reads"]; W = data["tour_writes"]
tot_r = sum(R.values()); tot_w = sum(W.values())
print(f"tour reads {tot_r:,}  writes {tot_w:,}  total {tot_r+tot_w:,}")
allacc = {}
for a, c in R.items():
    allacc[a] = allacc.get(a, 0) + c
for a, c in W.items():
    allacc[a] = allacc.get(a, 0) + c
top = sorted(allacc.items(), key=lambda kv: -kv[1])
print("rank addr  accesses  share  cum")
cum = 0
for i, (a, c) in enumerate(top[:40]):
    cum += c
    print(f"{i:4d} {a:4d} {c:9,} {100*c/(tot_r+tot_w):6.2f}% {100*cum/(tot_r+tot_w):6.2f}%")
cum = 0
marks = {}
for i, (a, c) in enumerate(top):
    cum += c
    for k in (8, 16, 32, 64, 128, 256, 512):
        if i + 1 == k:
            marks[k] = 100 * cum / (tot_r + tot_w)
print("cumulative share by hottest k slots:", {k: round(v, 2) for k, v in marks.items()})
print("distinct slots touched:", len(allacc), "of", data["tape_n"])

with open("/tmp/menprobe/traffic21.json", "w") as f:
    json.dump({str(k): v for k, v in [("meta", data["tape_n"])]} | {
        "tour_reads": {str(k): v for k, v in R.items()},
        "tour_writes": {str(k): v for k, v in W.items()},
        "boot_reads": {str(k): v for k, v in data["boot_reads"].items()},
        "boot_writes": {str(k): v for k, v in data["boot_writes"].items()},
    }, f)
print("wrote /tmp/menprobe/traffic21.json")
