#!/usr/bin/env python3
"""Public compatibility facade and executable entry point for inedit."""

from __future__ import annotations

import os as os
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from prompt_toolkit.key_binding.vi_state import InputMode as InputMode

from _inedit.application import (
    BuiltEditor,
    EditorController,
    EditorDependencies,
    build_application as _build_application,
)
from _inedit.cli import parse_args
from _inedit.model import (
    AUTO_MAXIMUM_TEXT_ROWS,
    AUTO_MINIMUM_TEXT_ROWS,
    AUTO_SAVE_INTERVAL_SECONDS,
    EXIT_PROMPT,
    HEIGHT_MESSAGE_SECONDS,
    MINIMUM_HEIGHT,
    SIGNAL_DISCARD_MESSAGE,
    AutoSaveError,
    AutoSaveSnapshot,
    AutoSaveState,
    ConflictError,
    Document,
    EditorOptions,
    EditorResult,
    EditorState,
    EditorView,
    ExitReason,
    FinalOutcome,
    FinalSummary,
    Fingerprint,
    IneditError,
    LoadError,
    NewlineStyle,
    SaveError,
    TerminalError,
)
from _inedit.presentation import (
    EMACS_HELP_TEXT,
    VI_HELP_TEXT,
    _display_width as _display_width,
    _one_line as _one_line,
    _truncate_left as _truncate_left,
    _truncate_right as _truncate_right,
    adjusted_height,
    automatic_height,
    capture_final_summary,
    effective_height,
    format_final_summary,
    format_status,
    format_viewport_position,
    initial_requested_height,
    logical_text_rows,
    vi_mode_label,
)
from _inedit.storage import (
    _check_conflict as _check_conflict,
    _create_temporary_sibling as _create_temporary_sibling,
    _fingerprint as _fingerprint,
    _load_snapshot_is_current as _load_snapshot_is_current,
    _lstat_optional as _lstat_optional,
    _new_file_mode as _new_file_mode,
    _resolved_path as _resolved_path,
    _stat_optional as _stat_optional,
    auto_save_path,
    decode_document,
    encode_document,
    load_document,
    remove_auto_save,
)
from _inedit import storage as _storage
from _inedit.terminal import (
    TerminationRequested,
    _diagnostic as _diagnostic,
    _guard_terminal_cursor_column as _guard_terminal_cursor_column,
    _signal_name as _signal_name,
    _termination_handlers as _termination_handlers,
    run_editor,
)

__all__ = [
    "AUTO_MAXIMUM_TEXT_ROWS",
    "AUTO_MINIMUM_TEXT_ROWS",
    "AUTO_SAVE_INTERVAL_SECONDS",
    "AutoSaveError",
    "AutoSaveSnapshot",
    "AutoSaveState",
    "BuiltEditor",
    "ConflictError",
    "Document",
    "EMACS_HELP_TEXT",
    "EXIT_PROMPT",
    "EditorController",
    "EditorDependencies",
    "EditorOptions",
    "EditorResult",
    "EditorState",
    "EditorView",
    "ExitReason",
    "FinalOutcome",
    "FinalSummary",
    "Fingerprint",
    "HEIGHT_MESSAGE_SECONDS",
    "IneditError",
    "InputMode",
    "LoadError",
    "MINIMUM_HEIGHT",
    "NewlineStyle",
    "SIGNAL_DISCARD_MESSAGE",
    "SaveError",
    "TerminalError",
    "TerminationRequested",
    "VI_HELP_TEXT",
    "adjusted_height",
    "auto_save_path",
    "automatic_height",
    "build_application",
    "capture_final_summary",
    "decode_document",
    "effective_height",
    "encode_document",
    "format_final_summary",
    "format_status",
    "format_viewport_position",
    "initial_requested_height",
    "load_document",
    "logical_text_rows",
    "main",
    "parse_args",
    "remove_auto_save",
    "run_editor",
    "save_document",
    "vi_mode_label",
    "write_auto_save",
]


def _write_all(descriptor: int, data: bytes) -> None:
    """Compatibility hook for save failure-injection tests."""

    _storage._write_all(descriptor, data)


def save_document(document: Document, text: str) -> Document:
    """Atomically save through the public facade's replaceable write hook."""

    return _storage.save_document(document, text, write_all=_write_all)


def write_auto_save(
    document: Document,
    text: str,
    previous: AutoSaveSnapshot | None = None,
) -> AutoSaveSnapshot:
    """Write recovery data through the public facade's write hook."""

    return _storage.write_auto_save(
        document,
        text,
        previous,
        write_all=_write_all,
    )


def build_application(
    document: Document,
    options: EditorOptions,
    *,
    program_name: str = "inedit.py",
    initial_height: int | None = None,
    input: Any = None,
    output: Any = None,
) -> BuiltEditor:
    """Construct the internal editor while preserving facade patch points."""

    dependencies = EditorDependencies(
        save_document=save_document,
        write_auto_save=write_auto_save,
        remove_auto_save=remove_auto_save,
        auto_save_interval_seconds=lambda: AUTO_SAVE_INTERVAL_SECONDS,
        height_message_seconds=lambda: HEIGHT_MESSAGE_SECONDS,
    )
    return _build_application(
        document,
        options,
        program_name=program_name,
        initial_height=initial_height,
        input=input,
        output=output,
        dependencies=dependencies,
    )


def main(argv: Sequence[str] | None = None) -> int:
    options = parse_args(argv)
    return run_editor(
        options,
        program_name=Path(sys.argv[0]).name or "inedit.py",
        load_document=load_document,
        build_application=build_application,
    )


if __name__ == "__main__":
    raise SystemExit(main())
