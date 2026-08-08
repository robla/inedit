# `inedit.py` roadmap

## Status

The first version of `inedit.py` is implemented as a minimal text editor for
short-lived files such as Git commit messages. Its defining behavior is a
bounded-height editor rendered below the shell prompt without switching to the
terminal's alternate screen. `Alt-Up` and `Alt-Down` adjust that height during
the current session; their `Height: N` feedback clears one second after the
most recent adjustment. With no explicit height, the editor automatically uses
7–20 text rows plus its footer and grows with the buffer.

The implementation uses **prompt_toolkit**, not raw terminal escape sequences,
and targets prompt-toolkit `>=3.0.36,<4`. That baseline provides a multiline
`TextArea`, fixed dimensions, Emacs and vi editing modes, custom key bindings,
Unicode width handling, scrolling, and an explicitly non-full-screen
`Application`.

A pipe-input/Vt100-output probe against the supported baseline rendered and
closed a fixed-height `TextArea` without emitting `CSI ? 1049 h`, `CSI ? 1047
h`, or `CSI ? 47 h`, the common alternate-screen entry sequences. This
verified the central rendering assumption before implementation, and PTY tests
continue to enforce it.

`inedit.py` remains its own editor: prompt_toolkit supplies terminal mechanics
and buffer primitives, while this program defines the file lifecycle, layout,
key bindings, save policy, and cancellation behavior.

## Near-term priorities

### 1. Add search and replace

Incremental forward and reverse search is implemented through prompt-toolkit's
native search state. Replacement should build on that state rather than create
a second search engine. The first useful slice should support replacing the
current match and an interactive replace/skip/all/cancel workflow, while
keeping every replacement undoable and leaving the filesystem untouched until
the user saves.

Decide these details before assigning final keys:

- whether the initial implementation is literal-only or exposes regular
  expressions;
- whether default mode follows Nano's `Ctrl-\`, Emacs's `Alt-%`, or offers
  one as an alias;
- how a replacement prompt shares the one-row footer with search, status, and
  vi Ex commands;
- whether the last accepted search pre-populates the find field; and
- which vi substitution subset is supportable without pretending to implement
  the complete Ex grammar (`:s` and `:%s` are the likely starting points).

Tests should cover replace-one, skip, replace-all, cancellation, no-match and
empty-query handling, Unicode text, replacement after search wraparound, and
undo grouping. Documentation must clearly distinguish literal replacement from
regular-expression replacement if both are not implemented together.

### 2. Decide whether the final editor display should remain visible

The current application uses `erase_when_done=True`, which removes the bounded
editor region before returning to the shell. Evaluate a less `-X`-style user
experience by setting it to false so the final rendered editor view remains in
terminal history and the next shell prompt appears below it.

The provisional preference is to retain the final display by default. Avoid
adding a permanent option until testing shows that both behaviors serve real
workflows; if both are useful, prefer an explicit `--erase-on-exit` option over
making retention opt-in. Resolve these cases before changing the default:

- a successful exit should leave a view consistent with the bytes saved;
- discard and cancellation must not leave unsaved text looking as though it
  was committed to disk;
- exit from a save prompt, help view, or search prompt must not strand a stale
  transient UI in terminal history;
- signals and exceptions must restore terminal modes even if their display is
  erased; and
- retained output must work in short terminals, after resizing, and when the
  last line is wider than the terminal.

PTY coverage should locate the next shell cursor relative to the retained
region and continue to prove that output above the editor is untouched. If the
best policy differs by exit reason, document that explicitly rather than
treating one `erase_when_done` value as the whole design.

### 3. Add optional system-clipboard integration

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

### 4. Keep a small, prompt-toolkit-aligned default keymap

Use prompt-toolkit's Emacs bindings as the editing baseline. Nano is a useful
precedent for application-level help and exit behavior, not a reason to
replace working buffer operations. Every explicit override should satisfy an
`inedit` lifecycle requirement or a concrete usability need and should appear
in both in-editor help and [README.md](README.md).

Current and tentative direction:

