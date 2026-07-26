#!/usr/bin/env python3
"""A tiny assembler front end for the LM-1 programs this repo generates.

``lm1/asm.py`` parses ``.asm`` text and has no macro facility of any kind, so a
program of a few hundred instructions has to be emitted from Python (ARCH.md
§7.5, and every existing program over ~100 lines in ``lm1/programs`` is generated
that way).  This is that emitter, kept separate from the program it builds so the
program reads as a program.

What it adds over raw text: slot allocation (address 0 is unusable — the
generated hardware puts the read/write marker in the *sign* of the address word),
unique label minting, and the two composite forms this ISA lacks — an indexed
load and an indexed store.
"""

from __future__ import annotations

__all__ = ["Asm"]


class Asm:
    """Emits LM-1 assembly text with named slots, arrays and unique labels."""

    def __init__(self, first_slot: int = 1) -> None:
        self.lines: list[str] = []
        self.equs: list[tuple[str, int, str]] = []
        self._next = first_slot
        self._uid = 0
        self._names: dict[str, int] = {}

    # ── storage ──────────────────────────────────────────────────────────────
    def slot(self, name: str, note: str = "") -> str:
        return self.array(name, 1, note)

    def array(self, name: str, n: int, note: str = "") -> str:
        if name in self._names:
            raise ValueError(f"slot {name} declared twice")
        self._names[name] = self._next
        self.equs.append((name, self._next, note if n == 1 else f"{note} [{n}]"))
        self._next += n
        return name

    def const(self, name: str, value: int, note: str = "") -> str:
        """A pure assemble-time constant — no storage."""
        if name in self._names:
            raise ValueError(f"constant {name} declared twice")
        self._names[name] = value
        self.equs.append((name, value, note))
        return name

    def at(self, name: str, index: int) -> str:
        """``name + index`` as a literal address, for unrolled array access."""
        return str(self._names[name] + index)

    def addr(self, name: str) -> int:
        return self._names[name]

    @property
    def slots(self) -> int:
        """One past the highest allocated slot — the tape size the machine needs."""
        return self._next

    # ── labels and raw instructions ──────────────────────────────────────────
    def label(self, name: str) -> str:
        self.lines.append(f"{name}:")
        return name

    def new_label(self, stem: str) -> str:
        self._uid += 1
        return f"{stem}{self._uid}"

    def op(self, mnemonic: str, arg: object = None, note: str = "") -> None:
        if isinstance(arg, int) and arg < 0:
            raise ValueError(f"{mnemonic} {arg}: the ROM holds only non-negative literals")
        text = f"        {mnemonic:<5}" + ("" if arg is None else f" {arg}")
        if note:
            text = f"{text:<30}; {note}"
        self.lines.append(text)

    def comment(self, text: str = "") -> None:
        self.lines.append(f"; {text}" if text else "")

    def section(self, title: str) -> None:
        self.comment()
        self.comment("── " + title + " " + "─" * max(3, 66 - len(title)))

    # ── shorthands ───────────────────────────────────────────────────────────
    def ldi(self, v: int, note: str = "") -> None:
        """ACC = v.  Negative values become ``LDI 0`` / ``SUBI |v|``."""
        if v < 0:
            self.op("LDI", 0, note)
            self.op("SUBI", -v)
        else:
            self.op("LDI", v, note)

    def ld(self, s: str, note: str = "") -> None:
        self.op("LD", s, note)

    def st(self, s: str, note: str = "") -> None:
        self.op("ST", s, note)

    def set_slot(self, s: str, v: int, note: str = "") -> None:
        self.ldi(v, note)
        self.st(s)

    def copy(self, dst: str, src: str, note: str = "") -> None:
        self.ld(src, note)
        self.st(dst)

    def inc(self, s: str, by: int = 1, note: str = "") -> None:
        self.ld(s, note)
        self.op("ADDI" if by >= 0 else "SUBI", abs(by))
        self.st(s)

    def jmp(self, label: str, note: str = "") -> None:
        self.op("JMP", label, note)

    def brz(self, label: str, note: str = "") -> None:
        self.op("BRZ", label, note)

    def brn(self, label: str, note: str = "") -> None:
        self.op("BRN", label, note)

    # ── composite forms ──────────────────────────────────────────────────────
    def load_at(self, base: str, index: str, note: str = "") -> None:
        """ACC = ``base[index]`` — two instructions and one store read."""
        self.ld(index, note)
        self.op("ADDI", base)
        self.op("LDA")

    def store_at(self, base: str, index: str, value: str, note: str = "") -> None:
        """``base[index] = value``, both named slots."""
        self.ld(index, note)
        self.op("ADDI", base)
        self.op("MOVA", value)

    def br_lt(self, a: str, b: str, label: str, note: str = "") -> None:
        """Branch to `label` when ``a < b`` (slots)."""
        self.ld(a, note)
        self.op("SUB", b)
        self.brn(label)

    def br_eq(self, a: str, b: str, label: str, note: str = "") -> None:
        self.ld(a, note)
        self.op("SUB", b)
        self.brz(label)

    def br_eq_imm(self, a: str, v: int, label: str, note: str = "") -> None:
        self.ld(a, note)
        self.op("SUBI", v)
        self.brz(label)

    # ── output ───────────────────────────────────────────────────────────────
    def text(self, header: str) -> str:
        width = max((len(n) for n, _, _ in self.equs), default=8)
        equs = [
            f".equ {n:<{width}} {a:<5}" + (f"; {note}" if note else "") for n, a, note in self.equs
        ]
        return "\n".join([header, "", *equs, "", *self.lines, ""])
