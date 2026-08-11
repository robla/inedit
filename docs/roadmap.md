# `inedit.py` roadmap

## Status

The first version of `inedit.py` is implemented as a minimal text editor for
short-lived files such as Git commit messages. Its defining behavior is a
bounded-height editor rendered below the shell prompt without switching to the
terminal's alternate screen. `Alt-Up` and `Alt-Down` adjust that height during
the current session; their `Height: N` feedback clears one second after the
most recent adjustment. With no explicit height, the editor automatically uses
7–20 text rows plus its footer and grows with the buffer. On a controlled exit,
the final viewport remains in terminal history with a plain saved, unchanged,
or discarded summary in place of the live status bar.

Modified buffers now receive a private Emacs-style `#filename#` recovery
snapshot every 30 seconds. Explicit save removes a snapshot owned by the
current session; discard or abnormal termination leaves it available for
manual recovery.

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

The current implementation is a well-tested prototype for small Git messages
and other user-owned UTF-8 files on the tested POSIX terminal. It should not yet
be promoted as a hardened general replacement for `$EDITOR`. The actionable
release work, rationale, priorities, and completion criteria live in
[tasks.org](../tasks.org); the sections below summarize the design consequences
that belong in the roadmap.

## Promotion readiness

### Save confidentiality and correctness

The first confidentiality blocker is fixed: a save transaction now creates its
random sibling without group or other access, writes the content, and only then
applies the existing file's mode or the new file's umask-derived mode. Recovery
auto-saves are also private mode `0600` files and never overwrite a pre-existing
`#filename#` that might contain work from another session.

One immediate correctness fix remains. An explicit save of a new empty buffer
does not create the file because
the buffer compares equal to its initial empty text. `Ctrl-S` and vi `:w`
should create a zero-byte target; merely opening and canceling it should not.
The behavior of unchanged exit and `ZZ` on a new empty target needs an explicit
decision and tests.

The project must also decide what “safe save” promises beyond those fixes:

- `fsync()` of the temporary file gives atomic visibility after
  `os.replace()`, but full power-loss durability generally requires syncing the
  containing directory and carefully reporting a failure after replacement;
- inode replacement preserves permission bits but can change ownership or
  group, break hard links, and lose ACLs, extended attributes, security labels,
  and filesystem-specific metadata; and
- device, inode, size, and `mtime_ns` detect common conflicts, but not a
  same-size in-place change with a restored timestamp, nor another writer in
  the final check/replace race window.

Either preserve important metadata and strengthen detection where practical,
or keep the supported target class narrow and reject or prominently document
risky cases. Avoid categorical claims that every external modification is
detected. Expand failure injection across writes, permission changes, file and
directory sync, close, replacement, and cleanup.

### Terminal lifecycle and interoperability

Prompt-toolkit owns the main raw-mode lifecycle, but the custom cursor-column
guard runs before those protections. It temporarily changes terminal modes and
reads stdin directly, can discard early typeahead while extracting a CPR
response, and installs `SIGTERM`/`SIGHUP` restoration handling only afterward.
Redesign it so unrelated input is replayed and no startup signal can leave the
terminal altered. CPR timeout and malformed-response behavior should remain
conservative and quick.

The guard's newline prevents Git's unterminated “Waiting for your editor” hint
from corrupting the first editor row, but now leaves that hint in scrollback.
Documenting `git config --global advice.waitingForEditor false` is safer than
automatically erasing arbitrary caller output. Any future current-line
reclamation must be explicit and tested with both Git and a caller whose
unterminated text is meaningful.

The current PTY matcher proves that expected text appeared in terminal update
streams; it does not reconstruct the final screen. Promotion-quality coverage
should assert complete cells, scrolling position, line numbers, styling, and
the next shell prompt after save, unchanged exit, discard, help, search, vi Ex
input, resizing, and horizontal or vertical scrolling. Add live `SIGHUP`, CPR
failure, narrow-terminal, Unicode, and terminal/multiplexer coverage. Decide
whether strict TTY stdin/stdout remains the contract or whether a POSIX
controlling-terminal fallback is supported when standard streams are
redirected.

Retained output remains the preferred default, but it leaves discarded and
possibly sensitive text in scrollback. Keep that consequence prominent and add
an erase-on-exit option only if real privacy-sensitive or scripted workflows
demonstrate a need for both policies.

### Distribution and supported scope

The copyright holder selected the [MIT License](../LICENSE.md), and the
user-facing README now lives at the repository root. Remaining distribution
work includes reproducible Python and prompt-toolkit dependency metadata, an
installable `inedit` entry point, a version command, release notes, and CI. CI
should exercise the minimum and newest supported Python and prompt-toolkit
versions, claimed operating systems, static checks, and PTY tests where
available. A disposable real-Git test should cover saved, unchanged, and
discarded commit-message flows rather than only imitating Git's hint.

