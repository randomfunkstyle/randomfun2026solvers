"""Reference and bit-parallel models for the ``pathfinder`` problem.

Two implementations of the same thing live here, and ``tests/test_pathfinder_sim.py``
pins them against each other and against every frame of ``tasks/problems/pathfinder.json``:

* the **oracle** (:func:`solve_case`) — plain BFS from the flag plus a greedy preferred
  walk, written to be obviously correct and nothing else;
* the **bit-parallel model** (:func:`solve_case_bitset`) — the algorithm that gets compiled
  to the CPU's assembly, simulated in exact 64-bit-wrapping words so the assembly can be
  trusted before it exists.

The board is 16x16 with ``0,0`` top-left, ``x`` right, ``y`` down; cell index is
``p = y * 16 + x``; every border cell is a wall. Round 0 supplies the 256 cells
(``0`` path, ``1`` wall) plus the robot's start ``rx ry`` and commits **one** frame; every
later round supplies a flag ``fx fy`` and commits **one frame after each move**, ``k``
frames where ``k`` is the shortest-path length. Ties are broken by preferring
**up, right, down, left** — at each step, among the neighbours whose distance to the flag
is one less, the first in that order wins.

Colours: path ``0``, wall ``7``, flag ``9``, robot ``10``. The display is a *persistent*
framebuffer, so a frame is the accumulation of every pixel written so far — which is why
"the flag is not drawn on the last frame of each round" needs no special case: the robot
steps onto the flag cell and overwrites it.

Bit layout of the bit-parallel model
------------------------------------
The 256 cells are packed into **four** 64-bit words. Word ``w`` holds cells
``64w .. 64w+63``, i.e. rows ``4w .. 4w+3``, in **reversed** bit order: bit ``b`` of word
``w`` is cell ``64w + (63 - b)``. That is the order the hardware's setup loop produces for
free, since it folds the 256 row-major input values with ``acc = 2 * acc + is_path``, which
lands a word's *first* cell in bit 63 and its *last* in bit 0 (:func:`free_words_by_accumulation`).
It also avoids ever needing a ``1 << 63`` literal, which the ROM cannot encode as a
positive value.

Because each cell belongs to exactly one word, a whole BFS level expands word by word with
no interaction between words other than the explicit ``prev[w-1] << 48`` /
``prev[w+1] >> 48`` carries for the vertical neighbours.

Three facts the assembly leans on, all asserted by :func:`bfs_dirs` itself and re-checked
independently by the tests:

* bit 63 of ``free``, ``avail``, ``prev`` and each direction mask is always **clear** —
  bit 63 of word ``w`` is cell ``64w``, i.e. row ``4w`` column 0, and column 0 is a border
  wall on every row — so every one of those values is a non-negative 64-bit integer and the
  hardware's floor-division really is a logical shift;
* the cheap single-bit horizontal shifts could only lie about cells in column 0 (``Wsrc``)
  or column 15 (``Esrc``), and lying there needs ``prev`` to *contain* an edge-column cell.
  Since ``prev`` only ever holds path cells and the whole border is wall, the shifts are in
  fact **exactly** the geometric masks — see :func:`horizontal_source_masks`;
* ``avail[w] -= n`` is exactly ``avail[w] & ~n`` and ``Dd[w] += n`` is exactly
  ``Dd[w] | n``, so the hardware may use whichever of the pair is cheaper.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Callable, Iterator, Sequence
from typing import NamedTuple

__all__ = [
    "BITS",
    "CELLS",
    "DELTAS",
    "DIRECTION_NAMES",
    "FLAG",
    "HEIGHT",
    "MASK64",
    "PATH",
    "ROBOT",
    "SIGN_BIT",
    "WALL",
    "WIDTH",
    "WORDS",
    "Board",
    "CaseStats",
    "Frame",
    "Step",
    "bfs_dirs",
    "bit_of",
    "case_stats",
    "cell_of",
    "distances_from",
    "frame_rows",
    "free_words",
    "free_words_by_accumulation",
    "horizontal_source_masks",
    "is_set",
    "neighbours",
    "parse_case_rounds",
    "setup_frame",
    "shl64",
    "shortest_path",
    "shr64",
    "solve_case",
    "solve_case_bitset",
    "walk_from_dirs",
    "word_of",
    "words_to_cells",
]

WIDTH = 16
HEIGHT = 16
CELLS = WIDTH * HEIGHT
#: 64-bit words the board is packed into, and bits per word.
WORDS = 4
BITS = 64

PATH = 0
WALL = 7
FLAG = 9
ROBOT = 10

MASK64 = (1 << BITS) - 1
#: bit 63 of word ``w`` is cell ``64w`` — row ``4w`` column 0, always a border wall.
SIGN_BIT = 1 << (BITS - 1)

#: cell-index deltas for up, right, down, left — the tie-break order, most preferred first.
DELTAS = (-WIDTH, 1, WIDTH, -1)
DIRECTION_NAMES = ("up", "right", "down", "left")

#: 256 cells, row-major, ``0`` = path and ``1`` = wall.
Board = Sequence[int]
#: One committed frame as ``HEIGHT`` rows of ``WIDTH`` colour numbers, indexed ``[y][x]``.
Frame = list[list[int]]


# -- 64-bit arithmetic ----------------------------------------------------------------


def shl64(value: int, count: int) -> int:
    """``value << count`` truncated to 64 bits.

    On the real hardware this is a wrapping signed multiply by ``2**count``, so the bits
    shifted past bit 63 are gone rather than kept in a big integer.
    """
    return (value << count) & MASK64


def shr64(value: int, count: int) -> int:
    """``value >> count`` as a *logical* shift of the low 64 bits.

    On the real hardware this is a floor-division by ``2**count``, which only agrees with a
    logical shift while the value is non-negative — hence the bit-63 invariant.
    """
    return (value & MASK64) >> count


# -- cells, words and boards ----------------------------------------------------------


def cell_of(x: int, y: int) -> int:
    """The cell index of column ``x``, row ``y``."""
    return y * WIDTH + x


def xy_of(cell: int) -> tuple[int, int]:
    """The ``(x, y)`` of a cell index."""
    return cell % WIDTH, cell // WIDTH


def word_of(cell: int) -> int:
    """Which of the four words holds ``cell``."""
    return cell // BITS


def bit_of(cell: int) -> int:
    """The single-bit mask of ``cell`` inside its word — reversed order, cell ``64w`` is bit 63."""
    return 1 << (BITS - 1 - cell % BITS)


def is_set(words: Sequence[int], cell: int) -> bool:
    """Is ``cell``'s bit set in a four-word mask?"""
    return bool(words[word_of(cell)] & bit_of(cell))


