"""Word-level profile of the taped DOOM ROM image."""
import collections

from randomfun2026solvers.lm1 import machine as M
from randomfun2026solvers.lm1 import programs
from randomfun2026solvers.lm1 import rom as rommod
from randomfun2026solvers.lm1.seekrom import SEEK_K, build_seek_rom

SLUG = "deadman-3d"
STORE = "taped"

program = programs.load(SLUG)
program = M.seek_split(program, threshold=M.SEEK_THRESHOLD, ops=M.SEEK_OPS)
middle_order = M.LANE_ORDER.get(SLUG)
if middle_order is not None:
    used = {op.mnemonic for op in program.ops_used}
    classic = ("JMPF", "BRZ", "BRN")
    order = list(middle_order)
    at = min((order.index(c) for c in classic if c in order), default=len(order))
    for new in ("JMPS", "BRZS", "BRNS"):
        if new in used and new not in order:
            order.insert(at, new)
            at += 1
    middle_order = order
p = M.plan(program, middle_order=middle_order)

plain = M.rom_words(program, p)
rom_rows = M.SEEK_TIER_LAYOUT[(SLUG, STORE)]["rom_rows"]
budget = rommod.build_packed_rom(plain, rows=rom_rows).width + 4
print(f"P (plain words) = {len(plain)}")
print(f"rom_rows pin = {rom_rows}, plain packed width = {budget - 4}, budget = {budget}")

for extra in range(0, 24):
    words, layout = M.seek_words(program, p, rows=rom_rows + extra)
    if layout.width <= budget:
        break
print(f"seek drum: extra={extra} rows_used={layout.rows_used} {layout.width}x{layout.height}")
print(f"words = {len(words)}")

# ── token width distribution ────────────────────────────────────────────────
instrs = sorted(program.instrs, key=lambda i: i.pos)
targets = {
    k: 1 for k, ins in enumerate(instrs) if ins.sem in M._SEEK_SEMS
}
wide = frozenset(2 * k + 1 for k in targets)
wide_digits = len(str((layout.rows_used + 2) * SEEK_K))
print(f"wide (seek) literals: {len(wide)} at {wide_digits} digits")

toks = [
    (f"`{w:0{wide_digits}d}`s" if i in wide else rommod.token_cells(w))
    for i, w in enumerate(words)
]
total_cells = sum(len(t) for t in toks)
print(f"token cells total = {total_cells}, cells/word = {total_cells/len(words):.3f}")

hist = collections.Counter(len(t) for t in toks)
print("token-cell-length histogram (len -> count, cells, %cells):")
for L in sorted(hist):
    n = hist[L]
    print(f"  {L:2d} cells x {n:5d} words = {n*L:6d} cells ({100*n*L/total_cells:5.1f}%)")

# opcode vs operand split
opc = [w for i, w in enumerate(words) if i % 2 == 0]
opr = [w for i, w in enumerate(words) if i % 2 == 1]
opc_cells = sum(len(toks[i]) for i in range(0, len(words), 2))
opr_cells = sum(len(toks[i]) for i in range(1, len(words), 2))
print(f"opcodes: {len(opc)} words, {opc_cells} cells ({opc_cells/len(opc):.2f}/word), max={max(opc)}")
print(f"operands: {len(opr)} words, {opr_cells} cells ({opr_cells/len(opr):.2f}/word), max={max(opr)}")

# operand value distribution by digit count
dh = collections.Counter(len(str(w)) for w in opr)
print("operand digit histogram:")
for d in sorted(dh):
    print(f"  {d} digits x {dh[d]:5d}")

# what the biggest operands are
big = sorted(((w, i) for i, w in enumerate(opr) if len(str(w)) >= 4), reverse=True)
print(f"operands with >=4 digits: {len(big)}")
print("  top 30:", [w for w, _ in big[:30]])

# mnemonic histogram of the big-operand instructions
mh = collections.Counter()
for w, i in big:
    mh[instrs[i].mnemonic] += 1
print("  by mnemonic:", dict(mh.most_common()))

# ── actual packed geometry ──────────────────────────────────────────────────
lit_rows = layout.rows_used
data_w = layout.width - 8 - 8  # DL=8 ... WRAP=DR+7
print(f"\ndrum: data_w={data_w} rows={lit_rows} -> data area {data_w*lit_rows}")
print(f"packing efficiency: {total_cells}/{data_w*lit_rows} = {100*total_cells/(data_w*lit_rows):.1f}%")
print(f"drum box {layout.width}x{layout.height} = {layout.width*layout.height} cells")
print(f"cells/word incl. all drum overhead = {layout.width*layout.height/len(words):.3f}")
