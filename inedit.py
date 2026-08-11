#!/usr/bin/env python3
"""A small, bounded-height inline terminal text editor."""

from __future__ import annotations

import argparse
import asyncio
import codecs
import os
import re
import secrets
import shlex
import signal
import stat
import subprocess
import sys
import tempfile
import time
from collections.abc import Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Iterator

from prompt_toolkit.application import Application, run_in_terminal
from prompt_toolkit.buffer import Buffer, reshape_text
from prompt_toolkit.clipboard import InMemoryClipboard
from prompt_toolkit.enums import EditingMode
from prompt_toolkit.filters import (
    Condition,
    has_selection,
    is_searching,
    vi_insert_mode,
)
from prompt_toolkit.formatted_text import FormattedText
from prompt_toolkit.key_binding import KeyBindings, KeyPressEvent
from prompt_toolkit.key_binding.bindings import search as search_bindings
from prompt_toolkit.key_binding.vi_state import InputMode
from prompt_toolkit.keys import Keys
from prompt_toolkit.layout import (
    ConditionalContainer,
    DynamicContainer,
    HSplit,
    Layout,
    Window,
)
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.styles import Style
from prompt_toolkit.utils import get_cwidth
from prompt_toolkit.widgets import SearchToolbar, TextArea

MINIMUM_HEIGHT = 4
AUTO_MINIMUM_TEXT_ROWS = 7
AUTO_MAXIMUM_TEXT_ROWS = 20
AUTO_SAVE_INTERVAL_SECONDS = 30.0
HEIGHT_MESSAGE_SECONDS = 1.0
SIGNAL_DISCARD_MESSAGE = "Unsaved changes; interrupt again to discard"
EXIT_PROMPT = "Save modified buffer? Y Yes | N No | ^C Cancel"
EMACS_HELP_TEXT = """inedit help (Emacs mode)

File
  Ctrl-X        Exit; prompt to save when modified
  Ctrl-S        Save and continue editing
  Ctrl-C        Same as Ctrl-X; cancel an active exit prompt
  Ctrl-G        Close this help

Recovery
  Every 30 sec  Copy a modified buffer to private #filename#
  Explicit save removes this session's recovery file

Display
  Alt-Up        Reduce the editor height by one row
  Alt-Down      Increase the editor height by one row

Clipboard and selection
  Ctrl-Space    Set the mark / start a selection
  Ctrl-W        Cut the region; without one, search forward
  Alt-W         Copy the selected region
  Ctrl-K        Kill from the cursor to the end of the line
  Ctrl-U        Kill from the cursor to the start of the line
  Ctrl-Y        Paste/yank the latest cut or copy

Search
  Ctrl-W        Start or continue a forward search
  Ctrl-R        Start or continue a reverse search
  Up / Down     Search backward / forward for another match
  Enter / Esc   Accept the current match
  Ctrl-C/G      Cancel the search
  F3            Repeat the accepted search

Undo and redo
  Ctrl-Z        Undo
  Ctrl-_        Undo (prompt-toolkit default)
  Alt-E         Redo

Formatting
  Alt-Q         Fill (reflow) the current paragraph

External editor
  Alt-V         Open the buffer in $VISUAL or $EDITOR (else vi)

Movement
  Left / Right  Move by character, crossing line boundaries
  Up / Down     Move by logical line
  Home / End    Start / end of logical line
  PageUp/Down   Move by a viewport
  Ctrl-A/E      Start / end of logical line
  Ctrl-B/F      Backward / forward one character
  Ctrl-P/N      Previous / next logical line
  Alt-B/F       Backward / forward one word

The internal clipboard retains only the latest cut or copy. Terminal paste
(often Ctrl-Shift-V or Shift-Insert) continues to insert system clipboard
text as terminal input.
"""

VI_HELP_TEXT = """inedit help (vi mode)

File (all modes)
  Ctrl-X        Exit; prompt to save when modified
  Ctrl-S        Save and continue editing
  Ctrl-C        Same as Ctrl-X; cancel an active exit prompt
  Ctrl-G        Close this help

Recovery (all modes)
  Every 30 sec  Copy a modified buffer to private #filename#
  Explicit save removes this session's recovery file

Display (all modes)
  Alt-Up        Reduce the editor height by one row
  Alt-Down      Increase the editor height by one row

Modes
  Esc           Return to Normal mode
  i / a         Insert before / after the cursor
  I / A         Insert at first nonblank / end of line
  o / O         Open a line below / above
  R             Enter Replace mode
  v / V         Visual character / line selection
  Ctrl-V        Visual block selection

Normal-mode movement
  h j k l       Left, down, up, right
  Arrow keys    Move the cursor
  w / b / e     Next word, previous word, end of word
  0 / ^ / $     Start, first nonblank, end of line
  gg / G        First / last line
  f/F + char    Find next / previous character on this line
  ; / ,         Repeat / reverse the last character find
  PageUp/Down   Move by a viewport

Search (Normal mode)
  / / ?         Search forward / backward
  n / N         Repeat / reverse the accepted search
  Enter / Esc   Accept the current match
  Ctrl-C/G      Cancel the search

Normal-mode editing
  x / X         Delete character under / before cursor
  dd / D        Delete line / through end of line
  cc / C        Change line / through end of line
  yy / Y        Yank (copy) line
  d/c/y + move  Delete, change, or yank using a motion
  p / P         Paste after / before cursor
  r + char / R  Replace one character / enter Replace mode
  u             Undo
  J             Join the next line
  >> / <<       Indent / unindent line
  ZZ            Save if modified, then exit

Visual mode
  Movement      Extend the selection
  d or x        Cut the selection
  y             Yank (copy) the selection
  Esc           Return to Normal mode

Insert and Replace modes
  Enter         Insert a newline
  Arrow keys    Move the cursor
  Backspace     Delete before the cursor
  Delete        Delete under the cursor
  Esc           Return to Normal mode

Ex commands (Normal mode)
  :w            Save and continue editing
  :q            Exit only when the buffer is unchanged
  :wq           Save and exit
  :h            Open this help
  :external     Edit with $VISUAL or $EDITOR (else vi)
  Esc / Ctrl-C  Cancel the command line

Counts work with Normal-mode commands and operators. Yanks and deletions use
the one-entry internal clipboard, not the system clipboard. The long forms
:write, :quit, and :help also work. Filenames, ! variants, options, and other
Ex syntax are not supported.
"""


class IneditError(Exception):
    """Base class for expected editor errors."""


class LoadError(IneditError):
    """A document could not be loaded safely."""


class SaveError(IneditError):
    """A document could not be saved safely."""


class AutoSaveError(IneditError):
    """A recovery auto-save file could not be updated safely."""


class ConflictError(SaveError):
    """The file or its path changed after it was loaded."""


class TerminalError(IneditError):
    """The terminal cannot support the requested editor layout."""


class NewlineStyle(Enum):
    LF = "lf"
    CRLF = "crlf"


class ExitReason(Enum):
    SAVED = "saved"
    CANCELED = "canceled"
    ERROR = "error"


class FinalOutcome(Enum):
    SAVED = "saved"
    UNCHANGED = "unchanged"
    DISCARDED = "discarded"


