"""Run the **real** ``lm1.machine.check_bindings`` on every candidate layout.

This is the whole point of the exercise.  ``ARCH.md`` §7.1 decides which pipe an
``s``/``r`` binds to by *relative distance to rivals*, so moving block A can
silently rebind block B's ``s`` — a place-then-route design emits machines that
are geometrically valid and functionally wrong.  So the search loop calls the
production checker, unmodified, on each candidate.

Two things had to be bridged, and both are named rather than hidden:

**The vocabulary.**  ``check_bindings`` is written from the CPU's point of view:
its ``incoming`` set is the literal ``{"rom", "in", "mem_resp", "stream_resp"}``,
so "is this pipe incoming?" is decided by *name*.  A gate's request pipe is
outgoing at the adapter and incoming at the gate, which no single global name can
express.  :func:`check_layout` therefore assigns each block a deterministic
**alias** per pipe, out of the real band vocabulary and on the correct side of
that divide, and calls the real function with those.  The algorithm under test —
direction filter, Manhattan distance, strict rejection of ties — is untouched.

**The rivals.**  ``SPEC.md`` §"Which pipe do I talk to?" measures to "the pipe
segment attached to the current room", so only pipes attached to *this* block are
rivals.  That is exactly the per-block call ``machine.py`` already makes, and it
is what makes the input room a rival for a memory ``r`` (M11c) while a pipe on
the far side of the machine is not.

:func:`audit_segments` is the stricter reading of the same sentence — distance to
the nearest cell of the attached *segment* rather than to its first cell.  It is
run alongside and disagreements are reported, because "the checker and the engine
read the rule differently" is itself a result worth having.
"""

from __future__ import annotations

import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2] / "solvers" / "python"
if str(_SRC) not in sys.path:  # pragma: no cover - import plumbing
    sys.path.insert(0, str(_SRC))

from randomfun2026solvers.lm1.machine import (  # noqa: E402
    MachineError,
    check_bindings,
)

from .geom import Layout  # noqa: E402
from .model import Route  # noqa: E402

#: Real band names on the incoming side of ``check_bindings``' hardcoded divide.
INCOMING_POOL = ("mem_resp", "rom", "in", "stream_resp")
#: ...and on the outgoing side.
OUTGOING_POOL = ("mem_req", "out", "dsp_addr", "dsp_data", "dsp_swap", "stream_cmd", "dsp")


class Unexpressible(Exception):
    """The model cannot state this problem to the real checker.

    Raised rather than worked around: a block with more incoming pipes than the
    checker has incoming band names is a limit of the production code, and
    silently inventing a name would hide it.
    """


def _aliases(layout: Layout, block: str) -> dict[str, str]:
    """Map this block's attached pipes onto real band names, by direction."""
    inc = sorted(p.name for p in layout.problem.pipes if p.dst[0] == block)
    out = sorted(p.name for p in layout.problem.pipes if p.src[0] == block)
    if len(inc) > len(INCOMING_POOL) or len(out) > len(OUTGOING_POOL):
        raise Unexpressible(
            f"block {block!r} attaches {len(inc)} incoming / {len(out)} outgoing pipes; "
            f"check_bindings knows {len(INCOMING_POOL)} / {len(OUTGOING_POOL)} band names"
        )
    return {**dict(zip(inc, INCOMING_POOL, strict=False)),
            **dict(zip(out, OUTGOING_POOL, strict=False))}


def check_layout(layout: Layout) -> None:
    """Assert every glyph in every block binds the pipe the problem says it must.

    Raises ``MachineError`` (the production exception) on a misbind.
    """
    for bname, pl in layout.placed.items():
        alias = _aliases(layout, bname)
        touches: dict[str, tuple[int, int]] = {}
        for pipe in layout.problem.pipes:
            for ref in (pipe.src, pipe.dst):
                if ref[0] == bname:
                    touches[alias[pipe.name]] = layout.touch(ref)
        glyphs: list[tuple[int, int, str, str]] = []
        for pipe in layout.problem.pipes:
            for ref in (pipe.src, pipe.dst):
                if ref[0] != bname:
                    continue
                _pl, port, off = layout.port_of(ref)
                if port.glyph:
                    for gx, gy in pl.glyphs(port, off):
                        glyphs.append((gx, gy, port.glyph, alias[pipe.name]))
        if glyphs:
            check_bindings(glyphs, touches)


def _segment(cells: tuple[tuple[int, int], ...]) -> list[tuple[int, int]]:
    """The straight run a pipe starts with — its "segment attached to the room"."""
    if len(cells) < 2:
        return list(cells)
    dx, dy = cells[1][0] - cells[0][0], cells[1][1] - cells[0][1]
    run = [cells[0]]
    for a, b in zip(cells, cells[1:], strict=False):
        if (b[0] - a[0], b[1] - a[1]) != (dx, dy):
            break
        run.append(b)
    return run


def audit_segments(layout: Layout, routes: dict[str, Route]) -> list[str]:
    """The stricter reading: nearest *cell of the attached segment*, not first cell.

    Returns a list of complaints; empty means the two readings agree.  A glyph
    whose own pipe wins on the touch cell but loses on the segment is exactly the
    kind of thing that is invisible until a program reads the wrong bank.
    """
    out: list[str] = []
    for bname, pl in layout.placed.items():
        attached: dict[str, tuple[str, list[tuple[int, int]]]] = {}
        for pipe in layout.problem.pipes:
            route = routes.get(pipe.name)
            if route is None:
                continue
            if pipe.src[0] == bname:
                attached[pipe.name] = ("out", _segment(route.legs[0].cells))
            if pipe.dst[0] == bname:
                tail = tuple(reversed(route.legs[-1].cells))
                attached[pipe.name] = ("in", _segment(tail))
        for pipe in layout.problem.pipes:
            for ref in (pipe.src, pipe.dst):
                if ref[0] != bname:
                    continue
                _pl, port, off = layout.port_of(ref)
                if not port.glyph:
                    continue
                for gx, gy in pl.glyphs(port, off):
                    want = "in" if port.glyph == "r" else "out"
                    d = {
                        name: min(abs(x - gx) + abs(y - gy) for x, y in seg)
                        for name, (role, seg) in attached.items()
                        if role == want
                    }
                    if not d:
                        continue
                    best = min(d.values())
                    if d[pipe.name] != best or sum(1 for v in d.values() if v == best) > 1:
                        out.append(
                            f"{bname}.{port.name} {port.glyph!r} at {(gx, gy)} wants "
                            f"{pipe.name!r} but segment distances are "
                            f"{sorted(d.items(), key=lambda kv: kv[1])}"
                        )
    return out


__all__ = ["MachineError", "Unexpressible", "audit_segments", "check_layout"]
