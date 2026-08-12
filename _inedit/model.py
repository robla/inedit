"""Shared data types and expected errors for inedit."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

MINIMUM_HEIGHT = 4
AUTO_MINIMUM_TEXT_ROWS = 7
AUTO_MAXIMUM_TEXT_ROWS = 20
AUTO_SAVE_INTERVAL_SECONDS = 30.0
HEIGHT_MESSAGE_SECONDS = 1.0
SIGNAL_DISCARD_MESSAGE = "Unsaved changes; interrupt again to discard"
EXIT_PROMPT = "Save modified buffer? Y Yes | N No | ^C Cancel"


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
