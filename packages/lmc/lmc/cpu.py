"""Soft-CPU: a program-agnostic processor that fetches bytecode from a separate ROM block,
decodes (dispatch), and runs microcode. Pipeline: [ROM] -> [CPU] -> (O / RAM).

The CPU while it only touches romin(W) + out(E) has two pipes -> routes trivially -> its
fetch/decode/microcode is COMPOSED (if3 / dispatch2), not hand-laid. Adding RAM (a req/resp
bus to a separate RAM block) is the next step and needs zoning.

Instruction model (growing): a byte stream. This starter CPU decodes
  0 = HALT
  1 = OUT_IMM : emit the next byte
so `[1,42, 1,7, 0]` prints 42, 7. Real ISA (PUSH/LOAD/STORE/ADD/JUMP/...) extends the
dispatch with more opcodes + a RAM block. Programs are ROM bytecode; one CPU runs them all.
"""

from __future__ import annotations

from .blockspec import BlockGraph, E, Instr, Pipe, W
from .loopgen import forever_loop, if3, linear_block, seq_block
from .rom import ROM, rom_trail
from .router import Canvas, solve_attachments
from .trail import TrailLayout

Op = Instr


def cpu_program() -> TrailLayout:
    """Fetch/decode/execute loop. Fetch opcode from ROM, dispatch to microcode."""
    body = seq_block([
        linear_block([Op("r", "romin")]),                 # fetch opcode -> A
        if3(neg=[Op("H")],
            zero=[Op("H")],                               # 0 = HALT
            pos=[Op("r", "romin"), Op("s", "out")]),      # 1 = OUT_IMM
    ])
    return forever_loop(prologue=[Op("@")], body=body)


def render_rom_cpu(bytecode: list[int], rom_len: int = 8, gap: int = 3) -> str:
    """Pipeline grid: [ROM: bytecode] -> [CPU: fetch/dispatch] -> O."""
    rom = rom_trail(bytecode)
    cpu = cpu_program()
    RWi, RHi = rom.width, rom.height
    CWi, CHi = cpu.width, cpu.height

    rg = BlockGraph(cpu="ROM")
    rg.pipes = [Pipe("romout", "ROM", E, "CPU", W), *ROM.pipes("ROM")]
    rseg = solve_attachments(rg, rom)
    _, romout_row = rseg["romout"]

    c = Canvas()
    c.rect(-1, -1, RWi, RHi)
    for cc in rom.cells:
        c.put(cc.x, cc.y, cc.char)
    mx = RWi + gap + 1
    c.rect(mx - 1, -1, mx + CWi, CHi)
    for cc in cpu.cells:
        c.put(cc.x + mx, cc.y, cc.char)
    for x in range(RWi + 1, mx - 1):                      # romout pipe (between walls)
        c.put(x, romout_row, ">")
    c.put(mx + CWi + 1, 0, ">")                           # O east of CPU
    c.put(mx + CWi + 2, 0, ">")
    c.rect(mx + CWi + 3, -1, mx + CWi + 5, 1)
    c.put(mx + CWi + 4, 0, "O")
    buf_top = RHi + rom_len + 1
    c.rect(-1, buf_top, max(RWi, 5), buf_top + 3)
    c.text("@>rsv", 0, buf_top + 1)
    c.text(".^..<", 0, buf_top + 2)
    for p in ROM.pipes("ROM"):
        col, _ = rseg[p.id]
        ch = "v" if p.cpu_dir("ROM") == "out" else "^"
        for y in range(RHi + 1, buf_top):
            c.put(col, y, ch)
    return c.render()
