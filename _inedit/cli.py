"""Command-line and environment parsing for inedit."""

from __future__ import annotations

import argparse
import os
from collections.abc import Mapping, Sequence
from pathlib import Path

from .model import EditorOptions, MINIMUM_HEIGHT


def _height_value(value: str) -> int:
    try:
        height = int(value, 10)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be an integer") from exc
    if height < MINIMUM_HEIGHT:
        raise argparse.ArgumentTypeError(f"must be at least {MINIMUM_HEIGHT}")
    return height


def _height_setting(value: str) -> int | None:
    if value.casefold() == "auto":
        return None
    return _height_value(value)


def parse_args(
    argv: Sequence[str] | None = None,
    environ: Mapping[str, str] | None = None,
) -> EditorOptions:
    """Parse command-line arguments without reading environment at import."""

    parser = argparse.ArgumentParser(
        prog="inedit.py",
        description="Edit one UTF-8 file in a bounded inline terminal UI.",
        epilog=(
            "Modified buffers receive a private #filename# recovery snapshot "
            "every 30 seconds. Saving atomically replaces the target inode; "
            "hard-link identity, ownership, ACLs, and extended attributes are "
            "not preserved."
        ),
    )
    parser.add_argument(
        "--height",
        metavar="ROWS|auto",
        type=_height_setting,
        default=argparse.SUPPRESS,
        help=(
            "fixed total height, or adaptive sizing "
            "(default: INEDIT_HEIGHT or auto)"
        ),
    )
    parser.add_argument(
        "--vi",
        action="store_true",
        help="use vi editing mode, starting in Normal mode",
    )
    parser.add_argument(
        "--no-line-numbers",
        action="store_true",
        help="hide the line-number gutter",
    )
    parser.add_argument("file", metavar="FILE", help="file to edit")
    namespace = parser.parse_args(argv)

    environment = os.environ if environ is None else environ
    if hasattr(namespace, "height"):
        height = namespace.height
    else:
        raw_height = environment.get("INEDIT_HEIGHT", "auto")
        try:
            height = _height_setting(raw_height)
        except argparse.ArgumentTypeError as exc:
            parser.error(f"INEDIT_HEIGHT {exc}")

    return EditorOptions(
        path=Path(namespace.file),
        height=height,
        vi=namespace.vi,
        line_numbers=not namespace.no_line_numbers,
    )