class EditorView(Enum):
    """The mutually exclusive inedit-owned views in the application body."""

    EDITOR = "editor"
    HELP = "help"
    EXIT_PROMPT = "exit-prompt"
    EX_COMMAND = "ex-command"


@dataclass(frozen=True)
class Fingerprint:
    device: int
    inode: int
    size: int
    mtime_ns: int


@dataclass(frozen=True)
class Document:
    display_path: str
    requested_path: Path
    target_path: Path
    text: str
    newline_style: NewlineStyle
    has_bom: bool
    mode: int | None
    fingerprint: Fingerprint | None
    entry_fingerprint: Fingerprint | None


@dataclass(frozen=True)
class EditorOptions:
    path: Path
    height: int | None
    vi: bool
    line_numbers: bool


@dataclass(frozen=True)
class FinalSummary:
    program_name: str
    display_path: str
    outcome: FinalOutcome
    saved_during_session: bool
    file_exists: bool | None
    byte_count: int | None


@dataclass(frozen=True)
class AutoSaveSnapshot:
    path: Path
    fingerprint: Fingerprint


@dataclass
class AutoSaveState:
    snapshot: AutoSaveSnapshot | None = None
    text: str | None = None
    disabled: bool = False
    error: str | None = None


@dataclass
class EditorState:
    document: Document
    original_text: str
    program_name: str = "inedit.py"
    message: str | None = None
    discard_armed: bool = False
    view: EditorView = EditorView.EDITOR
    auto_height: bool = False
    requested_height: int = AUTO_MINIMUM_TEXT_ROWS + 1
    effective_height: int = AUTO_MINIMUM_TEXT_ROWS + 1
    saved_during_session: bool = False
    final_summary: FinalSummary | None = None
    auto_save: AutoSaveState = field(default_factory=AutoSaveState)

    def is_modified(self, current_text: str) -> bool:
        return current_text != self.original_text


@dataclass(frozen=True)
class EditorResult:
    reason: ExitReason
    message: str | None = None


@dataclass(frozen=True)
class BuiltEditor:
    application: Application[EditorResult]
    state: EditorState
    text_area: TextArea
    help_area: TextArea
    command_area: TextArea
    search_toolbar: SearchToolbar


class TerminationRequested(BaseException):
    """Raised by a termination-signal handler to unwind the terminal UI."""

    def __init__(self, signum: int) -> None:
        super().__init__(signum)
        self.signum = signum


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


def _fingerprint(st: os.stat_result) -> Fingerprint:
    return Fingerprint(st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns)


def _lstat_optional(path: Path) -> os.stat_result | None:
    try:
        return path.lstat()
    except FileNotFoundError:
        return None


def _stat_optional(path: Path) -> os.stat_result | None:
    try:
        return path.stat()
    except FileNotFoundError:
        return None


def _resolved_path(path: Path) -> Path:
    return Path(os.path.realpath(os.fspath(path)))


def decode_document(data: bytes) -> tuple[str, NewlineStyle, bool]:
    """Decode and normalize a supported on-disk document."""

    if b"\0" in data:
        raise LoadError("file contains a NUL byte")

    has_bom = data.startswith(codecs.BOM_UTF8)
    encoded_text = data[len(codecs.BOM_UTF8) :] if has_bom else data
    try:
        text = encoded_text.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise LoadError(f"file is not valid UTF-8 at byte {exc.start}") from exc

    without_crlf = text.replace("\r\n", "")
    if "\r" in without_crlf:
        raise LoadError("file contains a bare carriage return")
    has_crlf = "\r\n" in text
    if has_crlf and "\n" in without_crlf:
        raise LoadError("file contains mixed LF and CRLF line endings")

    newline_style = NewlineStyle.CRLF if has_crlf else NewlineStyle.LF
    return text.replace("\r\n", "\n"), newline_style, has_bom


def _load_snapshot_is_current(
    requested_path: Path,
    target_path: Path,
    entry_fingerprint: Fingerprint | None,
    target_fingerprint: Fingerprint | None,
) -> bool:
    if _resolved_path(requested_path) != target_path:
        return False
    current_entry = _lstat_optional(requested_path)
    current_target = _stat_optional(target_path)
    return (
        _fingerprint(current_entry) if current_entry else None
    ) == entry_fingerprint and (
        _fingerprint(current_target) if current_target else None
    ) == target_fingerprint


def load_document(path: Path) -> Document:
    """Load one regular UTF-8 file, or describe a new empty target."""

    display_path = os.fspath(path)
    requested_path = Path(os.path.abspath(display_path))

    try:
        entry_stat = _lstat_optional(requested_path)
        entry_fingerprint = _fingerprint(entry_stat) if entry_stat else None
        target_path = _resolved_path(requested_path)
        target_stat = _stat_optional(target_path)

        if target_stat is None:
            if entry_stat is not None and not stat.S_ISLNK(entry_stat.st_mode):
                raise LoadError(f"{display_path}: file changed while opening")
            data = b""
            mode = None
            target_fingerprint = None
        else:
            if not stat.S_ISREG(target_stat.st_mode):
                raise LoadError(f"{display_path}: not a regular file")

            with target_path.open("rb") as stream:
                before = os.fstat(stream.fileno())
                if not stat.S_ISREG(before.st_mode):
                    raise LoadError(f"{display_path}: not a regular file")
                if _fingerprint(before) != _fingerprint(target_stat):
                    raise LoadError(f"{display_path}: file changed while opening")
                data = stream.read()
                after = os.fstat(stream.fileno())
                if _fingerprint(after) != _fingerprint(before):
                    raise LoadError(f"{display_path}: file changed while reading")
                target_fingerprint = _fingerprint(after)
                mode = stat.S_IMODE(after.st_mode)

        if not _load_snapshot_is_current(
            requested_path,
            target_path,
            entry_fingerprint,
            target_fingerprint,
        ):
            raise LoadError(f"{display_path}: file changed while loading")
    except LoadError:
        raise
    except OSError as exc:
        detail = exc.strerror or str(exc)
        raise LoadError(f"{display_path}: {detail}") from exc

    try:
        text, newline_style, has_bom = decode_document(data)
    except LoadError as exc:
        raise LoadError(f"{display_path}: {exc}") from exc

    return Document(
        display_path=display_path,
        requested_path=requested_path,
        target_path=target_path,
        text=text,
        newline_style=newline_style,
        has_bom=has_bom,
        mode=mode,
        fingerprint=target_fingerprint,
        entry_fingerprint=entry_fingerprint,
    )


def encode_document(document: Document, text: str) -> bytes:
    """Encode normalized editor text in the document's original format."""

    if "\0" in text:
        raise SaveError("buffer contains a NUL character")
    if "\r" in text:
        raise SaveError("buffer contains a carriage return")

    disk_text = (
        text.replace("\n", "\r\n")
        if document.newline_style is NewlineStyle.CRLF
        else text
    )
    try:
        encoded = disk_text.encode("utf-8", errors="strict")
    except UnicodeEncodeError as exc:
        raise SaveError("buffer cannot be encoded as UTF-8") from exc
    return codecs.BOM_UTF8 + encoded if document.has_bom else encoded


