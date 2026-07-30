import json, sys
sys.path.insert(0, "/tmp/menprobe")
from common import setup

d3, hires, M, prog = setup()
names = {}
for name, addr in prog.equs.items():
    names.setdefault(addr, []).append(name)
data = json.load(open("/tmp/menprobe/traffic21.json"))
acc = {}
for key in ("tour_reads", "tour_writes"):
    for a, c in data[key].items():
        acc[int(a)] = acc.get(int(a), 0) + c
tot = sum(acc.values())
print(f"total tour accesses {tot:,}")
print("addr  accesses  share  reads   writes  equ name(s)")
for a, c in sorted(acc.items(), key=lambda kv: -kv[1])[:34]:
    r = data["tour_reads"].get(str(a), 0)
    w = data["tour_writes"].get(str(a), 0)
    print(f"{a:4d} {c:9,} {100*c/tot:6.2f}% {r:7,} {w:7,}  {','.join(names.get(a, ['-']))}")
