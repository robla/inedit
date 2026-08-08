#!/usr/bin/env python3
"""A small, fixed-height inline terminal text editor."""

from __future__ import annotations

import argparse
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
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Iterator

from prompt_toolkit.application import Application, run_in_terminal
from prompt_toolkit.buffer import reshape_text
from prompt_toolkit.clipboard import InMemoryClipboard
from prompt_toolkit.enums import EditingMode
from prompt_toolkit.filters import (
    Condition,
    has_selection,
    is_searching,
    vi_insert_mode,
)
from prompt_toolkit.formatted_text import FormattedText
from prompt_toolkit.key_binding import KeyBindings
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

DEFAULT_HEIGHT = 20
MINIMUM_HEIGHT = 4
SIGNAL_DISCARD_MESSAGE = "Unsaved changes; interrupt again to discard"
EXIT_PROMPT = "Save modified buffer? Y Yes | N No | ^C Cancel"
EMACS_HELP_TEXT = """inedit help (Emacs mode)

File
  Ctrl-X        Exit; prompt to save when modified
  Ctrl-S        Save and continue editing
  Ctrl-C        Same as Ctrl-X; cancel an active exit prompt
  Ctrl-G        Close this help

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
    height: int
    vi: bool
    line_numbers: bool


@dataclass
class EditorState:
    document: Document
    original_text: str
    message: str | None = None
    discard_armed: bool = False
    help_visible: bool = False
    exit_prompt: bool = False
    ex_command_visible: bool = False
    effective_height: int = DEFAULT_HEIGHT

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


def parse_args(
    argv: Sequence[str] | None = None,
    environ: Mapping[str, str] | None = None,
) -> EditorOptions:
    """Parse command-line arguments without reading environment at import."""

    parser = argparse.ArgumentParser(
        prog="inedit.py",
        description="Edit one UTF-8 file in a fixed-height inline terminal UI.",
        epilog=(
            "Saving atomically replaces the target inode; hard-link identity, "
            "ownership, ACLs, and extended attributes are not preserved."
        ),
    )
    parser.add_argument(
        "--height",
        metavar="ROWS",
        type=_height_value,
        help="total editor height (default: INEDIT_HEIGHT or 20)",
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
    if namespace.height is None:
        raw_height = environment.get("INEDIT_HEIGHT", str(DEFAULT_HEIGHT))
        try:
            height = _height_value(raw_height)
        except argparse.ArgumentTypeError as exc:
            parser.error(f"INEDIT_HEIGHT {exc}")
    else:
        height = namespace.height

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
    for _ in range(100):
        name = f".inedit-{os.getpid()}-{secrets.token_hex(8)}.tmp"
        temporary_path = target_path.parent / name
        try:
            descriptor = os.open(temporary_path, flags, 0o666)
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
    try:
        descriptor, temporary_path = _create_temporary_sibling(document.target_path)
        _write_all(descriptor, encoded)
        if document.mode is not None:
            os.fchmod(descriptor, document.mode)
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


def effective_height(configured_height: int, terminal_rows: int) -> int:
    if terminal_rows < MINIMUM_HEIGHT + 1:
        raise TerminalError(
            f"terminal has {terminal_rows} rows; at least "
            f"{MINIMUM_HEIGHT + 1} are required"
        )
    return min(configured_height, terminal_rows - 1)


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


def format_status(
    state: EditorState,
    current_text: str,
    cursor_row: int,
    cursor_column: int,
    columns: int,
    vi_mode: str | None = None,
) -> str:
    """Format a single status row, truncating the filename first."""

    filename = _one_line(state.document.display_path)
    message = _one_line(state.message) if state.message else ""
    modified = "modified" if state.is_modified(current_text) else "unchanged"
    if state.exit_prompt:
        return _truncate_right(EXIT_PROMPT, columns)
    help_action = "^G Close" if state.help_visible else "^G Help"
    suffix_parts = [f"Ln {cursor_row + 1}, Col {cursor_column + 1}", modified]
    if vi_mode is not None:
        suffix_parts.append(f"[{vi_mode}]")
    suffix_parts.extend((help_action, "^S Save", "^X/^C Exit"))
    suffix = " | ".join(suffix_parts)
    separator = " | "

    if _display_width(suffix) >= columns:
        return _truncate_right(suffix, columns)

    optional_suffix = separator + suffix
    if message:
        message_suffix = separator + message + optional_suffix
    else:
        message_suffix = optional_suffix

    filename_width = columns - _display_width(message_suffix)
    if filename_width > 0:
        return _truncate_left(filename, filename_width) + message_suffix

    if message:
        message_width = columns - _display_width(optional_suffix)
        if message_width > 0:
            return _truncate_right(message, message_width) + optional_suffix

    return suffix


def build_application(
    document: Document,
    options: EditorOptions,
    *,
    initial_height: int | None = None,
    input: Any = None,
    output: Any = None,
) -> BuiltEditor:
    """Construct the prompt_toolkit application and its mutable editor state."""

    state = EditorState(
        document=document,
        original_text=document.text,
        effective_height=initial_height or options.height,
    )
    search_toolbar = SearchToolbar(vi_mode=options.vi)
    text_area = TextArea(
        text=document.text,
        multiline=True,
        read_only=Condition(lambda: state.exit_prompt),
        wrap_lines=False,
        scrollbar=True,
        line_numbers=options.line_numbers,
        height=lambda: state.effective_height - 1,
        search_field=search_toolbar,
    )
    current_document = document
    help_area = TextArea(
        text=VI_HELP_TEXT if options.vi else EMACS_HELP_TEXT,
        multiline=True,
        read_only=True,
        wrap_lines=False,
        scrollbar=True,
        line_numbers=False,
        height=lambda: state.effective_height - 1,
    )
    command_area = TextArea(
        text="",
        multiline=False,
        prompt=":",
        wrap_lines=False,
        height=1,
        style="class:status",
    )
    application_reference: list[Application[EditorResult]] = []

    def invalidate() -> None:
        if application_reference:
            application_reference[0].invalidate()

    def buffer_changed(_buffer: Any) -> None:
        state.discard_armed = False
        state.exit_prompt = False
        state.message = None
        invalidate()

    text_area.buffer.on_text_changed += buffer_changed

    bindings = KeyBindings()
    ex_command_mode = Condition(lambda: state.ex_command_visible)

    def leave_ex_command(application: Application[EditorResult]) -> None:
        state.ex_command_visible = False
        command_area.buffer.text = ""
        if options.vi:
            application.vi_state.input_mode = InputMode.NAVIGATION
        application.layout.focus(text_area)
        application.invalidate()

    def save_buffer(event: Any) -> bool:
        nonlocal current_document

        state.exit_prompt = False
        current_text = text_area.buffer.text
        if state.is_modified(current_text):
            try:
                current_document = save_document(current_document, current_text)
            except SaveError as exc:
                state.message = _one_line(str(exc))
                state.discard_armed = False
                event.app.invalidate()
                return False
            state.document = current_document
            state.original_text = current_text
            state.discard_armed = False
            state.message = "Saved"
        else:
            state.message = "No changes to save"
        event.app.invalidate()
        return True

    @bindings.add("c-s", filter=~is_searching, eager=True)
    def save(event: Any) -> None:
        if state.ex_command_visible:
            leave_ex_command(event.app)
        save_buffer(event)

    def request_exit(event: Any) -> None:
        if state.ex_command_visible:
            leave_ex_command(event.app)
        if state.help_visible:
            state.help_visible = False
            event.app.layout.focus(text_area)
        if state.is_modified(text_area.buffer.text):
            state.exit_prompt = True
            state.discard_armed = False
            state.message = None
            event.app.invalidate()
        else:
            event.app.exit(result=EditorResult(ExitReason.SAVED))

    @bindings.add(
        "c-x",
        filter=~is_searching,
        eager=True,
        save_before=lambda _event: False,
    )
    def exit_editor(event: Any) -> None:
        if not state.exit_prompt:
            request_exit(event)

    @bindings.add(
        "c-c",
        filter=~ex_command_mode & ~is_searching,
        eager=True,
        save_before=lambda _event: False,
    )
    def ctrl_c_exit(event: Any) -> None:
        if state.exit_prompt:
            state.exit_prompt = False
            state.message = None
            event.app.invalidate()
        else:
            request_exit(event)

    @bindings.add(Keys.SIGINT, eager=True)
    def signal_cancel(event: Any) -> None:
        if state.exit_prompt:
            state.exit_prompt = False
            state.message = None
            event.app.invalidate()
            return
        if not state.is_modified(text_area.buffer.text):
            event.app.exit(result=EditorResult(ExitReason.CANCELED))
        elif state.discard_armed:
            event.app.exit(result=EditorResult(ExitReason.CANCELED))
        else:
            state.discard_armed = True
            state.message = SIGNAL_DISCARD_MESSAGE
            event.app.invalidate()

    @bindings.add(
        "c-g",
        filter=~is_searching,
        eager=True,
        save_before=lambda _event: False,
    )
    def toggle_help(event: Any) -> None:
        if state.ex_command_visible:
            leave_ex_command(event.app)
        state.exit_prompt = False
        state.message = None
        state.help_visible = not state.help_visible
        if state.help_visible:
            help_area.buffer.cursor_position = 0
            event.app.layout.focus(help_area)
        else:
            event.app.layout.focus(text_area)
        event.app.invalidate()

    exit_prompt = Condition(lambda: state.exit_prompt)

    @bindings.add(
        Keys.Any,
        filter=exit_prompt,
        eager=True,
        save_before=lambda _event: False,
    )
    def answer_exit_prompt(event: Any) -> None:
        answer = event.data.casefold()
        if answer == "y":
            if save_buffer(event):
                event.app.exit(result=EditorResult(ExitReason.SAVED))
        elif answer == "n":
            event.app.exit(result=EditorResult(ExitReason.CANCELED))

    emacs_mode = Condition(
        lambda: not options.vi
        and not state.help_visible
        and not state.exit_prompt
    )

    @bindings.add(
        "c-w",
        filter=emacs_mode & ~has_selection & ~is_searching,
        eager=True,
    )
    def start_forward_search(event: Any) -> None:
        search_bindings.start_forward_incremental_search.call(event)

    @bindings.add(
        "c-w",
        filter=emacs_mode & is_searching,
        eager=True,
    )
    def continue_forward_search(event: Any) -> None:
        search_bindings.forward_incremental_search.call(event)

    repeatable_search = Condition(
        lambda: not options.vi
        and not state.help_visible
        and not state.exit_prompt
        and not state.ex_command_visible
        and not is_searching()
        and bool(text_area.control.search_state.text)
        and bool(application_reference)
        and application_reference[0].layout.current_buffer
        is text_area.buffer
    )

    @bindings.add("f3", filter=repeatable_search, eager=True)
    def repeat_search(event: Any) -> None:
        text_area.buffer.apply_search(
            text_area.control.search_state,
            include_current_position=False,
            count=event.arg,
        )
        event.app.invalidate()

    arrow_mode = emacs_mode | vi_insert_mode
    vi_normal_editor = Condition(
        lambda: options.vi
        and not state.help_visible
        and not state.exit_prompt
        and not state.ex_command_visible
        and bool(application_reference)
        and application_reference[0].vi_state.input_mode
        is InputMode.NAVIGATION
        and text_area.buffer.selection_state is None
        and application_reference[0].layout.current_buffer is text_area.buffer
    )

    @bindings.add(":", filter=vi_normal_editor, eager=True)
    def open_ex_command(event: Any) -> None:
        state.ex_command_visible = True
        command_area.buffer.text = ""
        event.app.vi_state.input_mode = InputMode.INSERT
        event.app.layout.focus(command_area)
        event.app.invalidate()

    @bindings.add(
        "escape",
        filter=ex_command_mode,
        eager=True,
        save_before=lambda _event: False,
    )
    @bindings.add(
        "c-c",
        filter=ex_command_mode,
        eager=True,
        save_before=lambda _event: False,
    )
    def cancel_ex_command(event: Any) -> None:
        leave_ex_command(event.app)

    @bindings.add(
        "enter",
        filter=ex_command_mode,
        eager=True,
        save_before=lambda _event: False,
    )
    def accept_ex_command(event: Any) -> None:
        command = command_area.buffer.text.strip()
        leave_ex_command(event.app)
        if command in ("w", "write"):
            save_buffer(event)
        elif command in ("q", "quit"):
            if state.is_modified(text_area.buffer.text):
                state.message = "No write since last change"
                event.app.invalidate()
            else:
                event.app.exit(result=EditorResult(ExitReason.SAVED))
        elif command == "wq":
            if save_buffer(event):
                event.app.exit(result=EditorResult(ExitReason.SAVED))
        elif command in ("h", "help"):
            state.help_visible = True
            help_area.buffer.cursor_position = 0
            event.app.layout.focus(help_area)
            event.app.invalidate()
        elif command == "external":
            open_in_external_editor(event)
        elif command:
            state.message = f"Not an editor command: {command}"
            event.app.invalidate()

    def move_by_character(event: Any, count: int) -> None:
        buffer = event.current_buffer
        if (
            buffer.selection_state is not None
            and buffer.selection_state.shift_mode
        ):
            buffer.exit_selection()
        buffer.cursor_position += count

    @bindings.add("left", filter=arrow_mode, eager=True)
    def move_left(event: Any) -> None:
        move_by_character(event, -event.arg)

    @bindings.add("right", filter=arrow_mode, eager=True)
    def move_right(event: Any) -> None:
        move_by_character(event, event.arg)

    @bindings.add(
        "c-z",
        filter=emacs_mode,
        eager=True,
        save_before=lambda _event: False,
    )
    def undo(event: Any) -> None:
        text_area.buffer.undo()
        event.app.invalidate()

    @bindings.add(
        "escape",
        "e",
        filter=emacs_mode,
        eager=True,
        save_before=lambda _event: False,
    )
    def redo(event: Any) -> None:
        text_area.buffer.redo()
        event.app.invalidate()

    @bindings.add(
        "escape",
        "q",
        filter=emacs_mode,
        eager=True,
    )
    def fill_paragraph(event: Any) -> None:
        buffer = text_area.buffer
        document = buffer.document
        start = document.cursor_position + document.start_of_paragraph()
        end = document.cursor_position + document.end_of_paragraph()
        from_row, _ = document.translate_index_to_position(start)
        to_row, _ = document.translate_index_to_position(end)
        reshape_text(buffer, from_row, to_row)
        event.app.invalidate()

    @bindings.add(
        "escape",
        "v",
        filter=emacs_mode,
        eager=True,
    )
    def open_in_external_editor(event: Any) -> None:
        buffer = text_area.buffer

        async def run() -> None:
            suffix = Path(document.display_path).suffix
            descriptor, filename = tempfile.mkstemp(suffix=suffix)
            try:
                with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                    stream.write(buffer.text)

                editor = os.environ.get("VISUAL") or os.environ.get("EDITOR") or "vi"
                command = shlex.split(editor) + [filename]

                def run_editor() -> int:
                    return subprocess.call(command)

                try:
                    returncode = await run_in_terminal(run_editor, in_executor=True)
                except OSError as exc:
                    state.message = f"could not launch external editor: {exc}"
                else:
                    if returncode != 0:
                        state.message = (
                            f"External editor exited with status {returncode}"
                        )
                    else:
                        with open(filename, "rb") as stream:
                            data = stream.read()
                        try:
                            text, _newline_style, _has_bom = decode_document(data)
                        except LoadError as exc:
                            state.message = _one_line(str(exc))
                        else:
                            buffer.text = text
                            buffer.cursor_position = 0
                            state.message = "Applied external edit"
            finally:
                try:
                    os.unlink(filename)
                except FileNotFoundError:
                    pass
            event.app.invalidate()

        event.app.create_background_task(run())

    def status_fragments() -> FormattedText:
        buffer_document = text_area.buffer.document
        columns = 80
        mode = None
        if application_reference:
            running_application = application_reference[0]
            columns = running_application.output.get_size().columns
            if options.vi:
                mode = vi_mode_label(
                    running_application.vi_state.input_mode,
                    has_selection=text_area.buffer.selection_state is not None,
                    temporary_navigation=(
                        running_application.vi_state.temporary_navigation_mode
                    ),
                )
        status = format_status(
            state,
            text_area.buffer.text,
            buffer_document.cursor_position_row,
            buffer_document.cursor_position_col,
            columns,
            mode,
        )
        return FormattedText([("class:status", status)])

    status_window = Window(
        content=FormattedTextControl(status_fragments),
        height=1,
        dont_extend_height=True,
        wrap_lines=False,
        style="class:status",
    )
    root = HSplit(
        [
            DynamicContainer(
                lambda: help_area if state.help_visible else text_area
            ),
            search_toolbar,
            ConditionalContainer(
                command_area,
                filter=ex_command_mode & ~is_searching,
            ),
            ConditionalContainer(
                status_window,
                filter=~ex_command_mode & ~is_searching,
            ),
        ],
        height=lambda: state.effective_height,
    )
    layout = Layout(root, focused_element=text_area)

    def before_render(application: Application[EditorResult]) -> None:
        if application.is_done:
            return
        rows = application.output.get_size().rows
        try:
            new_height = effective_height(options.height, rows)
        except TerminalError as exc:
            application.exit(result=EditorResult(ExitReason.ERROR, str(exc)))
            return
        if new_height != state.effective_height:
            state.effective_height = new_height

    def initialize_vi_mode(application: Application[EditorResult]) -> None:
        if options.vi:
            application.vi_state.input_mode = InputMode.NAVIGATION

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
        key_bindings=bindings,
        clipboard=InMemoryClipboard(max_size=1),
        editing_mode=EditingMode.VI if options.vi else EditingMode.EMACS,
        enable_page_navigation_bindings=True,
        full_screen=False,
        erase_when_done=True,
        terminal_size_polling_interval=0.5,
        on_reset=initialize_vi_mode,
        before_render=before_render,
        input=input,
        output=output,
    )

    initialize_vi_mode(application)
    application_reference.append(application)
    return BuiltEditor(
        application,
        state,
        text_area,
        help_area,
        command_area,
        search_toolbar,
    )


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
        initial_height = effective_height(options.height, terminal_rows)
    except IneditError as exc:
        _diagnostic(str(exc))
        return 1
    except OSError as exc:
        _diagnostic(exc.strerror or str(exc))
        return 1

    editor = build_application(
        document,
        options,
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