def neighbours(cell: int) -> Iterator[int]:
    """``cell``'s on-board neighbours in tie-break order: up, right, down, left.

    Geometric, so a step never wraps from column 15 to column 0 of the next row. (Border
    cells are always walls, so for any *path* cell all four neighbours exist anyway; the
    guards are here so the reference implementation is correct on its own terms.)
    """
    x, y = xy_of(cell)
    if y > 0:
        yield cell - WIDTH
    if x < WIDTH - 1:
        yield cell + 1
    if y < HEIGHT - 1:
        yield cell + WIDTH
    if x > 0:
        yield cell - 1


def words_to_cells(words: Sequence[int]) -> set[int]:
    """The set of cell indices whose bit is set in a four-word mask."""
    return {cell for cell in range(CELLS) if is_set(words, cell)}


def free_words(board: Board) -> list[int]:
    """Pack ``board`` into four 64-bit words, bit set iff the cell is a path."""
    words = [0] * WORDS
    for cell in range(CELLS):
        if not board[cell]:
            words[word_of(cell)] |= bit_of(cell)
    return words


def free_words_by_accumulation(board: Board) -> list[int]:
    """:func:`free_words` the way the assembly builds it: ``acc = 2 * acc + is_path``.

    The setup round reads the 256 values in row-major order, so each group of 64 folds into
    one word with the first cell of the group ending up in bit 63 — which is exactly the
    reversed bit order :func:`bit_of` describes. The accumulator stays non-negative because
    the first cell of every group is in column 0 and therefore a wall.
    """
    words = []
    for word in range(WORDS):
        acc = 0
        for cell in range(word * BITS, (word + 1) * BITS):
            acc = 2 * acc + (1 - board[cell])
        words.append(acc)
    return words


