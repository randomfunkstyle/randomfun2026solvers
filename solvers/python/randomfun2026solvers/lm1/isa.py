"""LM-1's instruction set, as *data*.

``ARCH.md`` §6 is explicit that the accumulator ISA is "one instantiation, not the
architecture": the decode trie, the lane layout and the ROM encoder are all meant
to be generated from a table. This module is that table.

Everything downstream (:mod:`~randomfun2026solvers.lm1.asm`,
:mod:`~randomfun2026solvers.lm1.emulator`, and later the ``.man`` generator) reads
it and *never* hardcodes an opcode number:

* the assembler resolves mnemonics through :meth:`Isa.by_mnemonic`;
* the emulator dispatches on :attr:`Op.sem` (a semantic tag), not on ``op.code``;
* the tick model derives per-instruction cost from :attr:`Op.micro`.

Adding an opcode is one :class:`Op` row (plus a handler if its :class:`Sem` is
new). Swapping in a whole different ISA is one :class:`Isa` value, since every
public entry point takes ``isa=`` as a parameter.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, model_validator

__all__ = [
    "Micro",
    "MICRO_TICKS",
    "Sem",
    "Op",
    "Isa",
    "TickModel",
    "LM1_V1",
    "LM1_EXT",
    "DEFAULT_ISA",
]


# ── micro-program glyphs ─────────────────────────────────────────────────────
class Micro(StrEnum):
    """Glyphs (and pipe-targeted glyph variants) used by micro-programs.

    ``SPEC.md`` glyphs are single characters; the ARROW forms below name *which
    pipe* an ``s``/``r`` talks to, which in the real grid is decided by geometry
    (``ARCH.md`` §7.1) rather than by the glyph. ``RING_READ`` is ARCH's ``r↺`` —
    a ring read that is immediately paired with a write-back (§5.3 invariant).
    """

    RING_READ = "r↺"  # read the next ring word into A *and* send it back
    RING_SEND = "s→ring"  # bare recirculation (skip cycle emits these)
    READ_IN = "r→in"
    READ_MEM = "r→mem"
    READ_SPILL = "r→spill"
    SEND_OUT = "s→out"
    SEND_MEM = "s→mem"
    SEND_SPILL = "s→spill"
    SEND_DSP = "s→addr/data/swap"

    MOV = "M"  # B = A
    SWAP = "W"  # A <-> B
    ADD = "+"
    SUB = "-"
    MUL = "*"
    DIV = "/"
    MOD = "%"
    NEG = "N"
    SIGN_BRANCH = "X"  # native three-way branch on sign(A)
    BP_LOAD = "b"
    BP_DEC = "m"
    BP_BRANCH = "d"
    HALT = "H"
    LIT0 = "0"
    LIT1 = "1"

    # Pseudo-tokens: whole sub-structures the generator lays out, not one cell.
    SKIP_CYCLE = "…skip-cycle"  # r/s/m/d loop, billed per skipped word instead
    THREE_WAY = "…3 lanes"  # the X fan-out plus each lane's fix-up `W`


#: Rough tick cost of one micro glyph (``ARCH.md`` §7.2 puts execute at ~3–10
#: ticks, i.e. a few cells of walking per operation).
MICRO_TICKS: dict[Micro, int] = {m: 3 for m in Micro}
MICRO_TICKS[Micro.SKIP_CYCLE] = 0  # billed per skipped word by TickModel
MICRO_TICKS[Micro.THREE_WAY] = 6  # fan-out plus the per-lane restore `W`

#: Glyphs that exchange one word with the STORE block (billed extra latency).
STORE_GLYPHS = frozenset({Micro.SEND_MEM, Micro.READ_MEM})


# ── semantic tags ────────────────────────────────────────────────────────────
class Sem(StrEnum):
    """What an opcode *does*, independent of its number.

    The emulator keeps a handler per tag, so renumbering the ISA (which
    ``ARCH.md`` §7.1 treats as a free layout variable) cannot break execution.
    """

    NOP = "nop"
    SET_IMM = "set-imm"
    INPUT = "input"
    OUTPUT = "output"
    ADD_IMM = "add-imm"
    SUB_IMM = "sub-imm"
    MUL_IMM = "mul-imm"
    DIV_IMM = "div-imm"
    MOD_IMM = "mod-imm"
    LOAD = "load"
    STORE = "store"
    ADD_MEM = "add-mem"
    SUB_MEM = "sub-mem"
    MUL_MEM = "mul-mem"
    LOAD_IND = "load-ind"
    STORE_IND = "store-ind"
    NEGATE = "negate"
    SPILL_PUSH = "spill-push"
    SPILL_POP = "spill-pop"
    JUMP = "jump"
    BR_ZERO = "br-zero"
    BR_NEG = "br-neg"
    DISPLAY = "display"
    HALT = "halt"


#: Tags whose operand word is a *skip count* the assembler computes from a label.
TARGET_SEMS = frozenset({Sem.JUMP, Sem.BR_ZERO, Sem.BR_NEG})


# ── the table ────────────────────────────────────────────────────────────────
class Op(BaseModel):
    """One opcode row."""

    model_config = ConfigDict(frozen=True)

    code: int
    mnemonic: str
    operands: int  # 0 or 1 *words* following the opcode word (ARCH §5.2)
    description: str
    micro: tuple[Micro, ...]
    sem: Sem
    ext: bool = False
    """True for opcodes *added* on top of ARCH.md §6's v1 table (see LM1_EXT)."""
    aliases: tuple[str, ...] = ()
    """Extra source-level spellings the assembler accepts for this opcode."""

    @model_validator(mode="after")
    def _check(self) -> Op:
        if self.operands not in (0, 1):
            raise ValueError(f"{self.mnemonic}: operands must be 0 or 1 (ARCH §5.2)")
        if self.code < 0:
            raise ValueError(f"{self.mnemonic}: opcode must be non-negative")
        reads = self.micro.count(Micro.RING_READ)
        if reads != self.operands:
            raise ValueError(
                f"{self.mnemonic}: {reads} ring read(s) but {self.operands} operand word(s); "
                "every operand word must be fetched with `r↺` (ARCH §5.2/§5.3)"
            )
        return self

    @property
    def takes_target(self) -> bool:
        """True when the operand is a label the assembler turns into a skip count."""
        return self.sem in TARGET_SEMS

    @property
    def words(self) -> int:
        """Total ring words this instruction occupies."""
        return 1 + self.operands

    @property
    def store_words(self) -> int:
        """Words exchanged with the STORE block per execution."""
        return sum(1 for g in self.micro if g in STORE_GLYPHS)


