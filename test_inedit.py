from __future__ import annotations

import codecs
import io
import os
import stat
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest import mock

from prompt_toolkit.data_structures import Size
from prompt_toolkit.input.defaults import create_pipe_input
from prompt_toolkit.output import DummyOutput

import inedit


class ArgumentTests(unittest.TestCase):
    def test_defaults_and_switches(self) -> None:
        options = inedit.parse_args(["--vi", "--no-line-numbers", "file.txt"], {})
        self.assertEqual(options.path, Path("file.txt"))
        self.assertEqual(options.height, 20)
        self.assertTrue(options.vi)
        self.assertFalse(options.line_numbers)

    def test_environment_height_and_command_line_override(self) -> None:
        from_environment = inedit.parse_args(["file"], {"INEDIT_HEIGHT": "9"})
        overridden = inedit.parse_args(
            ["--height", "12", "file"], {"INEDIT_HEIGHT": "9"}
        )
        self.assertEqual(from_environment.height, 9)
        self.assertEqual(overridden.height, 12)

    def test_invalid_environment_height_is_usage_error(self) -> None:
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
            inedit.parse_args(["file"], {"INEDIT_HEIGHT": "3"})
        self.assertEqual(error.exception.code, 2)

    def test_double_dash_allows_option_like_filename(self) -> None:
        options = inedit.parse_args(["--", "-temporary"], {})
        self.assertEqual(options.path, Path("-temporary"))


class DocumentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.directory = Path(self.temporary_directory.name)

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_new_file_loads_empty_and_is_created_only_on_save(self) -> None:
        path = self.directory / "new.txt"
        document = inedit.load_document(path)
        self.assertEqual(document.text, "")
        self.assertIsNone(document.fingerprint)
        self.assertFalse(path.exists())

        inedit.save_document(document, "new content")
        self.assertEqual(path.read_bytes(), b"new content")

    def test_utf8_bom_crlf_and_final_newline_are_preserved(self) -> None:
        path = self.directory / "windows.txt"
        path.write_bytes(codecs.BOM_UTF8 + b"one\r\ntwo\r\n")
        document = inedit.load_document(path)
        self.assertEqual(document.text, "one\ntwo\n")
        self.assertIs(document.newline_style, inedit.NewlineStyle.CRLF)
        self.assertTrue(document.has_bom)

        inedit.save_document(document, "changed\n")
        self.assertEqual(path.read_bytes(), codecs.BOM_UTF8 + b"changed\r\n")

    def test_file_without_final_newline_stays_without_one(self) -> None:
        path = self.directory / "no-final-newline.txt"
        path.write_bytes(b"one\ntwo")
        document = inedit.load_document(path)
        self.assertEqual(document.text, "one\ntwo")
        inedit.save_document(document, document.text)
        self.assertEqual(path.read_bytes(), b"one\ntwo")

    def test_invalid_input_is_rejected(self) -> None:
        cases = {
            "nul": b"one\0two",
            "invalid UTF-8": b"\xff",
            "bare CR": b"one\rtwo",
            "mixed newlines": b"one\r\ntwo\n",
        }
        for name, data in cases.items():
            with self.subTest(name=name):
                path = self.directory / name
                path.write_bytes(data)
                with self.assertRaises(inedit.LoadError):
                    inedit.load_document(path)

    def test_directory_is_rejected(self) -> None:
        with self.assertRaisesRegex(inedit.LoadError, "not a regular file"):
            inedit.load_document(self.directory)

    def test_existing_symlink_is_preserved_when_target_is_saved(self) -> None:
        target = self.directory / "target.txt"
        link = self.directory / "link.txt"
        target.write_text("old", encoding="utf-8")
        link.symlink_to(target.name)
        link_inode = link.lstat().st_ino

        document = inedit.load_document(link)
        inedit.save_document(document, "new")

        self.assertTrue(link.is_symlink())
        self.assertEqual(link.lstat().st_ino, link_inode)
        self.assertEqual(target.read_text(encoding="utf-8"), "new")

    def test_dangling_symlink_creates_its_target_without_replacing_link(self) -> None:
        target = self.directory / "future.txt"
        link = self.directory / "link.txt"
        link.symlink_to(target.name)
        link_inode = link.lstat().st_ino

        document = inedit.load_document(link)
        self.assertEqual(document.text, "")
        inedit.save_document(document, "created")

        self.assertTrue(link.is_symlink())
        self.assertEqual(link.lstat().st_ino, link_inode)
        self.assertEqual(target.read_text(encoding="utf-8"), "created")

    def test_existing_permission_bits_are_preserved(self) -> None:
        path = self.directory / "mode.txt"
        path.write_text("old", encoding="utf-8")
        path.chmod(0o640)
        document = inedit.load_document(path)

        inedit.save_document(document, "new")

        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o640)

    def test_external_content_change_is_a_conflict(self) -> None:
        path = self.directory / "conflict.txt"
        path.write_text("original", encoding="utf-8")
        document = inedit.load_document(path)
        path.write_text("external change", encoding="utf-8")

        with self.assertRaises(inedit.ConflictError):
            inedit.save_document(document, "editor change")
        self.assertEqual(path.read_text(encoding="utf-8"), "external change")

    def test_replaced_inode_is_a_conflict(self) -> None:
        path = self.directory / "conflict.txt"
        replacement = self.directory / "replacement.txt"
        path.write_text("same size", encoding="utf-8")
        document = inedit.load_document(path)
        replacement.write_text("same size", encoding="utf-8")
        os.replace(replacement, path)

        with self.assertRaises(inedit.ConflictError):
            inedit.save_document(document, "editor change")

    def test_new_target_appearing_is_a_conflict(self) -> None:
        path = self.directory / "new.txt"
        document = inedit.load_document(path)
        path.write_text("external", encoding="utf-8")

        with self.assertRaises(inedit.ConflictError):
            inedit.save_document(document, "editor change")
        self.assertEqual(path.read_text(encoding="utf-8"), "external")

    def test_symlink_retargeting_is_a_conflict(self) -> None:
        first = self.directory / "first.txt"
        second = self.directory / "second.txt"
        link = self.directory / "link.txt"
        first.write_text("first", encoding="utf-8")
        second.write_text("second", encoding="utf-8")
        link.symlink_to(first.name)
        document = inedit.load_document(link)
        link.unlink()
        link.symlink_to(second.name)

        with self.assertRaises(inedit.ConflictError):
            inedit.save_document(document, "editor change")
        self.assertEqual(first.read_text(encoding="utf-8"), "first")
        self.assertEqual(second.read_text(encoding="utf-8"), "second")

    def test_failed_replace_cleans_up_temporary_sibling(self) -> None:
        path = self.directory / "failure.txt"
        path.write_text("original", encoding="utf-8")
        document = inedit.load_document(path)

        with mock.patch("inedit.os.replace", side_effect=OSError("injected")):
            with self.assertRaises(inedit.SaveError):
                inedit.save_document(document, "changed")

        self.assertEqual(path.read_text(encoding="utf-8"), "original")
        self.assertEqual(list(self.directory.glob(".inedit-*.tmp")), [])

    def test_invalid_buffer_content_does_not_touch_target(self) -> None:
        path = self.directory / "safe.txt"
        path.write_text("original", encoding="utf-8")
        document = inedit.load_document(path)

        for text in ("nul\0", "bare\r", "surrogate\ud800"):
            with self.subTest(text=repr(text)), self.assertRaises(inedit.SaveError):
                inedit.save_document(document, text)
        self.assertEqual(path.read_text(encoding="utf-8"), "original")


class LayoutAndStateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.directory = Path(self.temporary_directory.name)

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def options(self, path: Path, *, vi: bool = False) -> inedit.EditorOptions:
        return inedit.EditorOptions(path, 8, vi, True)

    def run_editor(
        self, path: Path, keys: str, *, vi: bool = False
    ) -> tuple[inedit.EditorResult, inedit.BuiltEditor]:
        document = inedit.load_document(path)
        with create_pipe_input() as pipe:
            editor = inedit.build_application(
                document,
                self.options(path, vi=vi),
                input=pipe,
                output=DummyOutput(),
            )
            pipe.send_text(keys)
            result = editor.application.run(set_exception_handler=False)
        return result, editor

    def test_effective_height_caps_and_rejects_tiny_terminal(self) -> None:
        self.assertEqual(inedit.effective_height(20, 24), 20)
        self.assertEqual(inedit.effective_height(20, 10), 9)
        with self.assertRaises(inedit.TerminalError):
            inedit.effective_height(20, 4)

    def test_application_is_inline_and_erased_when_done(self) -> None:
        path = self.directory / "new.txt"
        document = inedit.load_document(path)
        with create_pipe_input() as pipe:
            editor = inedit.build_application(
                document,
                self.options(path),
                input=pipe,
                output=DummyOutput(),
            )
            self.assertFalse(editor.application.full_screen)
            self.assertTrue(editor.application.erase_when_done)

    def test_status_truncates_filename_from_left(self) -> None:
        path = self.directory / ("very-long-name-" * 8)
        document = inedit.load_document(path)
        state = inedit.EditorState(document, "")
        status_line = inedit.format_status(state, "", 0, 0, 80)
        self.assertTrue(status_line.startswith("…"))
        self.assertIn("Ln 1, Col 1", status_line)
        self.assertIn("^G Help", status_line)
        self.assertTrue(status_line.endswith("^S Save | ^C Cancel"))

        state.help_visible = True
        help_status = inedit.format_status(state, "", 0, 0, 80)
        self.assertIn("^G Close", help_status)

    def test_multiline_edit_saves(self) -> None:
        path = self.directory / "new.txt"
        result, _editor = self.run_editor(path, "hello\nworld\x13")
        self.assertIs(result.reason, inedit.ExitReason.SAVED)
        self.assertEqual(path.read_bytes(), b"hello\nworld")

    def test_unchanged_save_does_not_replace_file(self) -> None:
        path = self.directory / "unchanged.txt"
        path.write_text("unchanged", encoding="utf-8")
        before = path.stat()
        result, _editor = self.run_editor(path, "\x13")
        after = path.stat()
        self.assertIs(result.reason, inedit.ExitReason.SAVED)
        self.assertEqual(
            (after.st_ino, after.st_mtime_ns), (before.st_ino, before.st_mtime_ns)
        )

    def test_unchanged_cancel_is_immediate(self) -> None:
        path = self.directory / "unchanged.txt"
        path.write_text("unchanged", encoding="utf-8")
        result, _editor = self.run_editor(path, "\x03")
        self.assertIs(result.reason, inedit.ExitReason.CANCELED)
        self.assertEqual(path.read_text(encoding="utf-8"), "unchanged")

    def test_modified_cancel_requires_two_ctrl_c_presses(self) -> None:
        path = self.directory / "unchanged.txt"
        path.write_text("unchanged", encoding="utf-8")
        result, editor = self.run_editor(path, "x\x03\x03")
        self.assertIs(result.reason, inedit.ExitReason.CANCELED)
        self.assertEqual(editor.state.message, inedit.DISCARD_MESSAGE)
        self.assertEqual(path.read_text(encoding="utf-8"), "unchanged")

    def test_edit_disarms_discard_confirmation(self) -> None:
        path = self.directory / "new.txt"
        result, _editor = self.run_editor(path, "x\x03y\x03\x13")
        self.assertIs(result.reason, inedit.ExitReason.SAVED)
        self.assertEqual(path.read_text(encoding="utf-8"), "xy")

    def test_ctrl_z_and_alt_e_undo_and_redo(self) -> None:
        path = self.directory / "new.txt"
        result, _editor = self.run_editor(path, "x\x1a\x1be\x13")
        self.assertIs(result.reason, inedit.ExitReason.SAVED)
        self.assertEqual(path.read_text(encoding="utf-8"), "x")

    def test_alt_u_retains_prompt_toolkit_uppercase_word(self) -> None:
        path = self.directory / "word.txt"
        path.write_text("word", encoding="utf-8")
        result, _editor = self.run_editor(path, "\x1bu\x13")
        self.assertIs(result.reason, inedit.ExitReason.SAVED)
        self.assertEqual(path.read_text(encoding="utf-8"), "WORD")

    def test_ctrl_g_toggles_help_without_changing_the_buffer(self) -> None:
        path = self.directory / "new.txt"
        result, editor = self.run_editor(path, "x\x07\x07y\x13")
        self.assertIs(result.reason, inedit.ExitReason.SAVED)
        self.assertEqual(path.read_text(encoding="utf-8"), "xy")
        self.assertFalse(editor.state.help_visible)
        self.assertIn("Ctrl-Y", editor.help_area.buffer.text)
        self.assertTrue(editor.help_area.buffer.read_only())

    def test_ctrl_w_cuts_a_region_and_ctrl_y_yanks_it(self) -> None:
        path = self.directory / "region.txt"
        path.write_text("alpha beta", encoding="utf-8")
        keys = "\x00" + "\x06" * 5 + "\x17\x05\x19\x13"
        result, editor = self.run_editor(path, keys)
        self.assertIs(result.reason, inedit.ExitReason.SAVED)
        self.assertEqual(path.read_text(encoding="utf-8"), " betaalpha")
        self.assertEqual(editor.application.clipboard.get_data().text, "alpha")

    def test_alt_w_copies_a_region_and_ctrl_y_yanks_it(self) -> None:
        path = self.directory / "region.txt"
        path.write_text("alpha beta", encoding="utf-8")
        keys = "\x00" + "\x06" * 5 + "\x1bw\x05\x19\x13"
        result, _editor = self.run_editor(path, keys)
        self.assertIs(result.reason, inedit.ExitReason.SAVED)
        self.assertEqual(path.read_text(encoding="utf-8"), "alpha betaalpha")

    def test_ctrl_k_uses_prompt_toolkit_kill_to_end_of_line(self) -> None:
        path = self.directory / "lines.txt"
        path.write_text("one\ntwo", encoding="utf-8")
        result, editor = self.run_editor(path, "\x06\x06\x0b\x19!\x13")
        self.assertIs(result.reason, inedit.ExitReason.SAVED)
        self.assertEqual(path.read_text(encoding="utf-8"), "one!\ntwo")
        self.assertEqual(editor.application.clipboard.get_data().text, "e")

    def test_ctrl_u_uses_prompt_toolkit_kill_to_start_of_line(self) -> None:
        path = self.directory / "lines.txt"
        path.write_text("one\ntwo", encoding="utf-8")
        result, editor = self.run_editor(path, "\x06\x06\x15\x19!\x13")
        self.assertIs(result.reason, inedit.ExitReason.SAVED)
        self.assertEqual(path.read_text(encoding="utf-8"), "on!e\ntwo")
        self.assertEqual(editor.application.clipboard.get_data().text, "on")

    def test_internal_clipboard_retains_only_the_latest_entry(self) -> None:
        path = self.directory / "new.txt"
        document = inedit.load_document(path)
        with create_pipe_input() as pipe:
            editor = inedit.build_application(
                document,
                self.options(path),
                input=pipe,
                output=DummyOutput(),
            )
            editor.application.clipboard.set_text("first")
            editor.application.clipboard.set_text("second")
            editor.application.clipboard.rotate()
            self.assertEqual(
                editor.application.clipboard.get_data().text,
                "second",
            )

    def test_save_conflict_keeps_editor_open_for_cancel(self) -> None:
        path = self.directory / "new.txt"
        document = inedit.load_document(path)
        path.write_text("external", encoding="utf-8")
        with create_pipe_input() as pipe:
            editor = inedit.build_application(
                document,
                self.options(path),
                input=pipe,
                output=DummyOutput(),
            )
            pipe.send_text("x\x13\x03\x03")
            result = editor.application.run(set_exception_handler=False)
        self.assertIs(result.reason, inedit.ExitReason.CANCELED)
        self.assertEqual(path.read_text(encoding="utf-8"), "external")

    def test_tiny_resized_terminal_exits_with_error(self) -> None:
        class TinyOutput(DummyOutput):
            def get_size(self) -> Size:
                return Size(rows=4, columns=80)

        path = self.directory / "new.txt"
        document = inedit.load_document(path)
        with create_pipe_input() as pipe:
            editor = inedit.build_application(
                document,
                self.options(path),
                input=pipe,
                output=TinyOutput(),
            )
            result = editor.application.run(set_exception_handler=False)
        self.assertIs(result.reason, inedit.ExitReason.ERROR)
        self.assertIn("at least 5", result.message or "")

    def test_global_save_binding_works_in_vi_mode(self) -> None:
        path = self.directory / "existing.txt"
        path.write_text("same", encoding="utf-8")
        result, _editor = self.run_editor(path, "\x13", vi=True)
        self.assertIs(result.reason, inedit.ExitReason.SAVED)