def _check_conflict(document: Document) -> None:
    try:
        if _resolved_path(document.requested_path) != document.target_path:
            raise ConflictError("file path changed; refusing to overwrite")

        entry_stat = _lstat_optional(document.requested_path)
        entry_fingerprint = _fingerprint(entry_stat) if entry_stat else None
        if entry_fingerprint != document.entry_fingerprint:
            raise ConflictError("file path changed; refusing to overwrite")

        target_stat = _stat_optional(document.target_path)
        target_fingerprint = _fingerprint(target_stat) if target_stat else None
        if target_fingerprint != document.fingerprint:
            raise ConflictError("file changed on disk; refusing to overwrite")
    except ConflictError:
        raise
    except OSError as exc:
        detail = exc.strerror or str(exc)
        raise ConflictError(f"could not check file for conflicts: {detail}") from exc


def _create_temporary_sibling(target_path: Path) -> tuple[int, Path]:
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
    flags |= getattr(os, "O_NOFOLLOW", 0)
    flags |= getattr(os, "O_CLOEXEC", 0)
    for _ in range(100):
        name = f".inedit-{os.getpid()}-{secrets.token_hex(8)}.tmp"
        temporary_path = target_path.parent / name
        try:
            descriptor = os.open(temporary_path, flags, 0o600)
        except FileExistsError:
            continue
        return descriptor, temporary_path
    raise SaveError("could not allocate a temporary sibling file")


def _write_all(descriptor: int, data: bytes) -> None:
    remaining = memoryview(data)
    while remaining:
        written = os.write(descriptor, remaining)
        if written <= 0:
            raise OSError("short write while saving")
        remaining = remaining[written:]


def _new_file_mode() -> int:
    """Return the mode that creating a regular 0666 file would produce."""

    # Python has no read-only umask operation. Temporarily making it maximally
    # restrictive is safer than setting it to zero if another thread happens
    # to create a file during this very small window.
    previous_umask = os.umask(0o777)
    try:
        return 0o666 & ~previous_umask
    finally:
        os.umask(previous_umask)


def save_document(document: Document, text: str) -> Document:
    """Atomically save text and return the new on-disk snapshot."""

    encoded = encode_document(document, text)
    _check_conflict(document)
    try:
        requested_entry = _lstat_optional(document.requested_path)
    except OSError as exc:
        detail = exc.strerror or str(exc)
        raise ConflictError(
            f"could not inspect file path before saving: {detail}"
        ) from exc
    requested_entry_is_symlink = (
        requested_entry is not None and stat.S_ISLNK(requested_entry.st_mode)
    )

    descriptor: int | None = None
    temporary_path: Path | None = None
    saved_fingerprint: Fingerprint | None = None
    saved_mode: int | None = None
    final_mode = document.mode if document.mode is not None else _new_file_mode()
    try:
        descriptor, temporary_path = _create_temporary_sibling(document.target_path)
        _write_all(descriptor, encoded)
        os.fchmod(descriptor, final_mode)
        os.fsync(descriptor)
        saved_stat = os.fstat(descriptor)
        saved_fingerprint = _fingerprint(saved_stat)
        saved_mode = stat.S_IMODE(saved_stat.st_mode)
        os.close(descriptor)
        descriptor = None

        _check_conflict(document)
        os.replace(temporary_path, document.target_path)
        temporary_path = None
    except (SaveError, ConflictError):
        raise
    except OSError as exc:
        detail = exc.strerror or str(exc)
        raise SaveError(f"could not save {document.display_path}: {detail}") from exc
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass
        if temporary_path is not None:
            try:
                temporary_path.unlink()
            except FileNotFoundError:
                pass
            except OSError:
                # The original save error is more useful, and the target was not
                # replaced. Never risk deleting anything except this exact name.
                pass

    assert saved_fingerprint is not None
    entry_fingerprint = (
        document.entry_fingerprint
        if requested_entry_is_symlink
        else saved_fingerprint
    )
    return Document(
        display_path=document.display_path,
        requested_path=document.requested_path,
        target_path=document.target_path,
        text=text,
        newline_style=document.newline_style,
        has_bom=document.has_bom,
        mode=saved_mode,
        fingerprint=saved_fingerprint,
        entry_fingerprint=entry_fingerprint,
    )


def auto_save_path(document: Document) -> Path:
    """Return the Emacs-style recovery filename for a document."""

    name = document.requested_path.name
    return document.requested_path.with_name(f"#{name}#")


def write_auto_save(
    document: Document,
    text: str,
    previous: AutoSaveSnapshot | None = None,
) -> AutoSaveSnapshot:
    """Atomically write one private recovery snapshot without touching FILE."""

    encoded = encode_document(document, text)
    path = auto_save_path(document)
    if previous is not None and previous.path != path:
        raise AutoSaveError("auto-save path changed; refusing to overwrite")

    descriptor: int | None = None
    temporary_path: Path | None = None
    saved_fingerprint: Fingerprint | None = None
    try:
        descriptor, temporary_path = _create_temporary_sibling(path)
        _write_all(descriptor, encoded)
        os.fchmod(descriptor, 0o600)
        os.fsync(descriptor)
        saved_fingerprint = _fingerprint(os.fstat(descriptor))
        os.close(descriptor)
        descriptor = None

        current = _lstat_optional(path)
        if previous is None:
            if current is not None:
                raise AutoSaveError(
                    f"auto-save file already exists; preserving {path}"
                )
            try:
                # Linking installs a complete first snapshot without ever
                # replacing a recovery file from an earlier session.
                os.link(temporary_path, path)
            except FileExistsError as exc:
                raise AutoSaveError(
                    f"auto-save file already exists; preserving {path}"
                ) from exc
            try:
                temporary_path.unlink()
            except OSError:
                # The complete #name# snapshot is already installed. The
                # finally block gets one more chance to remove this private
                # hard-link sibling without misreporting auto-save failure.
                pass
            else:
                temporary_path = None
        else:
            current_fingerprint = _fingerprint(current) if current else None
            if current_fingerprint != previous.fingerprint:
                raise AutoSaveError(
                    f"auto-save file changed; preserving {path}"
                )
            os.replace(temporary_path, path)
            temporary_path = None
    except (SaveError, AutoSaveError):
        raise
    except OSError as exc:
        detail = exc.strerror or str(exc)
        raise AutoSaveError(f"could not auto-save {path}: {detail}") from exc
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass
        if temporary_path is not None:
            try:
                temporary_path.unlink()
            except FileNotFoundError:
                pass
            except OSError:
                pass

    assert saved_fingerprint is not None
    return AutoSaveSnapshot(path, saved_fingerprint)


def remove_auto_save(snapshot: AutoSaveSnapshot) -> None:
    """Remove only the recovery file represented by this session's snapshot."""

    try:
        current = _lstat_optional(snapshot.path)
        if current is None:
            return
        if _fingerprint(current) != snapshot.fingerprint:
            raise AutoSaveError(
                f"auto-save file changed; preserving {snapshot.path}"
            )
        snapshot.path.unlink()
    except AutoSaveError:
        raise
    except OSError as exc:
        detail = exc.strerror or str(exc)
        raise AutoSaveError(
            f"could not remove auto-save {snapshot.path}: {detail}"
        ) from exc


