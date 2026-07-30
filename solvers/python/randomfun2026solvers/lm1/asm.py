"""The LM-1 assembler: mnemonics, labels, comments -> a ring of words.

Source form (see ``lm1/programs/*.asm``)::

    ; comments start with ';' or '#'
    .equ N 0                 ; symbolic STORE address
    start:  IN               ; label, then an instruction
            ST  N
            BRZ done         ; label operands become forward-skip counts
            JMP start
    done:   HALT

Two things make this more than a mnemonic table:

* **Jumps.** The hardware only skips *forward* (``ARCH.md`` §5.3), so every
  label resolves to ``n = (target - after_this_instruction) mod P``. A backward
  jump to ``L`` words back is the documented ``n = P - L``.
* **The §5.3 invariant.** Every ring read must be paired with a write-back, or
  the program erases itself on the first lap. :func:`check_ring_writeback`
  enforces that against the ISA table before a single word is emitted.

:class:`Program` also reports ``P`` (word count) and the ring capacity the
generator must build (``P + 2..4``, §2.1).
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from .isa import DEFAULT_ISA, DEFAULT_TICKS, Isa, Micro, Op, Sem, TickModel

__all__ = [
    "AsmError",
    "Instr",
    "Program",
    "assemble",
    "assemble_file",
    "check_ring_writeback",
    "strip_comment",
    "RING_SLACK_MIN",
    "RING_SLACK_MAX",
    "UNITS",
]

#: Coprocessors a program may name with ``.unit``. ``SND``/``RCV`` are the whole
#: interface to whichever one is wired in, so the *program* has to say which — the
#: emulator picks a model and the generator picks a block from this one word.
#: ``stream`` is ``lm1/stream.py``'s three rings and a MAC (``matmul``); ``snake`` is
#: ``lm1/snake_unit.py``'s body FIFO, which also owns the display and answers nothing.
#: ``doom`` is ``lm1/d3_unit.py``'s column painter: the deadman-3d panel plus the
#: baked HUD/FLASH patterns, write-only like ``snake`` and ``path``.
#: ``doom4`` is ``lm1/d3_router.py``'s tiled wall — the *same* unit four times over
#: behind a 1-of-4 router, so one ``SND`` lane drives a 128x96 framebuffer as four
#: 64x48 panels.  The panel's interior is capped at 64x64 (``SPEC.md``), so tiling
#: is the only way past it; the word carries a tile selector in its low three bits
#: (:func:`d3_router.word`) and everything above that is the unmodified ``doom``
#: protocol.
#:
#: ``stream4`` is the *same block* as ``stream`` built with a depth-4 decode trie:
#: sixteen leaves, twelve arms, and a ``16 * arg + code`` wire format. It is a
#: separate name rather than a flag because the trie width is wired into the
#: hardware once and cannot be widened in place — a ``stream`` program's command
#: words alias into different arms at mod-16 (see :class:`~.store.StreamUnit`'s
#: docstring), so a program has to name the width it was written against.
UNITS = frozenset({"stream", "stream4", "snake", "path", "doom", "doom4"})

#: Decode-trie depth per unit name. Only ``stream``/``stream4`` differ.
UNIT_TRIE_BITS = {"stream": 3, "stream4": 4}

#: ``.equ`` name by which a program declares the shift its STREAM unit's ``UPDB``
#: arm applies. It belongs in the program because it is not a command field — the
#: shift is wired into the unit — so a program that assumes one and is run against
#: a unit built with another gets wrong arithmetic and no error. Naming it here
#: means the emitted ``.asm`` is self-describing: assembling it and running it is
#: enough, with no out-of-band setup, which is the property the verification
#: ladder rests on. Absent, the unit keeps its own default.
STREAM_LR_SHIFT_EQU = "STREAM_LR_SHIFT"

#: ``ARCH.md`` §2.1: ring capacity must be ``P + slack``; too small deadlocks,
#: too large starves the CPU.
RING_SLACK_MIN = 2
RING_SLACK_MAX = 4

_LABEL_DEF = re.compile(r"^([A-Za-z_.][A-Za-z0-9_.\-]*):")
_NAME = re.compile(r"^[A-Za-z_.][A-Za-z0-9_.\-]*$")
_STRING = re.compile(r'"((?:[^"\\]|\\.)*)"')
_ESCAPES = {"n": "\n", "t": "\t", "r": "\r", "0": "\0", '"': '"', "\\": "\\", "'": "'"}


def strip_comment(line: str) -> str:
    """Drop a ``;``/``#`` comment, ignoring delimiters inside a quoted string.

    ``.ascii "…);…"`` is common in the ASCII problems, so a naive regex would
    truncate the data.
    """
    out: list[str] = []
    quote: str | None = None
    escaped = False
    for ch in line:
        if quote:
            out.append(ch)
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == quote:
                quote = None
            continue
        if ch in ";#":
            break
        if ch in "\"'":
            quote = ch
        out.append(ch)
    return "".join(out)


class AsmError(ValueError):
    """A syntax, resolution or invariant error, with the source line attached."""

    def __init__(self, message: str, *, line: int | None = None, text: str = "") -> None:
        where = f" (line {line}: {text.strip()!r})" if line is not None else ""
        super().__init__(f"{message}{where}")
        self.line = line
        self.text = text


# ── ISA-level invariant check (ARCH §5.3) ────────────────────────────────────
def check_ring_writeback(isa: Isa) -> None:
    """Assert that every ring read in ``isa`` is paired with a write-back.

    Concretely: an opcode may only touch the code ring through ``r↺`` (the
    read-and-send-back glyph pair), it must do so exactly once per operand word,
    and any opcode that skips words must recirculate them (``…skip-cycle`` /
    ``…3 lanes``, both of which contain the ``s`` that puts the word back).
    """
    for op in isa:
        reads = op.micro.count(Micro.RING_READ)
        if reads != op.operands:
            raise AsmError(
                f"{op.mnemonic}: {reads} ring read(s) for {op.operands} operand word(s) — "
                "an unpaired ring read erases the program (ARCH §5.3)"
            )
        if op.takes_target and not (Micro.SKIP_CYCLE in op.micro or Micro.THREE_WAY in op.micro):
            raise AsmError(
                f"{op.mnemonic}: skips words but has no recirculating skip cycle — "
                "skipped words must be written back too (ARCH §5.3)"
            )


# ── models ───────────────────────────────────────────────────────────────────
class Instr(BaseModel):
    """One assembled instruction, with its ring position."""

    model_config = ConfigDict(frozen=True)

    pos: int  # word index of the opcode word
    mnemonic: str
    code: int
    sem: Sem
    operand: int | None = None
    operand_token: str | None = None
    line: int = 0
    text: str = ""

    @property
    def words(self) -> int:
        return 1 if self.operand is None else 2


class Program(BaseModel):
    """An assembled ring: the word list plus everything the tools need."""

    model_config = ConfigDict(frozen=True)

    name: str
    isa: Isa
    words: tuple[int, ...]
    instrs: tuple[Instr, ...]
    labels: dict[str, int]
    equs: dict[str, int]
    source: str = ""
    unit: str = "stream"
    """Which coprocessor ``SND``/``RCV`` talk to (``.unit``; see :data:`UNITS`)."""

    # ring geometry ----------------------------------------------------------
    @property
    def P(self) -> int:  # noqa: N802 - ARCH.md calls it P
        """Program length in ring words."""
        return len(self.words)

    @property
    def ring_capacity(self) -> tuple[int, int]:
        """Inclusive range of ring capacities the hardware may use (``P + 2..4``)."""
        return (self.P + RING_SLACK_MIN, self.P + RING_SLACK_MAX)

    # ISA usage --------------------------------------------------------------
    @property
    def ops_used(self) -> tuple[Op, ...]:
        codes = {i.code for i in self.instrs}
        return tuple(op for op in self.isa if op.code in codes)

    @property
    def ext_ops(self) -> tuple[str, ...]:
        """Mnemonics used that are *not* in ``ARCH.md`` §6's v1 table."""
        return tuple(op.mnemonic for op in self.ops_used if op.ext)

    def at(self, pos: int) -> Instr | None:
        for instr in self.instrs:
            if instr.pos == pos:
                return instr
        return None

    # cost estimate ----------------------------------------------------------
    def static_ticks(self, ticks: TickModel = DEFAULT_TICKS) -> int:
        """Ticks to execute each instruction once (no loops) — a sanity figure."""
        return sum(ticks.instruction(self.isa.by_code(i.code)) for i in self.instrs)

    def report(self) -> str:
        lo, hi = self.ring_capacity
        ext = ", ".join(self.ext_ops) or "none"
        return (
            f"{self.name}: P={self.P} words, {len(self.instrs)} instructions, "
            f"ring capacity {lo}..{hi}, ISA {self.isa.name} "
            f"(decode depth {self.isa.decode_bits}), ext ops: {ext}"
        )

    def listing(self) -> str:
        out = []
        rev: dict[int, list[str]] = {}
        for label, pos in self.labels.items():
            rev.setdefault(pos, []).append(label)
        for instr in self.instrs:
            for label in rev.get(instr.pos, []):
                out.append(f"{label}:")
            operand = "" if instr.operand is None else f" {instr.operand}"
            token = f"   ; {instr.operand_token}" if instr.operand_token else ""
            out.append(f"  {instr.pos:4d}  {instr.mnemonic}{operand}{token}")
        return "\n".join(out)