@unittest.skipUnless(os.name == "posix", "PTY integration tests require POSIX")
class PtyIntegrationTests(unittest.TestCase):
    alternate_screen_entries = (
        b"\x1b[?1049h",
        b"\x1b[?1047h",
        b"\x1b[?47h",
    )

    def run_pty_case(
        self, initial: bytes | None, action: str
    ) -> tuple[int, bytes, bytes | None, bool]:
        import fcntl
        import pty
        import select
        import signal
        import struct
        import subprocess
        import sys
        import termios
        import time

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "edit.txt"
            if initial is not None:
                path.write_bytes(initial)

            master, slave = pty.openpty()
            fcntl.ioctl(
                slave,
                termios.TIOCSWINSZ,
                struct.pack("HHHH", 24, 80, 0, 0),
            )
            attributes_before = termios.tcgetattr(slave)
            os.write(slave, b"SENTINEL-ABOVE\r\n")
            environment = dict(os.environ)
            environment["PYTHONDONTWRITEBYTECODE"] = "1"
            process = subprocess.Popen(
                [
                    sys.executable,
                    str(Path(inedit.__file__).resolve()),
                    "--height",
                    "8",
                    str(path),
                ],
                stdin=slave,
                stdout=slave,
                stderr=slave,
                cwd=Path(inedit.__file__).parent,
                env=environment,
                close_fds=True,
            )
            captured = bytearray()
            answered_cursor_position_request = False

            def read_until(marker: bytes) -> None:
                nonlocal answered_cursor_position_request
                deadline = time.monotonic() + 5
                while marker not in captured and time.monotonic() < deadline:
                    readable, _, _ = select.select([master], [], [], 0.1)
                    if readable:
                        output = os.read(master, 65536)
                        captured.extend(output)
                        if (
                            not answered_cursor_position_request
                            and b"\x1b[6n" in output
                        ):
                            os.write(master, b"\x1b[2;1R")
                            answered_cursor_position_request = True
                self.assertIn(marker, captured)

            try:
                read_until(b"^S Save")
                if action == "save":
                    os.write(master, b"alpha\nbeta\x13")
                elif action == "cancel":
                    os.write(master, b"x\x03\x03")
                elif action == "help":
                    os.write(master, b"\x07")
                    read_until(b"inedit help")
                    read_until(b"Close this help")
                    os.write(master, b"\x07\x13")
                elif action == "sigint":
                    os.write(master, b"x")
                    read_until(b"| modifi")
                    process.send_signal(signal.SIGINT)
                    read_until(b"Unsaved ch")
                    process.send_signal(signal.SIGINT)
                elif action == "sigterm":
                    process.send_signal(signal.SIGTERM)
                else:
                    self.fail(f"unknown PTY action: {action}")

                returncode = process.wait(timeout=5)
                while True:
                    readable, _, _ = select.select([master], [], [], 0)
                    if not readable:
                        break
                    try:
                        captured.extend(os.read(master, 65536))
                    except OSError:
                        break
                attributes_after = termios.tcgetattr(slave)
                final_bytes = path.read_bytes() if path.exists() else None
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait(timeout=5)
                os.close(master)
                os.close(slave)

        return (
            returncode,
            bytes(captured),
            final_bytes,
            attributes_before == attributes_after,
        )

    def assert_rendering_contract(self, output: bytes, restored: bool) -> None:
        self.assertIn(b"SENTINEL-ABOVE", output)
        for sequence in self.alternate_screen_entries:
            self.assertNotIn(sequence, output)
        self.assertTrue(restored)

    def test_save_and_cancel_in_a_real_pty(self) -> None:
        saved = self.run_pty_case(None, "save")
        canceled = self.run_pty_case(b"original", "cancel")

        self.assertEqual(saved[0], 0)
        self.assertEqual(saved[2], b"alpha\nbeta")
        self.assertEqual(canceled[0], 130)
        self.assertEqual(canceled[2], b"original")
        self.assert_rendering_contract(saved[1], saved[3])
        self.assert_rendering_contract(canceled[1], canceled[3])

    def test_signals_do_not_save_and_restore_the_terminal(self) -> None:
        interrupted = self.run_pty_case(b"original", "sigint")
        terminated = self.run_pty_case(b"original", "sigterm")

        self.assertEqual(interrupted[0], 130)
        self.assertEqual(interrupted[2], b"original")
        self.assertEqual(terminated[0], 1)
        self.assertEqual(terminated[2], b"original")
        self.assert_rendering_contract(interrupted[1], interrupted[3])
        self.assert_rendering_contract(terminated[1], terminated[3])

    def test_help_renders_inline_and_returns_to_the_editor(self) -> None:
        helped = self.run_pty_case(b"original", "help")

        self.assertEqual(helped[0], 0)
        self.assertEqual(helped[2], b"original")
        self.assertIn(b"inedit help", helped[1])
        self.assertIn(b"Close this help", helped[1])
        self.assert_rendering_contract(helped[1], helped[3])


if __name__ == "__main__":
    unittest.main()
