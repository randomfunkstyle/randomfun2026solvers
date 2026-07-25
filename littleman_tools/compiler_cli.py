"""Command-line compiler for textual Little Man circuit programs."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path

from .composer import write
from .language import LanguageError, parse_file


def main(argv: Sequence[str] | None = None) -> int:
    """Compile one .lmc source file to the explicitly requested .man output."""

    parser = argparse.ArgumentParser(prog="littleman-compile")
    parser.add_argument("source", type=Path, metavar="INPUT.lmc")
    parser.add_argument("-o", "--output", type=Path, required=True, metavar="OUTPUT.man")
    args = parser.parse_args(argv)
    try:
        write(parse_file(args.source), args.output)
    except (LanguageError, OSError, ValueError) as exc:
        parser.error(str(exc))
    return 0