# ── assembler ────────────────────────────────────────────────────────────────
class _Parsed(BaseModel):
    """An instruction after pass 1: position known, operand still a token."""

    model_config = ConfigDict(frozen=True)

    pos: int
    op: Op
    token: str | None
    line: int
    text: str


def assemble(
    source: str,
    *,
    name: str = "<asm>",
    isa: Isa = DEFAULT_ISA,
    allow_negative_words: bool = False,
) -> Program:
    """Assemble ``source`` into a :class:`Program`.

    ``allow_negative_words`` defaults to False on purpose: the ROM encodes words
    as ``` `NNN` ``` literals, which are digits only, so a negative *word* is not
    representable in hardware (``ARCH.md`` §4.2). Build negatives at runtime
    (``LDI 0`` / ``SUBI 1``) or with the ``NEG`` extension.
    """
    check_ring_writeback(isa)
    equs: dict[str, int] = {}
    labels: dict[str, int] = {}
    parsed: list[_Parsed] = []
    unit = "stream"
    pos = 0

    for lineno, raw in enumerate(source.splitlines(), start=1):
        line = strip_comment(raw).strip()
        while line:
            m = _LABEL_DEF.match(line)
            if not m:
                break
            label = m.group(1)
            if label in labels:
                raise AsmError(f"duplicate label {label!r}", line=lineno, text=raw)
            labels[label] = pos
            line = line[m.end() :].strip()
        if not line:
            continue

        head, _, rest = line.partition(" ")
        head_upper = head.upper()
        rest = rest.strip()

        if head.lower() == ".unit":
            if rest not in UNITS:
                raise AsmError(
                    f"unknown coprocessor {rest!r}; have {sorted(UNITS)}", line=lineno, text=raw
                )
            unit = rest
            continue

        if head.startswith("."):
            for op, token in _expand_directive(head.lower(), rest, isa, equs, lineno, raw):
                parsed.append(_Parsed(pos=pos, op=op, token=token, line=lineno, text=raw))
                pos += op.words
            continue

        try:
            op = isa.by_mnemonic(head_upper)
        except KeyError as exc:
            raise AsmError(f"unknown mnemonic {head!r}", line=lineno, text=raw) from exc
        if op.operands and not rest:
            raise AsmError(f"{op.mnemonic} needs an operand", line=lineno, text=raw)
        if not op.operands and rest:
            raise AsmError(f"{op.mnemonic} takes no operand", line=lineno, text=raw)
        parsed.append(_Parsed(pos=pos, op=op, token=rest or None, line=lineno, text=raw))
        pos += op.words

    total = pos
    if total == 0:
        raise AsmError("empty program: the ring needs at least one word")

    words: list[int] = [0] * total
    instrs: list[Instr] = []
    for item in parsed:
        op = item.op
        words[item.pos] = op.code
        operand: int | None = None
        if op.operands:
            assert item.token is not None
            operand = _resolve(item, total, labels, equs)
            if operand < 0 and not allow_negative_words:
                raise AsmError(
                    f"operand {operand} is negative; the ROM stores non-negative "
                    "literals only (ARCH §4.2) — build it at runtime instead",
                    line=item.line,
                    text=item.text,
                )
            words[item.pos + 1] = operand
        instrs.append(
            Instr(
                pos=item.pos,
                mnemonic=op.mnemonic,
                code=op.code,
                sem=op.sem,
                operand=operand,
                operand_token=item.token,
                line=item.line,
                text=item.text.strip(),
            )
        )

    return Program(
        name=name,
        isa=isa,
        words=tuple(words),
        instrs=tuple(instrs),
        labels=labels,
        equs=equs,
        source=source,
        unit=unit,
    )