class Isa(BaseModel):
    """An immutable opcode table plus lookups."""

    model_config = ConfigDict(frozen=True)

    name: str
    ops: tuple[Op, ...]

    @model_validator(mode="after")
    def _unique(self) -> Isa:
        codes = [o.code for o in self.ops]
        names = [o.mnemonic for o in self.ops] + [a for o in self.ops for a in o.aliases]
        if len(set(codes)) != len(codes):
            clashing_codes = sorted({c for c in codes if codes.count(c) > 1})
            raise ValueError(f"{self.name}: duplicate opcode numbers {clashing_codes}")
        if len(set(names)) != len(names):
            clashing_names = sorted({n for n in names if names.count(n) > 1})
            raise ValueError(f"{self.name}: duplicate mnemonics {clashing_names}")
        return self

    def __iter__(self) -> Iterator[Op]:  # type: ignore[override]
        return iter(self.ops)

    def __len__(self) -> int:
        return len(self.ops)

    def by_code(self, code: int) -> Op:
        for op in self.ops:
            if op.code == code:
                return op
        raise KeyError(f"{self.name}: no opcode {code}")

    def by_mnemonic(self, mnemonic: str) -> Op:
        want = mnemonic.upper()
        for op in self.ops:
            if op.mnemonic == want or want in op.aliases:
                return op
        raise KeyError(f"{self.name}: no mnemonic {mnemonic!r}")

    def by_sem(self, sem: Sem) -> Op:
        for op in self.ops:
            if op.sem is sem:
                return op
        raise KeyError(f"{self.name}: no opcode with semantics {sem!r}")

    def has(self, mnemonic: str) -> bool:
        try:
            self.by_mnemonic(mnemonic)
        except KeyError:
            return False
        return True

    @property
    def decode_bits(self) -> int:
        """Depth of the ``b``/``]``/``x`` decode trie needed (ARCH §2.2)."""
        top = max((o.code for o in self.ops), default=0)
        return max(1, top.bit_length())

    def extended(self, name: str, extra: Iterable[Op]) -> Isa:
        """A new ISA with ``extra`` rows appended (validated for collisions)."""
        return Isa(name=name, ops=(*self.ops, *extra))

    def restricted(self, name: str, mnemonics: Iterable[str]) -> Isa:
        keep = {m.upper() for m in mnemonics}
        return Isa(name=name, ops=tuple(o for o in self.ops if o.mnemonic in keep))