def effective_height(configured_height: int, terminal_rows: int) -> int:
    if terminal_rows < MINIMUM_HEIGHT + 1:
        raise TerminalError(
            f"terminal has {terminal_rows} rows; at least "
            f"{MINIMUM_HEIGHT + 1} are required"
        )
    return min(configured_height, terminal_rows - 1)


def logical_text_rows(text: str) -> int:
    """Count prompt-toolkit-style logical rows, including a trailing blank."""

    return text.count("\n") + 1


def automatic_height(text: str) -> int:
    """Return adaptive total height: 7-20 text rows plus one footer."""

    text_rows = min(
        max(logical_text_rows(text), AUTO_MINIMUM_TEXT_ROWS),
        AUTO_MAXIMUM_TEXT_ROWS,
    )
    return text_rows + 1


def initial_requested_height(document: Document, options: EditorOptions) -> int:
    if options.height is not None:
        return options.height
    return automatic_height(document.text)


def adjusted_height(current_height: int, delta: int, terminal_rows: int) -> int:
    """Return a one-session height adjustment within terminal-safe bounds."""

    requested_height = max(MINIMUM_HEIGHT, current_height + delta)
    return effective_height(requested_height, terminal_rows)


def _display_width(text: str) -> int:
    return sum(get_cwidth(character) for character in text)


def _truncate_right(text: str, maximum_width: int) -> str:
    if maximum_width <= 0:
        return ""
    if _display_width(text) <= maximum_width:
        return text
    if maximum_width == 1:
        return "…"
    kept: list[str] = []
    width = 1
    for character in text:
        character_width = get_cwidth(character)
        if width + character_width > maximum_width:
            break
        kept.append(character)
        width += character_width
    return "".join(kept) + "…"


def _truncate_left(text: str, maximum_width: int) -> str:
    if maximum_width <= 0:
        return ""
    if _display_width(text) <= maximum_width:
        return text
    if maximum_width == 1:
        return "…"
    kept: list[str] = []
    width = 1
    for character in reversed(text):
        character_width = get_cwidth(character)
        if width + character_width > maximum_width:
            break
        kept.append(character)
        width += character_width
    return "…" + "".join(reversed(kept))


def _one_line(text: str) -> str:
    return " ".join(text.splitlines())


def capture_final_summary(
    state: EditorState,
    current_text: str,
) -> FinalSummary:
    """Capture the controlled-exit facts shown in terminal history."""

    if state.is_modified(current_text):
        outcome = FinalOutcome.DISCARDED
    elif state.saved_during_session:
        outcome = FinalOutcome.SAVED
    else:
        outcome = FinalOutcome.UNCHANGED

    try:
        byte_count = state.document.requested_path.stat().st_size
    except FileNotFoundError:
        file_exists: bool | None = False
        byte_count = None
    except OSError:
        file_exists = None
        byte_count = None
    else:
        file_exists = True

    return FinalSummary(
        program_name=_one_line(state.program_name) or "inedit.py",
        display_path=_one_line(state.document.display_path),
        outcome=outcome,
        saved_during_session=state.saved_during_session,
        file_exists=file_exists,
        byte_count=byte_count,
    )


def _byte_count_text(byte_count: int) -> str:
    unit = "byte" if byte_count == 1 else "bytes"
    return f"{byte_count} {unit}"


def format_final_summary(summary: FinalSummary, columns: int) -> str:
    """Format a plain final row, preserving the outcome before the path."""

    label = f"{summary.program_name}: "
    if summary.file_exists is True:
        assert summary.byte_count is not None
        size = _byte_count_text(summary.byte_count)
        if summary.outcome is FinalOutcome.SAVED:
            prefix = f"{label}saved {size} to "
        elif summary.outcome is FinalOutcome.UNCHANGED:
            prefix = f"{label}no changes; {size} on disk: "
        elif summary.saved_during_session:
            prefix = (
                f"{label}unsaved edits discarded; "
                f"previous save kept ({size}): "
            )
        else:
            verb = "remains" if summary.byte_count == 1 else "remain"
            prefix = (
                f"{label}unsaved edits discarded; "
                f"{size} {verb} on disk: "
            )
    elif summary.file_exists is False:
        if summary.outcome is FinalOutcome.DISCARDED:
            prefix = f"{label}unsaved edits discarded; no file created: "
        elif summary.outcome is FinalOutcome.UNCHANGED:
            prefix = f"{label}no changes; no file created: "
        else:
            prefix = f"{label}save completed; file is now missing: "
    else:
        if summary.outcome is FinalOutcome.SAVED:
            action = "saved;"
        elif summary.outcome is FinalOutcome.UNCHANGED:
            action = "no changes;"
        else:
            action = "unsaved edits discarded;"
        prefix = f"{label}{action} file size unavailable: "

    path_width = columns - _display_width(prefix)
    if path_width > 0:
        return prefix + _truncate_left(summary.display_path, path_width)
    return _truncate_right(prefix.rstrip(), columns)


def vi_mode_label(
    input_mode: InputMode,
    *,
    has_selection: bool = False,
    temporary_navigation: bool = False,
) -> str:
    """Return the compact status label for prompt-toolkit's vi state."""

    if has_selection:
        return "VISUAL"
    if temporary_navigation or input_mode is InputMode.NAVIGATION:
        return "NORMAL"
    if input_mode in (InputMode.REPLACE, InputMode.REPLACE_SINGLE):
        return "REPLACE"
    return "INSERT"


def format_viewport_position(
    first_visible_line: int,
    last_visible_line: int,
    line_count: int,
) -> str:
    """Return an Emacs-shaped viewport position label."""

    if line_count <= 0:
        return "All"
    first = min(max(first_visible_line, 0), line_count - 1)
    last = min(max(last_visible_line, first), line_count - 1)
    if first == 0 and last == line_count - 1:
        return "All"
    if first == 0:
        return "Top"
    if last == line_count - 1:
        return "Bot"
    return f"{100 * first // line_count}%"


def format_status(
    state: EditorState,
    current_text: str,
    cursor_row: int,
    cursor_column: int,
    columns: int,
    vi_mode: str | None = None,
    viewport_position: str = "All",
) -> str:
    """Format the one-row status, dropping redundant text when necessary."""

    filename = _one_line(state.document.display_path)
    displayed_message = state.message or state.auto_save.error
    message = _one_line(displayed_message) if displayed_message else ""
    modified = state.is_modified(current_text)
    marker = "**" if modified else "--"
    status_word = "modified" if modified else "unchanged"
    if state.view is EditorView.EXIT_PROMPT:
        return _truncate_right(EXIT_PROMPT, columns)
    help_action = "^G Close" if state.view is EditorView.HELP else "^G Help"
    suffix_parts = [
        f"{viewport_position} L{cursor_row + 1} C{cursor_column + 1}"
    ]
    if vi_mode is not None:
        suffix_parts.append(f"[{vi_mode}]")
    suffix_parts.extend((help_action, "^S Save", "^X/^C Exit"))
    stable_suffix = "   " + " | ".join(suffix_parts)
    marker_prefix = marker + " "
    message_suffix = f" | {message}" if message else ""

    def with_filename(include_status_word: bool) -> str | None:
        legend = f" | {status_word}" if include_status_word else ""
        fixed_width = _display_width(
            marker_prefix + message_suffix + stable_suffix + legend
        )
        filename_width = columns - fixed_width
        if filename_width < 1:
            return None
        return (
            marker_prefix
            + _truncate_left(filename, filename_width)
            + message_suffix
            + stable_suffix
            + legend
        )

    rendered = with_filename(include_status_word=True)
    if rendered is not None:
        return rendered

    rendered = with_filename(include_status_word=False)
    if rendered is not None:
        return rendered

    # A transient message is more useful than a filename on a very narrow
    # screen. Keep the state marker and stable controls, then fit what remains.
    if message:
        message_prefix = marker_prefix + "… | "
        message_width = columns - _display_width(
            message_prefix + stable_suffix
        )
        if message_width > 0:
            return (
                message_prefix
                + _truncate_right(message, message_width)
                + stable_suffix
            )

    essential = marker_prefix + " | ".join(suffix_parts)
    return _truncate_right(essential, columns)