- keep the implemented Nano/Pico-style `Ctrl-X` exit prompt;
- keep the implemented `Ctrl-G` inline help view;
- inherit `Ctrl-Space`, `Alt-W`, `Ctrl-K`, `Ctrl-U`, and `Ctrl-Y` directly
  from prompt-toolkit, while making `Ctrl-W` start search only when no
  selection exists;
- keep the internal clipboard at one entry and do not advertise yank-pop;
- retain `Ctrl-Z` as an undo convenience and `Alt-E` as redo for now;
- keep `Alt-Up` and `Alt-Down` as global, session-local height adjustments;
- keep `Ctrl-S` as save-without-exit;
- keep main-screen `Ctrl-C` equivalent to `Ctrl-X`; and
- keep `--vi` available, with global save and exit commands documented
  separately from vi navigation.

Decision order for an unsettled binding:

1. Satisfy `inedit`'s inline lifecycle and data-safety requirements.
2. Preserve prompt-toolkit behavior.
3. Use Nano as a precedent for application-level interactions.
4. Add a custom editing binding only for a demonstrated usability problem.

The shared `Ctrl-X`/`Ctrl-C` state machine is implemented. An unchanged buffer
exits with status 0. A modified buffer asks `Save modified buffer?`: `Y` saves
and exits with status 0, `N` discards and returns status 130, and `Ctrl-C`
returns to editing. Thus `Ctrl-C Ctrl-C` cannot discard. While the prompt is
active, the editing buffer is read-only so an unrecognized answer cannot leak
into the file.

### 5. Extend discoverable in-editor help

The `Ctrl-G` help view is implemented. It fits the inline rendering model,
shows the current keys, scrolls within the bounded editor body, and returns to
the same buffer, cursor, and selection without modifying the file. Its content
is mode-specific: default mode shows the Emacs-oriented bindings, while
`--vi` shows supported vi modes, motions, operators, selections, and editing
commands without advertising Emacs-only shortcuts. Future work should generate
or validate its content from the intentional binding registry.
[README.md](README.md) remains the authoritative user key reference.

### 6. Reconcile the status line with the keymap

The one-row status line includes `^G Help`, `^S Save`, and `^X/^C Exit`, with
`^G Help` changing to `^G Close` while help is visible. The full key list
belongs in help; the status line should favor help, save, exit, and the active
transient prompt. An active incremental search or vi Ex command temporarily
uses this same footer row, so neither feature changes the editor's height.

### 7. Refine content-aware automatic height

Content-aware height is implemented. Unset or `auto` configuration selects
7–20 logical text rows plus the one-row footer. A trailing newline contributes
the editable blank row after it. The region grows silently as the buffer gains
rows, never shrinks automatically, and stops growing automatically after the
first `Alt-Up` or `Alt-Down` adjustment.

Numeric `--height` and `INEDIT_HEIGHT` values retain their earlier meaning as
fixed requested total heights; `--height auto` can override a numeric
environment value. Automatic limits do not constrain explicit or manual
heights. Add a separate `--max-height`/`INEDIT_MAX_HEIGHT` only if real usage
demonstrates a need to customize the automatic ceiling without selecting a
fixed height.

## Goals

- Keep shell output above the editor visible while editing.
- Use a bounded region, automatically sized to 7–20 text rows plus the footer
  by default and adjustable during editing.
- Make `Enter` insert a newline rather than submit the buffer.
- Open one named file and work as an `$EDITOR` command for common workflows
  such as writing Git commit messages.
- Provide obvious save and cancel commands with a one-line reminder.
- Never modify the file after cancellation or a failed validation/save.
- Restore terminal modes and the cursor after normal exit, interruption, or an
  exception.

## Command-line interface

```text
inedit.py [--height ROWS|auto] [--vi] [--no-line-numbers] FILE
```

- `FILE` is required. One file is supported.
- `--height ROWS` sets a fixed requested total height, including the status
  line. `--height auto` selects content-aware sizing. An explicit option
  overrides `INEDIT_HEIGHT`, which accepts the same numeric or `auto` values.
- When neither source supplies a numeric height, use 7–20 logical text rows
  plus the footer and grow with the buffer until manually adjusted.
- `--vi` selects prompt_toolkit's vi editing mode, starting in Normal mode.
  Emacs mode is the default.
