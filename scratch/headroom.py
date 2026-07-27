"""Where are our remaining points? Reads the standings API's own rank fields.

Do not recompute rank from the sorted score list: ranking points count teams you
rank above *or tie*, so a large tie at the best known score already scores full
marks.  See littleman/HEADROOM.md.

    uv run python scratch/headroom.py
"""
from __future__ import annotations

import json
import pathlib
import subprocess

REPO = pathlib.Path(__file__).resolve().parents[1]
TEAM = "JSKVwHaBsZ63cz2EH3Lxfq5rwnz64jV6"
UA = "randomfun2026solvers/1.0 (+https://icfpcontest2026.com)"
API = "https://icfpcontest2026.com/api/v1"


def get(url: str) -> object:
    token = (REPO / ".icfp-token").read_text().strip()
    out = subprocess.run(
        ["curl", "-s", "--max-time", "45", "-A", UA,
         "-H", f"Authorization: Bearer {token}", url],
        capture_output=True, text=True, check=True,
    ).stdout
    return json.loads(out)


def main() -> None:
    problems = [p for p in get(f"{API}/public/problems") if p.get("status") == "graded"]
    rows = []
    for problem in problems:
        board = get(f"{API}/standings/problems/{problem['id']}")
        entries = board.get("rows", [])
        mine = next((r for r in entries if r["teamId"] == TEAM), None)
        if mine is None:
            rows.append((problem["slug"], 0.0, 0.0, 0.0, 0, None, 1.0))
            continue
        eligible = [r for r in entries if r.get("score")]
        others = max(len(eligible) - 1, 1)
        ahead = [r for r in eligible if mine.get("score") and r["score"] < mine["score"]]
        rows.append((
            problem["slug"], mine.get("points", 0.0), mine.get("passPoints", 0.0),
            mine.get("rankPoints", 0.0), mine.get("rank", 0), mine.get("score"),
            len(ahead) / others,
        ))

    rows.sort(key=lambda r: -(2 - r[1]))
    print(f"{'problem':26s} {'pts':>5s} {'pass':>5s} {'rank':>6s} {'#':>4s} "
          f"{'headroom':>9s} {'ahead':>7s} {'our score':>18s}")
    for slug, pts, pp, rp, rank, score, frac in rows:
        shown = f"{score:,.0f}" if score else "—"
        print(f"{slug:26s} {pts:>5.2f} {pp:>5.2f} {rp:>6.3f} {rank:>4d} "
              f"{2 - pts:>9.2f} {frac * 100:>6.1f}% {shown:>18s}")
    print(f"\ntotal {sum(r[1] for r in rows):.2f} / {2 * len(rows)} on graded problems")


if __name__ == "__main__":
    main()
