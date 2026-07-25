"""``PathUnit`` — the emulator-side model of the ``pathfinder`` coprocessor.

``pathfinder-unit.asm`` moves the LM-75 off the CPU and onto a write-only unit, which
takes the program from 18 opcodes to 16 (a depth-4 trie instead of depth-5 — ``ARCH.md``
§8.0 prices that step). The CPU therefore no longer draws anything: it sends one word per
command, ``8 * arg + code``, and every pixel and every commit comes out of this model.

Two tiers here, both cheap:

* the **program**, on the emulator, over all 7 public cases, graded against
  :mod:`randomfun2026solvers.pathfinder_sim` — an independently derived BFS oracle, so
  this compares two answers rather than a program with itself;
* the **unit alone**, driven with hand-built command words, for the one thing frames
  cannot show: *which* commands commit. ``CELL`` and ``FLAG`` must not, ``ROBOT`` and
  ``MOVE`` must commit exactly once, because a stray commit desynchronises every later
  frame and the panel offers no way to commit silently (§4.4).

The hardware block itself is graded by ``tests/test_path_unit.py``.
"""

from __future__ import annotations

import json
import re
import sys
from functools import cache, lru_cache
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "solvers" / "python"))

from randomfun2026solvers.lm1 import assemble_file  # noqa: E402
from randomfun2026solvers.lm1.display import frames_from_writes  # noqa: E402
from randomfun2026solvers.lm1.emulator import Emulator, Round  # noqa: E402
from randomfun2026solvers.lm1.store import PathUnit, StoreError  # noqa: E402
from randomfun2026solvers.pathfinder_sim import (  # noqa: E402
    case_stats,
    frame_rows,
    parse_case_rounds,
    solve_case,
)

ASM = (
    REPO
    / "solvers"
    / "python"
    / "randomfun2026solvers"
    / "lm1"
    / "programs"
    / "pathfinder-unit.asm"
)
PROBLEM = REPO / "tasks" / "problems" / "pathfinder.json"

MAX_INSTRUCTIONS = 9_000_000

#: an arbitrary path cell and its right-hand neighbour, for the direct-drive tests
CELL_A, CELL_B = 34, 35


@lru_cache(maxsize=1)
def _program():
    return assemble_file(ASM)


def _cases() -> list[dict]:
    return json.loads(PROBLEM.read_text(encoding="utf-8"))["publicTestData"]


def _rounds(case: dict) -> tuple[tuple[int, ...], ...]:
    return tuple(tuple(int(v) for v in r["in"]) for r in case["rounds"])


@cache
def _run(rounds: tuple[tuple[int, ...], ...]) -> Emulator:
    """One emulator run per case, cached: the dearest public case is ~90 frames."""
    em = Emulator(_program())
    em.run([Round(input=r) for r in rounds], max_instructions=MAX_INSTRUCTIONS)
    return em


def _frames(rounds: tuple[tuple[int, ...], ...]) -> list[list[str]]:
    return frames_from_writes(tuple(_run(rounds).display_writes), width=16, height=16)


def _drive(*words: int) -> tuple[PathUnit, list[tuple[int, int]]]:
    """A bare unit plus the port writes it produced, with no panel in the way."""
    writes: list[tuple[int, int]] = []
    unit = PathUnit(lambda port, value: writes.append((port, value)))
    for word in words:
        unit.send(word)
    return unit, writes


def _word(name: str, arg: int) -> int:
    return 8 * arg + PathUnit.CODES[name]


# ── the program, on the emulator ─────────────────────────────────────────────
def test_the_program_hands_the_panel_to_the_unit_and_keeps_sixteen_opcodes() -> None:
    """The whole point of the unit: no ``DSPA``/``DSPD``/``DSPS`` lanes on the CPU, and
    a decode trie of depth 4 rather than 5 (``ARCH.md`` §8.0)."""
    program = _program()
    used = {i.mnemonic for i in program.instrs}
    assert program.unit == "path"
    assert len(used) == 16, sorted(used)
    assert not used & {"DSPA", "DSPD", "DSPS"}, sorted(used & {"DSPA", "DSPD", "DSPS"})


def test_the_emulator_binds_the_path_unit_for_a_program_that_declares_it() -> None:
    em = Emulator(_program())
    assert isinstance(em.stream, PathUnit)


@pytest.mark.parametrize("case", _cases(), ids=lambda c: c["name"])
def test_every_public_case_commits_exactly_the_frames_the_oracle_expects(case: dict) -> None:
    """Pixel for pixel, against ``pathfinder_sim``'s plain BFS plus preferred walk.

    ``frames_from_writes`` returns rows of hex digits and ``solve_case`` returns rows of
    ints, so the oracle's side goes through ``frame_rows`` — comparing the two shapes
    directly fails on every frame for no reason at all.
    """
    rounds = _rounds(case)
    board, rx, ry, flags = parse_case_rounds([list(r) for r in rounds])
    want = [frame_rows(frame) for frame in solve_case(board, rx, ry, flags)]

    got = _frames(rounds)
    assert not _run(rounds).output, "a display problem must emit no program output"
    assert len(got) == len(want), (
        f"committed {len(got)} frames, expected {len(want)} "
        "(one for the setup round plus one per move)"
    )
    for index, (frame, expect) in enumerate(zip(got, want, strict=True)):
        assert frame == expect, f"frame {index} differs\n" + "\n".join(
            f"  row {y}: got {g} want {w}"
            for y, (g, w) in enumerate(zip(frame, expect, strict=True))
            if g != w
        )


