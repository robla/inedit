# `inedit.py` roadmap

## Status

The first version of `inedit.py` is implemented as a minimal text editor for
short-lived files such as the temporary directory-stack file created by
`dsedit`. Its defining behavior is a fixed-height editor rendered below the
shell prompt without switching to the terminal's alternate screen.

The implementation uses **prompt_toolkit**, not raw terminal escape sequences.
On the environment inspected on July 16, 2026, Python 3.11.2 and
prompt_toolkit 3.0.36 are already installed from Debian packages. That version
provides a multiline `TextArea`, fixed dimensions, Emacs and vi editing modes,
custom key bindings, Unicode width handling, scrolling, and an explicitly
non-full-screen `Application`.

A pipe-input/Vt100-output probe against the installed version rendered and
closed a fixed-height `TextArea` without emitting `CSI ? 1049 h`, `CSI ? 1047
h`, or `CSI ? 47 h`, the common alternate-screen entry sequences. This
verified the central rendering assumption before implementation, and PTY tests
continue to enforce it.

`inedit.py` remains its own editor: prompt_toolkit supplies terminal mechanics
and buffer primitives, while this program defines the file lifecycle, layout,
key bindings, save policy, and cancellation behavior.

## Near-term priorities

### 1. Add optional system-clipboard integration

The internal clipboard now uses prompt-toolkit's native Emacs editing commands
and retains only the latest cut or copy. This deliberately favors simple,
CUA-like replacement semantics over kill-ring history. Focused tests cover
native region cut/copy, `Ctrl-K`, `Ctrl-U`, `Ctrl-Y`, undo, and redo.

System-clipboard support needs a separate design because terminal applications
cannot portably read a desktop clipboard. Options to investigate include an
optional platform command (`wl-copy`/`wl-paste`, `xclip`, or equivalents),
terminal protocols such as OSC 52 where appropriate, and an internal-only
fallback that always works. Clipboard integration must not introduce a hard
desktop dependency or silently expose copied text.

Keep terminal-native bracketed paste working. Decide explicitly whether an
internal cut also populates the system clipboard and how clipboard lifetime
works across editor invocations.

### 2. Keep a small, prompt-toolkit-aligned default keymap

Use prompt-toolkit's Emacs bindings as the editing baseline. Nano is a useful
precedent for application-level help and exit behavior, not a reason to
replace working buffer operations. Every explicit override should satisfy an
`inedit` lifecycle requirement or a concrete usability need and should appear
in both in-editor help and [README.md](README.md).

Current and tentative direction:

- keep the implemented Nano/Pico-style `Ctrl-X` exit prompt;
- keep the implemented `Ctrl-G` inline help view;
- inherit `Ctrl-Space`, `Ctrl-W`, `Alt-W`, `Ctrl-K`, `Ctrl-U`, and `Ctrl-Y`
  directly from prompt-toolkit;
- keep the internal clipboard at one entry and do not advertise yank-pop;
- retain `Ctrl-Z` as an undo convenience and `Alt-E` as redo for now;
- keep `Ctrl-S` as save-without-exit;
- reconsider the eventual role of the current `Ctrl-C` safe-cancel shortcut
  as part of the exit-state design; and
- keep `--vi` available, with global save and exit commands documented
  separately from vi navigation.

Decision order for an unsettled binding:

1. Satisfy `inedit`'s inline lifecycle and data-safety requirements.
2. Preserve prompt-toolkit behavior.
3. Use Nano as a precedent for application-level interactions.
4. Add a custom editing binding only for a demonstrated usability problem.

The `Ctrl-X` state machine is implemented. An unchanged buffer exits with
status 0. A modified buffer asks `Save modified buffer?`: `Y` saves and exits
with status 0, `N` discards and returns status 130, and `Ctrl-C` returns to
editing. While the prompt is active, the editing buffer is read-only so an
unrecognized answer cannot leak into the file.

### 3. Extend discoverable in-editor help

