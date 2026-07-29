"""The four STREAM arms the CNN needs and matmul did not.

Design: docs/superpowers/specs/2026-07-29-mnist-cnn-design.md §4.2a.

The existing eight arms fill rings from the *input room* and emit to the *output
room*; a training loop needs to push CPU-computed scalars in, rotate ring B to a tap
offset, read a partial sum back at the CPU, and update weights in place without
routing 190 words through the CPU. This file pins the semantics; the grid that
implements them is pinned in the same file's slow tier once it exists.

``trie_bits`` is a property of the *unit*, not of the word: a depth-3 unit (the
default, ``matmul``'s own width) and a depth-4 unit (this machine's, with the four
new arms) are never mixed in one decode. See ``StreamUnit``'s docstring in
``store.py`` for why no single decode can serve both — this task's own
NEEDS_CONTEXT round found that widening to mod-16 corrupts real, already-shipped
``matmul`` words (``FILLA`` with an odd argument), and that a mod-8 decode can
never reach the new arms' codes at all. Hence: two widths, chosen at construction,
never one decode trying to serve both.
"""

from __future__ import annotations

from collections import deque

import pytest
from randomfun2026solvers.lm1.store import StoreError, StreamUnit


def _unit(trie_bits: int = 3) -> StreamUnit:
    return StreamUnit(read_input=lambda: 0, emit=lambda v: None, trie_bits=trie_bits)


def test_new_arms_have_distinct_codes():
    codes = StreamUnit.CODES
    for arm in ("PUSHA", "ROTB", "RDP", "UPDB"):
        assert arm in codes, f"{arm} must be a STREAM arm"
    assert len(set(codes.values())) == len(codes), "two arms cannot share a command code"
    assert max(codes.values()) < 16, "a depth-4 trie has 16 leaves"


def test_pusha_puts_a_cpu_value_on_ring_a():
    u = _unit(trie_bits=4)
    u.command(16 * 42 + StreamUnit.CODES["PUSHA"])
    assert list(u.ring_a) == [42]


def test_rotb_rotates_without_consuming():
    u = _unit(trie_bits=4)
    u.ring_b = deque([1, 2, 3, 4, 5])
    u.command(16 * 2 + StreamUnit.CODES["ROTB"])
    assert list(u.ring_b) == [3, 4, 5, 1, 2], "a rotation preserves the multiset and the order"


def test_rotb_by_full_length_is_identity():
    u = _unit(trie_bits=4)
    u.ring_b = deque(range(7))
    u.command(16 * 7 + StreamUnit.CODES["ROTB"])
    assert list(u.ring_b) == list(range(7))


def test_rdp_returns_one_partial_sum_to_the_cpu():
    u = _unit(trie_bits=4)
    u.p1 = deque([111, 222])
    assert u.command(16 * 0 + StreamUnit.CODES["RDP"]) == 111
    assert list(u.p1) == [222]


def test_updb_applies_a_rank_one_update_in_place():
    """W[j] -= (a * g[j]) >> lr, with g rotating through the accumulator ring."""
    u = _unit(trie_bits=4)
    u.ring_b = deque([1000, 2000, 3000])
    u.p1 = deque([4096, 8192, 0])  # Q12 gradients 1.0, 2.0, 0.0
    u.lr_shift = 12
    u.command(16 * 3 + StreamUnit.CODES["PUSHA"])  # scalar a = 3
    u.command(16 * 3 + StreamUnit.CODES["UPDB"])
    assert list(u.ring_b) == [1000 - 3, 2000 - 6, 3000 - 0]
    assert list(u.p2) == [4096, 8192, 0], "the gradients must circulate, not be consumed"


def test_mac_still_does_what_matmul_relies_on():
    """A regression guard: the depth-4 trie must not renumber the existing arms' behaviour."""
    u = _unit(trie_bits=4)
    u.ring_a = deque([2])
    u.ring_b = deque([10, 20, 30])
    u.p2 = deque([0, 0, 0])  # MAC reads its running sum from P2 (ZEROC's ring), not P1
    u.command(16 * 3 + StreamUnit.CODES["MAC"])
    assert list(u.ring_b) == [10, 20, 30], "MAC rotates B back to its start after a full lap"
    assert u.macs == 3


@pytest.mark.parametrize("n", [0, 1, 2, 7, 8, 35, 100])
def test_trie3_decodes_the_original_eight_arms_correctly_including_odd_args(n: int):
    """The actual matmul guard: this checks the *decode*, not just the code table.

    Comparing ``StreamUnit.CODES`` by hand only proves the labels didn't move.
    This proves that a depth-3 unit — the one ``matmul`` actually runs against —
    still recovers the right argument for every original arm, at several sizes
    including 35: the odd argument from the real ``FILLA`` word (283) that this
    task's design notes used to prove a mod-16 decode corrupts existing behaviour.
    A mod-8 decode, unchanged, must not.
    """
    codes = StreamUnit.CODES

    u = _unit()
    u.command(8 * n + codes["FILLA"])
    assert len(u.ring_a) == n

    u = _unit()
    u.command(8 * n + codes["FILLB"])
    assert len(u.ring_b) == n

    u = _unit()
    u.command(8 * n + codes["ZEROC"])
    assert list(u.p2) == [0] * n

    u = _unit()
    u.ring_b = deque(range(n))
    u.command(8 * n + codes["DRAINB"])
    assert len(u.ring_b) == 0

    u = _unit()
    u.p1 = deque(range(n))
    u.command(8 * n + codes["FWD"])
    assert list(u.p2) == list(range(n))

    emitted: list[int] = []
    u = StreamUnit(read_input=lambda: 0, emit=emitted.append)
    u.p1 = deque(range(n))
    u.command(8 * n + codes["EMIT"])
    assert emitted == list(range(n))

    u = _unit()
    u.ring_a = deque([2])
    u.ring_b = deque(range(1, n + 1))
    u.p2 = deque([0] * n)  # MAC reads its running sum from P2 (ZEROC's ring), not P1
    u.command(8 * n + codes["MAC"])
    assert list(u.ring_b) == list(range(1, n + 1)), "MAC must rotate B back after one full lap"
    assert u.macs == n

    u = _unit()
    u.command(8 * n + codes["RDIN"])
    assert u.recv() == 0, "RDIN's argument is unused; only its code needs to decode correctly"


def test_trie3_rejects_a_code_the_trie_does_not_have():
    """A depth-3 unit must refuse a code outside its own trie, not misdecode one.

    There is no *word* that exercises this through :meth:`StreamUnit.command`: a
    3-bit ``divmod`` can never produce a code >= 8 in the first place (``word % 8``
    is always < 8, for any word) — which is precisely why a depth-3 unit handed a
    depth-4 program's word can't notice: it silently decodes to some other, wrong,
    original arm instead (see ``test_pusha_puts_a_cpu_value_on_ring_a``'s 3-bit
    encoding, ``EMIT(43)`` instead of ``PUSHA(42)``, in this task's design notes).
    So this test exercises the seam one level down, at the already-decoded
    ``(code, arg)`` pair, which is where the real guard has to live.
    """
    u = _unit()
    with pytest.raises(StoreError, match="3-bit"):
        u._dispatch(StreamUnit.CODES["PUSHA"], 0)