Market the program according to the behavior it can defend: a bounded inline
editor for small, user-owned UTF-8 files, especially Git commit messages. The
default keymap is prompt-toolkit's Emacs-style editing plus Nano-inspired
application controls; `--vi` is prompt-toolkit vi mode plus a deliberately
small Ex subset, not complete vi compatibility. Benchmark and enforce a
reasonable small-file policy because loading, rendering, and saving currently
operate on the entire document synchronously.

External-editor handoff also needs hardened parsing, launch, returned-file, and
cleanup errors so a malformed `$VISUAL`/`$EDITOR` cannot emit an asyncio
traceback into the live UI. Document graphical editors' wait options and define
cursor and undo behavior for a successful returned edit.

Finally, reduce maintenance risk by separating application transitions from
widget construction, validating help and documentation from one intentional
key registry, adding useful static typing, auditing prompt-toolkit API use, and
adding property/adversarial tests for encoding, Unicode display, paths, and
edit/save state transitions.

## Product roadmap after release blockers

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

### 2. Refine retained exit display

The baseline is implemented. Saved, unchanged, and explicitly discarded exits
retain the final viewport by default, leave its text rows, scrolling position,
and line numbers intact, and replace the inverted live status bar with a plain
one-line result. The summary identifies the invocation basename and visible
path, reports the exact on-disk byte count when available, and distinguishes a
discard after an earlier save from a discard that wrote nothing. The next
shell prompt starts below the retained region.

Abnormal exceptions, terminal-size failures, `SIGTERM`, and `SIGHUP` keep the
erase-on-exit behavior. A confirmed `SIGINT` discard is an editor-controlled
outcome and is retained. This split prevents a partial failure screen from
being mistaken for a completed edit while still providing useful history for
ordinary workflows.

Do not add configuration merely for symmetry. If privacy-sensitive or
scripted workflows demonstrate a real need for both behaviors, add an explicit
`--erase-on-exit` option rather than making retention opt-in. Remaining PTY
work can reconstruct the final screen and assert the exact next-prompt cursor
position after vertical and horizontal scrolling, terminal resizing, help,
and active-search transitions.

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
in both in-editor help and [README.md](../README.md).

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
[README.md](../README.md) remains the authoritative user key reference.

### 6. Reconcile the status line with the keymap

The implemented one-row status line combines Emacs-like `--`/`**` state marks,
filename, `All`/`Top`/`Bot`/percentage viewport position, and one-based `L`/`C`
point with `^G Help`, `^S Save`, and `^X/^C Exit`. A trailing
`unchanged`/`modified` word intentionally teaches the symbolic state and is
the first field omitted after filename truncation on a narrow terminal. Vi
mode retains its explicit mode label. [Emacs brainspace](emacs-brainspace.md)
records the rationale and compatibility boundary.

`^G Help` changes to `^G Close` while help is visible. An active incremental
search or vi Ex command temporarily uses this same footer row, so neither
feature changes the editor's height. The full key list belongs in help.

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
    erase_when_done=True,  # safety default; controlled exits switch this off
    ...,
)
```

`full_screen=False` is a hard requirement: no `smcup`/`rmcup` or equivalent
alternate-screen sequence may be emitted. The application starts with
`erase_when_done=True`; a controlled exit first prepares its final summary and
sets the value to false, allowing prompt-toolkit's final done render to retain
the region and place the cursor below it. Error and termination paths leave the
safety default untouched. Output that preceded the invocation must remain
visible and unchanged throughout the edit under either policy.

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

See [README.md](../README.md) for the complete current key reference.

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
  buffer and is created only after a successful save. The current UI has a
  known gap: an explicit save of an initially empty new buffer does not create
  the zero-byte target because it compares as unchanged; this is a release
  blocker in [tasks.org](../tasks.org).
- Directories and other unsupported file types are rejected before terminal
  setup.
- Version 1 supports UTF-8 text, with or without a UTF-8 BOM, and rejects NUL
  bytes or undecodable input.
- Consistent LF and CRLF files are accepted. Line endings are normalized in the
  buffer and restored to the original style on save. Mixed line endings and
  bare carriage returns should be rejected rather than silently normalized.
- The presence or absence of a final newline is part of the editable content.
- Symlinks are followed so saving does not replace the symlink itself.
- A dirty buffer is copied every 30 seconds to a private `#filename#` recovery
  sibling. Explicit save removes a session-owned snapshot; discard and
  abnormal exit preserve it. Existing recovery files are never overwritten.