The `Ctrl-G` help view is implemented. It fits the inline rendering model,
shows the current keys, scrolls within the bounded editor body, and returns to
the same buffer, cursor, and selection without modifying the file. Future work
should generate or validate its content from the intentional binding registry
and tailor the text when `--vi` is active. [README.md](README.md) remains the
authoritative user key reference.

### 4. Reconcile the status line with the keymap

The one-row status line includes `^G Help`, `^S Save`, `^X Exit`, and the
current `^C Cancel`, with `^G Help` changing to `^G Close` while help is
visible. The full key list belongs in help; the status line should favor help,
save, exit/cancel, and the active transient prompt.

## Goals

- Keep shell output above the editor visible while editing.
- Use a bounded region, 20 terminal rows by default.
- Make `Enter` insert a newline; do not use Gum's submit-on-Enter behavior.
- Open one named file and work as an `$EDITOR` command for tools such as
  `dsedit`.
- Provide obvious save and cancel commands with a one-line reminder.
- Never modify the file after cancellation or a failed validation/save.
- Restore terminal modes and the cursor after normal exit, interruption, or an
  exception.

## Command-line interface

```text
inedit.py [--height ROWS] [--vi] [--no-line-numbers] FILE
```

- `FILE` is required. One file is supported.
- `--height ROWS` sets the total rendered height, including the status line.
  The default is `${INEDIT_HEIGHT:-20}`.
- `--vi` selects prompt_toolkit's vi editing mode. Emacs mode is the default.
- `--no-line-numbers` hides the line-number gutter. Numbers are shown by
  default because callers such as `dsedit` report validation errors by line.
- `--` permits a filename beginning with `-`.
- Standard `-h`/`--help` output may be supplied by `argparse`.

`ROWS` must be at least 4. At runtime, the editor should cap its height so that
at least one terminal row remains outside the application. A terminal too small
for a four-row editor is an error.

Reading file content from stdin and writing edited content to stdout are out of
scope for version 1. The user interface requires a TTY. Diagnostics go to
stderr; successful operation is otherwise quiet.

## Layout and rendering contract

The application occupies the full terminal width and exactly the effective
height. It contains:

1. A scrollable multiline editing area using all but the last row.
2. A one-row status line containing the shortened filename, cursor position,
   modified state, transient errors, and
   `^G Help | ^S Save | ^X Exit | ^C Cancel`.

Long logical lines scroll horizontally rather than soft-wrapping. The cursor's
logical line must remain visible as it moves. The filename should be truncated
from the left before hiding the cursor position or key hints.

The prompt_toolkit application must use:

```python
Application(
    full_screen=False,
    erase_when_done=True,
    ...,
)
```

`full_screen=False` is a hard requirement: no `smcup`/`rmcup` or equivalent
alternate-screen sequence may be emitted. `erase_when_done=True` removes the
editor region before returning control to the shell. Output that preceded the
invocation must remain visible and unchanged throughout the edit.

## Editing behavior

The current default mode uses prompt_toolkit's Emacs editing bindings. Its
current keys include:

| Key | Action |
|---|---|
| Arrow keys, `Home`, `End` | Move the cursor |
| `PageUp`, `PageDown` | Scroll by a viewport |
| `Enter` | Insert a newline |
| `Backspace`, `Delete` | Delete text |
| `Ctrl-A`, `Ctrl-E` | Move to start/end of logical line |
| `Ctrl-G` | Open or close inline help |
| `Ctrl-Z`, `Ctrl-_` | Undo |
| `Alt-E` | Redo |
| `Ctrl-Space` | Start a selection (prompt-toolkit) |
| `Ctrl-W`, `Alt-W` | Cut/copy a selected region (prompt-toolkit) |
| `Ctrl-K` | Kill to the end of the line (prompt-toolkit) |
| `Ctrl-U` | Kill to the beginning of the line (prompt-toolkit) |
| `Ctrl-Y` | Paste/yank the latest clipboard value (prompt-toolkit) |
| `Ctrl-S` | Save and continue editing |
| `Ctrl-X` | Exit, prompting to save a modified buffer |
| `Ctrl-C` | Cancel |

