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

    def test_returned_snapshot_allows_repeated_saves_through_symlink(self) -> None:
        target = self.directory / "target.txt"
        target.write_text("original", encoding="utf-8")
        link = self.directory / "link.txt"
        link.symlink_to(target.name)

        document = inedit.load_document(link)
        document = inedit.save_document(document, "first")
        document = inedit.save_document(document, "second")

        self.assertTrue(link.is_symlink())
        self.assertEqual(target.read_text(encoding="utf-8"), "second")
        self.assertEqual(document.text, "second")

    def test_returned_snapshot_handles_symlinked_parent_directory(self) -> None:
        real_directory = self.directory / "real"
        real_directory.mkdir()
        alias_directory = self.directory / "alias"
        alias_directory.symlink_to(real_directory.name, target_is_directory=True)
        path = alias_directory / "file.txt"
        path.write_text("original", encoding="utf-8")

        document = inedit.load_document(path)
        document = inedit.save_document(document, "first")
        document = inedit.save_document(document, "second")

        self.assertEqual(path.read_text(encoding="utf-8"), "second")
        self.assertEqual(document.text, "second")


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
        self.assertTrue(status_line.endswith("^S Save | ^X/^C Exit"))

        state.help_visible = True
        help_status = inedit.format_status(state, "", 0, 0, 80)
        self.assertIn("^G Close", help_status)

        state.exit_prompt = True
        prompt_status = inedit.format_status(state, "changed", 0, 0, 80)
        self.assertEqual(prompt_status, inedit.EXIT_PROMPT)

    def test_ctrl_s_saves_without_exiting(self) -> None:
        path = self.directory / "new.txt"
        result, _editor = self.run_editor(path, "hello\nworld\x13!\x18y")
        self.assertIs(result.reason, inedit.ExitReason.SAVED)
        self.assertEqual(path.read_bytes(), b"hello\nworld!")

    def test_unchanged_save_does_not_replace_file(self) -> None:
        path = self.directory / "unchanged.txt"
        path.write_text("unchanged", encoding="utf-8")
        before = path.stat()
        result, _editor = self.run_editor(path, "\x13\x18")
        after = path.stat()
        self.assertIs(result.reason, inedit.ExitReason.SAVED)
        self.assertEqual(
            (after.st_ino, after.st_mtime_ns), (before.st_ino, before.st_mtime_ns)
        )

    def test_unchanged_ctrl_c_exits_successfully(self) -> None:
        path = self.directory / "unchanged.txt"
        path.write_text("unchanged", encoding="utf-8")
        result, _editor = self.run_editor(path, "\x03")
        self.assertIs(result.reason, inedit.ExitReason.SAVED)
        self.assertEqual(path.read_text(encoding="utf-8"), "unchanged")

    def test_ctrl_c_saves_modified_buffer_on_yes(self) -> None:
        path = self.directory / "new.txt"
        result, _editor = self.run_editor(path, "hello\x03y")
        self.assertIs(result.reason, inedit.ExitReason.SAVED)
        self.assertEqual(path.read_text(encoding="utf-8"), "hello")

    def test_second_ctrl_c_cancels_prompt_instead_of_discarding(self) -> None:
        path = self.directory / "new.txt"
        result, _editor = self.run_editor(path, "x\x03\x03y\x13\x18")
        self.assertIs(result.reason, inedit.ExitReason.SAVED)
        self.assertEqual(path.read_text(encoding="utf-8"), "xy")

    def test_ctrl_z_and_alt_e_undo_and_redo(self) -> None:
        path = self.directory / "new.txt"
        result, _editor = self.run_editor(path, "x\x1a\x1be\x13\x18")
        self.assertIs(result.reason, inedit.ExitReason.SAVED)
        self.assertEqual(path.read_text(encoding="utf-8"), "x")

    def test_right_arrow_crosses_to_start_of_next_line(self) -> None:
        path = self.directory / "lines.txt"
        path.write_text("one\ntwo", encoding="utf-8")
        result, _editor = self.run_editor(path, "\x05\x1b[Cx\x13\x18")
        self.assertIs(result.reason, inedit.ExitReason.SAVED)
        self.assertEqual(path.read_text(encoding="utf-8"), "one\nxtwo")

    def test_left_arrow_crosses_to_end_of_previous_line(self) -> None:
        path = self.directory / "lines.txt"
        path.write_text("one\ntwo", encoding="utf-8")
        result, _editor = self.run_editor(path, "\x0e\x1b[Dx\x13\x18")
        self.assertIs(result.reason, inedit.ExitReason.SAVED)
        self.assertEqual(path.read_text(encoding="utf-8"), "onex\ntwo")

    def test_right_arrow_crosses_lines_in_vi_insert_mode(self) -> None:
        path = self.directory / "lines.txt"
        path.write_text("one\ntwo", encoding="utf-8")
        result, _editor = self.run_editor(
            path, "\x1b[F\x1b[Cx\x13\x18", vi=True
        )
        self.assertIs(result.reason, inedit.ExitReason.SAVED)
        self.assertEqual(path.read_text(encoding="utf-8"), "one\nxtwo")

    def test_alt_q_fills_the_current_paragraph(self) -> None:
        path = self.directory / "paragraph.txt"
        text = (
            "one two three four five six seven eight nine ten eleven twelve "
            "thirteen fourteen fifteen sixteen"
        )
        path.write_text(text, encoding="utf-8")
        result, _editor = self.run_editor(path, "\x1bq\x13\x18")
        self.assertIs(result.reason, inedit.ExitReason.SAVED)
        self.assertEqual(
            path.read_text(encoding="utf-8"),
            "one two three four five six seven eight nine ten eleven twelve "
            "thirteen fourteen\nfifteen sixteen\n",
        )

    def test_alt_q_only_reflows_the_paragraph_under_the_cursor(self) -> None:
        path = self.directory / "paragraphs.txt"
        text = (
            "first paragraph line\n"
            "\n"
            "second paragraph has many more words that certainly exceed the "
            "eighty column limit for wrapping purposes today and then some "
            "more words after that"
        )
        path.write_text(text, encoding="utf-8")
        result, _editor = self.run_editor(path, "\x0e\x0e\x1bq\x13\x18")
        self.assertIs(result.reason, inedit.ExitReason.SAVED)
        self.assertEqual(
            path.read_text(encoding="utf-8"),
            "first paragraph line\n"
            "\n"
            "second paragraph has many more words that certainly exceed the "
            "eighty column\nlimit for wrapping purposes today and then some "
            "more words after that\n",
        )

    def test_alt_q_fill_is_a_single_undo_step(self) -> None:
        path = self.directory / "paragraph.txt"
        text = (
            "one two three four five six seven eight nine ten eleven twelve "
            "thirteen fourteen fifteen sixteen"
        )
        path.write_text(text, encoding="utf-8")
        result, _editor = self.run_editor(path, "\x1bq\x1a\x13\x18")
        self.assertIs(result.reason, inedit.ExitReason.SAVED)
        self.assertEqual(path.read_text(encoding="utf-8"), text)

    def test_alt_u_retains_prompt_toolkit_uppercase_word(self) -> None:
        path = self.directory / "word.txt"
        path.write_text("word", encoding="utf-8")
        result, _editor = self.run_editor(path, "\x1bu\x13\x18")
        self.assertIs(result.reason, inedit.ExitReason.SAVED)
        self.assertEqual(path.read_text(encoding="utf-8"), "WORD")

    def test_ctrl_g_toggles_help_without_changing_the_buffer(self) -> None:
        path = self.directory / "new.txt"
        result, editor = self.run_editor(path, "x\x07\x07y\x13\x18")
        self.assertIs(result.reason, inedit.ExitReason.SAVED)
        self.assertEqual(path.read_text(encoding="utf-8"), "xy")
        self.assertFalse(editor.state.help_visible)
        self.assertIn("Ctrl-Y", editor.help_area.buffer.text)
        self.assertTrue(editor.help_area.buffer.read_only())

    def test_ctrl_w_cuts_a_region_and_ctrl_y_yanks_it(self) -> None:
        path = self.directory / "region.txt"
        path.write_text("alpha beta", encoding="utf-8")
        keys = "\x00" + "\x06" * 5 + "\x17\x05\x19\x13\x18"
        result, editor = self.run_editor(path, keys)
        self.assertIs(result.reason, inedit.ExitReason.SAVED)
        self.assertEqual(path.read_text(encoding="utf-8"), " betaalpha")
        self.assertEqual(editor.application.clipboard.get_data().text, "alpha")

    def test_alt_w_copies_a_region_and_ctrl_y_yanks_it(self) -> None:
        path = self.directory / "region.txt"
        path.write_text("alpha beta", encoding="utf-8")
        keys = "\x00" + "\x06" * 5 + "\x1bw\x05\x19\x13\x18"
        result, _editor = self.run_editor(path, keys)
        self.assertIs(result.reason, inedit.ExitReason.SAVED)
        self.assertEqual(path.read_text(encoding="utf-8"), "alpha betaalpha")

    def test_ctrl_k_uses_prompt_toolkit_kill_to_end_of_line(self) -> None:
        path = self.directory / "lines.txt"
        path.write_text("one\ntwo", encoding="utf-8")
        result, editor = self.run_editor(path, "\x06\x06\x0b\x19!\x13\x18")
        self.assertIs(result.reason, inedit.ExitReason.SAVED)
        self.assertEqual(path.read_text(encoding="utf-8"), "one!\ntwo")
        self.assertEqual(editor.application.clipboard.get_data().text, "e")

    def test_ctrl_u_uses_prompt_toolkit_kill_to_start_of_line(self) -> None:
        path = self.directory / "lines.txt"
        path.write_text("one\ntwo", encoding="utf-8")
        result, editor = self.run_editor(path, "\x06\x06\x15\x19!\x13\x18")
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
            pipe.send_text("x\x13\x03n")
            result = editor.application.run(set_exception_handler=False)
        self.assertIs(result.reason, inedit.ExitReason.CANCELED)
        self.assertEqual(path.read_text(encoding="utf-8"), "external")

    def test_ctrl_x_saves_modified_buffer_on_yes(self) -> None:
        path = self.directory / "new.txt"
        result, editor = self.run_editor(path, "hello\x18y")
        self.assertIs(result.reason, inedit.ExitReason.SAVED)
        self.assertFalse(editor.state.exit_prompt)
        self.assertEqual(path.read_text(encoding="utf-8"), "hello")

    def test_ctrl_x_discards_modified_buffer_on_no(self) -> None:
        path = self.directory / "existing.txt"
        path.write_text("original", encoding="utf-8")
        result, _editor = self.run_editor(path, "x\x18n")
        self.assertIs(result.reason, inedit.ExitReason.CANCELED)
        self.assertEqual(path.read_text(encoding="utf-8"), "original")

    def test_ctrl_c_returns_from_exit_prompt_to_editing(self) -> None:
        path = self.directory / "new.txt"
        result, _editor = self.run_editor(path, "x\x18q\x03y\x13\x18")
        self.assertIs(result.reason, inedit.ExitReason.SAVED)
        self.assertEqual(path.read_text(encoding="utf-8"), "xy")

    def test_repeated_ctrl_s_refreshes_the_conflict_snapshot(self) -> None:
        path = self.directory / "new.txt"
        result, _editor = self.run_editor(path, "one\x13two\x13\x18")
        self.assertIs(result.reason, inedit.ExitReason.SAVED)
        self.assertEqual(path.read_text(encoding="utf-8"), "onetwo")

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
        result, _editor = self.run_editor(path, "\x13\x18", vi=True)
        self.assertIs(result.reason, inedit.ExitReason.SAVED)