def parse_case_rounds(rounds: Sequence[Sequence[int]]) -> tuple[list[int], int, int, list[int]]:
    """Split a case's per-round inputs into ``(board, rx, ry, flag cells)``."""
    setup = list(rounds[0])
    if len(setup) != CELLS + 2:
        raise ValueError(f"setup round has {len(setup)} values, expected {CELLS + 2}")
    board = setup[:CELLS]
    rx, ry = setup[CELLS], setup[CELLS + 1]
    flags = [cell_of(*values) for values in rounds[1:]]
    return board, rx, ry, flags


# -- the oracle: plain BFS plus a greedy preferred walk --------------------------------


def distances_from(board: Board, goal: int) -> list[int | None]:
    """Breadth-first distance from ``goal`` to every path cell, ``None`` if unreachable."""
    dist: list[int | None] = [None] * CELLS
    dist[goal] = 0
    queue = deque([goal])
    while queue:
        cell = queue.popleft()
        step = dist[cell]
        assert step is not None
        for neighbour in neighbours(cell):
            if not board[neighbour] and dist[neighbour] is None:
                dist[neighbour] = step + 1
                queue.append(neighbour)
    return dist


def shortest_path(board: Board, start: int, goal: int) -> list[int]:
    """The cells visited *after* ``start`` on the preferred shortest path to ``goal``.

    ``len(...)`` is therefore the number of moves — the ``k`` frames the round commits.
    """
    if board[start] or board[goal]:
        raise ValueError(f"{start} -> {goal} is not a path-to-path walk")
    dist = distances_from(board, goal)
    if dist[start] is None:
        raise ValueError(f"cell {goal} is not reachable from cell {start}")
    path: list[int] = []
    cell = start
    while cell != goal:
        here = dist[cell]
        assert here is not None
        for neighbour in neighbours(cell):
            if not board[neighbour] and dist[neighbour] == here - 1:
                cell = neighbour
                break
        else:  # pragma: no cover - unreachable once dist[start] is not None
            raise AssertionError(f"no descending neighbour at cell {cell}")
        path.append(cell)
    return path


def setup_frame(board: Board, robot_cell: int) -> Frame:
    """Round 0's frame: walls ``7``, paths ``0``, the robot ``10``."""
    grid = [[WALL if board[cell_of(x, y)] else PATH for x in range(WIDTH)] for y in range(HEIGHT)]
    x, y = xy_of(robot_cell)
    grid[y][x] = ROBOT
    return grid


def _paint(grid: Frame, cell: int, colour: int) -> None:
    x, y = xy_of(cell)
    grid[y][x] = colour


def _snapshot(grid: Frame) -> Frame:
    return [list(row) for row in grid]


def _run_case(
    board: Board,
    rx: int,
    ry: int,
    flags: Sequence[int],
    walk: Callable[[int, int], list[int]],
) -> list[Frame]:
    """The frame sequence of a whole case, given something that produces each round's path.

    Shared by both models so the frame bookkeeping cannot drift between them: the display
    is persistent, so one grid is carried through the case and copied at every commit.
    """
    robot = cell_of(rx, ry)
    grid = setup_frame(board, robot)
    frames = [_snapshot(grid)]
    for flag in flags:
        if flag == robot:
            raise ValueError(f"flag {flag} is the robot's own cell")
        _paint(grid, flag, FLAG)  # drawn, but no frame is committed for it
        path = walk(robot, flag)
        for step in path:
            _paint(grid, robot, PATH)
            _paint(grid, step, ROBOT)
            robot = step
            frames.append(_snapshot(grid))
        assert robot == flag
    return frames


def solve_case(board: Board, rx: int, ry: int, flags: Sequence[int]) -> list[Frame]:
    """Every frame a case commits, setup frame first — the reference answer.

    ``flags`` are cell indices, one per pathfinding round, in order.
    """
    return _run_case(board, rx, ry, flags, lambda robot, flag: shortest_path(board, robot, flag))


# -- the bit-parallel model -----------------------------------------------------------


class Step(NamedTuple):
    """One ``(level, word)`` slice of :func:`bfs_dirs`, for tests to pick apart."""

    level: int
    word: int
    prev: tuple[int, ...]
    avail_before: int
    avail_after: int
    #: source masks, in tie-break order: bit ``c`` set iff ``c``'s neighbour that way is in ``prev``
    sources: tuple[int, int, int, int]
    #: the cells actually claimed for each direction at this ``(level, word)``
    claimed: tuple[int, int, int, int]