See [README.md](README.md) for the complete current key reference.

In `--vi` mode, prompt_toolkit owns vi insert/normal navigation and `Escape`
returns to normal mode. `Ctrl-S`, `Ctrl-X`, and `Ctrl-C` retain their global
meanings. Version 1 does not implement Ex commands such as `:wq`.

If the buffer is unmodified, `Ctrl-C` cancels immediately. If it is modified,
the first `Ctrl-C` changes the status line to `Unsaved changes; Ctrl-C again to
discard`. A second `Ctrl-C` cancels. Any intervening edit disarms that
confirmation. This avoids a modal dialog while protecting against accidental
loss.

`Ctrl-C` is reserved for possible future Nano alignment, where it would show
the cursor position rather than cancel. Do not expand its current role; prefer
`Ctrl-X` as the documented normal exit path.

A save failure leaves the editor open and displays the error in the status
line. The user can correct the problem, retry, or cancel.

## File behavior

- An existing regular file is loaded. A nonexistent file starts with an empty
  buffer and is created only after save.
- Directories and other unsupported file types are rejected before terminal
  setup.
- Version 1 supports UTF-8 text, with or without a UTF-8 BOM, and rejects NUL
  bytes or undecodable input.
- Consistent LF and CRLF files are accepted. Line endings are normalized in the
  buffer and restored to the original style on save. Mixed line endings and
  bare carriage returns should be rejected rather than silently normalized.
- The presence or absence of a final newline is part of the editable content.
- Symlinks are followed so saving does not replace the symlink itself.

Saving should be atomic: encode the complete new content first, write it to a
temporary sibling, flush and `fsync` it, preserve the original permission bits
when applicable, then replace the target with `os.replace()`. Clean up an
unfinished sibling after failure. This approach may replace the inode behind a
hard link; preserving hard-link identity and extended attributes is outside the
initial scope and should be documented in `--help` if the tool becomes general
purpose.

Before replacement, compare the target's current identity, size, and
nanosecond-resolution modification time with the values captured at load time.
If another process changed the file, refuse to overwrite it and keep the editor
open. Likewise, if a new target appeared after opening a nonexistent file,
report a conflict.

## Exit status

| Status | Meaning |
|---|---|
| `0` | Exited through `Ctrl-X` after saving or with an unchanged buffer |
| `1` | Load, terminal, encoding, or unrecoverable runtime error |
| `2` | Command-line usage error |
| `130` | User canceled or answered `N`; any earlier Ctrl-S save remains on disk |

`SIGINT` should follow the same safe cancellation path when practical.
`SIGTERM`, `SIGHUP`, and unexpected exceptions must restore the terminal and
must not save implicitly.

## Implementation structure

The first implementation is one importable script with small testable units:

- `parse_args()` validates command-line and environment settings.
- `load_document()` returns decoded text, newline style, BOM state, permissions,
  and the file-change fingerprint.
- `save_document()` performs conflict checking and atomic replacement, then
  returns the refreshed document snapshot needed for another save.
- `EditorState` tracks the path, original text, modified state, status message,
  and armed discard confirmation.
- `build_application()` constructs the `TextArea`, status control, layout,
  styles, and key bindings.
- `main()` performs preflight checks, runs the application, and maps outcomes to
  exit statuses.

The central layout can be an `HSplit` containing a `TextArea` and a one-row
`Window` backed by `FormattedTextControl`. Configure the text area with
`multiline=True`, `wrap_lines=False`, a scrollbar, the selected line-number
setting, and a fixed height of `effective_height - 1`. Buffer change events
should update modified state, clear discard confirmation, and invalidate the
status line.

Target prompt_toolkit `>=3.0.36,<4`. Do not require Rich merely to style one
status row.

## Alternatives investigated

