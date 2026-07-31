"""Decide every captured (ROM_TOUCH_DROP, pad) geometry exactly, and ask Z3 what
`rom` would need to be if it may only slide in y (which is all the knob does)."""
import json, os, sys
from pathlib import Path
sys.path.insert(0, str(Path(os.environ["CLAUDE_JOB_DIR"]) / "tmp"))
import z3
from z3bind import model_for, shipped_ties, by  # noqa: E402

recs = json.loads((Path(os.environ["CLAUDE_JOB_DIR"]) / "tmp" / "knobs.json").read_text())
print("=== exact decision per captured geometry ===")
for r in recs:
    s, _, _ = model_for(r, free=())
    res = s.check()
    if res == z3.sat or r["pad"] == 1:
        print(f"  drop={r['drop']:2} pad={r['pad']}: {res}   rom={tuple(r['touches']['rom'])}")

print("\n=== pad 1: what y could `rom` take, with x pinned at 7? ===")
base = [r for r in recs if r["drop"] == 9 and r["pad"] == 1][0]
s, T, _ = model_for(base, free={"rom"}, box=(0, base["w"] - 1, 0, base["h"] - 1))
s.add(T["rom"][0] == 7)
print(f"  x=7: {s.check()}")
s2, T2, _ = model_for(base, free={"rom"}, box=(0, base["w"] - 1, 0, base["h"] - 1))
print(f"  x free: {s2.check()}")
if s2.check() == z3.sat:
    m = s2.model()
    print(f"    witness {(m[T2['rom'][0]].as_long(), m[T2['rom'][1]].as_long())}")
    # enumerate the feasible x values, to see how far west it must come
    xs = []
    for x in range(0, 60):
        s3, T3, _ = model_for(base, free={"rom"}, box=(0, base["w"] - 1, 0, base["h"] - 1))
        s3.add(T3["rom"][0] == x)
        if s3.check() == z3.sat:
            m3 = s3.model()
            xs.append((x, m3[T3["rom"][1]].as_long()))
    print(f"    feasible rom x (with a witness y): {xs}")
