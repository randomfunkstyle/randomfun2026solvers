"""The dispatch loop's vertical cost as a function of the trie's *root row*.

The band cannot be compacted (`band_pitch_probe.py`: 22 leaves + 21 `x` nodes =
43 rows, and every node needs its own row), so M17 §3's three terms cannot be
halved together. But they are not independent of each other either, and writing
the loop out shows which knob is still live.

Per instruction, with the collector at ``C``, the fetch/root row at ``F`` and the
lane at row ``r``:

    trie descent  |F - r| + zigzag      drop  C - 1 - r      riser  C - F

For a lane **below** the root the first two telescope: ``(r - F) + (C - 1 - r) +
(C - F) = 2C - 2F - 1``, independent of ``r``. For a lane **above** it they add:
``(F - r) + (C - 1 - r) + (C - F) = 2C - 2r - 1``. So

    cost(r) = 2*C - 2*min(r, F) - 1 + zigzag(r)

— which is the registry note on :data:`machine.LANE_ORDER` ("a row above the
fetch row costs 2 ticks per row of height while every row below it costs a
constant") derived rather than asserted, and it says the root wants to be as far
**south** as it can get: every row of root travel converts one lane's 2-per-row
term into the constant and lowers the constant for all the lanes already below.

The root's row is not free geometry — it is an in-order position, so it is set by
how many of the trie's 22 used slots fall in the west half ``[0, 16)``:
``F = band_y0 + 2u - 1`` for ``u`` leaves above. ``u`` is chosen by
:data:`machine.OPCODE_SLOTS`, and a **rank-preserving** re-assignment (the i-th
smallest slot keeps going to the i-th lane) leaves every row, drop column and
lane micro-program untouched — exactly the property that registry documents.

So this probe sweeps ``u``, rebuilds the real :func:`machine._uneven_trie` for
each, walks every opcode through it, and prices the loop against the execution
profile. It changes nothing; it reports a derivative.
"""

from __future__ import annotations

import itertools

from randomfun2026solvers.lm1 import machine

K = 5
SLUG, TIER = "deadman-3d", "taped"

#: Frame-weighted execution profile — the same counts `trie_probe.py` uses, which
#: reproduce the profile's trie line to the tick.
EXEC = dict(
    LD=41622, ST=26102, ADD=15116, BRN=13355, BRZ=11205, SUB=10103, DIV=8673,
    MODI=7961, LDA=7235, LDI=4905, JMPF=4782, SUBI=4539, DIVI=4490, MULI=3800,
    MUL=2580, ADDI=2433, SND=1742, JMPS=1212, INCM=1201, MOVA=960, IN=889, NEG=248,
)
TOTAL = sum(EXEC.values())

DIRS = {"E": (1, 0), "W": (-1, 0), "N": (0, -1), "S": (0, 1)}
CW = {"E": "S", "S": "W", "W": "N", "N": "E"}
CCW = {"E": "N", "N": "W", "W": "S", "S": "E"}


def bitrev(v: int, k: int = K) -> int:
    return int(format(v, f"0{k}b")[::-1], 2)


def walk(cells, bp, entry_row, lane_x0, limit=400):
    """Replay the decode from the fetch cell. Returns (ticks, leaf_row)."""
    x, y, d, t = 5, entry_row, "E", 0
    while t < limit:
        g = cells.get((x, y), " ")
        if g == "x":
            d = CW[d] if (bp & 1) else CCW[d]
        elif g == "]":
            bp >>= 1
        elif g == ">":
            d = "E"
        elif g == "<":
            d = "W"
        elif g == "^":
            d = "N"
        elif g in "vV":
            d = "S"
        elif g not in ". ":
            raise AssertionError(f"decode hit {g!r} at {(x, y)}")
        dx, dy = DIRS[d]
        x, y, t = x + dx, y + dy, t + 1
        if x >= lane_x0:
            return t, y
    raise AssertionError("decode did not reach a lane")


