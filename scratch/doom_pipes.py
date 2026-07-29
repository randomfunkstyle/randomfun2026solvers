"""Pipe traffic for deadman-3d_taped: sends, receives, length, and cell-traversals.

    uv run python scratch/doom_pipes.py [--rounds 8] [--json out.json]

Every number is exact (counted in the engine, not sampled) and comes from the
native `fast_littleman` backend running the *gated* display-judged case — see
`doom_case.py` for why the gate matters and why `littleman/tools/heatmap.mjs`
cannot be used on this machine.

Counted per pipe:

* **sends** — values stored into the pipe's source end (`s`/`S`, plus the input
  room's own injection, which is a store the language performs for you).
* **receives** — values taken out of the destination end (`r`/`R`/`U`, plus the
  output room's and the display's own takes).
* **length** — cells in the pipe's path, i.e. what one value costs to traverse.
* **traversals** — receives x length.  This is the interesting quantity: a
  437-cell pipe used twice a frame is cheaper than a 6-cell pipe used 15,000
  times.  It is a *latency* budget, not a tick count — a pipe is a shift
  register that moves every value one cell per tick in parallel, so traversals
  measure the delay the receiver waits through, which is what a room/teleport
  removes (`SPEC.md`: `R` receives with no distance term).
* **send-blocked / recv-blocked** — how often a store found the head still
  occupied (backpressure) or a read found the tail empty (starvation).  Counted
  once per park, because the engine sleeps a blocked man rather than retrying.
* **wait** — sampled runner-ticks spent parked on an op bound to this pipe.
  This is blocked *duration*, and it is the pipe's real cost to the schedule.
"""

from __future__ import annotations

import argparse
import json

from doom_case import (
    DEFAULT_ROUNDS,
    cell_labels,
    gated_case,
    machine,
    pipe_names,
    profile,
    room_labels,
)