| Option | Environment status | Assessment |
|---|---|---|
| **prompt_toolkit `TextArea`** | 3.0.36 installed | Recommended. Inline rendering and editor primitives are available with modest glue code. |
| **Textual `TextArea` in inline mode** | Not installed; Debian candidate is 0.1.13 | Strong second choice. Upstream Textual 0.55+ has an official inline code-editor example and richer selection/undo behavior, but the available Debian package predates inline mode. |
| **`curses.textpad.Textbox`** | Python standard library, installed | Supplies elementary Emacs-like editing, but normal `curses.initscr()` owns the whole screen. A small curses window does not create the required shell-friendly inline lifecycle by itself. |
| **Urwid `Edit`** | 2.1.2 installed | Mature editor widget, but the normal display and event-loop model is screen-oriented. A custom inline screen adapter would be more work than the prompt_toolkit implementation. |
| **Raw `termios` + ANSI** | Standard library only | Maximum control and no package dependency, but requires implementing escape parsing, Unicode cell widths, bracketed paste, scrolling, resize handling, and crash-safe terminal restoration. Consider only as a deliberate second implementation. |
| **Rich or readline** | Rich and Python readline available | Neither provides a bounded multiline editor. Rich renders output; readline edits command lines. |

Textual's upstream example is notably close to the requested UI:

```python
class InlineApp(App):
    CSS = """
    TextArea {
        height: auto;
        max-height: 50vh;
    }
    """

    def compose(self) -> ComposeResult:
        yield TextArea()

InlineApp().run(inline=True)
```

It is worth revisiting if a current Textual package becomes easy to install.
For the present environment, prompt_toolkit reaches the same essential result
without changing Python package sources.

## Ongoing verification

Unit tests cover and should continue to cover argument parsing, height capping,
UTF-8/BOM handling, LF and CRLF preservation, final-newline preservation,
new-file creation, permission preservation, conflict detection, and cleanup
after failed saves.

Prompt behavior is tested with prompt_toolkit's pipe-input and dummy-output
helpers. PTY integration tests:

- Start the editor in an 80x24 pseudo-terminal with recognizable output above
  it and verify that output remains present.
- Assert that captured output never contains alternate-screen sequences such as
  `CSI ? 1049 h`, `CSI ? 1047 h`, or `CSI ? 47 h`.
- Verify that a requested height of 20 never renders a larger region.
- Insert and delete lines, save, and verify exact file bytes and status `0`.
- Cancel an unchanged and a modified buffer, verify status `130`, and verify the
  original bytes remain unchanged.
- Simulate a save error and an external file change and confirm that editing
  continues without overwriting the target.
- Send resize and termination signals and verify terminal cleanup.

Finally, test manually as a `dsedit` editor:

```bash
EDITOR='python3 /path/to/inedit.py' dsedit
```

Use a directory stack longer than the viewport and include paths containing
spaces and long paths requiring horizontal scrolling.

## Continuing non-goals

- Multiple files, tabs, split views, syntax highlighting, or plugins.
- Search and replace, macros, or an Ex command line.
- Mouse selection. System-clipboard integration is now a near-term roadmap
  item, but must remain optional and terminal-safe.
- Arbitrary encodings or binary-file editing.
- Remote files, file locking protocols, swap files, or crash recovery.
- A full-screen fallback. If inline rendering cannot be established safely,
  exit with an error instead.

## References

- [GNU Nano command cheat sheet](https://www.nano-editor.org/dist/latest/cheatsheet.html)
- [OpenBSD `mg` manual](https://man.openbsd.org/mg)
- [prompt_toolkit API reference](https://python-prompt-toolkit.readthedocs.io/en/stable/pages/reference.html)
- [Textual inline application explanation and editor example](https://textual.textualize.io/blog/2024/04/20/behind-the-curtain-of-inline-terminal-applications/)
- [Textual `TextArea` documentation](https://textual.textualize.io/widgets/text_area/)
- [Python `curses.textpad` documentation](https://docs.python.org/3/library/curses.html#module-curses.textpad)