@unittest.skipUnless(os.name == "posix", "PTY integration tests require POSIX")
class PtyIntegrationTests(unittest.TestCase):
    alternate_screen_entries = (
        b"\x1b[?1049h",
        b"\x1b[?1047h",
        b"\x1b[?47h",
    )

    @staticmethod
    def terminal_updates_contain(output: bytes, expected: bytes) -> bool:
        """Match text in a VT update stream, including retained screen cells."""
        operations: list[tuple[str, int]] = []
        index = 0
        while index < len(output):
            if output[index : index + 2] == b"\x1b[":
                end = index + 2
                while end < len(output) and not 0x40 <= output[end] <= 0x7E:
                    end += 1
                if end == len(output):
                    break
                final = output[end]
                parameters = output[index + 2 : end]
                if final == ord("C"):
                    try:
                        count = int(parameters.split(b";", 1)[0] or b"1")
                    except ValueError:
                        count = 1
                    operations.append(("forward", count))
                elif final not in b"mhl":
                    operations.append(("break", 0))
                index = end + 1
                continue
            byte = output[index]
            if 0x20 <= byte <= 0xFF and byte != 0x7F:
                operations.append(("character", byte))
            elif byte in b"\r\n\b":
                operations.append(("break", 0))
            index += 1

        for start, operation in enumerate(operations):
            if operation != ("character", expected[0]):
                continue
            expected_index = 0
            for kind, value in operations[start:]:
                if kind == "break":
                    break
                if kind == "forward":
                    expected_index += value
                    if expected_index > len(expected):
                        break
                elif expected_index >= len(expected) or value != expected[expected_index]:
                    break
                else:
                    expected_index += 1
                if expected_index == len(expected):
                    return True
        return False

    def test_terminal_update_matcher_accepts_retained_screen_cells(self) -> None:
        expected = b"Save modified buffer?"
        incremental_update = b"Save mod\x1b[Cfied buffer?"

        self.assertTrue(
            self.terminal_updates_contain(incremental_update, expected)
        )
        self.assertFalse(
            self.terminal_updates_contain(b"Save\x1b[79C", expected)
        )

    def run_pty_case(
        self,
        initial: bytes | None,
        action: str,
        *,
        sentinel: bytes = b"SENTINEL-ABOVE\r\n",
        cursor_position_response: bytes = b"\x1b[2;1R",
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
            os.write(slave, sentinel)
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
            cursor_position_requests_answered = 0

            def read_until(marker: bytes) -> None:
                nonlocal cursor_position_requests_answered
                deadline = time.monotonic() + 5
                while (
                    not self.terminal_updates_contain(captured, marker)
                    and time.monotonic() < deadline
                ):
                    readable, _, _ = select.select([master], [], [], 0.1)
                    if readable:
                        output = os.read(master, 65536)
                        captured.extend(output)
                        total_requests = bytes(captured).count(b"\x1b[6n")
                        while cursor_position_requests_answered < total_requests:
                            os.write(master, cursor_position_response)
                            cursor_position_requests_answered += 1
                self.assertTrue(
                    self.terminal_updates_contain(captured, marker),
                    f"{marker!r} was not rendered by terminal updates {captured!r}",
                )

            try:
                read_until(b"^S Save")
                if action == "save":
                    os.write(master, b"alpha\nbeta\x13\x18")
                elif action == "cancel":
                    os.write(master, b"x\x03n")
                elif action == "help":
                    os.write(master, b"\x07")
                    read_until(b"inedit help")
                    read_until(b"Close this help")
                    os.write(master, b"\x07\x13\x18")
                elif action == "exit_prompt":
                    os.write(master, b"x\x18")
                    read_until(b"Save modified buffer?")
                    os.write(master, b"\x03\x13\x18")
                elif action == "sigint":
                    os.write(master, b"x")
                    read_until(b"| modifi")
                    process.send_signal(signal.SIGINT)
                    time.sleep(0.2)
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

    def test_ctrl_x_prompt_renders_inline_and_can_be_canceled(self) -> None:
        prompted = self.run_pty_case(b"original", "exit_prompt")

        self.assertEqual(prompted[0], 0)
        self.assertEqual(prompted[2], b"xoriginal")
        self.assertTrue(
            self.terminal_updates_contain(
                prompted[1], b"Save modified buffer?"
            )
        )
        self.assert_rendering_contract(prompted[1], prompted[3])

    def test_editor_breaks_the_line_when_invoked_mid_row(self) -> None:
        """Reproduces git's GIT_EDITOR hint, which ends without a newline."""
        hint = b"hint: Waiting for your editor to close the file... "
        result = self.run_pty_case(
            b"original",
            "cancel",
            sentinel=hint,
            cursor_position_response=b"\x1b[1;53R",
        )
        self.assertEqual(result[0], 130)
        self.assertEqual(result[2], b"original")

        output = result[1]
        first_request = output.index(b"\x1b[6n")
        second_request = output.index(b"\x1b[6n", first_request + 1)
        gap = output[first_request + len(b"\x1b[6n") : second_request]
        # The pty's ONLCR output processing may double the carriage return
        # (turning our explicit "\r\n" into "\r\r\n"); either is a fresh line.
        self.assertIn(gap, (b"\r\n", b"\r\r\n"))

    def test_editor_skips_the_newline_when_already_at_column_one(self) -> None:
        hint = b"hint: Waiting for your editor to close the file... "
        result = self.run_pty_case(
            b"original",
            "cancel",
            sentinel=hint,
            cursor_position_response=b"\x1b[1;1R",
        )
        self.assertEqual(result[0], 130)
        self.assertEqual(result[2], b"original")

        output = result[1]
        first_request = output.index(b"\x1b[6n")
        second_request = output.index(b"\x1b[6n", first_request + 1)
        gap = output[first_request + len(b"\x1b[6n") : second_request]
        self.assertEqual(gap, b"")

    def run_external_editor_case(
        self, *, editor_text: str, exit_code: int
    ) -> tuple[bytes, int, str]:
        import fcntl
        import pty
        import select
        import struct
        import subprocess
        import sys
        import termios
        import time

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "edit.txt"
            path.write_text("original", encoding="utf-8")

            fake_editor = Path(directory) / "fake_editor.sh"
            fake_editor.write_text(
                "#!/bin/sh\n"
                'printf "%s" "$FAKE_EDITOR_TEXT" > "$1"\n'
                'exit "$FAKE_EDITOR_EXIT_CODE"\n'
            )
            fake_editor.chmod(0o755)

            master, slave = pty.openpty()
            fcntl.ioctl(
                slave, termios.TIOCSWINSZ, struct.pack("HHHH", 24, 80, 0, 0)
            )
            environment = dict(os.environ)
            environment["PYTHONDONTWRITEBYTECODE"] = "1"
            environment.pop("VISUAL", None)
            environment["EDITOR"] = str(fake_editor)
            environment["FAKE_EDITOR_TEXT"] = editor_text
            environment["FAKE_EDITOR_EXIT_CODE"] = str(exit_code)

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

            def read_until(marker: bytes) -> None:
                deadline = time.monotonic() + 5
                while (
                    not self.terminal_updates_contain(captured, marker)
                    and time.monotonic() < deadline
                ):
                    readable, _, _ = select.select([master], [], [], 0.1)
                    if readable:
                        captured.extend(os.read(master, 65536))
                self.assertTrue(
                    self.terminal_updates_contain(captured, marker),
                    f"{marker!r} was not rendered by terminal updates {captured!r}",
                )

            try:
                read_until(b"^S Save")
                os.write(master, b"\x1bv")
                if exit_code == 0:
                    read_until(b"Applied external edit")
                    os.write(master, b"\x13\x18")
                else:
                    # The status line truncates long messages, so match a
                    # prefix that survives truncation.
                    read_until(b"External editor exit")
                    os.write(master, b"\x03")
                returncode = process.wait(timeout=5)
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait(timeout=5)
                os.close(master)
                os.close(slave)

            return bytes(captured), returncode, path.read_text(encoding="utf-8")

    def test_alt_v_applies_external_editor_changes(self) -> None:
        _output, returncode, text = self.run_external_editor_case(
            editor_text="edited by external tool", exit_code=0
        )
        self.assertEqual(returncode, 0)
        self.assertEqual(text, "edited by external tool")

    def test_alt_v_discards_changes_when_external_editor_fails(self) -> None:
        _output, returncode, text = self.run_external_editor_case(
            editor_text="should not appear", exit_code=7
        )
        self.assertEqual(returncode, 0)
        self.assertEqual(text, "original")


if __name__ == "__main__":
    unittest.main()