def price(slots: dict[str, int], y0: int = 1, collector_off: int = 44) -> dict[str, object]:
    """Exec-weighted trie / drop / riser for one slot assignment.

    ``collector_off`` is ``collector - y0``; the band is 43 rows whatever the
    trie's shape, so the collector does not move and this is a constant.
    """
    lane_x0 = 4 + 2 * K
    order = sorted(slots.values())
    rank = {s: i for i, s in enumerate(order)}
    slot_rows = {s: y0 + 2 * rank[s] for s in order}
    entry, cells = machine._uneven_trie(K, slot_rows, lane_x0)
    collector = y0 + collector_off - 1

    trie = drop = riser = direct = 0
    per: dict[str, tuple[int, int]] = {}
    for m, s in slots.items():
        t, row = walk(cells, bitrev(s), entry, lane_x0)
        assert row == slot_rows[s], (m, row, slot_rows[s])
        e = EXEC[m]
        per[m] = (t, row)
        trie += e * t
        drop += e * (collector - 1 - row)
        riser += e * (collector - entry)
        direct += e * abs(entry - row)
    horiz = (lane_x0 - 5) * TOTAL
    return dict(
        root_row=entry,
        leaves_above=sum(1 for s in order if s < 16),
        trie=trie,
        trie_vertical=trie - horiz,
        zigzag=(trie - horiz) - direct,
        drop=drop,
        riser=riser,
        total=trie + drop + riser,
        per=per,
    )


def shipped() -> dict[str, int]:
    return dict(machine.OPCODE_SLOTS[(SLUG, TIER)])


def relabel(u: int) -> dict[str, int] | None:
    """A rank-preserving assignment with ``u`` of the 22 slots in ``[0, 16)``.

    Rank order — and therefore every lane row — is the shipped one; only the slot
    *values* move, which is all ``number = bitrev(slot)`` and the trie's shape
    depend on. Slots are packed to the *east* end of each half so the two
    subtrees stay as shallow as the counts allow.
    """
    n = len(shipped())
    if not 1 <= u <= 16 or not 1 <= n - u <= 16:
        return None
    low = list(range(16 - u, 16))
    high = list(range(16, 16 + (n - u)))
    lanes = [m for m, _ in sorted(shipped().items(), key=lambda kv: kv[1])]
    return dict(zip(lanes, low + high))


def static_hist() -> dict[str, int]:
    """Static opcode counts — what the drum's cells are charged against.

    Same construction as ``scratch/rom-opt/slots.py``, which is the DP that chose
    the shipped map; reusing it keeps the two objectives commensurable.
    """
    import collections

    from randomfun2026solvers.lm1 import programs

    prog = machine.seek_split(
        programs.load(SLUG), threshold=machine.SEEK_THRESHOLD, ops=machine.SEEK_OPS
    )
    return dict(collections.Counter(i.mnemonic for i in prog.instrs))


HIST = None


def opcode_cells(slots: dict[str, int]) -> int:
    """Drum cells the opcode field costs: ``Ns`` = 2 under ten, ```NN`s`` = 5 over."""
    global HIST
    if HIST is None:
        HIST = static_hist()
    return sum(HIST.get(m, 0) * (2 if bitrev(s) < 10 else 5) for m, s in slots.items())


#: Tour ticks per unit of each objective, calibrated on measured runs (see the
#: commit message): the dispatch derivative is counted over a ~60.3M-tick profile
#: window against a 609.9M-tick tour, and :data:`machine.OPCODE_SLOTS`' own
#: docstring prices 2,373 opcode cells at 0.077% of the tour.
TICKS_PER_DISPATCH = 609_871_597 / 60_330_000
TICKS_PER_CELL = 0.00077 * 609_871_597 / 2_373


def joint(slots: dict[str, int]) -> float:
    """Estimated tour ticks relative to an arbitrary origin."""
    return (
        price(slots)["total"] * TICKS_PER_DISPATCH + opcode_cells(slots) * TICKS_PER_CELL
    )


def search_joint(seed: int = 0, iters: int = 40_000):
    """Hill-climb the slot subset against dispatch **and** drum cells together.

    The shipped map is the drum DP's exact optimum and scores 6,557 cells; the
    dispatch-only optimum throws most of that away. Pricing both in tour ticks is
    what makes the two comparable, and the answer is not either extreme.
    """
    import random

    rng = random.Random(seed)
    lanes = [m for m, _ in sorted(shipped().items(), key=lambda kv: kv[1])]
    cur = sorted(shipped().values())

    def score(sl):
        try:
            return joint(dict(zip(lanes, sl)))
        except AssertionError:
            return float("inf")

    cur_s = score(cur)
    best, best_s = list(cur), cur_s
    for _ in range(iters):
        cand = list(cur)
        free = [s for s in range(32) if s not in cand]
        cand[rng.randrange(len(cand))] = rng.choice(free)
        cand.sort()
        s = score(cand)
        if s <= cur_s:
            cur, cur_s = cand, s
            if s < best_s:
                best, best_s = cand, s
        elif rng.random() < 0.02:
            cur, cur_s = cand, s
    return dict(zip(lanes, best))