class EditorController:
    """Own inedit's UI state transitions and prompt-toolkit callbacks.

    Prompt-toolkit still owns text editing. This controller owns only the
    application lifecycle around its buffers: save, exit, help, Ex commands,
    height, recovery, external handoff, and rendering.
    """

    def __init__(
        self,
        document: Document,
        options: EditorOptions,
        *,
        program_name: str = "inedit.py",
        initial_height: int | None = None,
        input: Any = None,
        output: Any = None,
    ) -> None:
        self.options = options
        requested_height = initial_requested_height(document, options)
        self.state = EditorState(
            document=document,
            original_text=document.text,
            program_name=program_name,
            auto_height=options.height is None,
            requested_height=requested_height,
            effective_height=(
                initial_height if initial_height is not None else requested_height
            ),
        )
        self.application: Application[EditorResult] | None = None
        self.height_message_generation = 0

        self.search_toolbar = SearchToolbar(vi_mode=options.vi)
        self.text_area = TextArea(
            text=document.text,
            multiline=True,
            read_only=Condition(
                lambda: self.state.view is EditorView.EXIT_PROMPT
            ),
            wrap_lines=False,
            scrollbar=True,
            line_numbers=options.line_numbers,
            height=lambda: self.state.effective_height - 1,
            search_field=self.search_toolbar,
        )
        self.help_area = TextArea(
            text=VI_HELP_TEXT if options.vi else EMACS_HELP_TEXT,
            multiline=True,
            read_only=True,
            wrap_lines=False,
            scrollbar=True,
            line_numbers=False,
            height=lambda: self.state.effective_height - 1,
        )
        self.command_area = TextArea(
            text="",
            multiline=False,
            prompt=":",
            wrap_lines=False,
            height=1,
            style="class:status",
        )
        self.text_area.buffer.on_text_changed += self.buffer_changed

        self.bindings = KeyBindings()
        self.ex_command_mode = Condition(
            lambda: self.state.view is EditorView.EX_COMMAND
        )
        self.exit_prompt_mode = Condition(
            lambda: self.state.view is EditorView.EXIT_PROMPT
        )
        self.emacs_mode = Condition(
            lambda: not self.options.vi
            and self.state.view is EditorView.EDITOR
        )
        self.repeatable_search = Condition(self.can_repeat_search)
        self.vi_normal_editor = Condition(self.is_vi_normal_editor)
        self.install_bindings()

        layout = self.build_layout()
        application: Application[EditorResult] = Application(
            layout=layout,
            style=Style.from_dict(
                {
                    "status": "reverse",
                    "search-toolbar": "reverse",
                    "search-toolbar.prompt": "reverse",
                    "search-toolbar.text": "reverse",
                }
            ),
            key_bindings=self.bindings,
            clipboard=InMemoryClipboard(max_size=1),
            editing_mode=EditingMode.VI if options.vi else EditingMode.EMACS,
            enable_page_navigation_bindings=True,
            full_screen=False,
            erase_when_done=True,
            terminal_size_polling_interval=0.5,
            on_reset=self.initialize_vi_mode,
            before_render=self.before_render,
            input=input,
            output=output,
        )
        self.application = application
        application.pre_run_callables.append(self.start_auto_save)
        self.initialize_vi_mode(application)

    def built_editor(self) -> BuiltEditor:
        application = self.require_application()
        return BuiltEditor(
            application,
            self.state,
            self.text_area,
            self.help_area,
            self.command_area,
            self.search_toolbar,
        )

    def require_application(self) -> Application[EditorResult]:
        if self.application is None:
            raise RuntimeError("editor application has not been constructed")
        return self.application

    def invalidate(self) -> None:
        if self.application is not None:
            self.application.invalidate()

    # Lifecycle and persistence transitions.

    def finish_editor(
        self,
        application: Application[EditorResult],
        reason: ExitReason,
    ) -> None:
        """Exit through prompt-toolkit's final retained render."""

        if application.is_done:
            return
        self.state.final_summary = capture_final_summary(
            self.state,
            self.text_area.buffer.text,
        )
        application.erase_when_done = False
        application.exit(result=EditorResult(reason))

    def buffer_changed(self, _buffer: Buffer) -> None:
        self.state.discard_armed = False
        if self.state.view is EditorView.EXIT_PROMPT:
            self.state.view = EditorView.EDITOR
        self.state.message = None
        if self.state.auto_height:
            content_height = automatic_height(self.text_area.buffer.text)
            if content_height > self.state.requested_height:
                self.state.requested_height = content_height
                if self.application is not None:
                    rows = self.application.output.get_size().rows
                    try:
                        self.state.effective_height = effective_height(
                            self.state.requested_height,
                            rows,
                        )
                    except TerminalError as exc:
                        self.application.exit(
                            result=EditorResult(ExitReason.ERROR, str(exc))
                        )
        self.invalidate()

    def leave_ex_command(self, application: Application[EditorResult]) -> None:
        self.state.view = EditorView.EDITOR
        self.command_area.buffer.text = ""
        if self.options.vi:
            application.vi_state.input_mode = InputMode.NAVIGATION
        application.layout.focus(self.text_area)
        application.invalidate()

    def save_buffer(self, event: KeyPressEvent) -> bool:
        if self.state.view is EditorView.EXIT_PROMPT:
            self.state.view = EditorView.EDITOR
        current_text = self.text_area.buffer.text
        if self.state.is_modified(current_text):
            try:
                saved_document = save_document(self.state.document, current_text)
            except SaveError as exc:
                self.state.message = _one_line(str(exc))
                self.state.discard_armed = False
                event.app.invalidate()
                return False
            self.state.document = saved_document
            self.state.original_text = current_text
            self.state.saved_during_session = True
            self.state.discard_armed = False
            self.state.message = "Saved"
        else:
            self.state.message = "No changes to save"

        if self.state.auto_save.snapshot is not None:
            try:
                remove_auto_save(self.state.auto_save.snapshot)
            except AutoSaveError as exc:
                self.state.auto_save.error = (
                    f"Auto-save cleanup failed: {_one_line(str(exc))}"
                )
            else:
                self.state.auto_save = AutoSaveState()
        elif self.state.auto_save.disabled:
            # Saving is a useful retry point if a stale recovery file was
            # moved away in another terminal.
            self.state.auto_save = AutoSaveState()
        event.app.invalidate()
        return True

    async def auto_save_loop(
        self,
        application: Application[EditorResult],
    ) -> None:
        while True:
            await asyncio.sleep(AUTO_SAVE_INTERVAL_SECONDS)
            if application.is_done or self.state.auto_save.disabled:
                return
            current_text = self.text_area.buffer.text
            if (
                not self.state.is_modified(current_text)
                or current_text == self.state.auto_save.text
            ):
                continue
            try:
                snapshot = write_auto_save(
                    self.state.document,
                    current_text,
                    self.state.auto_save.snapshot,
                )
            except (SaveError, AutoSaveError) as exc:
                self.state.auto_save.disabled = True
                self.state.auto_save.error = (
                    f"Auto-save disabled: {_one_line(str(exc))}"
                )
            else:
                self.state.auto_save.snapshot = snapshot
                self.state.auto_save.text = current_text
                self.state.auto_save.error = None
            application.invalidate()

    def save(self, event: KeyPressEvent) -> None:
        if self.state.view is EditorView.EX_COMMAND:
            self.leave_ex_command(event.app)
        self.save_buffer(event)

    def request_exit(self, event: KeyPressEvent) -> None:
        if self.state.view is EditorView.EX_COMMAND:
            self.leave_ex_command(event.app)
        if self.state.view is EditorView.HELP:
            self.state.view = EditorView.EDITOR
            event.app.layout.focus(self.text_area)
        if self.state.is_modified(self.text_area.buffer.text):
            self.state.view = EditorView.EXIT_PROMPT
            self.state.discard_armed = False
            self.state.message = None
            event.app.invalidate()
        else:
            self.finish_editor(event.app, ExitReason.SAVED)

    def exit_editor(self, event: KeyPressEvent) -> None:
        if self.state.view is not EditorView.EXIT_PROMPT:
            self.request_exit(event)

    def ctrl_c_exit(self, event: KeyPressEvent) -> None:
        if self.state.view is EditorView.EXIT_PROMPT:
            self.state.view = EditorView.EDITOR
            self.state.message = None
            event.app.invalidate()
        else:
            self.request_exit(event)

    def signal_cancel(self, event: KeyPressEvent) -> None:
        if self.state.view is EditorView.EXIT_PROMPT:
            self.state.view = EditorView.EDITOR
            self.state.message = None
            event.app.invalidate()
            return
        if not self.state.is_modified(self.text_area.buffer.text):
            self.finish_editor(event.app, ExitReason.CANCELED)
        elif self.state.discard_armed:
            self.finish_editor(event.app, ExitReason.CANCELED)
        else:
            self.state.discard_armed = True
            self.state.message = SIGNAL_DISCARD_MESSAGE
            event.app.invalidate()

    def toggle_help(self, event: KeyPressEvent) -> None:
        if self.state.view is EditorView.EX_COMMAND:
            self.leave_ex_command(event.app)
        self.state.message = None
        if self.state.view is EditorView.HELP:
            self.state.view = EditorView.EDITOR
            event.app.layout.focus(self.text_area)
        else:
            self.state.view = EditorView.HELP
            self.help_area.buffer.cursor_position = 0
            event.app.layout.focus(self.help_area)
        event.app.invalidate()

    # Editing-mode and display commands.

    def adjust_editor_height(self, event: KeyPressEvent, delta: int) -> None:
        self.state.auto_height = False
        rows = event.app.output.get_size().rows
        try:
            new_height = adjusted_height(
                self.state.effective_height,
                delta,
                rows,
            )
        except TerminalError as exc:
            event.app.exit(result=EditorResult(ExitReason.ERROR, str(exc)))
            return

        if new_height != self.state.effective_height:
            self.state.requested_height = new_height
            self.state.effective_height = new_height
        message = f"Height: {self.state.effective_height}"
        self.state.message = message
        self.height_message_generation += 1
        generation = self.height_message_generation
        event.app.invalidate()
        event.app.create_background_task(
            self.clear_height_message(event.app, message, generation)
        )

    async def clear_height_message(
        self,
        application: Application[EditorResult],
        message: str,
        generation: int,
    ) -> None:
        await asyncio.sleep(HEIGHT_MESSAGE_SECONDS)
        if (
            generation == self.height_message_generation
            and self.state.message == message
        ):
            self.state.message = None
            application.invalidate()

    def shrink_editor(self, event: KeyPressEvent) -> None:
        self.adjust_editor_height(event, -1)

    def expand_editor(self, event: KeyPressEvent) -> None:
        self.adjust_editor_height(event, 1)

    def answer_exit_prompt(self, event: KeyPressEvent) -> None:
        answer = event.data.casefold()
        if answer == "y":
            if self.save_buffer(event):
                self.finish_editor(event.app, ExitReason.SAVED)
        elif answer == "n":
            self.finish_editor(event.app, ExitReason.CANCELED)

    def can_repeat_search(self) -> bool:
        return (
            not self.options.vi
            and self.state.view is EditorView.EDITOR
            and not is_searching()
            and bool(self.text_area.control.search_state.text)
            and self.application is not None
            and self.application.layout.current_buffer is self.text_area.buffer
        )

    def start_forward_search(self, event: KeyPressEvent) -> None:
        search_bindings.start_forward_incremental_search.call(event)

    def continue_forward_search(self, event: KeyPressEvent) -> None:
        search_bindings.forward_incremental_search.call(event)

    def repeat_search(self, event: KeyPressEvent) -> None:
        self.text_area.buffer.apply_search(
            self.text_area.control.search_state,
            include_current_position=False,
            count=event.arg,
        )
        event.app.invalidate()

    def is_vi_normal_editor(self) -> bool:
        return (
            self.options.vi
            and self.state.view is EditorView.EDITOR
            and self.application is not None
            and self.application.vi_state.input_mode is InputMode.NAVIGATION
            and self.text_area.buffer.selection_state is None
            and self.application.layout.current_buffer is self.text_area.buffer
        )

    def vi_write_if_modified_and_exit(self, event: KeyPressEvent) -> None:
        if (
            not self.state.is_modified(self.text_area.buffer.text)
            or self.save_buffer(event)
        ):
            self.finish_editor(event.app, ExitReason.SAVED)

    def open_ex_command(self, event: KeyPressEvent) -> None:
        self.state.view = EditorView.EX_COMMAND
        self.command_area.buffer.text = ""
        event.app.vi_state.input_mode = InputMode.INSERT
        event.app.layout.focus(self.command_area)
        event.app.invalidate()

    def cancel_ex_command(self, event: KeyPressEvent) -> None:
        self.leave_ex_command(event.app)

    def accept_ex_command(self, event: KeyPressEvent) -> None:
        command = self.command_area.buffer.text.strip()
        self.leave_ex_command(event.app)
        if command in ("w", "write"):
            self.save_buffer(event)
        elif command in ("q", "quit"):
            if self.state.is_modified(self.text_area.buffer.text):
                self.state.message = "No write since last change"
                event.app.invalidate()
            else:
                self.finish_editor(event.app, ExitReason.SAVED)
        elif command == "wq":
            if self.save_buffer(event):
                self.finish_editor(event.app, ExitReason.SAVED)
        elif command in ("h", "help"):
            self.state.view = EditorView.HELP
            self.help_area.buffer.cursor_position = 0
            event.app.layout.focus(self.help_area)
            event.app.invalidate()
        elif command == "external":
            self.open_in_external_editor(event)
        elif command:
            self.state.message = f"Not an editor command: {command}"
            event.app.invalidate()

    def move_by_character(self, event: KeyPressEvent, count: int) -> None:
        buffer = event.current_buffer
        if (
            buffer.selection_state is not None
            and buffer.selection_state.shift_mode
        ):
            buffer.exit_selection()
        buffer.cursor_position += count

    def move_left(self, event: KeyPressEvent) -> None:
        self.move_by_character(event, -event.arg)

    def move_right(self, event: KeyPressEvent) -> None:
        self.move_by_character(event, event.arg)

    def undo(self, event: KeyPressEvent) -> None:
        self.text_area.buffer.undo()
        event.app.invalidate()

    def redo(self, event: KeyPressEvent) -> None:
        self.text_area.buffer.redo()
        event.app.invalidate()

    def fill_paragraph(self, event: KeyPressEvent) -> None:
        buffer = self.text_area.buffer
        document = buffer.document
        start = document.cursor_position + document.start_of_paragraph()
        end = document.cursor_position + document.end_of_paragraph()
        from_row, _ = document.translate_index_to_position(start)
        to_row, _ = document.translate_index_to_position(end)
        reshape_text(buffer, from_row, to_row)
        event.app.invalidate()

    def open_in_external_editor(self, event: KeyPressEvent) -> None:
        event.app.create_background_task(self.run_external_editor(event.app))

    async def run_external_editor(
        self,
        application: Application[EditorResult],
    ) -> None:
        buffer = self.text_area.buffer
        suffix = Path(self.state.document.display_path).suffix
        descriptor, filename = tempfile.mkstemp(suffix=suffix)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                stream.write(buffer.text)

            editor = os.environ.get("VISUAL") or os.environ.get("EDITOR") or "vi"
            command = shlex.split(editor) + [filename]

            def invoke_editor() -> int:
                return subprocess.call(command)

            try:
                returncode = await run_in_terminal(
                    invoke_editor,
                    in_executor=True,
                )
            except OSError as exc:
                self.state.message = f"could not launch external editor: {exc}"
            else:
                if returncode != 0:
                    self.state.message = (
                        f"External editor exited with status {returncode}"
                    )
                else:
                    with open(filename, "rb") as stream:
                        data = stream.read()
                    try:
                        text, _newline_style, _has_bom = decode_document(data)
                    except LoadError as exc:
                        self.state.message = _one_line(str(exc))
                    else:
                        buffer.text = text
                        buffer.cursor_position = 0
                        self.state.message = "Applied external edit"
        finally:
            try:
                os.unlink(filename)
            except FileNotFoundError:
                pass
        application.invalidate()

    # Rendering and prompt-toolkit lifecycle hooks.

    def status_fragments(self) -> FormattedText:
        buffer_document = self.text_area.buffer.document
        columns = 80
        mode = None
        line_count = buffer_document.line_count
        visible_rows = max(1, self.state.effective_height - 1)
        viewport_position = format_viewport_position(
            0,
            min(line_count - 1, visible_rows - 1),
            line_count,
        )
        if self.application is not None:
            columns = self.application.output.get_size().columns
            render_info = self.text_area.window.render_info
            if render_info is not None and render_info.displayed_lines:
                viewport_position = format_viewport_position(
                    render_info.first_visible_line(),
                    render_info.last_visible_line(),
                    render_info.content_height,
                )
            if self.options.vi:
                mode = vi_mode_label(
                    self.application.vi_state.input_mode,
                    has_selection=(
                        self.text_area.buffer.selection_state is not None
                    ),
                    temporary_navigation=(
                        self.application.vi_state.temporary_navigation_mode
                    ),
                )
        status = format_status(
            self.state,
            self.text_area.buffer.text,
            buffer_document.cursor_position_row,
            buffer_document.cursor_position_col,
            columns,
            mode,
            viewport_position,
        )
        return FormattedText([("class:status", status)])

    def final_summary_fragments(self) -> FormattedText:
        columns = 80
        if self.application is not None:
            columns = self.application.output.get_size().columns
        assert self.state.final_summary is not None
        summary = format_final_summary(self.state.final_summary, columns)
        return FormattedText([("", summary)])

    def build_layout(self) -> Layout:
        status_window = Window(
            content=FormattedTextControl(self.status_fragments),
            height=1,
            dont_extend_height=True,
            wrap_lines=False,
            style="class:status",
        )
        final_summary_window = Window(
            content=FormattedTextControl(self.final_summary_fragments),
            height=1,
            dont_extend_height=True,
            wrap_lines=False,
        )
        final_summary_visible = Condition(
            lambda: self.state.final_summary is not None
        )
        root = HSplit(
            [
                DynamicContainer(
                    lambda: (
                        self.help_area
                        if self.state.view is EditorView.HELP
                        else self.text_area
                    )
                ),
                ConditionalContainer(
                    self.search_toolbar,
                    filter=~final_summary_visible,
                ),
                ConditionalContainer(
                    self.command_area,
                    filter=(
                        self.ex_command_mode
                        & ~is_searching
                        & ~final_summary_visible
                    ),
                ),
                ConditionalContainer(
                    status_window,
                    filter=(
                        ~self.ex_command_mode
                        & ~is_searching
                        & ~final_summary_visible
                    ),
                ),
                ConditionalContainer(
                    final_summary_window,
                    filter=final_summary_visible,
                ),
            ],
            height=lambda: self.state.effective_height,
        )
        return Layout(root, focused_element=self.text_area)

    def before_render(self, application: Application[EditorResult]) -> None:
        if application.is_done:
            return
        rows = application.output.get_size().rows
        try:
            new_height = effective_height(self.state.requested_height, rows)
        except TerminalError as exc:
            application.exit(result=EditorResult(ExitReason.ERROR, str(exc)))
            return
        if new_height != self.state.effective_height:
            self.state.effective_height = new_height

    def initialize_vi_mode(self, application: Application[EditorResult]) -> None:
        if self.options.vi:
            application.vi_state.input_mode = InputMode.NAVIGATION

    def start_auto_save(self) -> None:
        application = self.require_application()
        application.create_background_task(self.auto_save_loop(application))

    # The complete application-level keymap is registered in one place. Each
    # target is a named method above rather than a closure inside construction.

    def install_bindings(self) -> None:
        def no_implicit_save(_event: KeyPressEvent) -> bool:
            return False

        add = self.bindings.add

        add("c-s", filter=~is_searching, eager=True)(self.save)
        add(
            "c-x",
            filter=~is_searching,
            eager=True,
            save_before=no_implicit_save,
        )(self.exit_editor)
        add(
            "c-c",
            filter=~self.ex_command_mode & ~is_searching,
            eager=True,
            save_before=no_implicit_save,
        )(self.ctrl_c_exit)
        add(Keys.SIGINT, eager=True)(self.signal_cancel)
        add(
            "c-g",
            filter=~is_searching,
            eager=True,
            save_before=no_implicit_save,
        )(self.toggle_help)
        add(
            "escape",
            "up",
            filter=~self.exit_prompt_mode,
            eager=True,
            save_before=no_implicit_save,
        )(self.shrink_editor)
        add(
            "escape",
            "down",
            filter=~self.exit_prompt_mode,
            eager=True,
            save_before=no_implicit_save,
        )(self.expand_editor)
        add(
            Keys.Any,
            filter=self.exit_prompt_mode,
            eager=True,
            save_before=no_implicit_save,
        )(self.answer_exit_prompt)
        add(
            "c-w",
            filter=self.emacs_mode & ~has_selection & ~is_searching,
            eager=True,
        )(self.start_forward_search)
        add(
            "c-w",
            filter=self.emacs_mode & is_searching,
            eager=True,
        )(self.continue_forward_search)
        add("f3", filter=self.repeatable_search, eager=True)(self.repeat_search)

        arrow_mode = self.emacs_mode | vi_insert_mode
        add("left", filter=arrow_mode, eager=True)(self.move_left)
        add("right", filter=arrow_mode, eager=True)(self.move_right)
        add(
            "c-z",
            filter=self.emacs_mode,
            eager=True,
            save_before=no_implicit_save,
        )(self.undo)
        add(
            "escape",
            "e",
            filter=self.emacs_mode,
            eager=True,
            save_before=no_implicit_save,
        )(self.redo)
        add("escape", "q", filter=self.emacs_mode, eager=True)(
            self.fill_paragraph
        )
        add("escape", "v", filter=self.emacs_mode, eager=True)(
            self.open_in_external_editor
        )

        add(
            "Z",
            "Z",
            filter=self.vi_normal_editor,
            eager=True,
            save_before=no_implicit_save,
        )(self.vi_write_if_modified_and_exit)
        add(":", filter=self.vi_normal_editor, eager=True)(self.open_ex_command)
        add(
            "escape",
            filter=self.ex_command_mode,
            eager=True,
            save_before=no_implicit_save,
        )(self.cancel_ex_command)
        add(
            "c-c",
            filter=self.ex_command_mode,
            eager=True,
            save_before=no_implicit_save,
        )(self.cancel_ex_command)
        add(
            "enter",
            filter=self.ex_command_mode,
            eager=True,
            save_before=no_implicit_save,
        )(self.accept_ex_command)


