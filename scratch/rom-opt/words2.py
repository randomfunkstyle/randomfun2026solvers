"""Deeper: opcode-number cost, operand cost by mnemonic, renumbering headroom."""
import collections

from randomfun2026solvers.lm1 import machine as M
from randomfun2026solvers.lm1 import programs
from randomfun2026solvers.lm1 import rom as rommod

SLUG, STORE = "deadman-3d", "taped"
program = programs.load(SLUG)
program = M.seek_split(program, threshold=M.SEEK_THRESHOLD, ops=M.SEEK_OPS)
middle_order = M.LANE_ORDER.get(SLUG)
used_m = {op.mnemonic for op in program.ops_used}
order = list(middle_order)
at = min((order.index(c) for c in ("JMPF", "BRZ", "BRN") if c in order), default=len(order))
for new in ("JMPS", "BRZS", "BRNS"):
    if new in used_m and new not in order:
        order.insert(at, new)
        at += 1
middle_order = order
p = M.plan(program, middle_order=middle_order)
instrs = sorted(program.instrs, key=lambda i: i.pos)
print(f"{len(instrs)} instructions, k={p.k}, lanes={p.lanes}, {len(p.number)} opcodes")

hist = collections.Counter(i.mnemonic for i in instrs)
print(f"\n{'mnem':7s} {'count':>6s} {'code':>5s} {'slot':>5s} {'cells':>6s}  opcode-cell cost")
tot = 0
for m, n in hist.most_common():
    code = p.number[m]
    slot = p.row[m] // 2
    c = len(rommod.token_cells(code))
    tot += c * n
    print(f"{m:7s} {n:6d} {code:5d} {slot:5d} {c:6d}  {c*n:7d}")
print(f"opcode cells total = {tot}")

# best possible: greedily give the 10 single-digit codes to the 10 hottest
counts = sorted(hist.values(), reverse=True)
best = sum(2 * c for c in counts[:10]) + sum(5 * c for c in counts[10:])
print(f"best possible opcode cells (10 hottest single-digit) = {best}  (save {tot-best})")

# which slots yield a single-digit code?
single = sorted(M._bitrev(n, p.k) for n in range(10))
print(f"slots giving codes 0..9: {single}")
print("current slot -> mnemonic:")
by_slot = {p.row[m] // 2: m for m in p.number}
print("  " + " ".join(f"{s}:{by_slot.get(s,'-')}" for s in range(p.lanes)))

# ── operand cost by mnemonic ────────────────────────────────────────────────
print(f"\n{'mnem':7s} {'count':>6s} {'oper cells':>10s} {'avg':>6s}  distinct  min..max")
rows = []
for m in hist:
    vals = []
    for k, ins in enumerate(instrs):
        if ins.mnemonic != m:
            continue
        if ins.sem in M._SEEK_SEMS:
            vals.append(("wide", 8))
        elif ins.sem in M.TARGET_SEMS:
            t = M._target_index(program, instrs, {i.pos: j for j, i in enumerate(instrs)}, k)
            v = 2 * ((t - k - 1) % len(instrs))
            vals.append((v, len(rommod.token_cells(v))))
        else:
            v = 0 if ins.operand is None else ins.operand
            vals.append((v, len(rommod.token_cells(v))))
    cells = sum(c for _, c in vals)
    nums = [v for v, _ in vals if v != "wide"]
    rows.append((cells, m, len(vals), cells / len(vals), len(set(nums)), min(nums) if nums else 0, max(nums) if nums else 0))
for cells, m, n, avg, distinct, lo, hi in sorted(rows, reverse=True):
    print(f"{m:7s} {n:6d} {cells:10d} {avg:6.2f}  {distinct:7d}  {lo}..{hi}")
print(f"operand cells total = {sum(r[0] for r in rows)}")