def horizontal_source_masks(prev: Sequence[int], word: int) -> tuple[int, int]:
    """The *geometrically exact* ``(Esrc, Wsrc)`` for one word, carries across words included.

    :func:`bfs_dirs` uses the cheap ``p << 1`` / ``p >> 1`` instead. The two can only differ
    at a cell in column 15 (east) or column 0 (west), and only if ``prev`` holds the cell
    one index away — an edge-column cell, i.e. a border wall. So on any real board they are
    equal, which is what ``test_the_cheap_horizontal_shifts_are_exactly_the_geometric_masks``
    proves.
    """
    esrc = wsrc = 0
    for cell in range(word * BITS, (word + 1) * BITS):
        x, _ = xy_of(cell)
        if x < WIDTH - 1 and is_set(prev, cell + 1):
            esrc |= bit_of(cell)
        if x > 0 and is_set(prev, cell - 1):
            wsrc |= bit_of(cell)
    return esrc, wsrc


def _check_word(
    word: int,
    avail: int,
    prev: Sequence[int],
    masks: Sequence[list[int]],
    consumed: int,
) -> None:
    """The invariants the assembly depends on, at one ``(level, word)`` slice."""
    assert not avail & SIGN_BIT, f"avail[{word}] has bit 63 set"
    assert not prev[word] & SIGN_BIT, f"prev[{word}] has bit 63 set"
    for name, mask in zip(DIRECTION_NAMES, masks, strict=True):
        assert not mask[word] & SIGN_BIT, f"D{name}[{word}] has bit 63 set"
    for index, mask in enumerate(masks):
        for other in masks[index + 1 :]:
            assert not mask[word] & other[word], f"direction masks overlap in word {word}"
    union = masks[0][word] | masks[1][word] | masks[2][word] | masks[3][word]
    assert union == consumed, f"word {word}: direction masks are not exactly the consumed cells"


def bfs_dirs(
    freew: Sequence[int],
    flag_cell: int,
    robot_cell: int,
    trace: list[Step] | None = None,
    check: bool = True,
) -> tuple[list[int], list[int], list[int], list[int], int]:
    """Bit-parallel BFS *from the flag*, recording for every cell which way to step.

    Returns ``(DN, DE, DS, DW, levels)``. Each ``Dd`` is four 64-bit words: bit ``c`` of
    ``Dd`` is set iff the robot standing on cell ``c`` should move that way, i.e. iff
    ``c``'s neighbour in that direction was reached one level earlier and no
    higher-priority direction claimed ``c`` first. ``levels`` is the number of expansions
    run, which is exactly the shortest-path length from ``robot_cell`` to ``flag_cell``.

    The tie-break needs no comparisons: within a level the four directions consume the
    shared ``avail`` set in the order up, right, down, left, so the first one to reach a
    cell owns it.
    """
    avail = list(freew)
    prev = [0] * WORDS
    # avail = free minus {flag}; prev = {flag}
    avail[word_of(flag_cell)] -= bit_of(flag_cell)
    prev[word_of(flag_cell)] = bit_of(flag_cell)
    masks = [[0] * WORDS for _ in range(4)]
    dn, de, ds, dw = masks
    initial = list(avail)
    levels = 0
    robot_word, robot_bit = word_of(robot_cell), bit_of(robot_cell)

    while True:
        levels += 1
        nxt = [0] * WORDS
        for w in range(WORDS):
            p = prev[w]
            # bit c set iff the neighbour of c in that direction is in prev. Reversed bit
            # order, so "one cell later" is one bit *right*: N/S shift by 16, E/W by 1.
            nsrc = shr64(p, 16) | (shl64(prev[w - 1], 48) if w > 0 else 0)
            esrc = shl64(p, 1)
            ssrc = shl64(p, 16) | (shr64(prev[w + 1], 48) if w < WORDS - 1 else 0)
            wsrc = shr64(p, 1)

            avail_before = avail[w]
            a = avail_before
            claimed = []
            for src, mask in zip((nsrc, esrc, ssrc, wsrc), masks, strict=True):
                got = src & a
                if check:
                    # what the assembly is allowed to substitute for the -= and the +=
                    assert a - got == a & ~got, "avail -= n is not avail & ~n"
                    assert mask[w] + got == mask[w] | got, "D += n is not D | n"
                a -= got
                mask[w] += got
                claimed.append(got)
            avail[w] = a
            nxt[w] = claimed[0] | claimed[1] | claimed[2] | claimed[3]

            if check:
                _check_word(w, a, prev, masks, initial[w] & ~a)
            if trace is not None:
                trace.append(
                    Step(
                        level=levels,
                        word=w,
                        prev=tuple(prev),
                        avail_before=avail_before,
                        avail_after=a,
                        sources=(nsrc, esrc, ssrc, wsrc),
                        claimed=(claimed[0], claimed[1], claimed[2], claimed[3]),
                    )
                )

        prev = nxt
        if prev[robot_word] & robot_bit:
            break
        if not any(prev):
            raise ValueError(f"cell {flag_cell} is not reachable from cell {robot_cell}")

    return dn, de, ds, dw, levels