def build_application(
    document: Document,
    options: EditorOptions,
    *,
    program_name: str = "inedit.py",
    initial_height: int | None = None,
    input: Any = None,
    output: Any = None,
) -> BuiltEditor:
    """Construct the prompt-toolkit editor through its explicit controller."""

    return EditorController(
        document,
        options,
        program_name=program_name,
        initial_height=initial_height,
        input=input,
        output=output,
    ).built_editor()


def _signal_name(signum: int) -> str:
    try:
        return signal.Signals(signum).name
    except ValueError:
        return str(signum)


@contextmanager
def _termination_handlers(
    application: Application[EditorResult],
) -> Iterator[None]:
    signals = [
        candidate
        for candidate in (
            getattr(signal, "SIGTERM", None),
            getattr(signal, "SIGHUP", None),
        )
        if candidate is not None
    ]
    previous: dict[signal.Signals, Any] = {}

    def terminate(signum: int, _frame: Any) -> None:
        loop = application.loop
        if loop is None:
            raise TerminationRequested(signum)

        result = EditorResult(
            ExitReason.ERROR,
            f"terminated by {_signal_name(signum)}",
        )

        def exit_application() -> None:
            if not application.is_done:
                application.exit(result=result)

        loop.call_soon_threadsafe(exit_application)

    try:
        for candidate in signals:
            previous[candidate] = signal.getsignal(candidate)
            signal.signal(candidate, terminate)
        yield
    finally:
        for candidate, handler in previous.items():
            signal.signal(candidate, handler)


