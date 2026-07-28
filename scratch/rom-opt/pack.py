"""Where the seek drum's data area goes: tokens, blanks, and why."""
import collections

from randomfun2026solvers.lm1 import machine as M
from randomfun2026solvers.lm1 import programs
from randomfun2026solvers.lm1 import rom as rommod
from randomfun2026solvers.lm1 import seekrom as SR

SLUG, STORE = "deadman-3d", "taped"
program = M.seek_split(programs.load(SLUG), threshold=M.SEEK_THRESHOLD, ops=M.SEEK_OPS)
mo = list(M.LANE_ORDER[SLUG])
used_m = {op.mnemonic for op in program.ops_used}
at = min((mo.index(c) for c in ("JMPF", "BRZ", "BRN") if c in mo), default=len(mo))
for new in ("JMPS", "BRZS", "BRNS"):
    if new in used_m and new not in mo:
        mo.insert(at, new)
        at += 1
p = M.plan(program, middle_order=mo, slots=M.OPCODE_SLOTS.get((SLUG, STORE)))

plain = M.rom_words(program, p)
rr = M.SEEK_TIER_LAYOUT[(SLUG, STORE)]["rom_rows"]
budget = rommod.build_packed_rom(plain, rows=rr).width + 4
for extra in range(0, 24):
    words, layout = M.seek_words(program, p, rows=rr + extra)
    if layout.width <= budget:
        break
R = layout.rows_used
data_w = layout.width - 16
print(f"P={len(words)} rows={R} data_w={data_w} drum {layout.width}x{layout.height}")

instrs = sorted(program.instrs, key=lambda i: i.pos)
targets = {k for k, ins in enumerate(instrs) if ins.sem in M._SEEK_SEMS}
wide = frozenset(2 * k + 1 for k in targets)
wd = len(str((R + 2) * SR.SEEK_K))
toks = [(f"`{w:0{wd}d}`s" if i in wide else rommod.token_cells(w)) for i, w in enumerate(words)]
tot = sum(len(t) for t in toks)
area = data_w * R
print(f"token cells {tot} ({tot/len(words):.3f}/word); data area {area}; "
      f"blank {area-tot} = {100*(area-tot)/area:.1f}%")

lit = SR.pack_rows_even(toks, data_w)
per_row = [r.count("s") for r in lit]
print(f"words/row: min {min(per_row)} max {max(per_row)} mean {sum(per_row)/R:.1f}")

# per-row blank accounting: leading run, trailing run, interior gaps
lead = trail = interior = 0
odd_drop = 0
for i, row in enumerate(lit):
    east = i % 2 == 0
    body = row if east else row[::-1]
    st = body.lstrip(" ")
    lead += len(body) - len(st)
    tr = len(st) - len(st.rstrip(" "))
    trail += tr
    interior += st.strip(" ").count(" ")
print(f"blanks: head-of-row {lead}, tail-of-row {trail}, interior {interior}")
hist = collections.Counter()
for i, row in enumerate(lit):
    body = row if i % 2 == 0 else row[::-1]
    hist[len(body) - len(body.rstrip(" "))] += 1
print("tail-blank histogram (east-relative):", dict(sorted(hist.items())))

# what an unconstrained (parity-free) pack would cost
print("\n-- controls --")
plainrows = rommod.pack_data_rows(toks, data_w)
pt = sum(len(r.replace(" ", "")) for r in plainrows)
print(f"pack_data_rows (no even rule) at data_w={data_w}: {len(plainrows)} rows")
lo = SR._width_for_rows_even(toks, R)
print(f"narrowest even-pack at {R} rows: data_w={lo} (shipped {data_w})")
for r in (R - 4, R - 2, R, R + 2, R + 4, R + 8):
    try:
        w = SR._width_for_rows_even(toks, r)
        print(f"  rows={r:3d} -> data_w={w:4d}  area={w*r:6d}  drum_w={w+16}")
    except Exception as exc:
        print(f"  rows={r:3d} -> {exc}")
