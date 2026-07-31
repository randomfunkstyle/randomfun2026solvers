"""The seek-drum (lm1.seekrom) and machine's opt-in hybrid jump acceleration."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).parents[1]
PKG = REPO / "solvers" / "python"
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

from randomfun2026solvers import optimize  # noqa: E402
from randomfun2026solvers.lm1 import machine, seekrom  # noqa: E402
from randomfun2026solvers.lm1.isa import SEEK_OF, SEEK_SEMS, TARGET_SEMS  # noqa: E402
from randomfun2026solvers.lm1.programs import load  # noqa: E402
from randomfun2026solvers.lm1.rom import token_cells  # noqa: E402
from randomfun2026solvers.lm1.seekrom import (  # noqa: E402
    SEEK_K,
    build_seek_rom,
    pack_rows_even,
    seek_target,
)


def test_pack_rows_even_keeps_word_counts_even() -> None:
    words = list(range(200, 231))
    rows = pack_rows_even([token_cells(w) for w in words], 40)
    counts = [r.count("s") for r in rows]
    assert sum(counts) == len(words)
    # every row but the last closes on an even word, which is the counted
    # discard's invariant for the seek remainder
    assert all(c % 2 == 0 for c in counts[:-1])


def test_seek_rom_maps_every_word_to_a_row_and_offset() -> None:
    words = [200 + j for j in range(24)]
    rom = build_seek_rom(words, rows=4)
    assert len(rom.word_pos) == len(words)
    assert {r for r, _ in rom.word_pos} == set(range(rom.rows_used))
    for wi in range(0, len(words), 2):
        row, off = rom.word_pos[wi]
        assert seek_target(rom, wi) == row * SEEK_K + off
        assert off % 2 == 0  # instruction starts land on even offsets


def test_wide_operands_do_not_move_the_packing() -> None:
    """Fixed-width jump literals are what make the operand fixpoint terminate."""
    words = [7] * 40
    wide = frozenset(range(1, 40, 2))
    a = build_seek_rom(words, rows=4, wide=wide, wide_digits=4)
    b = build_seek_rom(
        [w if i not in wide else 1234 for i, w in enumerate(words)],
        rows=4,
        wide=wide,
        wide_digits=4,
    )
    assert a.word_pos == b.word_pos


def test_seek_split_only_rewrites_long_jumps() -> None:
    prog = load("deadman-3d")
    split = machine.seek_split(prog)
    assert len(split.instrs) == len(prog.instrs)
    seeks = [i for i in split.instrs if i.sem in SEEK_SEMS]
    assert seeks, "deadman-3d has long jumps to seek"
    # default SEEK_OPS is JMPF alone — measured best (see SEEK_DRUM's table)
    assert {i.mnemonic for i in seeks} == {"JMPS"}
    assert all(i.sem in TARGET_SEMS for i in seeks)
    # a threshold above every distance rewrites nothing
    assert not [
        i for i in machine.seek_split(prog, threshold=10**9).instrs if i.sem in SEEK_SEMS
    ]


def test_seek_of_covers_every_classic_target_sem() -> None:
    classic = {s for s in TARGET_SEMS if s not in SEEK_SEMS}
    assert set(SEEK_OF) == classic
    assert set(SEEK_OF.values()) == SEEK_SEMS


#: The two DOOM slugs are the whole of :data:`machine.SEEK_DRUM`. Named once so a
#: third slug joining has to touch this line and read the tests below it.
_SEEKING = {"deadman-3d", "deadman-3d_hires"}


def test_registry_is_narrow_so_every_other_machine_stays_byte_identical() -> None:
    """Only the DOOM slugs seek; the seek path must be unreachable elsewhere.

    The seek drum is a per-slug opt-in precisely so that turning it on for the
    DOOM demo cannot move any other checked-in grid. Assert the *scope*, not
    emptiness — the registry stopped being empty when the re-measured drum
    landed (canonical 372x377 -> 382x382 for -18.7% on the tour), and stopped
    being one slug when ``deadman-3d_hires`` measured -36.04%.
    """
    assert machine.SEEK_DRUM == _SEEKING
    # Every other registered slug builds the classic drum: `seek=None` (the
    # registry's own answer) must agree with an explicit `seek=False`.
    for slug in sorted(machine.TAPE_SIZE):
        if slug in _SEEKING:
            continue
        assert slug not in machine.SEEK_DRUM
        assert slug not in machine.SEEK_MEM_PAD
        assert slug not in machine.SEEK_SLAB_PITCH
        assert not any(k[0] == slug for k in machine.SEEK_TIER_LAYOUT)
    assert (
        machine.build(load("brackets")).rows
        == machine.build(load("brackets"), seek=False).rows
    )


def test_hires_seek_registries_are_complete_and_hires_keyed() -> None:
    """``deadman-3d_hires``' seek build needs five registries, all keyed to it.

    Turning the drum on for a slug is not one line: ``build_for`` resolves a
    family of coupled registries differently under ``seek``, and hires needed a
    non-default value in every one of them (see :data:`machine.SEEK_DRUM`'s
    table, where each is knocked back off one at a time and re-timed).

    The point of the test is the *keying*. Every entry is on the hires slug or on
    ``("deadman-3d_hires", "taped")``, so no other machine — and in particular not
    ``deadman-3d``, whose three grids are hash-pinned — can move a byte because of
    any of it. Its own named test, deliberately: the assertion it replaces
    ("hires is not in SEEK_DRUM") lived inside two unrelated tests, and merging
    contradictory claims into a shared test is how this file got confusing before.
    """
    key = ("deadman-3d_hires", "taped")
    assert "deadman-3d_hires" in machine.SEEK_DRUM
    assert machine.SEEK_TIER_LAYOUT[key] == {"rom_rows": 119}
    assert machine.SEEK_SLAB_PITCH["deadman-3d_hires"] == 11
    assert machine.MEM_PAD_FOR[key] == 1  # re-swept floor; see MEM_PAD_FOR
    # 11, not the 13 this used to pin, and the number was never the claim. This is
    # a distance *west of* ``lane_x0``, and both values name the same column —
    # ``CX + 1``, the westernmost the room may legally take, which is the placement
    # that buys the pad. What moved is ``lane_x0``: ``TIGHT_TRIE_COLS`` prices the
    # hires trie's columns per node and takes it 14 -> 12, and the room has to walk
    # with it or the input pipe leaves the CPU's north wall (the build says so).
    #
    # So assert the *difference against the sibling slug*, which is the invariant:
    # ``deadman-3d`` is not in ``TIGHT_TRIE_COLS``, its trie is still 14 wide, and
    # its 13 is untouched — hires' value is two smaller for exactly the two columns
    # the tighter trie saved. ``lane_x0`` itself cannot be computed here because the
    # hires program is assembled from a locally owned IWAD (see ``_WAD_ONLY`` in
    # ``tests/test_lm1_opcode_slots.py``).
    assert machine.INPUT_NORTH_WEST[("deadman-3d", "taped")] == 13
    assert machine.INPUT_NORTH_WEST[key] == 13 - 2
    assert key in machine.SEEK_TAKEN_DROP_EAST
    # All five now. `SEEK_TELEPORT` used to be given up to `SQUASH_BAND` — the two
    # coexist for k <= 8 (room H is bottom-anchored, its height is `12 - k`), and a
    # fully packed band is k = 12, past which room H had nowhere to stand. The bet
    # this pin recorded was that the ticks would come back through routing; they
    # did. `TAPED_BANK_LIFT` frees five rows *under* the store without touching the
    # band, so room H stands again at k = 12 and nothing is conceded: 643x386 and
    # `store->cpu` 2 with or without it, both gating `passed`, and -1.069% on the
    # 21-round tour (185,004,449 -> 183,026,898). So the squash keeps its 12 and
    # the teleport comes back — the k <= 8 arithmetic above describes the unlifted
    # geometry and is kept only to explain why it ever had to be a choice.
    #
    # **The squash is 13 now, and it is no longer a number worth pinning.** With
    # ``STRAIGHT_TRIE`` on this tier the tour depends on ``squash_band`` and
    # ``ROM_TOUCH_DROP`` *only through their difference* — see the pin below — and
    # the squash on its own only moves the box: 13, 14, 15 and 16 all run
    # 162,827,800 to the tick on the 21-round tour, with ``store_offset`` dy
    # following ``12 - squash_band``. 13 is the one that costs no store movement,
    # because it is the value the shipped dy of -1 already levels. So what is
    # asserted is the coexistence this test was really written for.
    assert machine.SQUASH_BAND[key] >= 8, "the band is still packed, not bottom-aligned"
    assert key in machine.SEEK_TELEPORT
    assert ("deadman-3d", "taped") in machine.SEEK_TELEPORT  # unaffected
    # **The invariant is the difference, not either number.** A squash of k is a
    # negative drop of k, so what the ROM path actually sees is the *effective
    # corridor* ``rom_touch_drop - squash_band``. This used to read `drop == 5` at
    # `squash == 12` — effective -7 — which was not a choice: at full squash that
    # was the only drop §7.1 would bind, and `STRAIGHT_TRIE` does not build there
    # at all ('r' at (43, 176) must bind 'rom' but 'mem_resp' is nearer).
    #
    # Once the drop is free to move, the pair collapses to this one number, and it
    # is a cliff rather than a slope: swept squash 8..16 x drop 5..20 on the
    # 21-round tour, every pair with effective corridor <= -1 lands on the same
    # curve, -1 is its minimum, and 0 is +2.8% instantly. Pin the -1.
    assert machine.ROM_TOUCH_DROP[key] - machine.SQUASH_BAND[key] == -1

    # Nothing was written on the bare slug where a `(slug, tier)` key was meant,
    # which is the mistake that would silently move the canonical hires build.
    assert "deadman-3d_hires" not in machine.SEEK_MEM_PAD
    assert "deadman-3d_hires" not in machine.MEM_PAD
    assert "deadman-3d_hires" not in machine.SLAB_PITCH  # classic pitch: declined
    assert "deadman-3d_hires" not in machine.ROM_BUFFER  # antagonistic to seeking
    # A drum build reads SEEK_TIGHT_STRUCT_DROPS, never TIGHT_STRUCT_DROPS, so the
    # tight entries have to be asked for on the seek side or they are dead config.
    # (They used to be *refused* under the drum; see SEEK_TIGHT_STRUCT_DROPS for
    # why that guard was an untested assumption and what replaced it.)
    assert "deadman-3d_hires" not in machine.TIGHT_STRUCT_DROPS
    assert ("deadman-3d_hires", "men-v3") in machine.SEEK_TIGHT_STRUCT_DROPS
    # The taped tier has them now too, at -3.227% on the 21-round tour. It was
    # excluded for not having been measured, not for a geometric reason, and the
    # precondition ``build_cpu`` demands under the drum — ``seek_taken_drop_east``,
    # without which the tightened entry hands the man the whole band twice — has
    # been true on this tier since that lever landed. Assert the pair, since the
    # tight entries are dead config (or a real loss) without it.
    assert key in machine.SEEK_TIGHT_STRUCT_DROPS
    assert key in machine.SEEK_TAKEN_DROP_EAST

    # `deadman-3d`'s own seek registries are untouched by all of it.
    assert machine.SEEK_MEM_PAD == {"deadman-3d": 22}
    assert machine.MEM_PAD_FOR[("deadman-3d", "taped")] == 16
    assert machine.SEEK_TIER_LAYOUT[("deadman-3d", "men-v3")] == {"rom_rows": 60}
    assert machine.SEEK_TIER_LAYOUT[("deadman-3d", "taped")] == {"rom_rows": 84}


def test_the_hires_slot_map_names_jmps_and_stays_inert_without_it() -> None:
    """The 22nd lane a seek build grows has to be named, and naming it is free.

    ``_relabel_slots`` rejects a map that does not name every *used* opcode, so
    ``OPCODE_SLOTS[("deadman-3d_hires", "taped")]`` — a 21-lane DP solution over a
    classic plan — made ``build_for(..., seek=True)`` fail outright. The fix is
    the one the function documents ("one registered map has to serve
    ``seek=True`` and ``seek=False`` alike"): name ``JMPS``, and let the classic
    build filter it back out.

    Both halves are asserted here because only the pair is safe. That ``JMPS``
    is present is what makes the seek build possible; that it is *ignored*
    without a seek split is what keeps the classic build byte-identical, and
    that is checked structurally rather than by rebuilding, since the program is
    IWAD-only.
    """
    slots = machine.OPCODE_SLOTS[("deadman-3d_hires", "taped")]
    assert slots["JMPS"] == 25
    # rank-preserving: the only gap the shipped 21 leave it is JMPF(24)..SND(28)
    assert slots["JMPF"] < slots["JMPS"] < slots["SND"]
    assert len(set(slots.values())) == len(slots)

    # inert without a seek split: `_relabel_slots` drops names the build never
    # placed, so the surviving assignment is exactly the 21 the DP chose.
    classic = sorted(set(slots) - {"JMPS"}, key=lambda m: slots[m])
    placed = {m: i for i, m in enumerate(classic)}
    kept = machine._relabel_slots(placed, slots, 32)
    assert "JMPS" not in kept
    assert kept == {m: s for m, s in slots.items() if m != "JMPS"}


def test_seek_declines_when_no_jump_is_long_enough() -> None:
    # brackets is 72 image words: nothing to seek at the real threshold, and
    # saying so is better than building hardware that never fires.
    with pytest.raises(machine.MachineError, match="no jump is long enough"):
        machine.build(load("brackets"), seek=True)


@pytest.mark.slow
def test_brackets_seek_variants_pass_public_cases() -> None:
    """Both the all-seek and the mixed-slab builds run on the engine."""
    for threshold in (0, 32):
        m = machine.build(load("brackets"), seek=True, seek_threshold=threshold)
        res = optimize.verify(m.rows, "brackets")
        assert res.n_passed == len(res.cases) == 9, [
            c.detail for c in res.cases if not c.passed
        ]
        # doctrine, not a bounding box: inside the size class, roughly square
        assert max(m.width, m.height) <= 130
        assert (
            max(m.width, m.height) - min(m.width, m.height)
            <= max(m.width, m.height) // 3
        )


@pytest.mark.slow
def test_deadman_seek_variant_is_near_square() -> None:
    m = machine.build_for("deadman-3d", trim_dead=True, seek=True)
    # Same ceiling as the canonical/trim artifacts (test_deadman3d*): the seek
    # drum is the shipped build now, so it does not get a looser bar. It lands
    # exactly square at 382x382, five under the ceiling.
    assert max(m.width, m.height) <= 390
    assert (
        max(m.width, m.height) - min(m.width, m.height) <= max(m.width, m.height) // 10
    )


def test_twin_station_drum_has_no_collector_riser_or_second_heading() -> None:
    """``twin_station``'s three load-bearing claims, as cells.

    Each of these is the kind of thing that binds cleanly and renders wrong, so
    it is asserted rather than reasoned about:

    * **no collector row and no riser.**  The whole point is that the arrival
      leg stops walking the room; if either survived, the topology would be a
      relabelling and the ticks would not move.
    * **both gadgets turn north** (``a`` entered heading east on the west side,
      ``d`` entered heading west on the east side), and the two cascade columns
      cross the top connector on a ``.``.  A ``<`` there — which is what the
      one-station drum has, redundantly, since a westbound man is already
      westbound — silently merges the diverted man back into the wrap path, and
      the drum then seeks to whatever row he happens to land on.
    * **both stations are walked east.**  ``x`` turns clockwise on an odd row
      and counter-clockwise on an even one, so entry *heading* is what decides
      which feeder row each parity lands on.  Mirror one station and the two
      parities swap rows, the two long feeders then want the same row in
      opposite directions, and the layout needs two more rows — so the shared
      feeders are sound only while the two headings agree.
    """
    words = [200 + j for j in range(60)]
    plain = build_seek_rom(words, rows=6)
    twin = build_seek_rom(words, rows=6, twin_station=True)
    assert plain.rows_used == twin.rows_used
    assert plain.word_pos == twin.word_pos  # same packing, so same operands
    R = twin.rows_used
    DR = twin.width - 1 - len(seekrom.STATION) - 1

    def at(layout, x: int, y: int) -> str:
        return layout.cells.get((x, y + layout.top_pad), " ")

    # the collector row and the riser column are gone, and the room is a row
    # shorter for it
    assert twin.height == plain.height - 1
    assert at(plain, 0, R + 2) == "^" and at(plain, 0, -2) == ">"
    assert not any(x == 0 for x, _ in twin.cells)

    # gadgets turn north, and the cascades cross row 0 on a nop
    for y in range(1, R + 1, 2):
        assert (at(plain, 7, y), at(twin, 7, y)) == ("d", "a")
    for y in range(2, R + 1, 2):
        assert (at(plain, DR + 1, y), at(twin, DR + 1, y)) == ("a", "d")
    assert at(twin, 7, 0) == "." and at(twin, DR + 1, 0) == "."
    assert at(twin, 7, -2) == ">" and at(twin, DR + 1, -2) == ">"

    # two stations, both entered heading east, so both end on the same `x` sense
    for x0 in (8, DR + 2):
        run = "".join(at(twin, x0 + j, -2) for j in range(len(seekrom.STATION)))
        assert run == seekrom.STATION
    # ...and BP is halved once per parity, on the drop column the two share
    assert at(twin, 1, -2) == "]"  # west ladder's drop
    assert at(twin, DR + 6, 0) == "]"  # east ladder's drop
    assert at(plain, 1, -2) == "." and at(plain, DR + 6, 0) == "."


def test_twin_station_registry_is_keyed_to_hires_only() -> None:
    """It moves hires and nothing else — ``deadman-3d``'s grids are hash-pinned."""
    assert machine.SEEK_TWIN_STATION == {
        ("deadman-3d_hires", "men-v3"),
        ("deadman-3d_hires", "taped"),
    }
