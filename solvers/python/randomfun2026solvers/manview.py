#!/usr/bin/env python3
"""Render a *partially built* block to PNG, so topology can be looked at.

:mod:`manpng` renders a complete ``.man`` file, which is the right tool once a
machine loads. The problem it cannot help with is the one that actually costs
rounds: a block under construction, which does not load yet, whose pipes have to
be argued about while they are still wrong.

This module closes that gap. It takes the ``{(x, y): glyph}`` mapping every
builder in this package already carries — :class:`~.lm1.stream.StreamBlock`'s
``cells``, a :class:`~.circuit.Circuit`, a bare dict — pads it into a rectangle,
writes it as a ``.man``, and hands it to :mod:`manpng`.

Why this is worth a module rather than a snippet
------------------------------------------------

``lm1/stream.py``'s docstring says every ``r``/``s`` in the block is decided by
geometry and that the tightest margin is one cell. ``ARCH.md`` §4.4 records the
failure that follows: a mis-bound pipe produces a machine that runs to completion
doing the wrong thing, with no error, and a stray ``|`` one cell behind a bend's
arrowhead deletes a whole pipe silently.

Those are *spatial* facts. Reasoning about them from an index-by-index reading
means holding a picture in your head, and :func:`~.lm1.stream.block_crossings`
only adjudicates part of it — it decides whether a *chord* routes, not whether
four legs of one room collide. That limit was found the expensive way, after a
relay's ports had to be rearranged three times. A picture shows both at once.

Usage::

    from randomfun2026solvers import manview
    from randomfun2026solvers.lm1 import stream

    b = stream.build_stream(a_slots=200, b_slots=200, c_slots=16, trie_bits=3)
    manview.show(b.cells, "/tmp/block3", width=b.width, height=b.height)

    # or straight from the CLI, for anything with a `cells` mapping
    python -m randomfun2026solvers.manview lm1.stream:build_stream --out /tmp/b

``show`` returns the paths it wrote. Read the ``.png`` to look at it; keep the
``.man`` beside it so a coordinate in the picture can be checked against a glyph.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path

__all__ = ["as_rows", "show"]

REPO = Path(__file__).resolve().parents[3]


def as_rows(
    cells: Mapping[tuple[int, int], str],
    *,
    width: int | None = None,
    height: int | None = None,
) -> list[str]:
    """Pad a sparse ``{(x, y): glyph}`` mapping into rectangular rows.

    ``width``/``height`` extend the box beyond the occupied extent, which is what
    you want when the thing being drawn is unfinished: the empty margin is where
    the missing pipes still have to go, and cropping it hides the space you are
    reasoning about.
    """
    if not cells:
        return []
    w = max(width or 0, max(x for x, _ in cells) + 1)
    h = max(height or 0, max(y for _, y in cells) + 1)
    grid = [[" "] * (w + 1) for _ in range(h + 1)]
    for (x, y), glyph in cells.items():
        if x < 0 or y < 0:
            raise ValueError(f"negative cell coordinate {(x, y)}: nothing can be drawn there")
        grid[y][x] = glyph
    return ["".join(row).rstrip() for row in grid]


def show(
    cells: Mapping[tuple[int, int], str],
    stem: str | Path,
    *,
    width: int | None = None,
    height: int | None = None,
    cell: int = 14,
) -> tuple[Path, Path]:
    """Write ``<stem>.man`` and ``<stem>.png``; return both paths.

    The ``.png`` colours every cell by what the *loader* decided it was, not by
    its glyph, so a wall, a pipe body, a bend and a direction opcode are four
    different colours even though three of them are ``>``. That classification is
    :mod:`manpng`'s, and it comes from the engine's own ``analyze``.
    """
    stem = Path(stem)
    rows = as_rows(cells, width=width, height=height)
    if not rows:
        raise ValueError("nothing to render: the cell mapping is empty")
    man = stem.with_suffix(".man")
    man.write_text("\n".join(rows) + "\n")
    png = stem.with_suffix(".png")
    subprocess.run(
        [
            sys.executable,
            "-m",
            "randomfun2026solvers.manpng",
            str(man),
            "--out",
            str(png),
            "--cell",
            str(cell),
        ],
        cwd=REPO / "solvers" / "python",
        check=True,
        capture_output=True,
    )
    return man, png


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    ap.add_argument("target", help="module:callable returning an object with .cells")
    ap.add_argument("--out", type=Path, required=True, help="output stem (no extension)")
    ap.add_argument("--cell", type=int, default=14)
    args = ap.parse_args(argv)

    mod_name, _, fn_name = args.target.partition(":")
    module = __import__(f"randomfun2026solvers.{mod_name}", fromlist=[fn_name])
    built = getattr(module, fn_name)()
    man, png = show(
        built.cells,
        args.out,
        width=getattr(built, "width", None),
        height=getattr(built, "height", None),
        cell=args.cell,
    )
    print(f"wrote {man}\nwrote {png}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
