"""`pathfinder` on the generated CPU: a bit-parallel BFS over four 64-bit words.

The emulator tests here grade *frames*, not output words, because the program emits
no output at all — it paints the LM-75 directly. Every expected frame comes from :mod:`randomfun2026solvers.pathfinder_sim`, which is verified
against its own straightforward BFS oracle on the public cases and a random sweep,
so this file compares two independently-derived answers rather than a program with
itself.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "solvers" / "python"))

from randomfun2026solvers.lm1 import assemble_file  # noqa: E402
from randomfun2026solvers.lm1.display import frames_from_writes  # noqa: E402
from randomfun2026solvers.lm1.emulator import Emulator, Round  # noqa: E402
from randomfun2026solvers.pathfinder_sim import (  # noqa: E402
    case_stats,
    frame_rows,
    parse_case_rounds,
    solve_case,
)

ASM = REPO / "solvers" / "python" / "randomfun2026solvers" / "lm1" / "programs" / "pathfinder.asm"
PROBLEM = REPO / "tasks" / "problems" / "pathfinder.json"

#: The judge's cap for this problem, from `pathfinder.json`.
STEP_CAP = 15_000_000


def _program():
    return assemble_file(ASM)


def _cases() -> list[dict]:
    return json.loads(PROBLEM.read_text())["publicTestData"]


def _rounds(case: dict) -> list[list[int]]:
    return [[int(v) for v in r["in"]] for r in case["rounds"]]


def _run(program, rounds: list[list[int]]) -> Emulator:
    em = Emulator(program)
    em.run([Round(input=tuple(r)) for r in rounds], max_instructions=9_000_000)
    return em


@pytest.mark.parametrize("index", range(7))
def test_every_public_case_commits_exactly_the_expected_frames(index: int) -> None:
    case = _cases()[index]
    rounds = _rounds(case)
    board, rx, ry, flags = parse_case_rounds(rounds)
    want = [frame_rows(f) for f in solve_case(board, rx, ry, flags)]

    em = _run(_program(), rounds)
    got = frames_from_writes(tuple(em.display_writes), width=16, height=16)

    assert len(got) == len(want), (
        f"{case['name']!r}: committed {len(got)} frames, expected {len(want)} "
        f"(one for the setup round plus one per move)"
    )
    for i, (g, w) in enumerate(zip(got, want)):
        assert g == w, f"{case['name']!r}: frame {i} differs"


@pytest.mark.parametrize("index", range(7))
def test_the_frame_count_is_one_setup_frame_plus_one_per_move(index: int) -> None:
    """The frame contract, stated separately because getting it wrong desynchronises
    every later frame and the panel offers no way to commit silently (§4.4)."""
    case = _cases()[index]
    rounds = _rounds(case)
    board, rx, ry, flags = parse_case_rounds(rounds)
    stats = case_stats(board, rx, ry, flags)

    em = _run(_program(), rounds)
    got = frames_from_writes(tuple(em.display_writes), width=16, height=16)
    assert len(got) == 1 + stats.total_levels


def test_the_program_uses_eighteen_opcodes_and_so_pays_a_depth_five_trie() -> None:
    """Recorded rather than aspired to. The CPU paints the panel itself, and
    `DSPA`/`DSPD`/`DSPS` are three opcodes where a write-only coprocessor would be
    one `SND`; that is what pushes the count past sixteen and the trie to depth 5,
    which costs decode on *every* instruction plus ~32 lane rows (§8.0 measured the
    same step on `snake` as 158x167 against 121x136). Dropping back to sixteen is
    the single largest optimisation left, and this assertion is here to fail loudly
    when someone takes it."""
    used = {i.mnemonic for i in _program().instrs}
    assert len(used) == 18, sorted(used)
    assert {"DSPA", "DSPD", "DSPS"} <= used


def test_the_board_bitsets_never_set_bit_63() -> None:
    """The load-bearing invariant: bit 63 of word w is cell 64w — row 4w column 0,
    always a border wall — so every bitset is non-negative and the program's `DIVI`
    really is a logical shift rather than an arithmetic one."""
    from randomfun2026solvers.pathfinder_sim import SIGN_BIT, free_words

    for case in _cases():
        board, _rx, _ry, _flags = parse_case_rounds(_rounds(case))
        for w, word in enumerate(free_words(board)):
            assert not word & SIGN_BIT, f"{case['name']!r}: free word {w} is negative"


def test_the_tape_is_sized_to_the_program_not_the_board() -> None:
    """256 cells as a bitset is 40 slots, not 512 — and slot count is a *tick* cost,
    ~8 ticks per slot on every read (§4.1), so this is the difference between a
    ~400-tick read and a ~2,400-tick one."""
    from randomfun2026solvers.lm1.machine import TAPE_SIZE

    text = ASM.read_text()
    assert f"TAPE_SIZE['pathfinder'] = {TAPE_SIZE['pathfinder']}" in text
    assert TAPE_SIZE["pathfinder"] < 64


def test_the_model_agrees_with_the_contests_own_expected_frames() -> None:
    """The strongest check available, and it validates the *oracle* rather than the
    program: `pathfinder.json` ships the expected frames per round, so this compares
    our BFS and its tie-break against the contest's ground truth instead of against
    another thing we wrote. In particular it is what settles the reading of "prefer
    moving up, then right, then down, then left" as a per-step greedy rule among
    neighbours that stay on a shortest path.
    """
    for case in _cases():
        official = [f for r in case["rounds"] for f in (r.get("frames") or [])]
        assert official, f"{case['name']!r}: the problem JSON ships no frames"
        board, rx, ry, flags = parse_case_rounds(_rounds(case))
        mine = [frame_rows(f) for f in solve_case(board, rx, ry, flags)]
        assert mine == official, f"{case['name']!r}: model disagrees with the contest"