def search_u(u: int, seed: int = 0, iters: int = 30_000):
    """Best dispatch total among maps with exactly ``u`` slots in ``[0, 16)``.

    ``u`` fixes the root row (``F = y0 + 2u - 1``) and so fixes the riser; the
    climb then only re-shapes the two subtrees. Sweeping ``u`` traces the trade
    the shipped map never priced: riser against the drum's one-digit opcodes,
    which are what force ``u = 11`` (``LD`` at slot 16 is the boundary).
    """
    import random

    rng = random.Random(seed)
    lanes = [m for m, _ in sorted(shipped().items(), key=lambda kv: kv[1])]
    n = len(lanes)
    if not (1 <= u <= 16 and 1 <= n - u <= 16):
        return None, None

    def score(sl):
        try:
            r = price(dict(zip(lanes, sl)))
        except AssertionError:
            return float("inf"), None
        return r["total"], r

    cur = sorted(rng.sample(range(16), u)) + sorted(rng.sample(range(16, 32), n - u))
    cur_s, _ = score(cur)
    best, best_s, best_r = list(cur), cur_s, score(cur)[1]
    for _ in range(iters):
        cand = list(cur)
        half = rng.random() < u / n
        lo, hi = (0, 16) if half else (16, 32)
        j = rng.randrange(0, u) if half else rng.randrange(u, n)
        free = [s for s in range(lo, hi) if s not in cand]
        if not free:
            continue
        cand[j] = rng.choice(free)
        cand[:u], cand[u:] = sorted(cand[:u]), sorted(cand[u:])
        s, r = score(cand)
        if s <= cur_s:
            cur, cur_s = cand, s
            if s < best_s:
                best, best_s, best_r = cand, s, r
        elif rng.random() < 0.02:
            cur, cur_s = cand, s
    return dict(zip(lanes, best)), best_r


def one_digit_kept(slots: dict[str, int]) -> int:
    return sum(1 for s in slots.values() if s in ONE_DIGIT)


def search(seed: int = 0, iters: int = 60_000) -> tuple[dict[str, int], dict[str, object]]:
    """Hill-climb the 22-of-32 slot subset against trie + riser.

    ``drop`` is rank-determined and so is the same for every candidate; the live
    terms are the trie walk and ``collector - root_row``. Moves swap one used slot
    for one unused slot, which re-shapes the tree *and* can move the root.
    """
    import random

    rng = random.Random(seed)
    lanes = [m for m, _ in sorted(shipped().items(), key=lambda kv: kv[1])]
    cur = sorted(shipped().values())

    def score(sl: list[int]) -> tuple[float, dict[str, object] | None]:
        try:
            r = price(dict(zip(lanes, sl)))
        except AssertionError:
            return float("inf"), None
        return r["total"], r

    best_s, best_r = score(cur)
    best = list(cur)
    cur_s = best_s
    for _ in range(iters):
        cand = list(cur)
        free = [s for s in range(32) if s not in cand]
        cand[rng.randrange(len(cand))] = rng.choice(free)
        cand.sort()
        s, r = score(cand)
        if s <= cur_s:
            cur, cur_s = cand, s
            if s < best_s:
                best, best_s, best_r = cand, s, r
        elif rng.random() < 0.02:
            cur, cur_s = cand, s
    return dict(zip(lanes, best)), best_r


#: The ten slots whose bit-reverse is below ten — the ones the drum charges
#: ``Ns`` = 2 cells for instead of ```NN`s`` = 5 (see :data:`machine.OPCODE_SLOTS`).
ONE_DIGIT = (0, 2, 4, 8, 12, 16, 18, 20, 24, 28)


