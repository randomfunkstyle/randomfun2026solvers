"""LM-1 with a stored program: ROM boots a men-v3 RAM, a fetcher demand-feeds the CPU.

Stage-1 prototype of the RAM-program machine (replacing the looping-drum
instruction supply). The architecture:

* The **ROM drum** survives only as the boot image. At power-on a **loader/
  fetcher man** (one man, one room, two phases) copies every program word into a
  dedicated **men-v3 program store** (word ``j`` at address ``j + 1``; address 0
  is unused so a jump target is always positive). All writes strictly precede
  all reads because they are issued by the same man down the same pipe.
* The fetcher then holds the **PC in his B hand** and serves commands from the
  CPU: ``0`` = next (pc += 2), ``t > 0`` = jump (pc = t). Each command issues
  two READs (pc, pc+1) to the store; the store's answers flow straight into the
  CPU's fetch corridor. Exactly one command is outstanding at a time — this is
  **demand fetch**, the correctness-simple Stage-1 design: nothing is ever in
  flight past a taken branch, so there is no flush protocol at all.
* The CPU is ``machine.build_cpu`` with a three-cell change to the fetch row —
  ``>0>s rbr`` instead of ``>rbr`` — and the discard loops deleted: a taken
  jump/branch carries its **absolute target address** in ``A`` down to a shared
  ``taken row`` and back up a private riser (col 3) that rejoins the fetch row
  *after* the ``0``, so the one shared ``s`` at (4, centre) sends either the 0
  the normal path loaded or the target the jump path carried. Jump cost is
  geometry (a walk), not ``8 x (P - L)`` recirculation.

``build_ram_cpu`` below is a modified copy of :func:`machine.build_cpu` (the
fetch-row / slab surgery is invasive enough that a flag in the original would
obscure both); drift is accepted for this exploration branch and called out in
the header of each copied block.

Verification: ``optimize.verify(machine.rows, slug)`` on the fast engine — the
same ladder as the shipped machines.
"""

from __future__ import annotations

from dataclasses import dataclass

from . import rom as rommod
from .asm import Program
from .isa import TARGET_SEMS, Sem
from .machine import (
    ADAPTER_IN_ROW,
    ADAPTER_OUT_ROW,
    ADAPTER_W,
    Band,
    MEMORY_SEMS,
    MachineError,
    _BRANCH_SEMS,
    _Cpu,
    _flat_lane,
    _Grid,
    _JUMP_SEMS,
    _HW,
    _Plan,
    _SLAB_PITCH,
    adapter_cells,
    check_bindings,
    hw_micro,
    plan,
    tape_block,
    _highest_address,
)

__all__ = ["build_ram", "ram_words", "digit_factors"]

#: Slabs hug this column in RAM mode; east of the jump riser (col 3) and the
#: shared send site (col 4), so a slab's own cells never collide with them.
_STRUCT_X0_RAM = 5
#: The fetch row grew from ``>rbr`` (cols 1..4) to ``>0>s rbr`` (cols 1..7), so
#: the trie and everything east of it shift by this much.
_F = 3


def digit_factors(n: int) -> tuple[int, str]:
    """The smallest product of digits 2..9 that is >= ``n``, and its glyphs.

    The fetcher needs ``BP = word count`` without a backtick literal (a literal
    column could pair vertically with the ROM's or the store's backticks, which
    is a load error — SPEC.md fine print). Any count is covered by padding the
    image with a few zero words up to a product of single digits; the glyph
    string builds it: ``d0 M d1 * M d2 * ...`` leaves the product in A.
    """
    if n <= 9:
        return n, str(n)
    best: tuple[int, str] | None = None
    # BFS over products; depth 4 covers everything to 6561 which is plenty.
    frontier: list[tuple[int, str]] = [(d, str(d)) for d in range(2, 10)]
    for _ in range(3):
        nxt = []
        for v, s in frontier:
            for d in range(2, 10):
                nv, ns = v * d, s + f"M{d}*"
                if nv >= n and (best is None or (nv, len(ns)) < (best[0], len(best[1]))):
                    best = (nv, ns)
                elif nv < n:
                    nxt.append((nv, ns))
        frontier = nxt
    if best is None:
        raise MachineError(f"no digit-product cover for {n}")
    return best


def ram_words(program: Program, p: _Plan) -> list[int]:
    """Fixed-width two-word image with **absolute** jump targets.

    Same re-encoding as :func:`machine.rom_words` except a jump/branch operand
    becomes the *store address of the target instruction's opcode word*:
    word ``j`` lives at address ``j + 1``, instruction ``t`` starts at word
    ``2t``, so the operand is ``2t + 1``. The fetcher sets ``pc`` to it directly.
    """
    instrs = sorted(program.instrs, key=lambda i: i.pos)
    index_of_word = {ins.pos: k for k, ins in enumerate(instrs)}
    out: list[int] = []
    for ins in instrs:
        if ins.sem in TARGET_SEMS:
            assert ins.operand is not None
            after = (ins.pos + ins.words) % program.P
            target_word = (after + ins.operand) % program.P
            if target_word not in index_of_word:
                raise MachineError(
                    f"{ins.mnemonic} at word {ins.pos} jumps to word {target_word}, "
                    "which is not an instruction boundary"
                )
            operand = 2 * index_of_word[target_word] + 1
        elif ins.operand is None:
            operand = 0
        else:
            operand = ins.operand
            if ins.sem in MEMORY_SEMS and operand < 1:
                raise MachineError(
                    f"{ins.mnemonic} at word {ins.pos} addresses STORE slot "
                    f"{operand}; hardware addresses start at 1"
                )
        out += [p.number[ins.mnemonic], operand]
    return out