def _diagnostic(message: str) -> None:
    print(f"inedit.py: {_one_line(message)}", file=sys.stderr)


def _guard_terminal_cursor_column(
    stdin_fd: int, stdout_fd: int, timeout: float = 0.3
) -> None:
    """Move to a fresh line before prompt_toolkit paints its first frame.

    Some invokers (notably git as GIT_EDITOR) print a message with no
    trailing newline immediately before exec'ing the editor, intending to
    overwrite it themselves later with a carriage return. prompt_toolkit's
    inline renderer assumes the cursor already sits at column 1 and never
    guards against this, so its first row gets painted onto the tail of
    that leftover text instead of a blank one. Query the real cursor
    column and, unless the terminal confirms it is already 1, emit a
    newline before prompt_toolkit ever draws a frame.
    """
    if os.name != "posix":
        return

    import select
    import termios

    try:
        original_attributes = termios.tcgetattr(stdin_fd)
    except termios.error:
        return

    raw_attributes = termios.tcgetattr(stdin_fd)
    raw_attributes[3] &= ~(termios.ECHO | termios.ICANON)
    raw_attributes[6][termios.VMIN] = 0
    raw_attributes[6][termios.VTIME] = 0

    column: int | None = None
    try:
        termios.tcsetattr(stdin_fd, termios.TCSADRAIN, raw_attributes)
        os.write(stdout_fd, b"\x1b[6n")
        buffer = b""
        deadline = time.monotonic() + timeout
        while column is None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            ready, _, _ = select.select([stdin_fd], [], [], remaining)
            if not ready:
                break
            chunk = os.read(stdin_fd, 64)
            if not chunk:
                break
            buffer += chunk
            match = re.search(rb"\x1b\[\d+;(\d+)R", buffer)
            if match:
                column = int(match.group(1))
    except OSError:
        column = None
    finally:
        termios.tcsetattr(stdin_fd, termios.TCSADRAIN, original_attributes)

    if column != 1:
        os.write(stdout_fd, b"\r\n")