def walk_from_dirs(
    dn: Sequence[int],
    de: Sequence[int],
    ds: Sequence[int],
    dw: Sequence[int],
    robot_cell: int,
    flag_cell: int,
) -> list[int]:
    """Follow the direction masks from robot to flag; the cells visited after the start.

    At each cell, test bit ``cell`` of ``DN``, then ``DE``, then ``DS``, then ``DW`` and
    move by ``-16``, ``+1``, ``+16``, ``-1``. The masks are disjoint, so exactly one hits.
    """
    masks = (dn, de, ds, dw)
    path: list[int] = []
    cell = robot_cell
    while cell != flag_cell:
        word, bit = word_of(cell), bit_of(cell)
        hits = [index for index, mask in enumerate(masks) if mask[word] & bit]
        if not hits:
            raise ValueError(f"cell {cell} has no direction; the BFS never reached it")
        assert len(hits) == 1, f"cell {cell} has {len(hits)} directions"
        cell += DELTAS[hits[0]]
        path.append(cell)
        if len(path) > CELLS:  # pragma: no cover - the masks are acyclic by construction
            raise AssertionError("the direction masks contain a cycle")
    return path


def solve_case_bitset(board: Board, rx: int, ry: int, flags: Sequence[int]) -> list[Frame]:
    """:func:`solve_case`, computed with :func:`bfs_dirs` — the model the assembly mirrors."""
    freew = free_words(board)

    def walk(robot: int, flag: int) -> list[int]:
        dn, de, ds, dw, levels = bfs_dirs(freew, flag, robot)
        path = walk_from_dirs(dn, de, ds, dw, robot, flag)
        assert len(path) == levels, f"{len(path)} moves but {levels} BFS levels"
        return path

    return _run_case(board, rx, ry, flags, walk)


# -- measurement ----------------------------------------------------------------------


class CaseStats(NamedTuple):
    """What the hardware has to be sized for, per case."""

    rounds: int
    frames: int
    #: BFS levels executed over the whole case (sum over rounds)
    total_levels: int
    #: the worst single round
    max_levels: int
    #: per level, how many of the four words of ``prev`` are non-empty
    prev_words_mean: float
    prev_words_max: int
    levels_per_round: tuple[int, ...]


def case_stats(board: Board, rx: int, ry: int, flags: Sequence[int]) -> CaseStats:
    """Run a whole case through :func:`bfs_dirs` and measure it."""
    freew = free_words(board)
    robot = cell_of(rx, ry)
    per_round: list[int] = []
    occupancy: list[int] = []
    for flag in flags:
        trace: list[Step] = []
        dn, de, ds, dw, levels = bfs_dirs(freew, flag, robot, trace=trace)
        per_round.append(levels)
        for step in trace:
            if step.word == 0:
                occupancy.append(sum(1 for word in step.prev if word))
        robot = walk_from_dirs(dn, de, ds, dw, robot, flag)[-1]
    return CaseStats(
        rounds=len(flags) + 1,
        frames=1 + sum(per_round),
        total_levels=sum(per_round),
        max_levels=max(per_round, default=0),
        prev_words_mean=sum(occupancy) / len(occupancy) if occupancy else 0.0,
        prev_words_max=max(occupancy, default=0),
        levels_per_round=tuple(per_round),
    )


def frame_rows(frame: Frame) -> list[str]:
    """A frame as the judge writes it: 16 rows of 16 hex digits."""
    return ["".join(f"{colour:x}" for colour in row) for row in frame]