def assemble_file(
    path: str | Path,
    *,
    isa: Isa = DEFAULT_ISA,
    allow_negative_words: bool = False,
) -> Program:
    """Assemble a ``.asm`` file; the program's name is the file stem."""
    p = Path(path)
    return assemble(
        p.read_text(encoding="utf-8"),
        name=p.stem,
        isa=isa,
        allow_negative_words=allow_negative_words,
    )


def _expand_directive(
    head: str,
    rest: str,
    isa: Isa,
    equs: dict[str, int],
    lineno: int,
    raw: str,
) -> list[tuple[Op, str | None]]:
    """Handle a ``.`` directive; returns (op, token) pairs to emit."""
    if head == ".equ":
        parts = rest.split()
        if len(parts) != 2:
            raise AsmError(".equ needs NAME VALUE", line=lineno, text=raw)
        equs[parts[0]] = _int_token(parts[1], lineno, raw)
        return []

    if head in (".ascii", ".emit"):
        values = (
            _ascii_values(rest, lineno, raw)
            if head == ".ascii"
            else [_int_token(t, lineno, raw) for t in rest.split()]
        )
        try:
            ldi = isa.by_sem(Sem.SET_IMM)
            out = isa.by_sem(Sem.OUTPUT)
        except KeyError as exc:
            raise AsmError(f"{head} needs SET_IMM and OUTPUT opcodes", line=lineno) from exc
        emitted: list[tuple[Op, str | None]] = []
        for value in values:
            emitted.append((ldi, str(value)))
            emitted.append((out, None))
        return emitted

    raise AsmError(f"unknown directive {head!r}", line=lineno, text=raw)


