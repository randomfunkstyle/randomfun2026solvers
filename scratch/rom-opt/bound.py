"""Decompose the drum's 12% blank: parity rule vs even-word rule vs row ends."""
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


def pack(width, *, parity_on=True, even_on=True):
    parity = [0] * width
    bad = [False] * width
    dig = [0] * width

    def feasible(col, g):
        if not parity_on:
            return True
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

    nrows = i = slide = 0
    while i < len(toks):
        east = nrows % 2 == 0
        cur = 0 if east else width - 1
        placed = []
        row_i = i
        while row_i < len(toks):
            gl = toks[row_i] if east else toks[row_i][::-1]
            st0 = cur if east else cur - len(gl) + 1
            st = st0
            while not ok(st, gl) and 0 <= st and st + len(gl) <= width:
                st += 1 if east else -1
            if not ok(st, gl):
                break
            slide += abs(st - st0)
            for j, g in enumerate(gl):
                commit(st + j, g)
            placed.append((st, gl))
            cur = st + len(gl) if east else st - 1
            row_i += 1
        if row_i == i:
            return None, None
        if even_on and (row_i - i) % 2 == 1 and row_i < len(toks):
            st, gl = placed.pop()
            for j, g in enumerate(gl):
                if g == "`":
                    parity[st + j] -= 1
            row_i -= 1
        nrows += 1
        i = row_i
    return nrows, slide


print(f"tokens {tot} cells, data_w={data_w}, ideal rows = {tot/data_w:.1f}")
for parity_on in (True, False):
    for even_on in (True, False):
        n, sl = pack(data_w, parity_on=parity_on, even_on=even_on)
        print(
            f"  parity={parity_on!s:5s} even={even_on!s:5s} -> rows={n:3d} "
            f"area={n*data_w:6d} blank={n*data_w-tot:5d} ({100*(n*data_w-tot)/(n*data_w):4.1f}%) "
            f"slide_cells={sl}"
        )
