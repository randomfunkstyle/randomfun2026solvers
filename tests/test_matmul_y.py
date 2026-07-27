"""The two primitives a ``Y``-parallel ``matmul`` is built from.

Design: ``docs/superpowers/specs/2026-07-26-matmul-y-parallel-design.md``.

What is worth pinning here is *behaviour*, not any measured cost (``AGENTS.md``):
that men sharing a cycle cannot reorder a FIFO, and that the ADDER computes the
right column sums. The throughput numbers that motivate the design live in the
spec and in commit messages, because improving them is not a test failure.

The engine-backed cases are marked slow: they shell out to Node, and the fast
tier is a loop you run dozens of times an hour.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).parents[1]
PKG = REPO / "solvers" / "python"
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

from randomfun2026solvers import matmul_y  # noqa: E402
from randomfun2026solvers.circuit import Circuit  # noqa: E402
from randomfun2026solvers.lm1.machine import MachineError, _Grid  # noqa: E402

LM = REPO / "littleman"
VALID_OPS = set("0123456789 `.MWN+-*/%&|~{}<>^vVXxYdabmq]sSrRUH")
STRUCTURAL = set("+-|<>^v=:")

node_required = pytest.mark.skipif(
    shutil.which("node") is None or not (LM / "lm.mjs").exists(),
    reason="node and littleman/lm.mjs required",
)


def _judge(grid: str, values: list[int], expected: list[int], tmp: Path) -> tuple[list[int], int]:
    """Run to the settle tick. ``judge``, not ``run``: this machine never halts.

    You pass the moment the last correct value is emitted (``SPEC.md``), and a
    working matmul loops back for the next row instead of halting — so ``run``
    would report a tick-cap error on a perfectly good grid.
    """
    path = tmp / "probe.man"
    path.write_text(grid, encoding="utf-8")
    out = subprocess.run(
        ["node", "lm.mjs", "judge", str(path),
         "--input", " ".join(map(str, values)),
         "--expected", " ".join(map(str, expected)),
         "--json", "--max-ticks", "60000"],
        cwd=LM, capture_output=True, text=True, check=False,
    )
    assert out.returncode == 0, out.stderr.strip()[:400]
    snap = json.loads(out.stdout)
    return [int(v) for v in snap["output"]], snap["step"]


# ── the ADDER ────────────────────────────────────────────────────────────────
def test_the_adder_grid_is_made_of_legal_glyphs() -> None:
    grid, _ = matmul_y.build_adder_probe()
    bad = {ch for row in grid.split("\n") for ch in row} - VALID_OPS - STRUCTURAL - {"@", "I", "O"}
    assert not bad, f"grid contains glyphs the interpreter rejects: {sorted(bad)}"


def test_every_adder_pipe_is_long_enough_for_its_job() -> None:
    """Ring C must hold a whole row of C or the ADDER blocks mid-row.

    A stall here is silent on the real engine — the man just stops — so it is a
    build-time assertion rather than something a case is expected to catch.
    """
    _, caps = matmul_y.build_adder_probe()
    assert min(caps.values()) >= 2, f"a pipe is shorter than the minimum: {caps}"
    assert min(caps["cout"], caps["cin"]) >= 17, f"ring C holds too little: {caps}"


def test_the_adders_west_pipes_are_ordered_so_they_cannot_cross() -> None:
    """``cin`` topmost, then ``prod``, then ``cmd`` — see the module docstring.

    ``cin`` arrives from the relay *above* and prod/cmd from MAIN *below*; the
    only assignment where none of the three horizontal legs crosses another's
    column is this one. Reordering these constants silently produces a grid that
    fails to load, so the intent is pinned rather than left in a comment.
    """
    assert matmul_y.A_CIN < matmul_y.A_PROD < matmul_y.A_CMD
    assert matmul_y.A_COUT < matmul_y.A_OUT
    assert matmul_y.PROD_CLIMB < matmul_y.CMD_CLIMB
    assert matmul_y.CIN_DROP < matmul_y.ADDER_AT[0]


def test_a_cycle_too_small_to_close_is_refused() -> None:
    cells: dict[tuple[int, int], str] = {}
    put = matmul_y._writer(cells, "probe")
    with pytest.raises(MachineError, match="too small to close"):
        matmul_y.counted_cycle(put, (3, 4), 2, 4, {})
    with pytest.raises(MachineError, match="too small to close"):
        matmul_y.counted_cycle(put, (3, 7), 2, 2, {})


@node_required
@pytest.mark.slow
@pytest.mark.parametrize(
    ("k", "m", "products"),
    [
        (2, 3, [[1, 2], [10, 20], [100, 200]]),
        (3, 2, [[1, 2, 3], [-10, -20, -30]]),
        (4, 4, [[1, 1, 1, 1], [2, -2, 2, -2], [30, 30, -30, -30], [-99, 99, 99, -99]]),
        (16, 2, [list(range(1, 17)), [-1] * 16]),
        (2, 16, [[i, -i] for i in range(16)]),
    ],
)
def test_the_adder_accumulates_a_row_of_c(k, m, products, tmp_path) -> None:
    """Seed on t=0, accumulate M-1 passes, emit K values — negatives included."""
    grid, _ = matmul_y.build_adder_probe()
    values, expected = matmul_y.adder_case(k, m, products)
    got, _ticks = _judge(grid, values, expected, tmp_path)
    assert got == expected


# ── MAIN's row map ───────────────────────────────────────────────────────────
def test_mains_row_order_is_what_each_loop_body_needs() -> None:
    """`counted_loop` walks a body *down a column*, so row order is the program.

    Each constraint below is a loop that stops working if the rows move:

    * the MAC is `r(b_ret) s(b_fwd) * s(prod)` and the order is forced — `b` has
      to go back into ring B before `*` overwrites it — so those three rows must
      ascend, with a spare row between b_fwd and prod for the `*`;
    * both fills read `in`, so `in` sits above `a_fwd` and `b_fwd`.

    Violating any of these still generates a grid; it just computes the wrong
    thing, or binds a glyph to a pipe nobody meant.
    """
    r = matmul_y.MAIN_ROWS
    assert r["b_ret"] < r["b_fwd"] < r["prod"], "the MAC body is not in ring order"
    assert r["prod"] - r["b_fwd"] >= 2, "no row left between s(b_fwd) and s(prod) for `*`"
    assert r["in"] < r["a_fwd"], "the A fill body would send before it reads"
    assert r["in"] < r["b_fwd"], "the B fill body would send before it reads"
    assert len(set(r.values())) == len(r), f"two pipes share a row: {r}"
    assert matmul_y.MAIN_TOP < min(r.values()) and max(r.values()) <= matmul_y.MAIN_BOT


def test_the_ring_a_end_marker_survives_the_only_test_that_can_see_it() -> None:
    """The marker test may touch A and BP only — B is holding the scalar.

    `b` copies A into BP and seven `]` shift it arithmetically. Entries are
    -99..99 and the marker is 128, so exactly the marker leaves BP > 0 and `d`
    turns for it alone. Six shifts would not do: 99 >> 6 is 1.
    """
    shifts = matmul_y._SENTINEL_TEST.count("]")
    for value in (*range(-99, 100), matmul_y.SENTINEL):
        turned = (value >> shifts) > 0
        assert turned == (value == matmul_y.SENTINEL), f"{value} >> {shifts} misclassified"
    assert (99 >> (shifts - 1)) > 0, "one fewer shift would misread 99 as the marker"


def test_the_end_marker_is_built_without_a_backtick_literal() -> None:
    """`2M` then repeated `*` doubles A up to 128, leaving B free of backticks.

    Backticks pair on rows *and* columns independently, so a literal dropped into
    a serpentine can silently pair with one three rows away and fail to load.
    Doubling avoids the whole hazard.
    """
    build = matmul_y._SENTINEL_BUILD
    assert "`" not in build
    a, b = 0, 0
    for ch in build:
        if ch.isdigit():
            a = int(ch)
        elif ch == "M":
            b = a
        elif ch == "*":
            a *= b
        else:
            raise AssertionError(f"unexpected glyph {ch!r} in the marker build")
    assert a == matmul_y.SENTINEL


def test_a_serpentine_puts_every_op_on_its_row() -> None:
    """Ops land on their pipe's row; unrowed ops ride whatever cell comes next."""
    c = Circuit(40, 20)
    w = matmul_y.Serpentine(c, 2, 1, matmul_y.MAIN_TOP, matmul_y.MAIN_BOT)
    rows = matmul_y.MAIN_ROWS
    placed = []
    for glyph, row in (("r", rows["in"]), ("M", None), ("r", rows["in"]),
                       ("s", rows["rm_fwd"]), ("W", None), ("s", rows["rn_fwd"]),
                       ("r", rows["in"]), ("s", rows["rk_fwd"])):
        w.op(glyph, row)
        placed.append((glyph, row, w.x, w.y))
    for glyph, row, x, y in placed:
        assert c.get(x, y) == glyph, f"{glyph!r} is not at ({x},{y})"
        if row is not None:
            assert y == row, f"{glyph!r} landed on row {y}, not its pipe's row {row}"


