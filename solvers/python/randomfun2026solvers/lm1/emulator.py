"""The LM-1 emulator: an assembled word ring, executed with LM-1's semantics.

What this models faithfully (``ARCH.md`` §5):

* **No PC.** The program counter *is* the code ring's rotation phase (§5.3).
  Words come out in program order; ``JMPF n`` recirculates the next ``n`` words
  without executing them, so a backward jump to ``L`` words back costs
  ``n = P - L``. :attr:`Emulator.phase` is the whole of the control state.
* **Three registers.** ``A`` (scratch — clobbered by *every* fetch, including the
  operand fetch), ``B`` (the accumulator), ``BP`` (write-only/branch-only).
  Handlers move A and B exactly the way §6's micro-programs do, so a program
  that "accidentally" relies on a value surviving a fetch fails here too.
* **64-bit signed wraparound**, floored division and B's-sign modulo, matching
  ``SPEC.md``.
* **STORE is abstract** (§4.1): a :class:`~.store.Store` speaking the ``memory``
  problem's wire protocol. The default is a dict stub.
* **Round-based input gating** (``GRADING.md``): round ``N+1``'s input stays
  withheld until round ``N``'s output is complete.

The tick figure is the §7.2 *estimate*, for comparing programs against each
other. The real number comes from the generated ``.man`` on the wasm engine.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .asm import Program
from .isa import DEFAULT_TICKS, Op, Sem, TickModel
from .store import DictStore, PathUnit, SnakeUnit, SpillRing, Store, StreamUnit

__all__ = [
    "Round",
    "RunResult",
    "Emulator",
    "EmulatorError",
    "InputWithheld",
    "run_rounds",
    "TICK_CAP",
    "WORD_MIN",
    "WORD_MAX",
]

#: ``GRADING.md``: default step cap.
TICK_CAP = 5_000_000

_MASK = (1 << 64) - 1
WORD_MIN = -(1 << 63)
WORD_MAX = (1 << 63) - 1


def wrap(value: int) -> int:
    """Signed 64-bit wraparound, as ``SPEC.md`` specifies for every operation."""
    return ((value + (1 << 63)) & _MASK) - (1 << 63)


def floor_div(a: int, b: int) -> tuple[int, int]:
    """``SPEC.md`` ``/``: floored quotient in A, remainder in B; b == 0 is defined."""
    if b == 0:
        return 0, a
    q = a // b  # Python's // is already floored
    return wrap(q), wrap(a - q * b)


def sign_mod(a: int, b: int) -> int:
    """``SPEC.md`` ``%``: remainder takes **B's** sign; 0 when b == 0."""
    if b == 0:
        return 0
    return wrap(a - (a // b) * b)


class EmulatorError(RuntimeError):
    """A fault the real machine would show as a hang or a wrong answer."""


class InputWithheld(EmulatorError):
    """``IN`` blocked forever: the judge is waiting for this round's output."""


class _InputExhausted(Exception):
    """Internal: no input left anywhere — in hardware the man just blocks."""


class Round(BaseModel):
    """One round of a test case: an input burst and the output it must produce."""

    model_config = ConfigDict(frozen=True)

    input: tuple[int, ...] = ()
    expected: tuple[int, ...] = ()


class RunResult(BaseModel):
    """Everything a caller needs to grade and to compare cost."""

    model_config = ConfigDict(frozen=True)

    output: tuple[int, ...]
    instructions: int
    ticks: int
    reason: str
    halted: bool
    phase: int
    a: int
    b: int
    bp: int
    words_skipped: int
    store_cells: dict[int, int] = Field(default_factory=dict)
    spill_high_water: int = 0
    display_writes: tuple[tuple[int, int], ...] = ()

    @property
    def over_tick_cap(self) -> bool:
        return self.ticks > TICK_CAP

    def matches(self, rounds: Sequence[Round]) -> bool:
        expected = tuple(v for r in rounds for v in r.expected)
        return self.output == expected


Handler = Callable[["Emulator", int | None], None]
_HANDLERS: dict[Sem, Handler] = {}


def _handler(sem: Sem) -> Callable[[Handler], Handler]:
    def register(fn: Handler) -> Handler:
        _HANDLERS[sem] = fn
        return fn

    return register


class Emulator:
    """Executes a :class:`~.asm.Program`.

    Dispatch is by :class:`~.isa.Sem` tag, never by opcode number, so the ISA
    table stays the single source of truth (``ARCH.md`` §6/§7.1).
    """

    def __init__(
        self,
        program: Program,
        *,
        store: Store | None = None,
        spill: SpillRing | None = None,
        ticks: TickModel = DEFAULT_TICKS,
    ) -> None:
        self.program = program
        self.words = list(program.words)
        self.store: Store = store if store is not None else DictStore()
        self.spill = spill if spill is not None else SpillRing()
        self._stream: StreamUnit | SnakeUnit | PathUnit | None = None
        self.tick_model = ticks

        self.a = 0
        self.b = 0  # the accumulator
        self.bp = 0
        self.phase = 0  # the ring's rotation phase == the PC (§5.3)
        self.halted = False
        self.reason = ""
        self.instructions = 0
        self.ticks = 0
        self.words_skipped = 0
        self.output: list[int] = []
        self.display_writes: list[tuple[int, int]] = []

        self._rounds: tuple[Round, ...] = ()
        self._cum_out: tuple[int, ...] = ()
        self._in_cursor = 0

    # ── ring ────────────────────────────────────────────────────────────────
    @property
    def P(self) -> int:  # noqa: N802
        return len(self.words)

    def _fetch(self) -> int:
        """Take the next ring word (and put it straight back — §5.3)."""
        word = self.words[self.phase]
        self.phase = (self.phase + 1) % self.P
        self.a = word  # every fetch clobbers A (§5.1)
        return word

    def _skip(self, n: int) -> None:
        self.phase = (self.phase + n) % self.P
        self.words_skipped += n
        self.ticks += self.tick_model.skip_word * n

    # ── input / output ──────────────────────────────────────────────────────
    @property
    def _rounds_released(self) -> int:
        released = 0
        produced = len(self.output)
        for k in range(len(self._rounds)):
            if produced >= self._cum_out[k]:
                released = k + 1
            else:
                break
        return released

    def _next_input(self) -> int:
        released = self._rounds_released
        available: list[int] = []
        for r in self._rounds[:released]:
            available.extend(r.input)
        if self._in_cursor < len(available):
            value = available[self._in_cursor]
            self._in_cursor += 1
            return value
        if released < len(self._rounds):
            raise InputWithheld(
                f"IN blocked: round {released + 1}'s input is withheld until round "
                f"{released}'s output is complete (produced {len(self.output)} of "
                f"{self._cum_out[released]} words)"
            )
        raise _InputExhausted

    def _emit(self, value: int) -> None:
        self.output.append(wrap(value))

    # ── run ─────────────────────────────────────────────────────────────────
    def run(
        self,
        rounds: Sequence[Round] | None = None,
        *,
        input: Sequence[int] | None = None,
        max_instructions: int = 1_000_000,
    ) -> RunResult:
        """Execute until halt, input exhaustion, or the instruction cap."""
        if rounds is None:
            rounds = [Round(input=tuple(input or ()))]
        self._rounds = tuple(rounds)
        cum, total = [], 0
        for r in self._rounds:
            cum.append(total)
            total += len(r.expected)
        self._cum_out = tuple(cum)

        while not self.halted:
            if self.instructions >= max_instructions:
                self.reason = "instruction-cap"
                break
            try:
                self.step()
            except _InputExhausted:
                self.reason = "input-exhausted"
                break
        return self.result()

    def step(self) -> Op:
        """Fetch, decode and execute one instruction."""
        code = self._fetch()
        try:
            op = self.program.isa.by_code(code)
        except KeyError as exc:
            raise EmulatorError(
                f"ring phase {self.phase - 1}: word {code} is not an opcode in "
                f"{self.program.isa.name}"
            ) from exc
        operand = self._fetch() if op.operands else None
        try:
            handler = _HANDLERS[op.sem]
        except KeyError as exc:
            raise EmulatorError(f"{op.mnemonic}: no handler for semantics {op.sem!r}") from exc
        handler(self, operand)
        self.instructions += 1
        self.ticks += self.tick_model.instruction(op)
        return op

    def result(self) -> RunResult:
        cells: dict[int, int] = {}
        snapshot = getattr(self.store, "snapshot", None)
        if callable(snapshot):
            cells = snapshot()
        return RunResult(
            output=tuple(self.output),
            instructions=self.instructions,
            ticks=self.ticks,
            reason=self.reason or ("halted" if self.halted else "stopped"),
            halted=self.halted,
            phase=self.phase,
            a=self.a,
            b=self.b,
            bp=self.bp,
            words_skipped=self.words_skipped,
            store_cells=cells,
            spill_high_water=self.spill.high_water,
            display_writes=tuple(self.display_writes),
        )

    # ── STREAM: created on first use, since it owns the I and O rooms ────────
    @property
    def stream(self) -> StreamUnit | SnakeUnit | PathUnit:
        """The coprocessor the program named with ``.unit``, wired to this run.

        Lazily built because a unit *is* part of the machine's I/O on a program that
        has one: the STREAM block's ``RDIN`` arm hands input words to the CPU and its
        ``EMIT`` arm writes the output room, and the snake and path units own the
        LM-75 — so in every case the hooks have to be this emulator's own. Recording a
        display-owning unit's port writes in ``display_writes`` is what lets
        ``display.frames_from_writes`` grade a machine whose CPU never draws.
        """
        if self._stream is None:
            if self.program.unit == "snake":
                self._stream = SnakeUnit(
                    lambda port, value: self.display_writes.append((port, value))
                )
            elif self.program.unit == "path":
                self._stream = PathUnit(
                    lambda port, value: self.display_writes.append((port, value))
                )
            else:
                self._stream = StreamUnit(self._next_input, self._emit)
        return self._stream

    # ── STORE helpers (the memory-problem wire protocol) ────────────────────
    def _mem_read(self, addr: int) -> int:
        self.store.send(0)
        self.store.send(addr)
        return wrap(self.store.recv())

    def _mem_write(self, addr: int, value: int) -> None:
        self.store.send(1)
        self.store.send(addr)
        self.store.send(value)


# ── handlers, one per Sem tag ────────────────────────────────────────────────
@_handler(Sem.NOP)
def _nop(em: Emulator, _: int | None) -> None:
    return None


@_handler(Sem.SET_IMM)
def _set_imm(em: Emulator, n: int | None) -> None:
    assert n is not None
    em.a = wrap(n)
    em.b = em.a  # `M`


@_handler(Sem.INPUT)
def _input(em: Emulator, _: int | None) -> None:
    em.a = wrap(em._next_input())  # `r→in`
    em.b = em.a  # `M`


@_handler(Sem.STREAM_SEND)
def _stream_send(em: Emulator, _: int | None) -> None:
    em.a, em.b = em.b, em.a  # `W`
    em.stream.send(em.a)  # `s→stream`
    em.a, em.b = em.b, em.a  # `W` — ACC survives, as with OUT


@_handler(Sem.STREAM_RECV)
def _stream_recv(em: Emulator, _: int | None) -> None:
    em.a = wrap(em.stream.recv())  # `r→stream`
    em.b = em.a  # `M`


@_handler(Sem.OUTPUT)
def _output(em: Emulator, _: int | None) -> None:
    em.a, em.b = em.b, em.a  # `W`
    em._emit(em.a)  # `s→out`
    em.a, em.b = em.b, em.a  # `W` — ACC survives for free (§6)


@_handler(Sem.ADD_IMM)
def _add_imm(em: Emulator, n: int | None) -> None:
    assert n is not None
    em.a = wrap(n)
    em.a = wrap(em.a + em.b)  # `+`
    em.b = em.a  # `M`


@_handler(Sem.SUB_IMM)
def _sub_imm(em: Emulator, n: int | None) -> None:
    assert n is not None
    em.a = wrap(n)
    em.a, em.b = em.b, em.a  # `W`
    em.a = wrap(em.a - em.b)  # `-`
    em.b = em.a  # `M`


@_handler(Sem.MUL_IMM)
def _mul_imm(em: Emulator, n: int | None) -> None:
    assert n is not None
    em.a = wrap(n)
    em.a = wrap(em.a * em.b)  # `*`
    em.b = em.a  # `M`


@_handler(Sem.DIV_IMM)
def _div_imm(em: Emulator, n: int | None) -> None:
    assert n is not None
    em.a = wrap(n)
    em.a, em.b = em.b, em.a  # `W`
    em.a, em.b = floor_div(em.a, em.b)  # `/`
    em.b = em.a  # `M`


@_handler(Sem.MOD_IMM)
def _mod_imm(em: Emulator, n: int | None) -> None:
    assert n is not None
    em.a = wrap(n)
    em.a, em.b = em.b, em.a  # `W`
    em.a = sign_mod(em.a, em.b)  # `%`
    em.b = em.a  # `M`


@_handler(Sem.LOAD)
def _load(em: Emulator, addr: int | None) -> None:
    assert addr is not None
    em.a = em._mem_read(addr)
    em.b = em.a  # `M`


@_handler(Sem.STORE)
def _store(em: Emulator, addr: int | None) -> None:
    assert addr is not None
    em._mem_write(addr, em.b)
    em.a = wrap(addr)  # the `W` … `W` sandwich leaves A = addr, ACC intact


@_handler(Sem.INC_MEM)
def _inc_mem(em: Emulator, addr: int | None) -> None:
    assert addr is not None
    old = em._mem_read(addr)
    em._mem_write(addr, wrap(old + 1))
    # `M` spent the incoming ACC on the address, and B has held the *old* value
    # since the `W` before the write marker. A is what the last `s→mem` sent.
    em.a = wrap(old + 1)
    em.b = old


@_handler(Sem.DEC_MEM)
def _dec_mem(em: Emulator, addr: int | None) -> None:
    assert addr is not None
    old = em._mem_read(addr)
    em._mem_write(addr, wrap(old - 1))
    em.a = wrap(old - 1)
    em.b = old


@_handler(Sem.ADD_MEM)
def _add_mem(em: Emulator, addr: int | None) -> None:
    assert addr is not None
    em.a = em._mem_read(addr)
    em.a = wrap(em.a + em.b)  # `+`
    em.b = em.a  # `M`


@_handler(Sem.AND_MEM)
def _and_mem(em: Emulator, addr: int | None) -> None:
    assert addr is not None
    em.a = em._mem_read(addr)
    em.a = wrap(em.a & em.b)  # `&` — commutative, so no `W` needed
    em.b = em.a  # `M`


@_handler(Sem.OR_MEM)
def _or_mem(em: Emulator, addr: int | None) -> None:
    assert addr is not None
    em.a = em._mem_read(addr)
    em.a = wrap(em.a | em.b)  # `|` — commutative, so no `W` needed
    em.b = em.a  # `M`


@_handler(Sem.SUB_MEM)
def _sub_mem(em: Emulator, addr: int | None) -> None:
    assert addr is not None
    em.a = em._mem_read(addr)
    em.a, em.b = em.b, em.a  # `W`
    em.a = wrap(em.a - em.b)  # `-`
    em.b = em.a  # `M`


@_handler(Sem.DIV_MEM)
def _div_mem(em: Emulator, addr: int | None) -> None:
    assert addr is not None
    em.a = em._mem_read(addr)
    em.a, em.b = em.b, em.a  # `W` — the dividend is ACC, so the operands swap
    em.a, em.b = floor_div(em.a, em.b)  # `/`
    em.b = em.a  # `M`


@_handler(Sem.MUL_MEM)
def _mul_mem(em: Emulator, addr: int | None) -> None:
    assert addr is not None
    em.a = em._mem_read(addr)
    em.a = wrap(em.a * em.b)  # `*`
    em.b = em.a  # `M`


@_handler(Sem.LOAD_IND)
def _load_ind(em: Emulator, addr: int | None) -> None:
    assert addr is not None
    ptr = em._mem_read(addr)
    em.spill.push(ptr)  # `s→spill` — `0` below would clobber A
    ptr = em.spill.pop()  # `r→spill`
    em.a = em._mem_read(ptr)
    em.b = em.a  # `M`


@_handler(Sem.STORE_IND)
def _store_ind(em: Emulator, addr: int | None) -> None:
    assert addr is not None
    ptr = em._mem_read(addr)
    em.spill.push(ptr)
    ptr = em.spill.pop()
    em._mem_write(ptr, em.b)
    em.a = wrap(ptr)


@_handler(Sem.LOAD_ACC)
def _load_acc(em: Emulator, _: int | None) -> None:
    em.a = 0  # `0` — clobbers A while the address sits safely in B
    em.store.send(em.a)
    em.a, em.b = em.b, em.a  # `W` — A = the address
    em.store.send(em.a)
    em.a = wrap(em.store.recv())  # `r→mem`
    em.b = em.a  # `M`


@_handler(Sem.STORE_ACC_MEM)
def _store_acc_mem(em: Emulator, src: int | None) -> None:
    assert src is not None
    value = em._mem_read(src)  # source first: its address is an immediate
    em.a, em.b = value, em.b  # `r→mem`
    em.a, em.b = em.b, em.a  # `W` — A = the destination address (was ACC)
    dest = em.a
    em.store.send(1)  # `N` + `s→mem`: the sign is the write marker in hardware
    em.store.send(dest)
    em.a, em.b = em.b, em.a  # `W` — A = the value again
    em.store.send(em.a)  # `s→mem`
    em.b = em.a  # `M`


@_handler(Sem.DISPLAY_ADDR)
def _display_addr(em: Emulator, _: int | None) -> None:
    em.a, em.b = em.b, em.a  # `W`
    em.display_writes.append((0, em.a))
    em.a, em.b = em.b, em.a  # `W` — ACC preserved


@_handler(Sem.DISPLAY_DATA)
def _display_data(em: Emulator, _: int | None) -> None:
    em.a, em.b = em.b, em.a
    em.display_writes.append((1, em.a))
    em.a, em.b = em.b, em.a


@_handler(Sem.DISPLAY_SWAP)
def _display_swap(em: Emulator, _: int | None) -> None:
    em.a, em.b = em.b, em.a
    em.display_writes.append((2, em.a))
    em.a, em.b = em.b, em.a


@_handler(Sem.NEGATE)
def _negate(em: Emulator, _: int | None) -> None:
    em.a, em.b = em.b, em.a  # `W`
    em.a = wrap(-em.a)  # `N`
    em.b = em.a  # `M`


@_handler(Sem.SPILL_PUSH)
def _spill_push(em: Emulator, _: int | None) -> None:
    em.a, em.b = em.b, em.a  # `W`
    em.spill.push(em.a)
    em.a, em.b = em.b, em.a  # `W`


@_handler(Sem.SPILL_POP)
def _spill_pop(em: Emulator, _: int | None) -> None:
    em.a = wrap(em.spill.pop())
    em.b = em.a  # `M`


@_handler(Sem.JUMP)
def _jump(em: Emulator, n: int | None) -> None:
    assert n is not None
    em.bp = n  # `b`
    em._skip(n)


@_handler(Sem.BR_ZERO)
def _br_zero(em: Emulator, n: int | None) -> None:
    assert n is not None
    # `W` `X`: branch on sign(ACC); every lane's second `W` restores A = n.
    if em.b == 0:
        em._skip(n)


@_handler(Sem.BR_NEG)
def _br_neg(em: Emulator, n: int | None) -> None:
    assert n is not None
    if em.b < 0:
        em._skip(n)


@_handler(Sem.DISPLAY)
def _display(em: Emulator, port: int | None) -> None:
    assert port is not None
    em.display_writes.append((port, em.b))


@_handler(Sem.HALT)
def _halt(em: Emulator, _: int | None) -> None:
    em.halted = True
    em.reason = "halted"


def run_rounds(
    program: Program,
    rounds: Sequence[Round],
    *,
    store: Store | None = None,
    max_instructions: int = 1_000_000,
    **kw: Any,
) -> RunResult:
    """Convenience wrapper: fresh :class:`Emulator`, one run, one result."""
    return Emulator(program, store=store, **kw).run(rounds, max_instructions=max_instructions)
