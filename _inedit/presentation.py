"""Pure height, status-line, final-summary, and help presentation."""

from __future__ import annotations

from prompt_toolkit.key_binding.vi_state import InputMode
from prompt_toolkit.utils import get_cwidth

from .model import (
    AUTO_MAXIMUM_TEXT_ROWS,
    AUTO_MINIMUM_TEXT_ROWS,
    EXIT_PROMPT,
    MINIMUM_HEIGHT,
    Document,
    EditorOptions,
    EditorState,
    EditorView,
    FinalOutcome,
    FinalSummary,
    TerminalError,
)

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