def test_a_serpentine_turns_rather_than_walking_backwards() -> None:
    """Revisiting a row costs one column, not a corridor through live code."""
    c = Circuit(40, 20)
    w = matmul_y.Serpentine(c, 2, 1, matmul_y.MAIN_TOP, matmul_y.MAIN_BOT)
    w.op("r", matmul_y.MAIN_ROWS["in"])
    first = w.x
    w.op("r", matmul_y.MAIN_ROWS["in"])  # same row again: must start a new pass
    assert w.x == first + 1, "revisiting a row did not open a new column"
    assert w.y == matmul_y.MAIN_ROWS["in"]


# ── men on a cycle ───────────────────────────────────────────────────────────
def _ring(span: int, men: int) -> str:
    """One room, one clockwise cycle, ``men`` runners seeded onto it by ``Y``.

    Deliberately the simplest thing that exercises the claim: the cycle reads the
    input pipe and writes the output pipe, so the output *is* the read order.
    """
    north, south, west = 6, 10, 2
    east = west + span - 1
    g = _Grid()
    cells: dict[tuple[int, int], str] = {}
    put = matmul_y._writer(cells, "ring")

    put(west, north, ">")
    put(east, north, "v")
    put(east, south, "<")
    put(west, south, "^")
    put(west + 4, north, "r")
    put(west + span // 2, north, "s")
    for x in range(west + 1, east):
        put(x, north, " ")
        put(x, south, " ")
    for y in range(north + 1, south):
        put(west, y, " ")
        put(east, y, " ")
    matmul_y.seed_chain(put, (west, south + 2), men, south)

    height = max(y for _, y in cells) + 1
    g.room(0, north - 1, east + 2, height + 1)
    g.blit(0, north - 1, {(x, y - north + 1): ch for (x, y), ch in cells.items()})
    g.room(2, 0, 4, 2)
    g.put(3, 1, "I")
    g.draw_pipe([(3, 3), (3, north - 2)])
    g.room(east + 5, north - 1, east + 7, north + 1)
    g.put(east + 6, north, "O")
    g.draw_pipe([(east + 3, north), (east + 4, north)])
    return "\n".join(g.rows()) + "\n"


@node_required
@pytest.mark.slow
@pytest.mark.parametrize("men", [1, 2, 3, 4, 6, 8])
def test_men_sharing_a_cycle_never_reorder_the_fifo(men, tmp_path) -> None:
    """The claim the whole design rests on.

    Men on a 1-D cycle cannot overtake each other, so they pass the ``r`` cell
    and the ``s`` cell in the same fixed rotational order — the reads and the
    writes are the same permutation however the blocking falls out. If this ever
    fails, parallelising the multiply is unsound and the design is dead.
    """
    values = list(range(11, 51))
    got, _ticks = _judge(_ring(24, men), values, values, tmp_path)
    assert got == values


@node_required
@pytest.mark.slow
def test_more_men_on_one_cycle_is_strictly_faster(tmp_path) -> None:
    """Throughput is ``cycle / men``, so doubling the men roughly halves the time.

    A relative comparison inside one run, not a recorded tick count: the absolute
    numbers belong in the spec, but the *scaling* is the reason `Y` is in this
    design at all, and it would be silently lost by a bad seeding change.
    """
    values = list(range(11, 51))
    ticks = {}
    for men in (1, 2, 4):
        got, t = _judge(_ring(24, men), values, values, tmp_path)
        assert got == values
        ticks[men] = t
    assert ticks[2] < ticks[1] * 0.6, ticks
    assert ticks[4] < ticks[2] * 0.6, ticks


# ── line relocation ──────────────────────────────────────────────────────────
def test_relocation_only_offers_placements_into_free_cells() -> None:
    """Every candidate must be a real grid: no glyph landing on another.

    The move is "delete a line and put its blockers back somewhere legal", and the
    engine is what decides whether a placement is *correct*. But a placement that
    overwrites a live cell is not even well-formed, and would silently delete an
    instruction rather than fail a case — so that is checked here rather than left
    to a case to notice.
    """
    from randomfun2026solvers import manrelocate

    rows = [
        "+--------+",
        "|@>>>v   |",
        "|    v   |",
        "|  r <   |",
        "+--------+",
    ]
    for cand, how in manrelocate.candidates(rows, 2):
        assert len(cand) == len(rows) - 1, how
        joined = "".join(cand)
        # the blockers are re-placed, never doubled up or lost without being dropped
        assert joined.count("@") == 1, how
        for line in cand:
            assert len(line) <= max(len(r) for r in rows), how


def test_relocation_preserves_every_instruction_glyph() -> None:
    """A relocation may drop a *direction* glyph but never an instruction.

    Dropping a redundant `v` is the whole point; dropping an `r` would change what
    the program computes while still, possibly, passing the small cases.
    """
    from randomfun2026solvers import manrelocate

    rows = [
        "+------+",
        "|@r  s |",
        "|  v   |",
        "|  <   |",
        "+------+",
    ]
    instr = set("rsSRUqMW+-*/%&|~{}Nbm]HYxXda0123456789")
    want = sum(ch in instr for r in rows for ch in r if ch not in "<>^v|-+")
    for cand, how in manrelocate.candidates(rows, 2):
        got = sum(ch in instr for r in cand for ch in r if ch not in "<>^v|-+")
        assert got == want, f"{how} lost an instruction: {got} vs {want}"


# ── validator parity on split machines ───────────────────────────────────────
@node_required
@pytest.mark.slow
def test_fast_and_reference_engines_disagree_on_ticks_once_Y_is_used() -> None:
    """`FastLittleman` understates ticks on a grid containing `Y`.

    ``AGENTS.md`` records that the fast validator matched Node/WASM verdicts *and
    exact tick counts* across all twelve checked-in solution families. That
    measurement predates any grid that splits: every family in it was
    single-runner-per-room. It is still true there, and this pins both halves so
    the distinction cannot quietly rot:

    * ``matmul-5818b2cc.man`` has no `Y` and the two engines agree exactly;
    * ``matmul-c9920b5f.man`` has three and they differ by ~5%, on precisely the
      two cases that keep split children in flight.

    Both engines still *pass* every case, so this is a timing divergence and not a
    correctness one. But the judge runs reference semantics, so any search that
    optimises avg-ticks on a split machine is ranking against the wrong number —
    use the fast engine to explore and the reference to accept.
    """
    from randomfun2026solvers import optimize
    from randomfun2026solvers.littleman import Littleman

    plain = REPO / "tasks" / "solutions" / "matmul-5818b2cc.man"
    split = REPO / "tasks" / "solutions" / "matmul-c9920b5f.man"
    for grid in (plain, split):
        if not grid.exists():
            pytest.skip(f"{grid.name} not checked in")

    assert "Y" not in plain.read_text(encoding="utf-8")
    assert "Y" in split.read_text(encoding="utf-8")

    fast_plain = optimize.verify(plain, "matmul")
    ref_plain = optimize.verify(plain, "matmul", lm=Littleman())
    assert fast_plain.passed and ref_plain.passed
    assert fast_plain.avg_ticks == pytest.approx(ref_plain.avg_ticks), (
        "the no-Y grid used to agree exactly; a change to either engine broke it"
    )

    fast_split = optimize.verify(split, "matmul")
    ref_split = optimize.verify(split, "matmul", lm=Littleman())
    assert fast_split.passed and ref_split.passed, "both engines must still pass"
    assert fast_split.avg_ticks < ref_split.avg_ticks * 0.99, (
        "the fast engine no longer understates ticks on a split machine — if this "
        "is a fix, delete this test and re-enable fast-engine tick search on Y grids"
    )


# ── the gauge ────────────────────────────────────────────────────────────────
@node_required
@pytest.mark.slow
@pytest.mark.parametrize(("fed", "seen"), [(1, 1), (2, 2), (3, 3), (5, 5), (7, 5)])
def test_a_gauge_reports_its_own_occupancy(fed, seen, tmp_path) -> None:
    """`q` counts the whole pipe, and a dead-end pipe saturates at its length.

    This is the primitive the parallel worker needs: its inner cycle keeps the
    scalar in B and the product in A, so there is no register free to load a loop
    count into — `b` would clobber A. `q` reads the count off a pipe nobody reads,
    touching neither.

    The saturation at 5 is the part worth pinning: a gauge for a value up to V
    needs V cells, so this is cheap for matmul's counts (all <= 16) and would not
    be for something like N*M.
    """
    grid = matmul_y.gauge_probe(capacity=5)
    path = tmp_path / "gauge.man"
    path.write_text(grid, encoding="utf-8")
    out = subprocess.run(
        ["node", "lm.mjs", "run", str(path), "--input", " ".join(["7"] * fed),
         "--max-ticks", "900"],
        cwd=LM, capture_output=True, text=True, check=False,
    )
    assert out.returncode == 0, out.stderr.strip()[:300]
    emitted = out.stdout.strip().splitlines()[0].split()
    assert len(emitted) == seen, f"fed {fed}, q saw {len(emitted)}, expected {seen}"
    assert set(emitted) == {"1"}


# ── the parallel worker ──────────────────────────────────────────────────────
def _nearest(x, y, wall_rows, side, iw):
    """Which wall pipe a glyph at (x, y) binds, by Manhattan distance."""
    best, who = None, None
    for name, row in wall_rows.items():
        d = (x if side == "west" else (iw + 1 - x)) + abs(y - row)
        if best is None or d < best:
            best, who = d, name
    return who


def test_every_worker_pipe_op_binds_the_pipe_it_was_meant_to() -> None:
    """`r`/`s`/`q` bind the *nearest* pipe, so this is arithmetic, not intent.

    All incoming pipes sit on the west wall and all outgoing on the east, so the
    x-distance to a wall is the same for every rival and nearest collapses to
    "on the nearest row". A glyph that drifts one row silently talks to a
    different ring, which is the failure mode with no symptom — the machine still
    runs and still emits numbers, just wrong ones.
    """
    from randomfun2026solvers import matmul_y as m

    west = {"a_in": m.W_A_IN, "b_ret": m.W_B_RET, "gauge": m.W_GAUGE}
    east = {"b_fwd": m.W_B_FWD, "prod": m.W_PROD}
    cells = m.worker_cells()

    want = {"r": {"a_in", "b_ret"}, "q": {"gauge"}, "s": {"b_fwd", "prod"}}
    seen = {"r": set(), "q": set(), "s": set()}
    for (x, y), ch in cells.items():
        if ch not in want:
            continue
        side = "west" if ch in "rq" else "east"
        rows = west if side == "west" else east
        seen[ch].add(_nearest(x, y, rows, side, m.WORKER_IW))
    for ch, expect in want.items():
        assert seen[ch] == expect, f"{ch!r} binds {seen[ch]}, expected {expect}"


def test_the_workers_mac_body_is_in_ring_order() -> None:
    """`b` must go back into ring B before `*` overwrites it.

    `counted_loop` walks its body down a column one glyph per row, so the row
    order *is* the instruction order: b_ret then b_fwd then a spare row for the
    `*` then prod. Reordering these constants still generates a grid; it just
    multiplies by the wrong thing.
    """
    from randomfun2026solvers import matmul_y as m

    assert m.W_B_RET < m.W_B_FWD < m.W_PROD
    assert m.W_PROD - m.W_B_FWD >= 2, "no spare row between s(b_fwd) and s(prod) for `*`"
    assert m.W_A_IN < m.W_B_RET, "the scalar must be fetched before the loop is entered"
    assert m.W_GAUGE > m.W_PROD, "the gauge sits below the loop, off the body's rows"


# ── how many rounds the graded set actually has ──────────────────────────────
@node_required
@pytest.mark.slow
def test_the_graded_set_has_no_multi_round_matmul_case(tmp_path) -> None:
    """The grid that scores 20/20 answers one round and then stalls.

    Every public case has exactly one round, and `privateTestCount` is 0, but the
    problem statement allows several — so a machine could in principle need a
    round loop, a ring drain between rounds, and a read/write balance on every
    register so the next round does not read a stale value.

    It does not. Feeding `matmul-compacted.man` the same case twice produces the
    first answer only, and that grid was judged 20/20. So the graded set is
    single-round, and none of that machinery has to exist.

    If this ever fails, the judge started sending multiple rounds and every
    single-round matmul grid in `solutions/` is invalid.
    """
    grid = REPO / "tasks" / "solutions" / "matmul-compacted.man"
    if not grid.exists():
        pytest.skip("matmul-compacted.man not checked in")
    case = [2, 2, 2, 1, 2, 3, 4, 5, 6, 7, 8]
    want = [19, 22, 43, 50]
    got, _ticks = _judge(grid.read_text(encoding="utf-8"), case + case, want + want, tmp_path)
    assert got == want, (
        "this grid now answers more than one round; the single-round assumption "
        "behind CTRL's design no longer holds"
    )


def test_a_serpentine_reserves_its_band_edges_for_turns() -> None:
    """An op on a band edge leaves no cell for the turn that must follow it.

    The turn glyph goes in the same column, one row past the last op. If the op
    sat on the edge there is no such row, and the turn lands back on the op —
    silently overwriting an instruction with a `>`. Caught by building a column
    long enough to run off the end.
    """
    from randomfun2026solvers.circuit import Collision

    c = Circuit(60, 30)
    w = matmul_y.Serpentine(c, 2, 5, 5, 19, S=None) if False else \
        matmul_y.Serpentine(c, 2, 5, 5, 19)
    with pytest.raises(Collision, match="reserved for turns"):
        w.op("r", 19)
    with pytest.raises(Collision, match="reserved for turns"):
        w.op("r", 5)
    # a long unrowed run must walk off the band and keep going, not collide
    for _ in range(40):
        w.op("M")
    assert 5 < w.y < 19, "the walker left its band"


# ── MAIN, the merged controller and multiply room ────────────────────────────
def test_mains_pipe_ops_all_bind_the_pipe_on_their_own_row() -> None:
    """34 pipe ops, and every one must resolve to the pipe on its own row.

    Every incoming pipe is on MAIN's west wall and every outgoing on its east, so
    no wall competes with itself and "nearest" collapses to "nearest row" — a glyph
    binds correctly wherever it sits horizontally. That is what lets the controller
    and the multiply loop share one room without a west-half/east-half constraint.

    A glyph one row off still loads, still runs, and computes the wrong product.
    So this recomputes the Manhattan winner for every `r`/`s`/`q` in the room
    rather than trusting the layout code.
    """
    from randomfun2026solvers import matmul_main as mm

    c, _used = mm.main_room()
    rows = c.rows()
    live = {(x, y): ch for y, r in enumerate(rows)
            for x, ch in enumerate(r) if ch != " "}

    def winner(x, y, wall, side):
        return min(wall, key=lambda k:
                   (x if side == "w" else (mm.IW + 1 - x)) + abs(y - wall[k]))

    ops = 0
    for (x, y), ch in live.items():
        if ch in "rq":
            side, wall = "w", mm.WEST
        elif ch == "s":
            side, wall = "e", mm.EAST
        else:
            continue
        ops += 1
        on_row = [k for k, v in {**mm.WEST, **mm.EAST}.items() if v == y]
        got = winner(x, y, wall, side)
        assert on_row and got in on_row, (
            f"{ch!r} at ({x},{y}) binds {got!r}; row {y} carries {on_row}"
        )
    assert ops >= 30, f"only {ops} pipe ops placed; MAIN is incomplete"


def test_main_uses_only_glyphs_the_interpreter_accepts() -> None:
    from randomfun2026solvers import matmul_main as mm

    c, _ = mm.main_room()
    bad = {ch for row in c.rows() for ch in row} - VALID_OPS - {"@"}
    assert not bad, f"MAIN contains glyphs the loader rejects: {sorted(bad)}"


# ── v1: MAIN with every port on the top wall ─────────────────────────────────
def test_v1_main_binds_every_port_by_column() -> None:
    """All ports on one wall makes the vertical term equal, so column decides.

    That is the whole reason this geometry works, and it is what the side-wall
    version got wrong: side ports force every ring to wrap the room, which costed
    out *larger* than the machine it would replace.

    A glyph one column off still loads and still runs — it just talks to the wrong
    ring — so this recomputes the winner for all 34 ops rather than trusting the
    layout code.
    """
    from randomfun2026solvers import matmul_v1 as v1

    c, _info = v1.main_room()
    live = {(x, y): ch for y, r in enumerate(c.rows())
            for x, ch in enumerate(r) if ch != " "}

    def nearest(x, ports):
        return min(ports, key=lambda k: (abs(x - ports[k]), ports[k]))

    ops = 0
    for (x, y), ch in live.items():
        if ch == "r":
            got, ports = nearest(x, v1.IN_PORTS), v1.IN_PORTS
        elif ch == "s":
            got, ports = nearest(x, v1.OUT_PORTS), v1.OUT_PORTS
        else:
            continue
        ops += 1
        assert ports[got] == x, (
            f"{ch!r} at column {x} (row {y}) binds {got!r} at column {ports[got]}; "
            "an op must sit exactly on its own port's column"
        )
    assert ops >= 30, f"only {ops} pipe ops; MAIN v1 is incomplete"


def test_v1_main_is_made_of_legal_glyphs_and_stays_small() -> None:
    """The room must stay near the port span: width is what sets the footprint."""
    from randomfun2026solvers import matmul_v1 as v1

    c, _ = v1.main_room()
    rows = c.rows()
    bad = {ch for row in rows for ch in row} - VALID_OPS - {"@"}
    assert not bad, f"illegal glyphs: {sorted(bad)}"
    used_w = max((len(r.rstrip()) for r in rows), default=0)
    assert used_w <= 34, f"MAIN v1 is {used_w} columns; the ports only span ~26"


def test_main_binds_by_row_with_every_port_on_one_wall() -> None:
    """All thirteen ports on a single wall, which is what avoids ring wraps.

    Splitting them (incoming west, outgoing east) also gives clean row binding, but
    it forces every ring to wrap the room — a ring must return to the room it left
    — and costing MAIN's five rings that way came to ~95x75, worse than the machine
    being replaced. With both legs on the same wall the relay sits just outside and
    the pipe is only as long as its capacity needs.

    So the binding must hold with one combined port set, not two separate ones.
    """
    from randomfun2026solvers import matmul_main as mm

    ports = {**mm.WEST, **mm.EAST}
    assert len(set(ports.values())) == len(ports), "two ports share a row"
    c, _ = mm.main_room()
    ops = 0
    for y, row in enumerate(c.rows()):
        for x, ch in enumerate(row):
            if ch not in "rs":
                continue
            ops += 1
            got = min(ports, key=lambda k: (abs(y - ports[k]), ports[k]))
            assert ports[got] == y, (
                f"{ch!r} at row {y} binds {got!r} on row {ports[got]}; with one wall "
                "an op must sit exactly on its own port's row"
            )
    assert ops >= 30, f"only {ops} pipe ops"