def critical_path(grid, built, prof, ticks: int, names: list[str]) -> dict:
    """What the CPU man — the only man on the critical path — is blocked on.

    Every other man is a servant: he idles on an `r` until work arrives, so his
    parked time is not a cost.  Only the CPU's parked time is, and it is exactly
    a tick count, not an estimate.

    The store round trip is then decomposed by *pipe length*, which is a sound
    accounting because the CPU blocks on it: a read is issued, and no further
    work happens on that man until the answer comes back, so every leg's cells
    are paid serially.  (For a *streaming* pipe like `rom->cpu` this would not
    hold — values pipeline, and the length is paid once per burst.)
    """
    labels = room_labels(grid, built)
    cpu = labels.index("cpu")
    lengths = {p.id: len(p.path) for p in grid.pipes}

    parked_by_pipe: dict[int, int] = {}
    for cell, n in prof.wait.items():
        if not grid.rooms[cpu].contains(cell):
            continue
        binding = grid._bindings.get(cell)
        for pid in binding if isinstance(binding, tuple) else (binding,):
            if isinstance(pid, int) and pid >= 0:
                parked_by_pipe[pid] = parked_by_pipe.get(pid, 0) + n
    cpu_parked = sum(parked_by_pipe.values())

    # Request path: walk the pipe graph out of the CPU, avoiding the answer
    # pipes (anything flowing back into the CPU), and record the cells crossed.
    banks = [rid for rid, name in enumerate(labels) if name.startswith("store:bank")]
    collector = labels.index("store:collector") if "store:collector" in labels else -1
    answer = [p for p in grid.pipes if p.dst == cpu and labels[p.src] == "store:collector"]
    back = sum(lengths[p.id] for p in answer)
    back += sum(
        lengths[p.id] for p in grid.pipes if p.dst == collector and p.src in banks
    ) // max(1, len(banks))

    dist: dict[int, tuple[int, list[str]]] = {cpu: (0, [])}
    frontier = [cpu]
    while frontier:
        nxt = []
        for rid in frontier:
            for p in grid.pipes:
                if p.src != rid or p.dst == collector or p.dst in dist:
                    continue
                d, path = dist[rid]
                dist[p.dst] = (d + lengths[p.id], [*path, f"{names[p.id]}({lengths[p.id]})"])
                nxt.append(p.dst)
        frontier = nxt

    rows, total = [], 0
    for rid in banks:
        out = next((p for p in grid.pipes if p.src == rid and p.dst == collector), None)
        reads = prof.recv[out.id] if out else 0
        forward = dist.get(rid, (0, []))[0]
        cells = forward + back
        rows.append((labels[rid], reads, forward, cells, reads * cells, dist.get(rid, (0, []))[1]))
        total += reads * cells

    lines = ["critical path — the CPU is the only man on it:"]
    for pid, n in sorted(parked_by_pipe.items(), key=lambda kv: -kv[1]):
        lines.append(f"  blocked on {names[pid]:<26} {n:>12,}  {100 * n / ticks:5.2f}% of the run")
    lines.append(
        f"  {'blocked, all pipes':<37} {cpu_parked:>12,}  {100 * cpu_parked / ticks:5.2f}%"
    )
    lines.append(
        f"  {'walking his own dispatch':<37} {ticks - cpu_parked:>12,} "
        f" {100 * (ticks - cpu_parked) / ticks:5.2f}%"
    )
    lines.append("")
    lines.append(
        "store round trip, by bank (cells = request legs + answer legs; the CPU "
        "blocks through all of them):"
    )
    lines.append(
        f"  {'bank':<14} {'reads':>9} {'req cells':>10} {'round trip':>11} "
        f"{'transit ticks':>14} {'%run':>7}   request path"
    )
    for name, reads, forward, cells, cost, path in sorted(rows, key=lambda r: -r[4]):
        lines.append(
            f"  {name:<14} {reads:>9,} {forward:>10} {cells:>11} {cost:>14,} "
            f"{100 * cost / ticks:>6.2f}%   " + " -> ".join(path)
        )
    lines.append(
        f"  {'TOTAL':<14} {sum(r[1] for r in rows):>9,} {'':>10} {'':>11} {total:>14,} "
        f"{100 * total / ticks:>6.2f}%"
    )
    seek = cpu_parked - total
    lines.append("")
    lines.append(
        f"  of the CPU's {cpu_parked:,} blocked ticks, {total:,} "
        f"({100 * total / max(1, cpu_parked):.0f}%) is pipe transit and {seek:,} "
        f"({100 * seek / max(1, cpu_parked):.0f}%) is tape seek + the servants' own walking."
    )
    reads = sum(r[1] for r in rows)
    if reads:
        store = max(parked_by_pipe.values(), default=0)
        lines.append(
            f"  {store / reads:.0f} ticks blocked per store read on the answer pipe alone "
            f"({reads:,} reads, {total / reads:.0f} cells of them transit); "
            f"{cpu_parked / reads:.0f} counting every pipe the CPU blocks on."
        )
    return {"text": "\n".join(lines), "rows": rows, "parked": parked_by_pipe, "transit": total}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--rounds", type=int, default=DEFAULT_ROUNDS, help="len(WALK) prefix")
    ap.add_argument("--stride", type=int, default=1, help="wait-sampling stride in ticks")
    ap.add_argument("--json", help="also write the table here")
    args = ap.parse_args()

    case = gated_case(args.rounds)
    built = machine()
    grid, prof, ticks = profile(case, stride=args.stride)
    names = pipe_names(grid, built)
    labels = cell_labels(grid, built)
    routes = built.route_lengths

    rows = []
    for pipe in grid.pipes:
        i = pipe.id
        length = len(pipe.path)
        # Which named build route drew this pipe, when one matches uniquely.
        route = [k for k, v in routes.items() if v == length and k.split("->")[0] in names[i]]
        rows.append(
            {
                "id": i,
                "name": names[i],
                "route": route[0] if len(route) == 1 else "",
                "length": length,
                "sends": prof.send[i],
                "receives": prof.recv[i],
                "traversals": prof.recv[i] * length,
                "send_blocked": prof.send_blocked[i],
                "recv_blocked": prof.recv_blocked[i],
                "wait": prof.pipe_wait[i],
                "src": labels.get(pipe.src_attach, names[i].split("->")[0]),
                "dst": labels.get(pipe.dst_attach, names[i].split("->")[1]),
            }
        )

    total_traversals = sum(r["traversals"] for r in rows)
    total_wait = sum(r["wait"] for r in rows)
    print(
        f"deadman-3d_taped {grid.width}x{grid.height} — native fast_littleman, gated "
        f"{case.rounds}-round case (WALK[:{args.rounds}])"
    )
    print(
        f"{ticks:,} ticks, {len(grid.pipes)} pipes, "
        f"{prof.samples:,} samples @ stride {args.stride}"
    )
    print()
    head = (
        f"{'#':>3} {'pipe':<34} {'len':>5} {'sends':>9} {'recvs':>9} "
        f"{'traversals':>13} {'%trav':>7} {'sblk':>7} {'rblk':>7} {'wait(ticks)':>13} {'%wait':>7}"
    )
    print(head)
    print("-" * len(head))
    for r in sorted(rows, key=lambda r: -r["traversals"]):
        print(
            f"{r['id']:>3} {r['name']:<34} {r['length']:>5} {r['sends']:>9,} {r['receives']:>9,} "
            f"{r['traversals']:>13,} {100 * r['traversals'] / max(1, total_traversals):>6.2f}% "
            f"{r['send_blocked']:>7,} {r['recv_blocked']:>7,} {r['wait']:>13,} "
            f"{100 * r['wait'] / max(1, total_wait):>6.2f}%"
        )
    print("-" * len(head))
    print(
        f"{'':>3} {'TOTAL':<34} {'':>5} {sum(r['sends'] for r in rows):>9,} "
        f"{sum(r['receives'] for r in rows):>9,} {total_traversals:>13,} {'':>7} "
        f"{sum(r['send_blocked'] for r in rows):>7,} {sum(r['recv_blocked'] for r in rows):>7,} "
        f"{total_wait:>13,}"
    )
    print()
    print("named build routes (lm1.machine.route_lengths):")
    for k, v in routes.items():
        print(f"  {k:<18} {v:>5} cells")

    critical = critical_path(grid, built, prof, ticks, names)
    print()
    print(critical["text"])

    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(
                {
                    "ticks": ticks,
                    "rounds": case.rounds,
                    "samples": prof.samples,
                    "stride": args.stride,
                    "pipes": rows,
                    "route_lengths": routes,
                },
                fh,
                indent=1,
            )
        print(f"\nwrote {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