- `--no-line-numbers` hides the line-number gutter. Numbers are shown by
  default to make multiline text easier to navigate and discuss.
- `--` permits a filename beginning with `-`.
- Standard `-h`/`--help` output may be supplied by `argparse`.

Numeric `ROWS` must be at least 4. `Alt-Up` and `Alt-Down` adjust the requested
session height one row at a time and disable further automatic growth. At
runtime, the editor caps its effective height so that at least one terminal row
remains outside the application. A terminal too small for a four-row editor is
an error.

Reading file content from stdin and writing edited content to stdout are out of
scope for version 1. The user interface requires a TTY. Diagnostics go to
stderr; successful operation is otherwise quiet.

## Layout and rendering contract

The application occupies the full terminal width and exactly the effective
height. It contains:

1. A scrollable multiline editing area using all but the last row.
2. A one-row status line containing the shortened filename, cursor position,
   modified state, transient errors, and
   `^G Help | ^S Save | ^X/^C Exit`.

Long logical lines scroll horizontally rather than soft-wrapping. The cursor's
logical line must remain visible as it moves. The filename should be truncated
from the left before hiding the cursor position or key hints.

The current prompt_toolkit application uses:

```python
Application(
    full_screen=False,
    erase_when_done=True,
    ...,
)
```

`full_screen=False` is a hard requirement: no `smcup`/`rmcup` or equivalent
alternate-screen sequence may be emitted. `erase_when_done=True` currently
removes the editor region before returning control to the shell, but the
near-term retention work above may change that value or make it depend on the
exit result. Output that preceded the invocation must remain visible and
unchanged throughout the edit under either policy.

## Editing behavior

The current default mode uses prompt_toolkit's Emacs editing bindings. Its
current keys include:

| Key | Action |
|---|---|
| `Left`, `Right` | Move by character, crossing logical-line boundaries |
| `Up`, `Down`, `Home`, `End` | Move by logical line or within one |
| `PageUp`, `PageDown` | Scroll by a viewport |
| `Enter` | Insert a newline |
| `Backspace`, `Delete` | Delete text |
| `Ctrl-A`, `Ctrl-E` | Move to start/end of logical line |
| `Ctrl-G` | Open or close inline help |
| `Alt-Up`, `Alt-Down` | Reduce or increase the editor height by one row |
| `Ctrl-Z`, `Ctrl-_` | Undo |
| `Alt-E` | Redo |
| `Ctrl-Space` | Start a selection (prompt-toolkit) |
| `Ctrl-W` | Cut a selected region; without a selection, search forward |
| `Ctrl-R` | Search backward |
| `F3` | Repeat the last accepted search |
| `Alt-W` | Copy a selected region (prompt-toolkit) |
| `Ctrl-K` | Kill to the end of the line (prompt-toolkit) |
| `Ctrl-U` | Kill to the beginning of the line (prompt-toolkit) |
| `Ctrl-Y` | Paste/yank the latest clipboard value (prompt-toolkit) |
| `Ctrl-S` | Save and continue editing |
| `Ctrl-X` | Exit, prompting to save a modified buffer |
| `Ctrl-C` | Same as `Ctrl-X`; cancel only while the exit prompt is active |

See [README.md](README.md) for the complete current key reference.

In `--vi` mode, prompt_toolkit owns vi insert/normal navigation, the editor
starts in Normal mode, and the status line shows `[NORMAL]`, `[INSERT]`,
`[REPLACE]`, or `[VISUAL]`. `Escape` returns to Normal mode. `Ctrl-S`,
`Ctrl-X`, and `Ctrl-C` retain their global meanings. A limited Ex command line
implements `:w`, `:q`, `:wq`, `:h`, and the inedit-specific `:external`
(plus the applicable long forms). It does not accept filenames, `!` variants,
options, or arbitrary Ex commands. Native vi `/` and `?` searches and `n`/`N`
repetition are available in Normal mode. `ZZ` uses vi's write-if-modified and
exit behavior.