# ── ISA v1, verbatim from ARCH.md §6 ────────────────────────────────────────
_V1_OPS: tuple[Op, ...] = (
    Op(
        code=0,
        mnemonic="NOP",
        operands=0,
        description="do nothing",
        micro=(),
        sem=Sem.NOP,
    ),
    Op(
        code=1,
        mnemonic="LDI",
        operands=1,
        description="ACC = n",
        micro=(Micro.RING_READ, Micro.MOV),
        sem=Sem.SET_IMM,
    ),
    Op(
        code=2,
        mnemonic="IN",
        operands=0,
        description="ACC = next input word",
        micro=(Micro.READ_IN, Micro.MOV),
        sem=Sem.INPUT,
    ),
    Op(
        code=3,
        mnemonic="OUT",
        operands=0,
        description="emit ACC (ACC preserved by the W/s/W sandwich)",
        micro=(Micro.SWAP, Micro.SEND_OUT, Micro.SWAP),
        sem=Sem.OUTPUT,
    ),
    Op(
        code=4,
        mnemonic="ADDI",
        operands=1,
        description="ACC += n",
        micro=(Micro.RING_READ, Micro.ADD, Micro.MOV),
        sem=Sem.ADD_IMM,
    ),
    Op(
        code=5,
        mnemonic="SUBI",
        operands=1,
        description="ACC -= n",
        # ARCH.md writes `r↺` `-` `N` `M`; `W` `-` `M` is one glyph shorter and
        # is what the emulator's cost model bills. See the report in ARCH's §6
        # notes: `N` is never needed when `W` can reorder the operands.
        micro=(Micro.RING_READ, Micro.SWAP, Micro.SUB, Micro.MOV),
        sem=Sem.SUB_IMM,
    ),
    Op(
        code=6,
        mnemonic="MULI",
        operands=1,
        description="ACC *= n",
        micro=(Micro.RING_READ, Micro.MUL, Micro.MOV),
        sem=Sem.MUL_IMM,
    ),
    Op(
        code=7,
        mnemonic="LD",
        operands=1,
        description="ACC = store[addr]",
        micro=(
            Micro.LIT0,
            Micro.SEND_MEM,
            Micro.RING_READ,
            Micro.SEND_MEM,
            Micro.READ_MEM,
            Micro.MOV,
        ),
        sem=Sem.LOAD,
    ),
    Op(
        code=8,
        mnemonic="ST",
        operands=1,
        description="store[addr] = ACC (ACC preserved)",
        micro=(
            Micro.LIT1,
            Micro.SEND_MEM,
            Micro.RING_READ,
            Micro.SEND_MEM,
            Micro.SWAP,
            Micro.SEND_MEM,
            Micro.SWAP,
        ),
        sem=Sem.STORE,
    ),
    Op(
        code=9,
        mnemonic="ADD",
        operands=1,
        description="ACC += store[addr]",
        micro=(
            Micro.LIT0,
            Micro.SEND_MEM,
            Micro.RING_READ,
            Micro.SEND_MEM,
            Micro.READ_MEM,
            Micro.ADD,
            Micro.MOV,
        ),
        sem=Sem.ADD_MEM,
    ),
    Op(
        code=10,
        mnemonic="SUB",
        operands=1,
        description="ACC -= store[addr]",
        micro=(
            Micro.LIT0,
            Micro.SEND_MEM,
            Micro.RING_READ,
            Micro.SEND_MEM,
            Micro.READ_MEM,
            Micro.SWAP,
            Micro.SUB,
            Micro.MOV,
        ),
        sem=Sem.SUB_MEM,
    ),
    Op(
        code=11,
        mnemonic="JMPF",
        operands=1,
        description="recirculate the next n words without executing them",
        micro=(Micro.RING_READ, Micro.BP_LOAD, Micro.SKIP_CYCLE),
        sem=Sem.JUMP,
        aliases=("JMP",),  # source-level jumps are absolute (ARCH §5.3)
    ),
    Op(
        code=12,
        mnemonic="BRZ",
        operands=1,
        description="skip n words if ACC == 0",
        micro=(Micro.RING_READ, Micro.SWAP, Micro.SIGN_BRANCH, Micro.THREE_WAY),
        sem=Sem.BR_ZERO,
    ),
    Op(
        code=13,
        mnemonic="BRN",
        operands=1,
        description="skip n words if ACC < 0",
        micro=(Micro.RING_READ, Micro.SWAP, Micro.SIGN_BRANCH, Micro.THREE_WAY),
        sem=Sem.BR_NEG,
    ),
    Op(
        code=14,
        mnemonic="DSP",
        operands=1,
        description="send ACC to LM-75 port p (0=ADDR, 1=DATA, 2=SWAP)",
        micro=(Micro.RING_READ, Micro.SWAP, Micro.SEND_DSP, Micro.SWAP),
        sem=Sem.DISPLAY,
    ),
    Op(
        code=15,
        mnemonic="HALT",
        operands=0,
        description="stop the CPU man",
        micro=(Micro.HALT,),
        sem=Sem.HALT,
    ),
)

#: ``ARCH.md`` §6's table exactly: 16 opcodes, a depth-4 decode trie.
LM1_V1 = Isa(name="lm1-v1", ops=_V1_OPS)


