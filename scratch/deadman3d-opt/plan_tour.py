"""Plan a walk-around-and-shoot command stream for deadman-3d.

Navigates on the open-cell grid (a move is 2 cells along the heading, so both
the half-way and destination cells must be open), turns 22.5 degrees a frame,
and fires only when a monster is actually painted at the crosshair column.
"""
import heapq
import sys

sys.path.insert(0, "solvers/python")
from randomfun2026solvers import deadman3d as d3  # noqa: E402

CARD = {0: (2, 0), 4: (0, 2), 8: (-2, 0), 12: (0, -2)}


def open_at(cx, cy):
    return 0 <= cx < d3.MAP_SIZE and 0 <= cy < d3.MAP_SIZE and d3.map_cell(cx, cy) == 0


def neighbours(cell):
    cx, cy = cell
    for h, (dx, dy) in CARD.items():
        mid = (cx + dx // 2, cy + dy // 2)
        dst = (cx + dx, cy + dy)
        if open_at(*mid) and open_at(*dst):
            yield h, dst


def bfs(start, goal_pred):
    """Cheapest route to the first cell satisfying goal_pred.

    Uniform-cost, not breadth-first: a nukage cell costs 30 steps to enter, so
    the patrol only wades into slime when the slime IS the goal. Plain BFS
    routed the hunts straight through the moat and drained the bar to zero
    before the second monster.
    """
    seen = {}
    heap = [(0, start, None)]
    while heap:
        cost, cell, via = heapq.heappop(heap)
        if cell in seen:
            continue
        seen[cell] = via
        if goal_pred(cell):
            path = []
            while seen[cell] is not None:
                h, parent = seen[cell]
                path.append((h, cell))
                cell = parent
            return path[::-1]
        for h, nxt in neighbours(cell):
            if nxt not in seen:
                step = 1 + 30 * d3.nukage_cell(*nxt)
                heapq.heappush(heap, (cost + step, nxt, (h, cell)))
    return None


def turn_chords(cur, want):
    """Frames of 'a'/'d' to rotate cur -> want the short way."""
    diff = (want - cur) % 16
    if diff == 0:
        return [], cur
    if diff <= 8:
        return ["a"] * diff, want          # A turns +1 (counter-clockwise)
    return ["d"] * (16 - diff), want       # D turns -1


def painted_at_crosshair(state):
    """True if a monster sprite survives the wall test at column 32."""
    real = d3._paint_monsters
    cap = {}

    def spy(cols, zbuf, *args):
        base = [c[:] for c in cols]
        real(cols, zbuf, *args)
        cap["hit"] = any(cols[32][y] != base[32][y] for y in range(len(cols[32])))

    d3._paint_monsters = spy
    try:
        d3.render(state)
    finally:
        d3._paint_monsters = real
    return cap.get("hit", False)


def aim_heading(state, target):
    """Best of the 16 headings for putting `target` under the crosshair."""
    import math
    cx, cy = target
    ang = math.degrees(math.atan2(cy * 1024 + 512 - state.posY,
                                  cx * 1024 + 512 - state.posX)) % 360
    return int(round(ang / 22.5)) % 16


SOAK_AT = int(sys.argv[2]) if len(sys.argv) > 2 else 2
SOAK_BEATS = int(sys.argv[3]) if len(sys.argv) > 3 else 5


def main():
    state = d3.SPAWN
    chords: list[str] = []

    def emit(ch):
        nonlocal state
        chords.append(ch)
        state = d3.step(state, d3.keys(ch))

    def cell():
        return d3.div(state.posX, 1024), d3.div(state.posY, 1024)

    # A tour of the level: five monster hunts, chosen nearest-first from the
    # start hall so the route reads as one continuous patrol.
    def soak(beats):
        """Wade into the nearest slime and stand in it: -5 HP a frame, the bar
        drains, the face drops through its health bands."""
        path = bfs(cell(), lambda c: d3.nukage_cell(*c) == 1)
        if path is None:
            return False
        for h, _dst in path:
            for t in turn_chords(state.heading, h)[0]:
                emit(t)
            emit("w")
        for _ in range(beats):
            emit(".")
        return True

    alive = [m for m in d3.MONSTERS]
    hunted = []
    log = []
    for hunt in range(6):
        # Two visits to the nukage, so the walk shows healthy -> hurt ->
        # bloodied rather than a full bar for 105 frames.
        if hunt == SOAK_AT:
            log.append(("soak", cell(), soak(SOAK_BEATS)))
        here = cell()
        # Nearest remaining monster by grid distance.
        alive.sort(key=lambda m: abs(m[0] - here[0]) + abs(m[1] - here[1]))
        target = None
        for mon in alive:
            path = bfs(here, lambda c, m=mon: abs(c[0] - m[0]) + abs(c[1] - m[1]) <= 6)
            if path is not None:
                target, tpath = mon, path
                break
        if target is None:
            break
        alive.remove(target)
        hunted.append(target)
        for h, _dst in tpath:
            turns, _ = turn_chords(state.heading, h)
            for t in turns:
                emit(t)
            emit("w")
        # Look for a heading that actually puts something under the crosshair:
        # try where we stand, then a couple of steps closer if the view is
        # blocked. The aim guess only orders the search.
        guess = aim_heading(state, (target[0], target[1]))
        order = sorted(range(16), key=lambda h: min((h - guess) % 16, (guess - h) % 16))
        shot = False
        for attempt in range(3):
            found = next(
                (h for h in order
                 if painted_at_crosshair(d3.State(state.posX, state.posY, h))),
                None)
            if found is not None:
                for t in turn_chords(state.heading, found)[0]:
                    emit(t)
                emit(" ")      # fire
                emit(" ")      # and again
                shot = True
                break
            # Blocked or out of range: close in and look again.
            closer = bfs(cell(), lambda c, m=target:
                         abs(c[0] - m[0]) + abs(c[1] - m[1]) <= 4 - attempt)
            if not closer:
                break
            for h, _dst in closer:
                for t in turn_chords(state.heading, h)[0]:
                    emit(t)
                emit("w")
        log.append((target, cell(), shot))
        emit(".")              # a held beat to see the result

    print("hunts (monster, ended at, shot on target):")
    for row in log:
        print("  ", row)
    print("frames:", len(chords))
    words = [d3.keys(ch) for ch in chords]
    fires = [i for i, w in enumerate(words) if d3.fire_bit(w)]
    print("fire frames:", fires)
    out = " ".join(str(w) for w in d3.input_words(words))
    path = sys.argv[1] if len(sys.argv) > 1 else "/tmp/tour.input.txt"
    open(path, "w").write(out + "\n")
    print("words written:", len(out.split()), "->", path)
    spelled = "".join("," if c == "." else c for c in chords)
    print("chords:", spelled)
    open(path.replace(".input.txt", ".chords.txt"), "w").write(spelled + "\n")

    # What the HUD actually does over the route.
    st, health, bands = d3.SPAWN, 100, []
    for ch in chords:
        st = d3.step(st, d3.keys(ch))
        if d3.nukage_cell(d3.div(st.posX, 1024), d3.div(st.posY, 1024)):
            health = max(0, health - 5)
        bands.append("healthy" if health > 66 else
                     "hurt" if health > 33 else "bloodied")
    print("nukage frames:", sum(1 for i in range(1, len(bands)) if True) and None or 0)
    print(f"health 100 -> {health}; face band changes:",
          [(i, bands[i]) for i in range(1, len(bands)) if bands[i] != bands[i - 1]])


main()