On the main screen, `Ctrl-C` follows the same clean-or-prompted exit path as
`Ctrl-X`. Within the prompt, `Ctrl-C` returns to editing.

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
| `0` | Exited through `Ctrl-X` or `Ctrl-C` after saving or with an unchanged buffer |
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
- `EditorState` tracks the document, original text, automatic-height mode,
  requested and effective heights, transient views and prompts, status message,
  and armed discard confirmation.
- `build_application()` constructs the editing/help/command areas, search
  toolbar, status control, conditional layout, styles, and key bindings.
- `main()` performs preflight checks, runs the application, and maps outcomes to
  exit statuses.

The central layout is an `HSplit` containing a dynamic editor/help body and a
one-row footer. The footer conditionally displays the search toolbar, vi Ex
command line, or status `Window`; the search toolbar remains in the layout
tree even while hidden so prompt-toolkit can focus it. Configure the editing
area with `multiline=True`, `wrap_lines=False`, a scrollbar, the selected
line-number setting, a search field, and a callable height of
`effective_height - 1`. Buffer change events update modified state, clear
discard confirmation, and invalidate the status line.

Target prompt_toolkit `>=3.0.36,<4`. Do not require Rich merely to style one
status row.

## Alternatives investigated

| Option | Assessment |
|---|---|
| **prompt_toolkit `TextArea`** | Selected. Inline rendering and editor primitives are available with modest glue code. |
| **Textual `TextArea` in inline mode** | A credible alternative with a richer editor widget, but switching would add a larger dependency and replace working prompt-toolkit behavior. |
| **`curses.textpad.Textbox`** | Part of the Python standard library and supplies elementary Emacs-like editing, but normal `curses.initscr()` owns the whole screen. A small curses window does not create the required shell-friendly inline lifecycle by itself. |
| **Urwid `Edit`** | Mature editor widget, but its normal display and event-loop model is screen-oriented. A custom inline screen adapter would be more work than the prompt-toolkit implementation. |
| **Raw `termios` + ANSI** | Maximum control and no package dependency, but requires implementing escape parsing, Unicode cell widths, bracketed paste, scrolling, resize handling, and crash-safe terminal restoration. Consider only as a deliberate second implementation. |
| **Rich or readline** | Neither provides a bounded multiline editor. Rich renders output; readline edits command lines. |

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

It is worth revisiting if prompt-toolkit becomes a limiting factor. For now,
prompt-toolkit reaches the required result without replacing the established
editing behavior.

## Ongoing verification

Unit tests cover and should continue to cover fixed and automatic height
parsing, content-driven growth, manual takeover, runtime adjustment and
capping,
UTF-8/BOM handling, LF and CRLF preservation, final-newline preservation,
new-file creation, permission preservation, conflict detection, and cleanup
after failed saves.

Prompt behavior is tested with prompt_toolkit's pipe-input and dummy-output
helpers, including mode-specific help, search and repetition in both keymaps,
vi Ex commands, `ZZ`, clipboard operations, save, and safe exit. PTY
integration tests:

- Start the editor in an 80x24 pseudo-terminal with recognizable output above
  it and verify that output remains present.
- Assert that captured output never contains alternate-screen sequences such as
  `CSI ? 1049 h`, `CSI ? 1047 h`, or `CSI ? 47 h`.
- Verify that a requested height of 20 never renders a larger region.
- Use `Alt-Up` and `Alt-Down` to shrink and expand the live region, verify its
  status feedback, and confirm that output above it remains untouched.
- Insert and delete lines, save, and verify exact file bytes and status `0`.
- Cancel an unchanged and a modified buffer, verify status `130`, and verify the
  original bytes remain unchanged.
- Simulate a save error and an external file change and confirm that editing
  continues without overwriting the target.
- Send resize and termination signals and verify terminal cleanup.

Finally, test manually while composing a Git commit message in a disposable
repository with a staged change:

```bash
GIT_EDITOR='python3 /path/to/inedit.py' git commit
```

Exercise a multiline message longer than the viewport, a long line requiring
horizontal scrolling, save, and cancellation. Add separate direct-file smoke
tests for filenames containing spaces.

## Continuing non-goals

- Multiple files, tabs, split views, syntax highlighting, or plugins.
- Macros or Ex commands beyond the small documented set, except for the
  deliberately scoped substitution commands considered above.
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