@pytest.mark.parametrize("case", _cases(), ids=lambda c: c["name"])
def test_the_frame_count_is_one_setup_frame_plus_one_per_move(case: dict) -> None:
    """The frame contract, stated on its own because the engine withholds the next
    round's input until the current round's frames arrive: a miscount desynchronises
    every later frame *and* deadlocks the machine."""
    rounds = _rounds(case)
    board, rx, ry, flags = parse_case_rounds([list(r) for r in rounds])
    stats = case_stats(board, rx, ry, flags)

    assert len(_frames(rounds)) == 1 + stats.total_levels


# ── the unit alone ───────────────────────────────────────────────────────────
def test_the_command_codes_are_the_ones_the_program_assembles() -> None:
    """``CODES`` and the ``.equ C_*`` lines are two copies of the decode trie's geometry.
    Pinned here so moving a leaf in one has to move it in the other."""
    text = ASM.read_text(encoding="utf-8")
    declared = {
        name: int(value)
        for name, value in re.findall(r"^\s*\.equ\s+C_(\w+)\s+(\d+)\s*$", text, re.MULTILINE)
    }
    assert declared == PathUnit.CODES
    assert declared == {"CELL": 0, "ROBOT": 1, "FLAG": 2, "MOVE": 3}
    assert _program().equs["C_MOVE"] == PathUnit.CODES["MOVE"]


def test_the_unit_answers_nothing_so_a_program_with_rcv_cannot_bind() -> None:
    """§7.1 makes an incoming pipe a rival for every ``r`` in the CPU, the jump slab's
    ROM read included, so a replying unit cannot be placed on a machine with jumps —
    and this program is nothing but jumps. ``recv`` raising is that rule, enforced."""
    unit, _writes = _drive()
    with pytest.raises(StoreError, match="answers nothing"):
        unit.recv()


def test_a_board_cell_is_one_colour_write_at_the_panels_own_cursor() -> None:
    """No ADDR and no commit: the DATA port advances the cursor itself, so the setup
    round's 256 cells are 256 bare colour writes in row-major order."""
    unit, writes = _drive(_word("CELL", 1), _word("CELL", 0))
    assert writes == [(PathUnit.DATA, PathUnit.WALL), (PathUnit.DATA, PathUnit.PATH)]
    assert unit.cells == 2
    assert unit.frames == 0
    assert unit.robot is None


def test_the_flag_is_painted_without_committing_a_frame_of_its_own() -> None:
    """The panel is persistent, so the flag drawn at the top of a round survives every
    later commit until the robot steps onto it and overwrites it (§4.4)."""
    unit, writes = _drive(_word("ROBOT", CELL_A), _word("FLAG", CELL_B))
    assert writes[3:] == [(PathUnit.ADDR, CELL_B), (PathUnit.DATA, PathUnit.FLAG)]
    assert unit.frames == 1, "FLAG committed a frame"
    assert unit.robot == CELL_A, "FLAG moved the robot"


def test_the_robot_lands_and_commits_the_setup_rounds_one_frame() -> None:
    unit, writes = _drive(_word("ROBOT", CELL_A))
    assert writes == [
        (PathUnit.ADDR, CELL_A),
        (PathUnit.DATA, PathUnit.ROBOT),
        (PathUnit.SWAP, 1),
    ]
    assert unit.frames == 1
    assert unit.robot == CELL_A


def test_a_move_erases_the_remembered_cell_redraws_and_commits_one_frame() -> None:
    """The unit's only state is the robot's cell, which is what makes ``MOVE`` carry one
    cell index instead of two — the CPU never has to remember where the robot was."""
    unit, writes = _drive(_word("ROBOT", CELL_A), _word("MOVE", CELL_B))
    assert writes[3:] == [
        (PathUnit.ADDR, CELL_A),
        (PathUnit.DATA, PathUnit.PATH),
        (PathUnit.ADDR, CELL_B),
        (PathUnit.DATA, PathUnit.ROBOT),
        (PathUnit.SWAP, 1),
    ]
    assert unit.frames == 2
    assert unit.robot == CELL_B


def test_the_commit_is_always_one_never_zero_so_the_framebuffer_persists() -> None:
    """Writing 0 to SWAP clears ``next`` and resets the cursor, which would throw the
    board away and make every frame a full 256-pixel repaint."""
    _unit, writes = _drive(_word("ROBOT", CELL_A), _word("MOVE", CELL_B))
    commits = [value for port, value in writes if port == PathUnit.SWAP]
    assert commits == [1, 1]


def test_only_the_robot_and_move_commands_commit_and_they_commit_once_each() -> None:
    """One table, so a stray commit anywhere in the arm set fails here rather than as a
    frame-count mismatch 90 frames into a case."""
    per_command = {}
    for name, arg in (("CELL", 1), ("FLAG", CELL_B), ("ROBOT", CELL_B), ("MOVE", CELL_B)):
        unit, writes = _drive(_word("ROBOT", CELL_A))  # a robot to move, and one frame
        before = len([1 for port, _ in writes if port == PathUnit.SWAP])
        unit.send(_word(name, arg))
        commits = len([1 for port, _ in writes if port == PathUnit.SWAP]) - before
        per_command[name] = commits
        assert commits == unit.frames - 1
    assert per_command == {"CELL": 0, "FLAG": 0, "ROBOT": 1, "MOVE": 1}


def test_the_unit_rejects_a_cell_value_that_is_not_a_path_or_a_wall() -> None:
    """The board is binary on the wire — the colours 0 and 7 are the unit's business,
    not the CPU's — so anything else is a decode bug and not a colour."""
    unit, _writes = _drive()
    with pytest.raises(StoreError, match="CELL takes 0"):
        unit.send(_word("CELL", 2))


def test_a_move_before_any_robot_is_refused_rather_than_painting_nowhere() -> None:
    unit, _writes = _drive()
    with pytest.raises(StoreError, match="MOVE before any ROBOT"):
        unit.send(_word("MOVE", CELL_B))