def main(argv: Sequence[str] | None = None) -> int:
    options = parse_args(argv)

    if not sys.stdin.isatty() or not sys.stdout.isatty():
        _diagnostic("stdin and stdout must both be terminals")
        return 1

    try:
        document = load_document(options.path)
        terminal_rows = os.get_terminal_size(sys.stdout.fileno()).lines
        requested_height = initial_requested_height(document, options)
        initial_height = effective_height(requested_height, terminal_rows)
    except IneditError as exc:
        _diagnostic(str(exc))
        return 1
    except OSError as exc:
        _diagnostic(exc.strerror or str(exc))
        return 1

    editor = build_application(
        document,
        options,
        program_name=Path(sys.argv[0]).name or "inedit.py",
        initial_height=initial_height,
    )
    _guard_terminal_cursor_column(sys.stdin.fileno(), sys.stdout.fileno())
    try:
        with _termination_handlers(editor.application):
            result = editor.application.run(set_exception_handler=False)
    except KeyboardInterrupt:
        return 130
    except TerminationRequested as exc:
        _diagnostic(f"terminated by {_signal_name(exc.signum)}")
        return 1
    except BaseException as exc:
        _diagnostic(f"unexpected runtime error: {exc}")
        return 1

    if result.reason is ExitReason.SAVED:
        return 0
    if result.reason is ExitReason.CANCELED:
        return 130
    if result.message:
        _diagnostic(result.message)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