def _ascii_values(rest: str, lineno: int, raw: str) -> list[int]:
    m = _STRING.match(rest.strip())
    if not m:
        raise AsmError('.ascii needs a "quoted string"', line=lineno, text=raw)
    text, i, out = m.group(1), 0, []
    while i < len(text):
        ch = text[i]
        if ch == "\\":
            i += 1
            if i >= len(text) or text[i] not in _ESCAPES:
                raise AsmError("bad escape in .ascii", line=lineno, text=raw)
            ch = _ESCAPES[text[i]]
        code = ord(ch)
        if not 0 <= code <= 127:
            raise AsmError(f"non-ASCII character {ch!r} in .ascii", line=lineno, text=raw)
        out.append(code)
        i += 1
    return out


def _int_token(token: str, lineno: int, raw: str) -> int:
    try:
        if len(token) == 3 and token[0] == token[2] == "'":
            return ord(token[1])
        return int(token, 0)
    except ValueError as exc:
        raise AsmError(f"expected an integer, got {token!r}", line=lineno, text=raw) from exc


def _resolve(item: _Parsed, total: int, labels: dict[str, int], equs: dict[str, int]) -> int:
    token = item.token or ""
    op = item.op
    if op.takes_target:
        if token in labels:
            target = labels[token]
            after = item.pos + op.words
            # Forward-skip-only hardware: a backward jump costs a full lap.
            return (target - after) % total
        if _NAME.match(token):
            raise AsmError(f"unknown label {token!r}", line=item.line, text=item.text)
        skip = _int_token(token, item.line, item.text)
        if not 0 <= skip < total:
            raise AsmError(
                f"skip count {skip} out of range for P={total}", line=item.line, text=item.text
            )
        return skip

    if token in equs:
        return equs[token]
    if token in labels:
        return labels[token]
    if _NAME.match(token):
        raise AsmError(f"unknown symbol {token!r}", line=item.line, text=item.text)
    return _int_token(token, item.line, item.text)


def program_table(programs: Iterable[Program]) -> str:
    """A one-line-per-program summary (used by the CLI and the report)."""
    rows: Sequence[Program] = list(programs)
    return "\n".join(p.report() for p in rows)
