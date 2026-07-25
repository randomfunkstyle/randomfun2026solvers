"""The LLLM ring machine: token-level program, then the grid it compiles to."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from randomfun2026solvers import lllm_ring, lllm_tables

PROBLEM = Path(__file__).resolve().parents[1] / "tasks" / "problems" / (
    "little-little-little-man.json"
)


def _cases() -> list[dict]:
    return json.loads(PROBLEM.read_text())["publicTestData"]


def _expected(case: dict) -> list[list[str]]:
    return [frame for rnd in case["rounds"] for frame in (rnd.get("frames") or [])]


# ── decode tables ─────────────────────────────────────────────────────────────
def test_hash_is_injective_over_every_non_digit_glyph() -> None:
    idx = [lllm_tables.hash_index(c) for c in lllm_tables.GLYPHS]
    assert len(set(idx)) == len(lllm_tables.GLYPHS)
    assert all(0 <= i < 16 for i in idx)


def test_magics_are_positive_64_bit_literals() -> None:
    for magic in (lllm_tables.CLASS_MAGIC, lllm_tables.COLOUR_MAGIC):
        assert 0 < magic < 1 << 63


def test_decode_matches_the_glyph_table() -> None:
    for code, want in lllm_tables.GLYPHS.items():
        assert lllm_tables.decode_ascii(code) == want
    for digit in range(10):
        assert lllm_tables.decode_ascii(48 + digit) == (digit, 8)


# ── the token program ─────────────────────────────────────────────────────────
def test_every_successor_names_a_real_block() -> None:
    for name, (_, succ) in lllm_ring.WORKER.items():
        targets = [succ] if isinstance(succ, str) else list(succ.values())
        for target in targets:
            assert target in lllm_ring.WORKER, f"{name} -> {target}"


def test_branch_blocks_end_in_a_branch_glyph() -> None:
    for name, (toks, succ) in lllm_ring.WORKER.items():
        if isinstance(succ, dict):
            assert toks[-1] in ("X", "x", "d"), (name, toks[-1])
            keys = set(succ)
            want = {"X": {"neg", "zero", "pos"}, "x": {"one", "zero"},
                    "d": {"pos", "zero"}}[toks[-1]]
            assert keys == want, (name, keys)
        else:
            assert not toks or toks[-1] not in ("X", "x", "d"), name


def test_store_and_file_capacities_cover_the_stated_constraints() -> None:
    # 4 <= W, H <= 16, so the store is at most sixteen rows of sixteen plus END.
    assert lllm_ring.store_words(16) == lllm_ring.STORE_WORDS == 257
    assert lllm_ring.store_words(4) == 65
    assert lllm_ring.FILE_WORDS >= 7


@pytest.mark.parametrize("case", _cases(), ids=lambda c: c["name"])
def test_token_program_reproduces_every_public_frame(case: dict) -> None:
    frames, _ = lllm_ring.simulate_worker(case["rounds"])
    want = _expected(case)
    assert len(frames) == len(want), f"{len(frames)} frames, expected {len(want)}"
    for i, (got, exp) in enumerate(zip(frames, want, strict=True)):
        assert got == exp, f"frame {i}"