# ── proposed extensions (see the step-2 report) ─────────────────────────────
# Everything below is *not* in ARCH.md §6. Each row exists because a task
# program could not be written without it; each is cheap in hardware (a lane of
# 3–6 glyphs, one more trie bit). `ext=True` so tooling can tell them apart.
_EXT_OPS: tuple[Op, ...] = (
    Op(
        code=16,
        mnemonic="DIVI",
        operands=1,
        description="ACC = floor(ACC / n) — v1 has no division at all",
        micro=(Micro.RING_READ, Micro.SWAP, Micro.DIV, Micro.MOV),
        sem=Sem.DIV_IMM,
        ext=True,
    ),
    Op(
        code=17,
        mnemonic="MODI",
        operands=1,
        description="ACC = ACC mod n (n's sign) — needed for any packed stack",
        micro=(Micro.RING_READ, Micro.SWAP, Micro.MOD, Micro.MOV),
        sem=Sem.MOD_IMM,
        ext=True,
    ),
    Op(
        code=18,
        mnemonic="MUL",
        operands=1,
        description="ACC *= store[addr] — v1 has MULI but no memory multiply",
        micro=(
            Micro.LIT0,
            Micro.SEND_MEM,
            Micro.RING_READ,
            Micro.SEND_MEM,
            Micro.READ_MEM,
            Micro.MUL,
            Micro.MOV,
        ),
        sem=Sem.MUL_MEM,
        ext=True,
    ),
    Op(
        code=19,
        mnemonic="LDP",
        operands=1,
        description="ACC = store[store[addr]] — indexed read (arrays)",
        micro=(
            Micro.LIT0,
            Micro.SEND_MEM,
            Micro.RING_READ,
            Micro.SEND_MEM,
            Micro.READ_MEM,
            Micro.SEND_SPILL,
            Micro.LIT0,
            Micro.SEND_MEM,
            Micro.READ_SPILL,
            Micro.SEND_MEM,
            Micro.READ_MEM,
            Micro.MOV,
        ),
        sem=Sem.LOAD_IND,
        ext=True,
    ),
    Op(
        code=20,
        mnemonic="STP",
        operands=1,
        description="store[store[addr]] = ACC — indexed write; needs the SPILL pipe",
        micro=(
            Micro.LIT0,
            Micro.SEND_MEM,
            Micro.RING_READ,
            Micro.SEND_MEM,
            Micro.READ_MEM,
            Micro.SEND_SPILL,  # park the pointer: `1` below would clobber A
            Micro.LIT1,
            Micro.SEND_MEM,
            Micro.READ_SPILL,
            Micro.SEND_MEM,
            Micro.SWAP,
            Micro.SEND_MEM,
            Micro.SWAP,
        ),
        sem=Sem.STORE_IND,
        ext=True,
    ),
    Op(
        code=21,
        mnemonic="NEG",
        operands=0,
        description="ACC = -ACC — the ROM can only hold non-negative literals",
        micro=(Micro.SWAP, Micro.NEG, Micro.MOV),
        sem=Sem.NEGATE,
        ext=True,
    ),
    Op(
        code=22,
        mnemonic="PUSH",
        operands=0,
        description="push ACC onto the SPILL ring (one extra live value)",
        micro=(Micro.SWAP, Micro.SEND_SPILL, Micro.SWAP),
        sem=Sem.SPILL_PUSH,
        ext=True,
    ),
    Op(
        code=23,
        mnemonic="POP",
        operands=0,
        description="ACC = pop from the SPILL ring",
        micro=(Micro.READ_SPILL, Micro.MOV),
        sem=Sem.SPILL_POP,
        ext=True,
    ),
)

#: v1 plus the extensions the task programs actually needed. Depth-5 trie.
LM1_EXT = LM1_V1.extended("lm1-ext", _EXT_OPS)

#: What the assembler and emulator use unless told otherwise.
DEFAULT_ISA = LM1_EXT


class TickModel(BaseModel):
    """``ARCH.md`` §7.2's budget, as tunable data.

    Only used to *compare* programs; the authority is the real ``.man`` run.
    """

    model_config = ConfigDict(frozen=True)

    fetch: int = 6  # ring throughput bound (§2.1)
    decode: int = 17  # depth-4 trie, mid of the 15–20 estimate
    ret: int = 15  # return to the fetch site, mid of 10–20
    operand_fetch: int = 6  # the extra ring lap for an operand word
    skip_word: int = 8  # per word recirculated by a taken jump (§5.4)
    store_word: int = 6  # per word exchanged with the STORE block

    def instruction(self, op: Op) -> int:
        micro = sum(MICRO_TICKS[g] for g in op.micro)
        return (
            self.fetch
            + self.decode
            + self.ret
            + micro
            + (self.operand_fetch if op.operands else 0)
            + self.store_word * op.store_words
        )


DEFAULT_TICKS = TickModel()
