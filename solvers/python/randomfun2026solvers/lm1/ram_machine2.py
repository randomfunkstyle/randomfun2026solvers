"""Stage-2 assembly: the prefetching RAM machine (see ram_machine's stage-2 half).

Layout (brackets-scale)::

    [ROM boot image]
    [fetcher]                      <- cmd comes in over the northern corridor
    [banked program store]  [I][CPU][adapter][tape]
        `- answers -> CPU west wall (fetch row), short
                                  [O]

The cmd pipe (taken-jump targets) leaves the CPU's **east** wall on the taken
row and rides around the machine's north side into the fetcher's east wall —
long, but its length is only jump-notice latency; the in-flight budget that
sets the flush bill is the *answer* corridor + store internals + request stub,
all kept short here.
"""

from __future__ import annotations

from .asm import Program
from .machine import (
    ADAPTER_IN_ROW,
    ADAPTER_OUT_ROW,
    ADAPTER_W,
    MachineError,
    _Grid,
    _Plan,
    adapter_cells,
    check_bindings,
    plan,
    tape_block,
    _highest_address,
)
from . import rom as rommod
from .ram_machine import (
    FETCH_H,
    FETCH_ROM_ROW,
    FETCH_W,
    FETCH2_CMD_ROW,
    RamMachine,
    build_ram_cpu2,
    digit_factors,
    fetcher_cells2,
    ram_words,
)

__all__ = ["build_ram2"]


def build_ram2(
    program: Program,
    *,
    tape_n: int | None = None,
    store_shape: tuple[int, int] | None = None,
    rom_rows: int | None = None,
    mem_pad: int | None = None,
) -> RamMachine:
    """Assemble the Stage-2 (prefetching) RAM machine."""
    from ..memory_men_v3 import v3_store_grid_block

    p = plan(program)
    words = ram_words(program, p)
    total, _prod = digit_factors(len(words))
    words = words + [0] * (total - len(words))
    tape_n = tape_n if tape_n is not None else _highest_address(program) + 1

    if store_shape is None:
        store_shape = (1, len(words) + 1)

    last: MachineError | None = None
    for pad in [mem_pad] if mem_pad is not None else list(range(0, 40)):
        try:
            return _assemble(
                program, p, words, tape_n, store_shape, rom_rows, pad, v3_store_grid_block
            )
        except MachineError as exc:
            last = exc
    raise MachineError(f"no mem_pad binds: {last}")


