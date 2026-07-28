"""How much of the drum's 12% blank is actually recoverable?

The word ORDER is fixed (the drum emits the program in order), so the only
freedom is where each token starts: >= the leftmost feasible column. Greedy
leftmost is optimal within a row but myopic across rows, because where the
backticks land sets the next row's parity. This tries a lookahead: at each
placement, consider the leftmost L feasible starts and score by how many
tokens the rest of the row then fits.
"""
import sys

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
R, data_w = layout.rows_used, layout.width - 16
instrs = sorted(program.instrs, key=lambda i: i.pos)
wide = frozenset(2 * k + 1 for k, ins in enumerate(instrs) if ins.sem in M._SEEK_SEMS)
wd = len(str((R + 2) * SR.SEEK_K))
toks = [(f"`{w:0{wd}d}`s" if i in wide else rommod.token_cells(w)) for i, w in enumerate(words)]
tot = sum(len(t) for t in toks)
print(f"tokens {len(toks)} cells {tot}; shipped {R} rows x {data_w} = {R*data_w}")
print(f"absolute floor: ceil({tot}/{data_w}) = {-(-tot//data_w)} rows")


def rows_needed(width, lookahead):
    parity = [0] * width
    bad = [False] * width
    dig = [0] * width

    def feasible(col, g):
        if g == "`":
            return parity[col] % 2 == 0 or (not bad[col] and dig[col] <= 18)
        if not (g.isdigit() or g == " "):
            return parity[col] % 2 == 0
        return True

    def ok(start, glyphs):
        return 0 <= start and start + len(glyphs) <= width and all(
            feasible(start + j, g) for j, g in enumerate(glyphs)
        )

    def commit(col, g):
        if g == "`":
            parity[col] += 1
            bad[col] = False
            dig[col] = 0
        elif parity[col] % 2 == 1:
            if g.isdigit():
                dig[col] += 1
            elif g != " ":
                bad[col] = True

    def snap():
        return parity[:], bad[:], dig[:]

    def restore(s):
        parity[:], bad[:], dig[:] = s[0][:], s[1][:], s[2][:]

    def trial(start, glyphs, cur, east, i, depth):
        """How many further tokens fit if this one goes at `start`."""
        s = snap()
        for j, g in enumerate(glyphs):
            commit(start + j, g)
        c = start + len(glyphs) if east else start - 1
        n = 0
        k = i + 1
        while k < len(toks) and n < depth:
            gl = toks[k] if east else toks[k][::-1]
            st = c if east else c - len(gl) + 1
            while not ok(st, gl) and 0 <= st and st + len(gl) <= width:
                st += 1 if east else -1
            if not ok(st, gl):
                break
            for j, g in enumerate(gl):
                commit(st + j, g)
            c = st + len(gl) if east else st - 1
            n += 1
            k += 1
        restore(s)
        return n, (c if east else width - 1 - c)

    nrows = 0
    i = 0
    while i < len(toks):
        east = nrows % 2 == 0
        cur = 0 if east else width - 1
        placed = []
        row_i = i
        while row_i < len(toks):
            gl = toks[row_i] if east else toks[row_i][::-1]
            st = cur if east else cur - len(gl) + 1
            while not ok(st, gl) and 0 <= st and st + len(gl) <= width:
                st += 1 if east else -1
            if not ok(st, gl):
                break
            best = st
            if lookahead:
                bn, bc = trial(st, gl, cur, east, row_i, lookahead)
                for alt in range(1, 4):
                    a = st + (alt if east else -alt)
                    if not ok(a, gl):
                        continue
                    n2, c2 = trial(a, gl, cur, east, row_i, lookahead)
                    if (n2, -c2) > (bn, -bc):
                        bn, bc, best = n2, c2, a
            for j, g in enumerate(gl):
                commit(best + j, g)
            placed.append((best, gl))
            cur = best + len(gl) if east else best - 1
            row_i += 1
        count = row_i - i
        if count == 0:
            return None
        if count % 2 == 1 and row_i < len(toks):
            start, gl = placed.pop()
            for j, g in enumerate(gl):
                if g == "`":
                    parity[start + j] -= 1
            row_i -= 1
        nrows += 1
        i = row_i
    return nrows


for la in (0, 4, 12):
    for w in (data_w, data_w - 5, data_w - 10, data_w - 15, data_w - 20):
        n = rows_needed(w, la)
        print(f"  lookahead={la:2d} width={w:4d} -> rows={n}  area={n*w if n else '-'}")
    sys.stdout.flush()
