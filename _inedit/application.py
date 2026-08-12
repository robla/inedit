"""Prompt-toolkit application construction and editor transitions."""

from __future__ import annotations

import asyncio
import os
import shlex
import subprocess
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

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
from prompt_toolkit.widgets import SearchToolbar, TextArea

from .model import (
    AUTO_SAVE_INTERVAL_SECONDS,
    HEIGHT_MESSAGE_SECONDS,
    SIGNAL_DISCARD_MESSAGE,
    AutoSaveError,
    AutoSaveSnapshot,
    AutoSaveState,
    Document,
    EditorOptions,
    EditorResult,
    EditorState,
    EditorView,
    ExitReason,
    LoadError,
    SaveError,
    TerminalError,
)
from .presentation import (
    EMACS_HELP_TEXT,
    VI_HELP_TEXT,
    _one_line,
    adjusted_height,
    automatic_height,
    capture_final_summary,
    effective_height,
    format_final_summary,
    format_status,
    format_viewport_position,
    initial_requested_height,
    vi_mode_label,
)
from .storage import (
    decode_document,
    remove_auto_save,
    save_document,
    write_auto_save,
)


SaveDocument = Callable[[Document, str], Document]
WriteAutoSave = Callable[
    [Document, str, AutoSaveSnapshot | None], AutoSaveSnapshot
]
RemoveAutoSave = Callable[[AutoSaveSnapshot], None]
Delay = Callable[[], float]


def _default_auto_save_interval() -> float:
    return AUTO_SAVE_INTERVAL_SECONDS


def _default_height_message_delay() -> float:
    return HEIGHT_MESSAGE_SECONDS


@dataclass(frozen=True)
class EditorDependencies:
    """Replaceable side effects and timers used by the controller."""

    save_document: SaveDocument = save_document
    write_auto_save: WriteAutoSave = write_auto_save
    remove_auto_save: RemoveAutoSave = remove_auto_save
    auto_save_interval_seconds: Delay = _default_auto_save_interval
    height_message_seconds: Delay = _default_height_message_delay


@dataclass(frozen=True)
class BuiltEditor:
    application: Application[EditorResult]
    state: EditorState
    text_area: TextArea
    help_area: TextArea
    command_area: TextArea
    search_toolbar: SearchToolbar


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
        dependencies: EditorDependencies | None = None,
    ) -> None:
        self.options = options
        self.dependencies = dependencies or EditorDependencies()
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
                saved_document = self.dependencies.save_document(
                    self.state.document,
                    current_text,
                )
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
                self.dependencies.remove_auto_save(self.state.auto_save.snapshot)
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
            await asyncio.sleep(self.dependencies.auto_save_interval_seconds())
            if application.is_done or self.state.auto_save.disabled:
                return
            current_text = self.text_area.buffer.text
            if (
                not self.state.is_modified(current_text)
                or current_text == self.state.auto_save.text
            ):
                continue
            try:
                snapshot = self.dependencies.write_auto_save(
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
        await asyncio.sleep(self.dependencies.height_message_seconds())
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
    dependencies: EditorDependencies | None = None,
) -> BuiltEditor:
    """Construct the prompt-toolkit editor through its explicit controller."""

    return EditorController(
        document,
        options,
        program_name=program_name,
        initial_height=initial_height,
        input=input,
        output=output,
        dependencies=dependencies,
    ).built_editor()
