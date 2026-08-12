"""Terminal startup, signal handling, application execution, and exit status."""

from __future__ import annotations

import os
import re
import signal
import sys
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from prompt_toolkit.application import Application

from .application import BuiltEditor
from .model import (
    Document,
    EditorOptions,
    EditorResult,
    ExitReason,
    IneditError,
)
from .presentation import _one_line, effective_height, initial_requested_height

LoadDocument = Callable[[Path], Document]
BuildApplication = Callable[..., BuiltEditor]


class TerminationRequested(BaseException):
    """Raised by a termination-signal handler before the UI loop exists."""

    def __init__(self, signum: int) -> None:
        super().__init__(signum)
        self.signum = signum


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


def run_editor(
    options: EditorOptions,
    *,
    program_name: str,
    load_document: LoadDocument,
    build_application: BuildApplication,
) -> int:
    """Run one parsed editor invocation and return its process status."""

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
        program_name=program_name,
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