def drum_neutral() -> list[dict[str, int]]:
    """Every rank-preserving map that leaves all ten one-digit opcodes in place.

    The shipped assignment already holds all ten of :data:`ONE_DIGIT`, so pinning
    those and letting the other twelve lanes slide inside the gaps between them
    keeps **every opcode in its digit class** — same cell count, same drum lap,
    same width. Rank order forces each free lane into the open interval between
    its pinned neighbours, so the space is small and can be enumerated whole.
    """
    ship = shipped()
    lanes = [m for m, _ in sorted(ship.items(), key=lambda kv: kv[1])]
    pinned = {m: s for m, s in ship.items() if s in ONE_DIGIT}
    assert len(pinned) == len(ONE_DIGIT), sorted(pinned.values())

    # Free lanes group into runs between consecutive pinned slots.
    runs: list[tuple[list[str], range]] = []
    prev = -1
    run: list[str] = []
    for m in lanes:
        if m in pinned:
            if run:
                runs.append((run, range(prev + 1, pinned[m])))
            run, prev = [], pinned[m]
        else:
            run.append(m)
    if run:
        runs.append((run, range(prev + 1, 32)))

    out = []
    for combo in itertools.product(*(itertools.combinations(r, len(ms)) for ms, r in runs)):
        cand = dict(pinned)
        for (ms, _), picks in zip(runs, combo):
            cand.update(zip(ms, picks))
        out.append(cand)
    return out


if __name__ == "__main__":
    base = price(shipped())
    print(f"shipped: root row {base['root_row'] + 99} (interior {base['root_row']}), "
          f"{base['leaves_above']} leaves above")
    print(f"  trie {base['trie']:>10,}  (vertical {base['trie_vertical']:,}, "
          f"zigzag {base['zigzag']:,})")
    print(f"  drop {base['drop']:>10,}   riser {base['riser']:,}")
    print(f"  TOTAL{base['total']:>11,}")
    print()
    print(f"{'u':>3} {'root':>5} {'trie':>11} {'zigzag':>10} {'drop':>11} "
          f"{'riser':>11} {'total':>12} {'vs shipped':>12} {'%run':>7}")
    for u in range(6, 17):
        s = relabel(u)
        if s is None:
            continue
        try:
            r = price(s)
        except AssertionError as exc:
            print(f"{u:3d}  decode broke: {exc}")
            continue
        d = r["total"] - base["total"]
        print(f"{u:3d} {r['root_row'] + 99:5d} {r['trie']:11,} {r['zigzag']:10,} "
              f"{r['drop']:11,} {r['riser']:11,} {r['total']:12,} {d:+12,} "
              f"{100 * d / 60_398_000:+7.2f}")

    cands = drum_neutral()
    scored = sorted(((price(c)["total"], i) for i, c in enumerate(cands)))
    print(f"\ndrum-neutral maps (all ten one-digit opcodes kept): {len(cands)} of them")
    for tot, i in scored[:3]:
        r = price(cands[i])
        d = tot - base["total"]
        print(f"  root {r['root_row'] + 99} trie {r['trie']:,} riser {r['riser']:,} "
              f"total {tot:,} {d:+,}")
        print(f"    slots = {dict(sorted(cands[i].items(), key=lambda kv: kv[1]))}")
    print(f"  worst {scored[-1][0]:,}; shipped ranks "
          f"{[i for i, (_, j) in enumerate(scored) if cands[j] == shipped()]} of {len(cands)}")

    print("\nbest map per root row (u = slots in [0,16); riser = 44 - 2u):")
    print(f"{'u':>3} {'root':>5} {'trie':>11} {'riser':>11} {'total':>12} "
          f"{'vs shipped':>12} {'1-digit':>8}")
    for u in range(9, 17):
        slots, r = min(
            (x for x in (search_u(u, s) for s in range(3)) if x[1]),
            key=lambda x: x[1]["total"],
            default=(None, None),
        )
        if r is None:
            continue
        d = r["total"] - base["total"]
        print(f"{u:3d} {r['root_row'] + 99:5d} {r['trie']:11,} {r['riser']:11,} "
              f"{r['total']:12,} {d:+12,} {one_digit_kept(slots):5d}/10")
        print(f"    {dict(sorted(slots.items(), key=lambda kv: kv[1]))}")

    print("\nhill-climb over the 22-of-32 slot subset (rank order frozen):")
    for seed in range(4):
        slots, r = search(seed)
        d = r["total"] - base["total"]
        print(f"  seed {seed}: root {r['root_row'] + 99} u={r['leaves_above']} "
              f"trie {r['trie']:,} riser {r['riser']:,} total {r['total']:,} "
              f"{d:+,} ({100 * d / 609_871_597:+.3f}% of the tour)")
        print(f"    slots = {dict(sorted(slots.items(), key=lambda kv: kv[1]))}")