def build_ram_cpu(program: Program, p: _Plan, *, mem_pad: int = 0) -> tuple[_Cpu, int]:
    """The CPU room for RAM fetch. Modified copy of :func:`machine.build_cpu`.

    Differences, all supply-side (the decode trie and every lane are verbatim):

    * fetch row ``>0>s rbr`` — send "next" (or a jump target) on the ``cmd``
      pipe, then read opcode and operand off the answer pipe;
    * jump/branch slabs send instead of discarding: the taken path carries the
      absolute target in ``A`` to a shared *taken row* below the slabs, west to
      a riser at col 3, and up onto the ``>`` at (3, centre) — past the ``0``;
    * no discard loop, no drain, no ROM recirculation.

    Returns the CPU and the taken row (for the region map).
    """
    k, lanes = p.k, p.lanes
    centre = 1 << k
    span = 2 * lanes - 1
    lane_x0 = 5 + _F + k
    used = list(p.number)
    by_row = {p.row[m]: m for m in used}
    all_rows = list(range(1, span + 1, 2))

    flat = {m: hw_micro(p.sem[m]) for m in used if p.sem[m] in _HW}
    structured = [m for m in used if p.sem[m] in _JUMP_SEMS | _BRANCH_SEMS]
    halting = {p.row[m] for m in used if p.sem[m] is Sem.HALT}

    prefixes = [
        next((i for i, (_, b) in enumerate(mc) if b == Band.MEM), len(mc))
        for mc in flat.values()
        if any(b == Band.MEM for _, b in mc)
    ]
    mem_x = lane_x0 + (max(prefixes) if prefixes else 0) + mem_pad
    band_x = {Band.MEM: mem_x}
    # Display/stream bands unsupported in the Stage-1 RAM prototype.
    if any(b not in (Band.MEM, Band.IN, Band.OUT, None) for mc in flat.values() for _, b in mc):
        raise MachineError("ram_machine stage 1 supports only IN/OUT/MEM/ALU programs")

    slab_rows = {m: (2 if p.sem[m] in _JUMP_SEMS else 5) for m in structured}
    struct_east = _STRUCT_X0_RAM + max(1, len(structured)) * _SLAB_PITCH

    # ── lane extents (verbatim, except the jump preamble is a nop) ───────────
    lane_cells: dict[tuple[int, int], tuple[str, str | None]] = {}
    lane_end: dict[int, int] = {}
    for r in all_rows:
        m = by_row.get(r)
        if m is None:
            lane_end[r] = lane_x0 - 1
        elif m in flat:
            cells = _flat_lane(flat[m], lane_x0, band_x, r)
            lane_cells.update(cells)
            lane_end[r] = max((x for x, _ in cells), default=lane_x0 - 1)
        else:
            pre = "." if p.sem[m] in _JUMP_SEMS else "W"
            lane_cells[(lane_x0, r)] = (pre, None)
            lane_end[r] = lane_x0

    # ── drop columns (short_return rule, verbatim) ───────────────────────────
    drop_x: dict[int, int] = {}
    assigned: set[int] = set()
    floor = lane_x0
    for r in sorted(all_rows, reverse=True):
        floor = max(floor, lane_end[r] + 1)
        if r in halting:
            continue
        m = by_row.get(r)
        if m is not None and m in slab_rows:
            c = max(floor, struct_east + 1)
            while c in assigned:
                c += 1
        else:
            c = floor
            if c > struct_east:
                while c in assigned:
                    c += 1
        drop_x[r] = c
        assigned.add(c)

    order = sorted(structured, key=lambda m: drop_x[p.row[m]])
    slab_at: dict[str, int] = {}
    slab_base: dict[str, int] = {}
    collector = span + 1
    for i, m in enumerate(order):
        slab_at[m] = collector + 1 + i
        slab_base[m] = _STRUCT_X0_RAM + i * _SLAB_PITCH
    # The taken row sits below every slab body; +1 keeps a branch's pos arm row
    # (s0 + 3) clear of it even for the deepest slab.
    taken_row = max((slab_at[m] + slab_rows[m] for m in order), default=collector + 1) + 1
    bottom = taken_row + 1

    g = _Grid()
    pipe_glyphs: list[tuple[int, int, str, str]] = []

    def emit(x: int, yy: int, glyph: str, band: str | None) -> None:
        g.put(x, yy, glyph)
        if glyph in "rs" and band:
            pipe_glyphs.append((x, yy, glyph, band))

    # ── fetch row: send "next"/target, then read opcode and operand ──────────
    g.text(1, centre, ">0>srbr")
    pipe_glyphs += [
        (4, centre, "s", "cmd"),
        (5, centre, "r", "rom"),
        (7, centre, "r", "rom"),
    ]

    # ── decode trie, shifted east by _F (verbatim otherwise) ─────────────────
    def trie(level: int, row: int) -> None:
        col, step = 4 + _F + level, 1 << (k - level)
        g.put(col, row, "x")
        for sign in (-1, +1):
            for d in range(1, step + 1):
                g.put(col, row + sign * d, ">" if d == step else ("]" if d == 1 else "."))
            if level < k:
                trie(level + 1, row + sign * step)

    trie(1, centre)

    for (x, yy), (glyph, band) in lane_cells.items():
        emit(x, yy, glyph, band)
    for r in all_rows:
        if r in halting:
            continue
        for x in range(lane_end[r] + 1, drop_x[r]):
            g.soft(x, r, ".")

    # ── the jump riser: col 3 from the taken row up onto (3, centre) '>' ─────
    # Drawn before the collector's soft '<' run so its crossing stays a '.'.
    for yy in range(centre + 1, taken_row):
        g.soft(3, yy, ".")
    g.put(3, taken_row, "^")

    # ── drops (verbatim) ─────────────────────────────────────────────────────
    for r in all_rows:
        if r in halting:
            continue
        g.put(drop_x[r], r, "v")
    for r in all_rows:
        if r in halting:
            continue
        m = by_row.get(r)
        stop = slab_at[m] if (m is not None and m in slab_at) else collector
        for yy in range(r + 1, stop):
            g.soft(drop_x[r], yy, ".")

    # ── structures band: send-the-target slabs ───────────────────────────────
    taken_drops: list[int] = []
    struct_drops: set[int] = set()
    for m in order:
        s0, base = slab_at[m], slab_base[m]
        sem = p.sem[m]
        if sem in _JUMP_SEMS:
            # Entry row runs west (drawn below); at ``base`` the man turns south
            # and drops to the taken row with the target still in A.
            g.put(base, s0, "v")
            for yy in range(s0 + 1, taken_row):
                g.soft(base, yy, ".")
            taken_drops.append(base)
        else:
            # X fan-out, verbatim from machine._slab; the taken arm drops south
            # instead of entering a discard loop.
            g.soft(base, s0 + 1, ".")
            g.put(base, s0 + 2, ">")
            g.put(base + 1, s0 + 2, "X")
            g.put(base + 1, s0 + 1, ">")
            g.put(base + 1, s0 + 3, ">")
            rows = {"neg": s0 + 1, "zero": s0 + 2, "pos": s0 + 3}
            cols = {"neg": base + 9, "zero": base + 6, "pos": base + 3}
            taken = "zero" if sem is Sem.BR_ZERO else "neg"
            for arm, row in rows.items():
                g.put(base + 2, row, "W")
                for c in range(base + 3, cols[arm]):
                    g.soft(c, row, ".")
                if arm == taken:
                    g.put(cols[arm], row, "v")
                    for yy in range(row + 1, taken_row):
                        g.soft(cols[arm], yy, ".")
                    taken_drops.append(cols[arm])
                else:
                    g.put(cols[arm], row, "^")
                    for yy in range(collector + 1, row):
                        g.soft(cols[arm], yy, ".")
                    struct_drops.add(cols[arm])

    # ── entry rows, drawn last so crossing drops keep their '.' holes ────────
    for m in order:
        s0, dx = slab_at[m], drop_x[p.row[m]]
        base = slab_base[m]
        g.put(dx, s0, "<")
        for x in range(base + 1, dx):
            g.soft(x, s0, "<")
        g.put(base, s0, "v")  # jump: down to the taken row; branch: down into the X

    # ── the taken row: everything lands here and walks west to the riser ─────
    if taken_drops:
        for x in range(4, max(taken_drops) + 1):
            g.soft(x, taken_row, "<")

    # ── collector -> west riser -> back into the fetch row (verbatim) ────────
    ret_x = max([*drop_x.values(), *struct_drops, *taken_drops])
    for x in range(3, ret_x + 1):
        g.soft(x, collector, "<")
    g.put(1, collector, "^")
    for yy in range(centre + 1, collector):
        g.soft(1, yy, ".")
    g.put(2, collector, "@")

    through = {drop_x[p.row[m]] for m in order}
    clash = {r: c for r, c in drop_x.items() if c in through and by_row.get(r) not in order}
    if clash:
        raise MachineError(
            f"simple lane drop column(s) {sorted(set(clash.values()))} collide with a "
            "slab entry column"
        )

    width = ret_x + 1
    height = bottom
    mem_rows = sorted(
        r
        for r in all_rows
        if by_row.get(r) in flat and any(b == Band.MEM for _, b in flat[by_row[r]])
    )
    mem_out_row = mem_rows[len(mem_rows) // 2] if mem_rows else centre
    in_rows = [p.row[m] for m in used if p.sem[m] is Sem.INPUT]

    regions: dict[str, tuple[int, int, int, int]] = {
        "fetch": (1, centre, 7, 1),
        "trie": (5 + _F, 1, k, span),
        "return:riser": (1, centre + 1, 1, collector - centre),
        "return:collector": (2, collector, ret_x - 1, 1),
        "jump:riser": (3, centre, 1, taken_row - centre + 1),
        "jump:taken-row": (3, taken_row, max(taken_drops, default=4) - 2, 1),
    }
    for r in all_rows:
        m = by_row.get(r)
        if m is None:
            continue
        end = drop_x.get(r, lane_end[r])
        regions[f"lane:{m}"] = (lane_x0, r, max(1, end - lane_x0 + 1), 1)
    for m in order:
        regions[f"slab:{m}"] = (slab_base[m], slab_at[m], _SLAB_PITCH, slab_rows[m])

    cpu = _Cpu(
        cells=g.c,
        width=width,
        height=height,
        centre=centre,
        in_row=in_rows[0] if in_rows else 1,
        in_col=lane_x0,
        out_col=lane_x0 + 1,
        mem_out_row=mem_out_row,
        mem_in_row=max(mem_out_row - 4, 1),
        ports={},
        regions=regions,
        pipe_glyphs=pipe_glyphs,
        has_in=bool(in_rows),
        has_out=any(p.sem[m] is Sem.OUTPUT for m in used),
    )
    return cpu, taken_row


# ── the loader/fetcher room ──────────────────────────────────────────────────
FETCH_W, FETCH_H = 19, 11
FETCH_ROM_ROW = 2  # ROM pipe enters the west wall here
FETCH_CMD_COL = 0  # cmd pipe enters the south wall here
FETCH_REQ_ROW = 10  # request pipe leaves the west wall here


def fetcher_cells(n_words: int) -> dict[tuple[int, int], str]:
    """Interior cells of the loader/fetcher room (local coords, FETCH_W x FETCH_H).

    Phase 1 (rows 0-3): ``BP = n_words`` by digit product, ``B = 1``; then a
    counted ring: send ``1 addr word`` triples (WRITE) reading each word off the
    ROM pipe, incrementing the address in B.  Phase 2 (rows 7-9): ``B = pc``,
    serve commands — ``r`` the command, ``X`` on its sign, seq arm ``2 + M`` or
    jump arm ``M``, then issue two READ pairs ``0 pc`` / ``0 pc+1``.

    Phase switch is pure geometry: the phase-1 ``r`` is nearest the ROM pipe
    (west row 2), the phase-2 ``r`` nearest the cmd pipe (south col 0).
    """
    total, prod = digit_factors(n_words)
    if total != n_words:
        raise MachineError("pad the image to digit_factors(len(words)) before building")
    init = "@" + prod + "b1M"
    vcol = max(10, len(init))
    if vcol > FETCH_W - 2:
        raise MachineError(f"fetcher init {init!r} too wide for the room")

    c: dict[tuple[int, int], str] = {}

    def text(x: int, y: int, s: str) -> None:
        for i, ch in enumerate(s):
            if ch != " ":
                c[(x + i, y)] = ch

    text(0, 0, init)
    c[(vcol, 0)] = "v"
    # phase-1 ring: top row eastbound (1 s W s + M), bottom row westbound (r s m d)
    text(2, 1, ">1sWs+Mv")
    text(1, 2, "vd...msr<")
    # init joins the ring from the west: down east of it, west along row 3, up col 0
    c[(vcol, 3)] = "<"
    for x in range(2, vcol):
        c.setdefault((x, 3), ".")
    c[(0, 3)] = "^"
    c[(0, 2)] = "."
    c[(0, 1)] = ">"
    c[(1, 1)] = "."
    # phase-1 exit (BP == 0): d falls west onto (1,2)'v', south through init2
    # ``1 N M`` (B = -1: the first "next" then fetches address 1), into the loop.
    c[(1, 3)] = "1"  # also crossed westbound by the init man: A dies anyway
    c[(1, 4)] = "N"
    c[(1, 5)] = "M"
    c[(1, 6)] = "<"
    c[(0, 6)] = "v"
    # phase-2 ring
    text(0, 7, ">rX2+M>0sWsM0s1+sv")
    c[(0, 8)] = "^"
    c[(2, 8)] = ">"
    c[(3, 8)] = "M"
    c[(4, 8)] = "."
    c[(5, 8)] = "."
    c[(6, 8)] = "^"
    c[(17, 8)] = "v"
    text(0, 9, "^" + "<" * 17)
    return c


@dataclass
class RamMachine:
    rows: list[str]
    program: Program
    words: list[int]
    mem_pad: int
    regions: dict[str, tuple[int, int, int, int]]

    @property
    def width(self) -> int:
        return max(len(r) for r in self.rows)

    @property
    def height(self) -> int:
        return len(self.rows)

    @property
    def footprint(self) -> int:
        return max(self.width, self.height) ** 2


def build_ram(
    program: Program,
    *,
    tape_n: int | None = None,
    store_ops: int = 96,
    store_per_row: int = 4,
    rom_rows: int | None = None,
    mem_pad: int | None = None,
) -> RamMachine:
    """Assemble the whole RAM-program machine. See the module docstring."""
    from ..memory_men_v3 import build_v3

    p = plan(program)
    words = ram_words(program, p)
    total, _prod = digit_factors(len(words))
    words = words + [0] * (total - len(words))
    tape_n = tape_n if tape_n is not None else _highest_address(program) + 1

    last: MachineError | None = None
    for pad in [mem_pad] if mem_pad is not None else list(range(0, 40)):
        try:
            return _assemble_ram(
                program, p, words, tape_n, store_ops, store_per_row, rom_rows, pad, build_v3
            )
        except MachineError as exc:
            last = exc
    raise MachineError(f"no mem_pad binds: {last}")


def _assemble_ram(
    program: Program,
    p: _Plan,
    words: list[int],
    tape_n: int,
    store_ops: int,
    store_per_row: int,
    rom_rows: int | None,
    mem_pad: int,
    build_v3,
) -> RamMachine:
    cpu, taken_row = build_ram_cpu(program, p, mem_pad=mem_pad)
    W, H = cpu.width, cpu.height
    centre = cpu.centre

    g = _Grid()
    regions: dict[str, tuple[int, int, int, int]] = {}

    # ── ROM drum (boot image), top-left at x=2 ───────────────────────────────
    nrows = rom_rows if rom_rows is not None else max(2, (len(words) * 4) // 60)
    romlay = rommod.build_packed_rom(words, rows=nrows)
    RX, RY = 2, 0
    g.room(RX, RY, RX + romlay.width, RY + romlay.height + 1)
    g.blit(RX, RY + 1, romlay.cells)
    rom_bottom = RY + romlay.height + 1
    regions["rom"] = (RX, RY, romlay.width + 1, romlay.height + 2)

    # ── fetcher room, west column under the ROM ──────────────────────────────
    FX, FY = 4, rom_bottom + 3
    g.room(FX - 1, FY - 1, FX + FETCH_W, FY + FETCH_H)
    g.blit(FX, FY, fetcher_cells(len(words)))
    regions["fetcher"] = (FX - 1, FY - 1, FETCH_W + 2, FETCH_H + 2)

    # ROM -> fetcher west wall (row FETCH_ROM_ROW): west out of the ROM's west
    # wall, down column 0, east into the fetcher.
    g.draw_pipe(
        [(RX - 1, RY + 1), (RX - 2, RY + 1), (RX - 2, FY + FETCH_ROM_ROW), (FX - 2, FY + FETCH_ROM_ROW)]
    )

    # ── CPU ──────────────────────────────────────────────────────────────────
    CX, CY = 30, rom_bottom + 8
    if CX <= FX + FETCH_W + 2:
        raise MachineError("CPU overlaps the fetcher")
    g.room(CX, CY, CX + W + 1, CY + H + 1)
    g.blit(CX, CY, cpu.cells)
    for name, (x, y, w, h) in cpu.regions.items():
        regions[f"cpu:{name}"] = (CX + x, CY + y, w, h)
    fetch_y = CY + centre
    cmd_y = CY + centre - 2

    # ── I room, north (in_north layout) ──────────────────────────────────────
    in_x = CX + cpu.in_col
    if cpu.has_in:
        if CY - 5 <= rom_bottom:
            raise MachineError("no room for the north I room")
        g.room(in_x - 1, CY - 5, in_x + 1, CY - 3)
        g.put(in_x, CY - 4, "I")
        g.draw_pipe([(in_x, CY - 2), (in_x, CY - 1)])
        regions["io:I"] = (in_x - 1, CY - 5, 3, 3)

    # ── O room, south ────────────────────────────────────────────────────────
    oy = CY + H + 2
    if cpu.has_out:
        g.draw_pipe([(CX + cpu.out_col, oy), (CX + cpu.out_col, oy + 1)])
        g.room(CX + cpu.out_col - 1, oy + 2, CX + cpu.out_col + 1, oy + 4)
        g.put(CX + cpu.out_col, oy + 3, "O")
        regions["io:O"] = (CX + cpu.out_col - 1, oy + 2, 3, 3)

    # ── cmd pipe: CPU west wall (centre-2) -> west -> down? no: north-west ───
    # Horizontal at centre-2 stays *above* the answer riser's span, so the two
    # never cross; the vertical leg climbs to the fetcher's south wall.
    g.draw_pipe([(CX - 1, cmd_y), (FX + FETCH_CMD_COL, cmd_y), (FX + FETCH_CMD_COL, FY + FETCH_H + 1)])

    # ── program store, far south ─────────────────────────────────────────────
    SY = CY + H + 12
    SX = 8
    v3 = build_v3(
        len(words) + 1, ops=store_ops, per_row=store_per_row, per_row_auto=False, io=False
    )
    store_cells = {
        (x, y): ch for y, row in enumerate(v3.rows) for x, ch in enumerate(row) if ch != " "
    }
    g.blit(SX, SY, store_cells)
    assert v3.in_cell is not None and v3.out_cell is not None
    in_sx, in_sy = v3.in_cell
    regions["pstore"] = (SX, SY, v3.width, v3.height)

    # fetcher west wall (row FETCH_REQ_ROW) -> down col FX-3 -> east into the
    # store's request stub.
    g.draw_pipe(
        [
            (FX - 2, FY + FETCH_REQ_ROW),
            (FX - 3, FY + FETCH_REQ_ROW),
            (FX - 3, SY + in_sy),
            (SX + in_sx - 1, SY + in_sy),  # the store's own '>' stub continues east
        ]
    )

    # store answers -> CPU west wall at the fetch row: up the stub column, west
    # along the south corridor, up the west corridor, east into the wall.
    out_sx, _out_sy = v3.out_cell
    stub_x = SX + out_sx
    # the stub's topmost drawn arrow; my pipe continues one cell above it
    top_arrow = min(y for (x, y) in store_cells if x == out_sx and y < 8)
    yy = top_arrow
    south_row = CY + H + 8
    west_x = CX - 6
    if west_x <= FX + FETCH_W:
        raise MachineError("answer riser collides with the fetcher")
    g.draw_pipe(
        [
            (stub_x, SY + yy - 1),
            (stub_x, south_row),
            (west_x, south_row),
            (west_x, fetch_y),
            (CX - 1, fetch_y),
        ]
    )

    # ── data store: adapter + tape, east of the CPU (copy of machine._assemble)
    AX = CX + W + 4
    AY = max(CY + cpu.mem_out_row - ADAPTER_IN_ROW, CY + cpu.mem_in_row + 3)
    resp_row = CY + cpu.mem_in_row
    if resp_row >= AY - 1:
        raise MachineError("response row grazes the adapter's top wall")
    req_row = AY + ADAPTER_IN_ROW
    g.room(AX, AY, AX + ADAPTER_W + 1, AY + 5)
    g.blit(AX, AY, adapter_cells())
    g.draw_pipe([(CX + W + 2, req_row), (AX - 1, req_row)])
    tape = tape_block(tape_n, skip_batch=1, relay_size=None)
    TX, TY = AX + ADAPTER_W + 3, CY
    g.blit(TX, TY, tape.cells)
    tin_x, tin_y = TX + tape.in_cell[0], TY + tape.in_cell[1]
    ax_out = AX + ADAPTER_W + 2
    mid = ax_out + 2
    g.draw_pipe(
        [(ax_out, AY + ADAPTER_OUT_ROW), (mid, AY + ADAPTER_OUT_ROW), (mid, tin_y), (tin_x - 1, tin_y)]
    )
    tout_x, tout_y = TX + tape.out_cell[0], TY + tape.out_cell[1]
    top = min(AY, tout_y, resp_row) - 1
    g.draw_pipe(
        [
            (tout_x, tout_y - 1),
            (tout_x, top),
            (CX + W + 3, top),
            (CX + W + 3, resp_row),
            (CX + W + 2, resp_row),
        ]
    )
    regions["adapter"] = (AX, AY, ADAPTER_W + 2, 6)
    regions["tape"] = (TX, TY, tape.width, tape.height)

    # ── bindings ─────────────────────────────────────────────────────────────
    touches = {
        "rom": (CX - 1, fetch_y),  # the answer pipe: plays the ROM corridor's role
        "cmd": (CX - 1, cmd_y),
        "mem_req": (CX + W + 2, req_row),
        "mem_resp": (CX + W + 2, resp_row),
    }
    if cpu.has_in:
        touches["in"] = (in_x, CY - 1)
    if cpu.has_out:
        touches["out"] = (CX + cpu.out_col, CY + H + 2)
    check_bindings(
        [(CX + x, CY + y, glyph, band) for x, y, glyph, band in cpu.pipe_glyphs], touches
    )

    return RamMachine(
        rows=g.rows(),
        program=program,
        words=words,
        mem_pad=mem_pad,
        regions=regions,
    )


# ═════════════════════════════════════════════════════════════════════════════
# Stage 2 — the prefetching fetcher
# ═════════════════════════════════════════════════════════════════════════════
#
# The fetcher no longer waits for a command per instruction: he free-runs,
# streaming sequential (pc, pc+1) READ pairs into the store, throttled only by
# pipe backpressure. Between pairs he polls the cmd pipe with ``q`` (BP = values
# in the nearest incoming pipe, non-blocking): a pending word is a taken jump's
# absolute target. The service arm reads it, sets pc, and issues a READ of
# **address 0 — the sentinel cell, boot-loaded with -1** — before resuming, so
# the CPU's flush sees exactly one negative word between the stale stream and
# the target stream. Program words are provably non-negative (ARCH §4.2), so
# the CPU flushes with a two-glyph test: ``r`` then ``X`` — negative turns out
# of the loop, zero/positive loop back. No register is touched: ACC stays in B
# through the whole flush.
#
# CPU deltas vs Stage 1: the fetch row reverts to the drum machine's plain
# ``>rbr`` (sequential instructions pay zero protocol overhead — supply
# overlaps execution exactly as the drum did); taken paths walk EAST along the
# taken row to one shared ``s`` (bound to the cmd pipe on the *east* wall),
# then drop a row and walk west into the flush block; the flush's negative
# exit rises col 4 to the collector and comes home via the normal riser.

FETCH2_CMD_ROW = 8   # cmd pipe enters the fetcher's EAST wall here
FETCH2_REQ_COL = 0   # request pipe leaves the fetcher's south wall at col 0 (abs FX)


def fetcher_cells2(n_words: int) -> dict[tuple[int, int], str]:
    """The streaming loader/fetcher (19 x 11 interior).

    Phase 1 is Stage 1's counted boot loader; its exit path then writes the
    sentinel (``1 0 -1`` = WRITE addr 0 value -1) and initialises ``B = pc = 1``
    before falling into the streaming ring: issue (pc, pc+1), pc += 2, ``q``,
    ``d`` — BP > 0 turns into the service arm (r target, M, issue the sentinel
    read ``0 0``), else loop.
    """
    total, prod = digit_factors(n_words)
    if total != n_words:
        raise MachineError("pad the image to digit_factors(len(words)) first")
    init = "@" + prod + "b1M"
    vcol = max(10, len(init))
    if vcol > 17:
        raise MachineError(f"fetcher init {init!r} too wide")

    c: dict[tuple[int, int], str] = {}

    def text(x: int, y: int, s: str) -> None:
        for i, ch in enumerate(s):
            if ch != " ":
                c[(x + i, y)] = ch

    # phase 1: identical to Stage 1
    text(0, 0, init)
    c[(vcol, 0)] = "v"
    text(2, 1, ">1sWs+Mv")
    text(1, 2, "vd...msr<")
    c[(vcol, 3)] = "<"
    for x in range(2, vcol):
        c.setdefault((x, 3), ".")
    c[(0, 3)] = "^"
    c[(0, 2)] = "."
    c[(0, 1)] = ">"
    c[(1, 1)] = "."
    # boot tail: sentinel write + pc := 1, then into the ring
    c[(1, 3)] = "."
    c[(1, 4)] = ">"
    text(2, 4, "1s0s1Ns1M")  # WRITE(0, -1); A=1, B=1 = the first pc
    c[(11, 4)] = "v"
    c[(11, 5)] = "<"
    for x in range(1, 11):
        c[(x, 5)] = "."
    c[(0, 5)] = "v"
    c[(0, 6)] = "."
    # streaming ring
    text(0, 7, ">0sWsM0s1+sM1+Mqdv")
    c[(0, 8)] = "^"
    text(11, 8, "ss0Mr<")  # written eastward: s(11) s(12)?? see below
    # careful: the service arm is WESTBOUND: r at 15, M 14, 0 13, s 12, s 11
    c[(16, 8)] = "<"
    c[(15, 8)] = "r"
    c[(14, 8)] = "M"
    c[(13, 8)] = "0"
    c[(12, 8)] = "s"
    c[(11, 8)] = "s"
    for x in range(1, 11):
        c[(x, 8)] = "."
    c[(17, 8)] = "v"
    text(0, 9, "^" + "<" * 17)
    return c


def build_ram_cpu2(program: Program, p: _Plan, *, mem_pad: int = 0) -> tuple[_Cpu, int]:
    """Stage-2 CPU: plain ``>rbr`` fetch, send-and-flush jump slabs.

    Layout of the control-flow tail (t = taken row):

        row t   : drops land and turn east ``>``, shared ``s`` (cmd, east wall),
                  then ``v`` one column further east
        row t+1 : westbound corridor back to the flush block (col 4 is the
                  '.'-hole the negative exit rises through)
        row t+2 : ``> r X v`` — the flush loop; X: neg exits north, else loops
        row t+3 : ``^ < < <`` — the loop's return leg
    """
    k, lanes = p.k, p.lanes
    centre = 1 << k
    span = 2 * lanes - 1
    lane_x0 = 5 + k
    used = list(p.number)
    by_row = {p.row[m]: m for m in used}
    all_rows = list(range(1, span + 1, 2))

    flat = {m: hw_micro(p.sem[m]) for m in used if p.sem[m] in _HW}
    structured = [m for m in used if p.sem[m] in _JUMP_SEMS | _BRANCH_SEMS]
    halting = {p.row[m] for m in used if p.sem[m] is Sem.HALT}

    prefixes = [
        next((i for i, (_, b) in enumerate(mc) if b == Band.MEM), len(mc))
        for mc in flat.values()
        if any(b == Band.MEM for _, b in mc)
    ]
    mem_x = lane_x0 + (max(prefixes) if prefixes else 0) + mem_pad
    band_x = {Band.MEM: mem_x}
    if any(b not in (Band.MEM, Band.IN, Band.OUT, None) for mc in flat.values() for _, b in mc):
        raise MachineError("ram_machine stage 2 supports only IN/OUT/MEM/ALU programs")

    slab_rows = {m: (2 if p.sem[m] in _JUMP_SEMS else 5) for m in structured}
    struct_east = _STRUCT_X0_RAM + max(1, len(structured)) * _SLAB_PITCH

    lane_cells: dict[tuple[int, int], tuple[str, str | None]] = {}
    lane_end: dict[int, int] = {}
    for r in all_rows:
        m = by_row.get(r)
        if m is None:
            lane_end[r] = lane_x0 - 1
        elif m in flat:
            cells = _flat_lane(flat[m], lane_x0, band_x, r)
            lane_cells.update(cells)
            lane_end[r] = max((x for x, _ in cells), default=lane_x0 - 1)
        else:
            pre = "." if p.sem[m] in _JUMP_SEMS else "W"
            lane_cells[(lane_x0, r)] = (pre, None)
            lane_end[r] = lane_x0

    drop_x: dict[int, int] = {}
    assigned: set[int] = set()
    floor = lane_x0
    for r in sorted(all_rows, reverse=True):
        floor = max(floor, lane_end[r] + 1)
        if r in halting:
            continue
        m = by_row.get(r)
        if m is not None and m in slab_rows:
            cc = max(floor, struct_east + 1)
            while cc in assigned:
                cc += 1
        else:
            cc = floor
            if cc > struct_east:
                while cc in assigned:
                    cc += 1
        drop_x[r] = cc
        assigned.add(cc)

    order = sorted(structured, key=lambda m: drop_x[p.row[m]])
    slab_at: dict[str, int] = {}
    slab_base: dict[str, int] = {}
    collector = span + 1
    for i, m in enumerate(order):
        slab_at[m] = collector + 1 + i
        slab_base[m] = _STRUCT_X0_RAM + i * _SLAB_PITCH
    taken_row = max((slab_at[m] + slab_rows[m] for m in order), default=collector + 1) + 1
    bottom = taken_row + 4  # taken row + westbound corridor + flush loop rows

    g = _Grid()
    pipe_glyphs: list[tuple[int, int, str, str]] = []

    def emit(x: int, yy: int, glyph: str, band: str | None) -> None:
        g.put(x, yy, glyph)
        if glyph in "rs" and band:
            pipe_glyphs.append((x, yy, glyph, band))

    # ── fetch row: the drum machine's, verbatim ──────────────────────────────
    g.text(1, centre, ">rbr")
    pipe_glyphs += [(2, centre, "r", "rom"), (4, centre, "r", "rom")]

    def trie(level: int, row: int) -> None:
        col, step = 4 + level, 1 << (k - level)
        g.put(col, row, "x")
        for sign in (-1, +1):
            for d in range(1, step + 1):
                g.put(col, row + sign * d, ">" if d == step else ("]" if d == 1 else "."))
            if level < k:
                trie(level + 1, row + sign * step)

    trie(1, centre)

    for (x, yy), (glyph, band) in lane_cells.items():
        emit(x, yy, glyph, band)
    for r in all_rows:
        if r in halting:
            continue
        for x in range(lane_end[r] + 1, drop_x[r]):
            g.soft(x, r, ".")

    # the flush's negative-exit riser: col 4 up to the collector's '<' turn
    for yy in range(collector + 1, taken_row):
        g.soft(4, yy, ".")

    for r in all_rows:
        if r in halting:
            continue
        g.put(drop_x[r], r, "v")
    for r in all_rows:
        if r in halting:
            continue
        m = by_row.get(r)
        stop = slab_at[m] if (m is not None and m in slab_at) else collector
        for yy in range(r + 1, stop):
            g.soft(drop_x[r], yy, ".")

    taken_drops: list[int] = []
    struct_drops: set[int] = set()
    for m in order:
        s0, base = slab_at[m], slab_base[m]
        sem = p.sem[m]
        if sem in _JUMP_SEMS:
            for yy in range(s0 + 1, taken_row):
                g.soft(base, yy, ".")
            taken_drops.append(base)
        else:
            g.soft(base, s0 + 1, ".")
            g.put(base, s0 + 2, ">")
            g.put(base + 1, s0 + 2, "X")
            g.put(base + 1, s0 + 1, ">")
            g.put(base + 1, s0 + 3, ">")
            rows = {"neg": s0 + 1, "zero": s0 + 2, "pos": s0 + 3}
            cols = {"neg": base + 9, "zero": base + 6, "pos": base + 3}
            taken = "zero" if sem is Sem.BR_ZERO else "neg"
            for arm, row in rows.items():
                g.put(base + 2, row, "W")
                for cc in range(base + 3, cols[arm]):
                    g.soft(cc, row, ".")
                if arm == taken:
                    g.put(cols[arm], row, "v")
                    for yy in range(row + 1, taken_row):
                        g.soft(cols[arm], yy, ".")
                    taken_drops.append(cols[arm])
                else:
                    g.put(cols[arm], row, "^")
                    for yy in range(collector + 1, row):
                        g.soft(cols[arm], yy, ".")
                    struct_drops.add(cols[arm])

    for m in order:
        s0, dx = slab_at[m], drop_x[p.row[m]]
        base = slab_base[m]
        g.put(dx, s0, "<")
        for x in range(base + 1, dx):
            g.soft(x, s0, "<")
        g.put(base, s0, "v")

    # ── taken row, send site, flush block ────────────────────────────────────
    t = taken_row
    e_s = struct_east + 2
    for col in taken_drops:
        g.put(col, t, ">")
    for x in range(4, e_s):
        g.soft(x, t, ".")
    emit(e_s, t, "s", "cmd")
    g.put(e_s + 1, t, "v")
    g.put(e_s + 1, t + 1, "<")
    for x in range(5, e_s + 1):
        g.soft(x, t + 1, ".")
    g.put(4, t + 1, ".")  # the negative exit crosses here heading north
    g.put(3, t + 1, ".")
    g.put(2, t + 1, "v")
    g.put(2, t + 2, ">")
    emit(3, t + 2, "r", "rom")
    g.put(4, t + 2, "X")
    g.put(5, t + 2, "v")
    g.put(2, t + 3, "^")
    g.put(3, t + 3, "<")
    g.put(4, t + 3, "<")
    g.put(5, t + 3, "<")

    ret_x = max([*drop_x.values(), *struct_drops, e_s + 1])
    for x in range(3, ret_x + 1):
        g.soft(x, collector, "<")
    g.put(1, collector, "^")
    for yy in range(centre + 1, collector):
        g.soft(1, yy, ".")
    g.put(2, collector, "@")

    through = {drop_x[p.row[m]] for m in order}
    clash = {r: cc for r, cc in drop_x.items() if cc in through and by_row.get(r) not in order}
    if clash:
        raise MachineError(
            f"simple lane drop column(s) {sorted(set(clash.values()))} collide with a "
            "slab entry column"
        )

    width = ret_x + 1
    height = bottom
    mem_rows = sorted(
        r
        for r in all_rows
        if by_row.get(r) in flat and any(b == Band.MEM for _, b in flat[by_row[r]])
    )
    mem_out_row = mem_rows[len(mem_rows) // 2] if mem_rows else centre
    in_rows = [p.row[m] for m in used if p.sem[m] is Sem.INPUT]

    regions: dict[str, tuple[int, int, int, int]] = {
        "fetch": (1, centre, 4, 1),
        "trie": (5, 1, k, span),
        "return:riser": (1, centre + 1, 1, collector - centre),
        "return:collector": (2, collector, ret_x - 1, 1),
        "jump:taken-row": (4, t, e_s - 2, 1),
        "jump:flush": (2, t + 1, 5, 3),
    }
    for r in all_rows:
        m = by_row.get(r)
        if m is None:
            continue
        end = drop_x.get(r, lane_end[r])
        regions[f"lane:{m}"] = (lane_x0, r, max(1, end - lane_x0 + 1), 1)
    for m in order:
        regions[f"slab:{m}"] = (slab_base[m], slab_at[m], _SLAB_PITCH, slab_rows[m])

    cpu = _Cpu(
        cells=g.c,
        width=width,
        height=height,
        centre=centre,
        in_row=in_rows[0] if in_rows else 1,
        in_col=lane_x0,
        out_col=lane_x0 + 1,
        mem_out_row=mem_out_row,
        mem_in_row=max(mem_out_row - 4, 1),
        ports={},
        regions=regions,
        pipe_glyphs=pipe_glyphs,
        has_in=bool(in_rows),
        has_out=any(p.sem[m] is Sem.OUTPUT for m in used),
    )
    return cpu, taken_row