def _assemble(
    program: Program,
    p: _Plan,
    words: list[int],
    tape_n: int,
    store_shape: tuple[int, int],
    rom_rows: int | None,
    mem_pad: int,
    v3_store_grid_block,
) -> RamMachine:
    cpu, taken_row = build_ram_cpu2(program, p, mem_pad=mem_pad)
    W, H = cpu.width, cpu.height
    centre = cpu.centre

    g = _Grid()
    regions: dict[str, tuple[int, int, int, int]] = {}

    # ── ROM (boot image) ─────────────────────────────────────────────────────
    nrows = rom_rows if rom_rows is not None else max(2, (len(words) * 4) // 60)
    romlay = rommod.build_packed_rom(words, rows=nrows)
    RX, RY = 2, 0
    g.room(RX, RY, RX + romlay.width, RY + romlay.height + 1)
    g.blit(RX, RY + 1, romlay.cells)
    rom_bottom = RY + romlay.height + 1
    regions["rom"] = (RX, RY, romlay.width + 1, romlay.height + 2)

    # ── fetcher ──────────────────────────────────────────────────────────────
    FX, FY = 6, rom_bottom + 2
    g.room(FX - 1, FY - 1, FX + FETCH_W, FY + FETCH_H)
    g.blit(FX, FY, fetcher_cells2(len(words)))
    regions["fetcher"] = (FX - 1, FY - 1, FETCH_W + 2, FETCH_H + 2)

    g.draw_pipe(
        [(RX - 1, RY + 1), (RX - 2, RY + 1), (RX - 2, FY + FETCH_ROM_ROW), (FX - 2, FY + FETCH_ROM_ROW)]
    )

    # ── program store, directly below the fetcher ────────────────────────────
    # A multi-column grid store CANNOT serve as the program store: its
    # collector merges the columns' equal-length answer pipes with ``R``, whose
    # tie-break is reading order, not arrival order — so under prefetch
    # backpressure the col-0 sentinel overtakes queued stale answers and the
    # flush protocol desynchronises (measured: gradebook (6,141) emits garbage,
    # traced to exactly this). Only the single-column block has one answer pipe
    # and is order-preserving by construction.
    cols, rows_per = store_shape
    if cols != 1:
        raise MachineError(
            "stage-2 requires a merge-free program store: store_shape=(1, n)"
        )
    store = v3_store_grid_block(1, rows_per, ops=64)
    SX, SY = 4, FY + FETCH_H + 2
    g.blit(SX, SY, store.cells)
    regions["pstore"] = (SX, SY, store.width, store.height)
    in_sx, in_sy = store.in_cell  # single column: a west-pointing '>' stub row

    # request: out of the fetcher's west wall, down the west margin, east in.
    g.draw_pipe(
        [
            (FX - 2, FY + 10),
            (FX - 3, FY + 10),
            (FX - 3, SY + in_sy),
            (SX + in_sx - 1, SY + in_sy),
        ]
    )

    # ── CPU ──────────────────────────────────────────────────────────────────
    CX = SX + store.width + 8
    CY = rom_bottom + 6
    g.room(CX, CY, CX + W + 1, CY + H + 1)
    g.blit(CX, CY, cpu.cells)
    for name, (x, y, w, h) in cpu.regions.items():
        regions[f"cpu:{name}"] = (CX + x, CY + y, w, h)
    fetch_y = CY + centre

    # ── I room, west of the CPU in the store/CPU gap ─────────────────────────
    iy = CY + cpu.in_row
    if cpu.has_in:
        g.room(CX - 5, iy - 1, CX - 3, iy + 1)
        g.put(CX - 4, iy, "I")
        g.draw_pipe([(CX - 2, iy), (CX - 1, iy)])
        regions["io:I"] = (CX - 5, iy - 1, 3, 3)

    # ── O room, south ────────────────────────────────────────────────────────
    oy = CY + H + 2
    if cpu.has_out:
        g.draw_pipe([(CX + cpu.out_col, oy), (CX + cpu.out_col, oy + 1)])
        g.room(CX + cpu.out_col - 1, oy + 2, CX + cpu.out_col + 1, oy + 4)
        g.put(CX + cpu.out_col, oy + 3, "O")
        regions["io:O"] = (CX + cpu.out_col - 1, oy + 2, 3, 3)

    # ── store answers -> CPU west wall at the fetch row ──────────────────────
    out_sx, _osy = store.out_cell
    top_arrow = min(y for (x, y) in store.cells if x == out_sx and y < 8)
    stub_x = SX + out_sx
    mid_row = SY - 2
    if stub_x <= FX + FETCH_W:
        raise MachineError("store outlet sits under the fetcher; widen the store")
    g.draw_pipe(
        [
            (stub_x, SY + top_arrow - 1),
            (stub_x, mid_row),
            (CX - 7, mid_row),
            (CX - 7, fetch_y),
            (CX - 1, fetch_y),
        ]
    )

    # ── data store: adapter + tape, east of the CPU ──────────────────────────
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

    # ── cmd: CPU east wall (taken row) -> around the north -> fetcher east ───
    cmd_y = CY + taken_row
    x_e = TX + tape.width + 3
    y_n = rom_bottom + 2
    if y_n >= CY - 3:
        raise MachineError("no northern corridor row for the cmd pipe")
    g.draw_pipe(
        [
            (CX + W + 2, cmd_y),
            (x_e, cmd_y),
            (x_e, y_n),
            (FX + FETCH_W + 2, y_n),
            (FX + FETCH_W + 2, FY + FETCH2_CMD_ROW),
            (FX + FETCH_W + 1, FY + FETCH2_CMD_ROW),
        ]
    )

    # ── bindings ─────────────────────────────────────────────────────────────
    touches = {
        "rom": (CX - 1, fetch_y),
        "cmd": (CX + W + 2, cmd_y),
        "mem_req": (CX + W + 2, req_row),
        "mem_resp": (CX + W + 2, resp_row),
    }
    if cpu.has_in:
        touches["in"] = (CX - 1, iy)
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