Saving should provide atomic visibility: encode the complete new content first,
write it to a private temporary sibling, flush and `fsync` it, apply the
intended permission bits when appropriate, then replace the target with
`os.replace()`. Clean up an unfinished sibling after failure. Full power-loss
durability and the handling of a directory-sync failure after replacement need
an explicit policy. Inode replacement can break hard links and lose ownership,
ACLs, extended attributes, security labels, and other metadata; these effects
must be supported safely, rejected, or kept prominent if the tool becomes
general purpose.

Before replacement, compare the target's current identity, size, and
nanosecond-resolution modification time with the values captured at load time.
This detects common external changes and keeps the editor open on a detected
conflict. It does not detect every same-inode change whose size and timestamp
are preserved, and another writer can race after the final check. If a new
target appears after opening a nonexistent file, report a conflict.

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
- `write_auto_save()` and `remove_auto_save()` maintain a fingerprinted,
  mode-`0600` `#filename#` recovery snapshot without mutating the target.
- `EditorState` tracks the document, original text, automatic-height mode,
  requested and effective heights, transient views and prompts, status message,
  armed discard confirmation, successful-write history, recovery snapshot and
  warning state, and prepared final summary.
- `build_application()` constructs the editing/help/command areas, search
  toolbar, status control, conditional layout, styles, and key bindings.
- `main()` performs preflight checks, runs the application, and maps outcomes to
  exit statuses.

The central layout is an `HSplit` containing a dynamic editor/help body and a
one-row footer. The footer conditionally displays the search toolbar, vi Ex
command line, live status `Window`, or plain final-summary `Window`; the search
toolbar remains in the layout tree even while hidden so prompt-toolkit can
focus it. Configure the editing
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

The current unit suite covers fixed and automatic height parsing, content-driven
growth, manual adjustment, UTF-8/BOM and newline handling, direct new-file
creation, private temporary-file creation, permission-bit and new-file umask
behavior, common conflict detection, replacement failure cleanup, periodic
private recovery snapshots, keymaps, help, search, clipboard operations, save
prompts, external handoff, and exit summaries. One known gap is important
enough to call out twice: direct `save_document()` can create a new empty file,
but the UI does not call it for an initially empty unchanged buffer.

Prompt behavior is exercised through prompt_toolkit pipe input and dummy output.
The POSIX PTY suite currently covers an 80x24 terminal, a sentinel above the
editor, absence of common alternate-screen sequences, help/search/exit-prompt
rendering, runtime height keys, save and discard results, `SIGINT`, `SIGTERM`,
terminal-attribute restoration, Git-like mid-row startup, and external-editor
success or nonzero exit.

Those tests do not yet establish the full promotion contract. Complete the
verification tasks in [tasks.org](../tasks.org), especially:

- reconstruct the terminal screen and assert final cells, styles, scrolling,
  and next-prompt location rather than only finding text in update fragments;
- resize a live PTY and cover `SIGHUP`, CPR timeout/malformed response, early
  typeahead, narrow widths, long lines, Unicode, and terminal variants;
- inject every save-transaction failure boundary, including write, `fchmod()`,
  file/directory sync, close, replacement, and cleanup;
- run CI across the declared Python, prompt-toolkit, OS, and static-analysis
  matrix; and
- execute a disposable end-to-end Git commit flow.

Until the Git test is automated, test manually with a staged change:

```bash
GIT_EDITOR='python3 /path/to/inedit.py' git commit
```

Exercise a multiline message longer than the viewport, a long line requiring
horizontal scrolling, save, unchanged exit, and cancellation, with Git's
waiting advice both enabled and disabled. Add separate direct-file smoke tests
for filenames containing spaces.

## Continuing non-goals

- Multiple files, tabs, split views, syntax highlighting, or plugins.
- Macros or Ex commands beyond the small documented set, except for the
  deliberately scoped substitution commands considered above.
- Mouse selection. System-clipboard integration is now a near-term roadmap
  item, but must remain optional and terminal-safe.
- Arbitrary encodings or binary-file editing.
- Remote files, file locking protocols, swap files, or automatic recovery-file
  discovery and selection beyond the private `#filename#` snapshot.
- A full-screen fallback. If inline rendering cannot be established safely,
  exit with an error instead.

## References

- [GNU Nano command cheat sheet](https://www.nano-editor.org/dist/latest/cheatsheet.html)
- [OpenBSD `mg` manual](https://man.openbsd.org/mg)
- [GNU Emacs auto-save files](https://www.gnu.org/software/emacs/manual/html_node/emacs/Auto-Save-Files.html)
- [prompt_toolkit API reference](https://python-prompt-toolkit.readthedocs.io/en/stable/pages/reference.html)
- [Textual inline application explanation and editor example](https://textual.textualize.io/blog/2024/04/20/behind-the-curtain-of-inline-terminal-applications/)
- [Textual `TextArea` documentation](https://textual.textualize.io/widgets/text_area/)
- [Python `curses.textpad` documentation](https://docs.python.org/3/library/curses.html#module-curses.textpad)
