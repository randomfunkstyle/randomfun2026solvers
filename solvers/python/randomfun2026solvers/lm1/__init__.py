"""LM-1 — the general-purpose CPU from ``littleman/ARCH.md``, in Python.

Step 2 of ``ARCH.md`` §8's build order: the ISA as data, an emulator with LM-1's
exact semantics, an assembler, and a task program per problem that needs no
array. Proves the ISA is sufficient before any ASCII is drawn.

    >>> from randomfun2026solvers.lm1 import assemble, Emulator
    >>> prog = assemble("LDI 41\\nADDI 1\\nOUT\\nHALT")
    >>> prog.P
    7
    >>> Emulator(prog).run().output
    (42,)
"""

from __future__ import annotations

from .asm import AsmError, Instr, Program, assemble, assemble_file, check_ring_writeback
from .display import ADDR, DATA, SWAP, Display, frames_from_writes
from .emulator import (
    Emulator,
    EmulatorError,
    InputWithheld,
    Round,
    RunResult,
    run_rounds,
)
from .isa import (
    DEFAULT_ISA,
    LM1_EXT,
    LM1_V1,
    Isa,
    Micro,
    Op,
    Sem,
    TickModel,
)
from .store import DictStore, SpillRing, Store, StoreError

__all__ = [
    "AsmError",
    "Instr",
    "Program",
    "assemble",
    "assemble_file",
    "check_ring_writeback",
    "ADDR",
    "DATA",
    "SWAP",
    "Display",
    "frames_from_writes",
    "Emulator",
    "EmulatorError",
    "InputWithheld",
    "Round",
    "RunResult",
    "run_rounds",
    "DEFAULT_ISA",
    "LM1_EXT",
    "LM1_V1",
    "Isa",
    "Micro",
    "Op",
    "Sem",
    "TickModel",
    "DictStore",
    "SpillRing",
    "Store",
    "StoreError",
]
