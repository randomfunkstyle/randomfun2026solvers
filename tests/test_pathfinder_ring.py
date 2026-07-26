"""The pathfinder floorplan: pipe binding, the placer, and the literal hazard.

Everything here is cheap on purpose.  The expensive thing -- a finished grid
run against the seven public cases -- does not exist yet (see
``pathfinder_ring``'s docstring), so what the fast tier pins instead is the
part of the layout that *is* settled: that the binding predicate agrees with
the engine cell for cell, that the placer refuses to mis-bind, and that the
backtick rule is enforced rather than hoped for.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest
from randomfun2026solvers import pathfinder_prog as pf
from randomfun2026solvers.circuit import Circuit, Collision
from randomfun2026solvers.fast_littleman import FastLittleman
from randomfun2026solvers.pathfinder_place import (
    Placer,
    PlacerError,
    check_backticks,
    glyph_of,
)
from randomfun2026solvers.pathfinder_ring import (
    Binding,
    GridBinding,
    arrangements,
    probe_grid,
    transitions,
)

ROOT = Path(__file__).resolve().parents[1]
OX, OY = 6, 6  # where ``probe_grid`` puts the worker room's interior origin

ANCHORS = ({"G": 3, "R": 8, "F": 13, "P": 18}, {"G": 3, "R": 8, "F": 13, "I": 18})


def probe() -> tuple[GridBinding, list[str]]:
    b = GridBinding(20, 20, *ANCHORS)
    return b, probe_grid(b)


# ── the binding predicate is the engine's, not a paraphrase of it ─────────────
def test_grid_binding_matches_the_engine():
    """Every ``s``/``r`` on the probe binds where ``GridBinding`` says it does.

    This is the whole correctness argument for scheme (c): a send may sit at
    any row of its column band and a receive at any column of its row band, and
    the engine has to agree at all 400 cells, not just near the anchors.
    """
    b, rows = probe()
    eng = FastLittleman("\n".join(rows))
    out = {p.src_attach: i for i, p in enumerate(eng.pipes)}
    inn = {p.dst_attach: i for i, p in enumerate(eng.pipes)}
    want_send = {z: out[(OX + c, OY - 1)] for z, c in b.cols.items()}
    want_recv = {z: inn[(OX - 1, OY + r)] for z, r in b.rows.items()}

    checked = 0
    for y in range(b.ih):
        for x in range(b.iw):
            ch = rows[OY + y][OX + x]
            if ch not in "sr":
                continue
            checked += 1
            zone = b.send(x) if ch == "s" else b.recv(y)
            want = (want_send if ch == "s" else want_recv)[zone]
            assert eng._bindings[(OX + x, OY + y)] == want, (ch, x, y, zone)
    assert checked == b.iw * b.ih


def test_grid_binding_demands_a_strict_minimum():
    """A cell equidistant from two anchors is legal for neither.

    Ties are resolved by reading order, which means an edit at the far side of
    the grid could silently rebind the op; refusing them outright is the only
    version of this that survives a later change.
    """
    b = GridBinding(20, 20, {"G": 2, "R": 6, "F": 12, "P": 18}, {"G": 3, "R": 8, "F": 13, "I": 18})
    assert b.send(4) is None  # |4-2| == |4-6|
    assert not b.ok(4, 0, "sg") and not b.ok(4, 0, "sr")
    assert b.ok(3, 0, "sg") and b.ok(5, 0, "sr")


def test_grid_binding_zones_partition_the_room():
    b, _ = probe()
    cols, rows = b.zones()
    assert sorted(x for v in cols.values() for x in v) == list(range(b.iw))
    assert sorted(y for v in rows.values() for y in v) == list(range(b.ih))
    # the order the arrangement search picks, west to east / north to south
    assert [min(cols[z]) for z in ("G", "R", "F", "P")] == sorted(min(cols[z]) for z in cols)


def test_quadrant_binding_still_rejects_ties():
    """Scheme (b) is kept for comparison; its no-tie conditions still hold."""
    with pytest.raises(ValueError):
        Binding(20, 21, 3, 8, 4, 9)  # odd height ties north against south
    with pytest.raises(ValueError):
        Binding(20, 20, 3, 9, 4, 9)  # XW + XE even ties east against west
    b = Binding(20, 20, 3, 8, 4, 9)
    assert b.send(0, 0) == "sg" and b.send(19, 0) == "sr"
    assert b.send(0, 19) == "sf" and b.send(19, 19) == "sp"


# ── the arrangement numbers the floorplan is argued from ──────────────────────
def test_transition_counts_are_what_the_docstring_claims():
    sends, recvs = transitions()
    assert sum(sends.values()) == 52
    assert sum(recvs.values()) == 29
    assert arrangements(sends, "RFGP")[0][:2] == (66, 10)
    assert arrangements(recvs, "RFGI")[0][:2] == (38, 9)
    assert arrangements(sends, "RFGP")[0][2] == ("G", "R", "F", "P")
    assert arrangements(recvs, "RFGI")[0][2] == ("G", "R", "F", "I")


def test_a_pipes_send_and_receive_can_share_a_place():
    """The lever the whole scheme rests on: 112 of 202 transitions are free.

    ``sr`` and ``rr`` are bound by *different* partitions, so they can be legal
    at the same cell -- which is why the op-level transition count, not the
    zone-level one, is the number that decides the layout.
    """
    b, _ = probe()
    same = [
        (x, y) for y in range(b.ih) for x in range(b.iw) if b.ok(x, y, "sr") and b.ok(x, y, "rr")
    ]
    assert len(same) >= 20, "colband(R) x rowband(R) must hold a run of its own"
    for x, y in same:
        assert not b.ok(x, y, "sg") and not b.ok(x, y, "rg")


# ── numeric literals ─────────────────────────────────────────────────────────
def test_check_backticks_catches_a_vertical_pair():
    """The reference interpreter rejects this grid; so must we, cheaply."""
    rows = ["+-----+", "|@ 1  |", "| ` 5`|", "| M   |", "| `7` |", "+-----+"]
    with pytest.raises(Collision, match="column"):
        check_backticks(rows)


def test_check_backticks_accepts_the_placers_discipline():
    """Backticks confined to two columns of blanks pair harmlessly."""
    rows = ["`  12` M", "`    ` W", "`   7` N", "`    ` M", "`  64` W"]
    check_backticks(rows)


def test_the_program_needs_delimited_literals():
    lits = [t for toks, _ in pf.build().values() for t in toks if t[0] == "L" and len(t) > 2]
    assert len(lits) == 20
    assert max(len(t) - 1 for t in lits) == 3  # `256` is the widest


# ── the placer ───────────────────────────────────────────────────────────────
def _tiny_placer(iw=14, ih=14):
    b = GridBinding(iw, ih, {"G": 2, "R": 5, "F": 8, "P": 12}, {"G": 2, "R": 5, "F": 8, "I": 12})
    c = Circuit(iw, ih)
    p = Placer(c, b.ok, backtick_cols=(10, 14))
    p.escape_cap = 20
    p._put(0, 0, "@")
    p.x, p.y, p.d = 1, 0, (1, 0)
    return b, p


def test_placer_lays_a_straight_run_straight():
    """With nothing in the way a run of tokens is a line, not a scribble."""
    _, p = _tiny_placer()
    for t in "MWNM":
        p.emit(t)
    assert p.c.rows()[0].startswith("@MWNM")
    assert p.travel_cells == 0


def test_placer_detours_rather_than_mis_binding():
    """A pipe op standing where its pipe does not win is a build error, so the
    placer must move the man instead of placing it."""
    b, p = _tiny_placer()
    p.emit("sg")  # legal in column 1 already
    cell = p.emit("sf")  # colband F is columns 7..9: this has to travel
    assert b.ok(*cell, "sf")
    assert p.travel_cells > 0
    for (x, y), ch in p.c.cell.items():
        if ch in "sr":
            assert any(b.ok(x, y, t) for t in ("sr", "sf", "sg", "sp", "rr", "rf", "rg", "ri"))


def test_placer_refuses_to_overwrite():
    _, p = _tiny_placer()
    with pytest.raises(PlacerError):
        p._put(0, 0, "M")


def test_placer_keeps_literals_in_the_backtick_columns():
    _, p = _tiny_placer(20, 20)
    p.emit("L256")
    p.emit("L64")
    rows = p.c.rows()
    check_backticks(rows)
    cols = {x for y, r in enumerate(rows) for x, ch in enumerate(r) if ch == "`"}
    assert cols == {10, 14}


def test_glyph_of_maps_a_pipe_op_to_one_cell():
    """``sr`` is a two-letter *name* for one glyph; the pipe comes from where
    the glyph stands, which is the whole premise of the module."""
    assert glyph_of("sr") == "s" and glyph_of("ri") == "r"
    assert glyph_of("L7") == "7" and glyph_of("L64") == ""
    assert glyph_of("M") == "M"
    total = sum(len(glyph_of(t)) or (len(t) + 1) for toks, _ in pf.build().values() for t in toks)
    assert total == sum(pf.cells(toks) for toks, _ in pf.build().values()) == 596


# ── the slow tier: the reference engine's own router ─────────────────────────
@pytest.mark.slow
def test_reference_engine_agrees_with_the_binding():
    """``route-check.mjs`` asks the WASM oracle which pipe each glyph reached."""
    b, rows = probe()
    path = ROOT / "tasks" / "solutions" / "_pathfinder_probe.man"
    path.write_text("\n".join(rows) + "\n")
    try:
        out = subprocess.run(
            ["node", str(ROOT / "littleman" / "tools" / "route-check.mjs"), str(path)],
            capture_output=True,
            text=True,
            check=True,
            cwd=ROOT,
        ).stdout
    finally:
        path.unlink(missing_ok=True)
    assert "ERR" not in out

    ids = {
        m.group(2).replace(" ", ""): int(m.group(1))
        for m in re.finditer(r"^  (\d+): \d+ cells  (\[[-\d, ]+\]) -> ", out, re.M)
    }
    want_send = {z: ids[f"[{OX + c},{OY - 2}]"] for z, c in b.cols.items()}
    want_recv = {z: ids[f"[{OX - 3},{OY + r}]"] for z, r in b.rows.items()}
    checked = 0
    pat = r"^  \'([sr])\' at \((\d+),(\d+)\)  ->  .*?\"cells\":\[(\[[-\d,]+\])"
    for m in re.finditer(pat, out, re.M):
        ch, x, y = m.group(1), int(m.group(2)) - OX, int(m.group(3)) - OY
        zone = b.send(x) if ch == "s" else b.recv(y)
        want = (want_send if ch == "s" else want_recv)[zone]
        assert ids[m.group(4).replace(" ", "")] == want, (ch, x, y, zone)
        checked += 1
    assert checked == b.iw * b.ih


@pytest.mark.slow
def test_execution_weights_still_point_at_the_same_order():
    """Weighted by real per-case execution counts, the axis orders do not move."""
    from randomfun2026solvers.pathfinder_ring import _execution_counts

    problem = ROOT / "tasks" / "problems" / "pathfinder.json"
    assert json.loads(problem.read_text())["publicTestData"]
    sends, recvs = transitions(_execution_counts(problem))
    assert arrangements(sends, "RFGP")[0][2] == ("G", "R", "F", "P")
    assert arrangements(recvs, "RFGI")[0][2] == ("G", "R", "F", "I")
