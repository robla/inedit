# `inedit`

`inedit.py` is a small editor that stays in the terminal's normal screen. It
uses a bounded region below the shell prompt, leaves earlier shell output
visible, and returns control to the caller when the editing session exits. Git
commit messages are a typical use, but it can edit any one named UTF-8 text
file.

This page documents the current behavior. Proposed changes are tracked in the
[roadmap](docs/roadmap.md) and [task list](tasks.org). Text editing follows
prompt-toolkit's default Emacs bindings wherever practical. Nano inspires
application-level controls such as help and the exit flow, rather than
replacing the editing engine. The design boundary and a terse guide to Emacs's
mode line are in [Emacs brainspace](docs/emacs-brainspace.md). Contributors can
start with the short [code-reading guide](docs/code-guide.md).

## Starting the editor

Until installable packaging is added, keep `inedit.py` beside the `_inedit/`
directory from the source checkout; copying the script by itself is not
sufficient.

```text
inedit.py [--height ROWS|auto] [--vi] [--no-line-numbers] FILE
```

- With no height setting, the editor automatically uses 7–20 text rows based
  on the file, plus its one-row footer.
- `--height ROWS` requests a fixed total height, including the footer.
  `--height auto` explicitly selects adaptive sizing.
- `INEDIT_HEIGHT` accepts the same numeric or `auto` values. An explicit
  command-line value overrides it.
- `--vi` selects prompt-toolkit's vi editing mode, starting in Normal mode.
  Emacs mode is the default.
- `--no-line-numbers` hides the line-number gutter.
- `--` permits a filename beginning with `-`.

For example, use it for one Git commit without changing global configuration:

```bash
GIT_EDITOR='/absolute/path/to/inedit.py' git commit
```

## Key notation

- `Ctrl-X` means hold Control while pressing X. Nano-style help often writes
  this as `^X`.
- `Alt-X` means hold Alt while pressing X. `Esc`, then X usually works when a
  terminal does not transmit Alt combinations distinctly.

## Essential keys

| Key | Current action |
|---|---|
| `Ctrl-X` | Exit; prompt to save if the buffer is modified |
| `Ctrl-S` | Save and continue editing |
| `Ctrl-C` | Same exit behavior as `Ctrl-X` |
| `Ctrl-G` | Open or close the inline help view |
| `Alt-Up` | Reduce the editor height by one row |
| `Alt-Down` | Increase the editor height by one row |
| `Enter` | Insert a newline |
| `Ctrl-Z`, `Ctrl-_` | Undo |
| `Alt-E` | Redo |
| `Ctrl-Y` | Paste/yank the latest internal clipboard value |
| `Ctrl-W` without a selection | Search forward |
| `Ctrl-R` | Search backward |
| `Left`, `Right` | Move by character, crossing logical-line boundaries |
| `Up`, `Down` | Move by logical line |
| `Home`, `End` | Move to the start or end of the logical line |
| `PageUp`, `PageDown` | Move by a viewport |
| `Backspace`, `Delete` | Delete without changing the internal clipboard |

## Status line

The footer combines terse Emacs-shaped state with a small command reminder:

```text
-- COMMIT_EDITMSG   All L1 C1 | ^G Help | ^S Save | ^X/^C Exit | unchanged
** COMMIT_EDITMSG   Top L12 C7 | ^G Help | ^S Save | ^X/^C Exit | modified
```

`--` means the buffer matches its last loaded or saved text; `**` means it has
unsaved changes. `All`, `Top`, `Bot`, or a percentage describes the visible
part of the buffer. `L` and `C` are one-based logical line and column numbers.
The final word deliberately repeats the symbolic state for discoverability.
On narrower terminals, `inedit` shortens the filename from the left and then
omits that redundant word before sacrificing position or key hints.

In vi mode, `[NORMAL]`, `[INSERT]`, `[REPLACE]`, or `[VISUAL]` appears between
the position and command hints. Search, Ex commands, and exit questions
temporarily replace the ordinary footer; errors and short-lived messages
appear within it.

`Ctrl-X` and `Ctrl-C` are equivalent main-screen exit commands. An unchanged
buffer exits immediately. A modified buffer displays
`Save modified buffer? Y Yes | N No | ^C Cancel`:

- `Y` saves and exits;
- `N` exits without saving; and
- `Ctrl-C` cancels the prompt and returns to editing.

Thus, `Ctrl-C Ctrl-C` cannot discard the buffer: the first press opens the
prompt and the second returns to editing.

## Display after exit

After an editor-controlled exit, `inedit` leaves the final text viewport and
line numbers in the terminal's normal-screen history. The inverted status bar
is replaced by a plain one-line summary, and the next shell prompt starts
below the retained region. Depending on the session, the summary reports one
of these outcomes:

```text
inedit.py: saved 842 bytes to COMMIT_EDITMSG
inedit.py: no changes; 842 bytes on disk: COMMIT_EDITMSG
inedit.py: unsaved edits discarded; 842 bytes remain on disk: COMMIT_EDITMSG
inedit.py: unsaved edits discarded; previous save kept (842 bytes): COMMIT_EDITMSG
inedit.py: unsaved edits discarded; no file created: new.txt
```

The byte count is the exact size of the resulting file on disk, including a
UTF-8 BOM or CRLF encoding when present. The leading name is the basename used
to invoke the program, and a long filename is shortened from the left to fit
the terminal. A discard deliberately preserves the last visible buffer rows,
which can include unsaved text; the summary makes clear that those edits were
not written.

This retention applies to normal saved, unchanged, and discard exits, including
the second real `SIGINT` used to confirm a discard. `SIGTERM`, `SIGHUP`, a
terminal-size failure, or an unexpected exception erases the application
region after restoring the terminal rather than leaving a possibly incomplete
or misleading view.

Retained editor text becomes part of terminal scrollback. Avoid this editor for
sensitive content when terminal history itself would be inappropriate. There
is currently no erase-on-exit option; one can be added if real workflows show
that both policies are needed.

## Display height

In automatic mode, the editor starts with one text row per logical buffer row,
bounded to 7–20 text rows, plus the footer. An empty file therefore occupies
eight total rows. A trailing newline contributes an editable trailing blank
row. The editor grows silently when editing adds enough logical rows, up to 20
text rows; it does not automatically shrink after deletions.

A numeric `--height` or `INEDIT_HEIGHT` value disables content-driven sizing
and keeps its existing meaning as a fixed requested total height. Use
`--height auto` to override a numeric environment setting for one invocation.

`Alt-Up` reduces the total editor height by one row and `Alt-Down` increases it
by one row. These commands work in both editing modes and in the help, search,
and vi Ex views. Each adjustment lasts for the current invocation only; it does
not change `INEDIT_HEIGHT` in the parent shell. The first manual adjustment
disables automatic growth for the rest of the invocation. The automatic
20-text-row ceiling does not restrict manual expansion.

The resulting `Height: N` message appears in the ordinary status line for
about one second after the most recent adjustment. Repeated adjustments restart
that interval. Afterward, the filename and normal status information return;
the timer never clears a newer save, warning, or error message.

Numeric and manually adjusted heights have a minimum total height of four rows,
including the footer. Expansion stops with one terminal row still outside the
application. When a terminal resize temporarily limits the editor, it can
return to its requested session height when room becomes available. A
deliberate height adjustment instead uses the currently visible height as its
starting point.

## Cutting, copying, and yanking

Clipboard commands use prompt-toolkit's Emacs bindings with an in-memory
clipboard that exists only for this invocation of `inedit`. Its capacity is
one: each cut or copy replaces the previous value, closer to a CUA-style
clipboard than an Emacs kill ring. It is not the desktop or terminal system
clipboard.

| Key | Current action |
|---|---|
| `Ctrl-Space` | Start a character selection |
| `Shift` + movement | Start or extend a selection when supported by the terminal |
| `Ctrl-G` | Open help without changing the buffer, cursor, or selection |
| `Ctrl-W` with a selection | Cut the selected text into the internal clipboard |
| `Ctrl-W` without a selection | Start a forward search instead of cutting |
| `Alt-W` with a selection | Copy the selection into the internal clipboard |
| `Ctrl-K` | Kill from the cursor to the end of the line; at end of line, kill the newline |
| `Ctrl-U` | Kill from the cursor back to the beginning of the line |
| `Alt-D` | Kill forward through the current or next word |
| `Ctrl-Y` | Yank the current internal clipboard value at the cursor |

There is no supported yank-pop or clipboard-history workflow. Prompt-toolkit's
inherited `Alt-Y` command has no useful effect with a one-entry clipboard.

Your terminal's own paste command—often `Ctrl-Shift-V`, `Shift-Insert`, or a
middle-click—can still send system-clipboard text as terminal input. That
shortcut belongs to the terminal emulator, not to `inedit`, and varies by
environment. Text killed inside `inedit` is not copied to the system clipboard.

## Search

Search uses prompt-toolkit's incremental search and temporarily replaces the
one-row status line with a search prompt. Search does not itself change the
current editor height. Matching wraps at the beginning or end of the buffer.

In default Emacs mode:

| Key | Action |
|---|---|
| `Ctrl-W` without a selection | Start a forward search |
| `Ctrl-W` while searching | Continue forward to the next match |
| `Ctrl-R` | Start or continue a reverse search |
| `Up`, `Down` while searching | Search backward or forward for another match |
| `Enter`, `Escape` | Accept the current match and close the search prompt |
| `Ctrl-C`, `Ctrl-G` | Cancel the search and restore the original cursor position |
| `F3` | Repeat the last accepted search in its original direction |

`Ctrl-W` remains Cut when a selection exists. `Ctrl-S` remains Save when no
search is active; once the search prompt is open, prompt-toolkit treats it as
another forward-search continuation key. A distinct `Shift-F3` binding is not
provided because prompt-toolkit and common terminal protocols do not expose it
portably.

## Keymap direction

Prompt-toolkit is the editing baseline. `inedit` should not reimplement a
prompt-toolkit editing command merely to resemble Nano or Emacs more closely.
This keeps selection boundaries, final-newline handling, cursor placement,
undo grouping, and clipboard behavior inside the library that owns the
buffer.

`inedit` currently adds `Ctrl-G` help, equivalent `Ctrl-X`/`Ctrl-C` prompted
exit, `Ctrl-S` save, `Ctrl-Z` undo, `Alt-E` redo, and `Alt-Up`/`Alt-Down`
display-height adjustment. System-clipboard support is a separate concern and
must not silently replace the one-entry internal clipboard.

## Inline help

Press `Ctrl-G` to replace the editing area with a scrollable key reference.
Press `Ctrl-G` again to return to the same buffer, cursor, and selection. The
status bar shows `^G Help` while editing and `^G Close` in the help view. Arrow
keys and `PageUp`/`PageDown` navigate the help text. The default help lists the
Emacs-mode commands documented below. With `--vi`, it instead lists the
supported vi modes, motions, operators, Visual selections, and Insert-mode
keys; Emacs-only commands are omitted.

## Movement in default Emacs mode

| Key | Action |
|---|---|
| `Left`, `Right` | Backward or forward one character, crossing logical-line boundaries |
| `Ctrl-A`, `Ctrl-E` | Start or end of the logical line |
| `Ctrl-B`, `Ctrl-F` | Backward or forward one character |
| `Ctrl-P`, `Ctrl-N` | Previous or next logical line |
| `Alt-B`, `Alt-F` | Backward or forward one word |
| `Ctrl-Left`, `Ctrl-Right` | Backward or forward one word |
| `Ctrl-Home`, `Ctrl-End` | Start or end of the buffer |

Long lines scroll horizontally rather than wrapping.

## Vi mode

With `--vi`, the editor starts in Normal mode. The status line displays
`[NORMAL]`, `[INSERT]`, `[REPLACE]`, or `[VISUAL]` as the active mode changes.
Use normal vi commands such as `i` or `a` to begin inserting text; `Escape`
returns to Normal mode. `Ctrl-S` keeps its global save meaning, while `Ctrl-X`
and `Ctrl-C` remain equivalent global exit commands.

Search in Normal mode follows vi: `/` searches forward, `?` searches
backward, `n` repeats the accepted search, and `N` repeats it in the opposite
direction. While entering a search, `Enter` or `Escape` accepts the current
match and `Ctrl-C` or `Ctrl-G` cancels it. These are prompt-toolkit's native vi
search bindings.

`ZZ` saves the current file only when the buffer is modified, then exits. It
does not display the interactive `Ctrl-X` save/discard prompt.

Normal mode also has a deliberately small Ex command line:

| Command | Action |
|---|---|
| `:w` | Save and return to Normal mode |
| `:q` | Exit if the buffer is unchanged; otherwise report an error |
| `:wq` | Save and exit |
| `:h` | Open the vi-mode help screen |
| `:external` | Open the buffer in `$VISUAL` or `$EDITOR`, falling back to `vi` |

The long forms `:write`, `:quit`, and `:help` also work. `:external` is an
`inedit` extension rather than a standard vi Ex command; it provides the same
external-editor handoff as default mode's `Alt-V`. Press `Escape` or `Ctrl-C`
to cancel command entry. Filenames, `!` variants, command options, and other
Ex commands are not supported. Use `Ctrl-X` when you want the interactive
save/discard prompt instead of vi-style `:q` behavior.

## File and save behavior

- Existing regular files and new files are supported.
- Text must be UTF-8, optionally with a UTF-8 BOM.
- Consistent LF and CRLF line endings are preserved. Mixed endings and bare
  carriage returns are rejected.
- The final newline is preserved as editable content.
- Saving follows symlinks rather than replacing the symlink itself.
- Atomic-save siblings are private while content is written. Saving preserves
  existing permission bits, while a new file receives the mode implied by the
  process umask.
- If another process changes the file after it is opened, saving is refused
  and the editor remains open.

## Recovery auto-saves

While the buffer is modified, `inedit` writes a recovery snapshot every 30
seconds without changing the file being edited. It follows Emacs's usual
naming convention: edits to `foo.txt` are copied to `#foo.txt#` in the same
directory. The recovery file is always mode `0600`, preserves the document's
UTF-8 BOM and newline format, and is installed and updated atomically.

An explicit save—including `Ctrl-S`, a `Y` response at the exit prompt, `:w`,
`:wq`, or `ZZ`—removes a recovery file created by this session. Discarding the
buffer or terminating abnormally leaves the latest snapshot for manual
recovery. A process killed before the first 30-second interval may not have
created one.

`inedit` never overwrites a pre-existing `#filename#`, because it may contain
work from an earlier session. Instead, it preserves that file, disables
auto-saving for the current edit, and reports the problem in the status line.
There is not yet an automatic recovery prompt; inspect, move, or remove the
recovery file explicitly.

## Exit statuses

| Status | Meaning |
|---|---|
| `0` | Exited with `Ctrl-X` or `Ctrl-C` after saving or with an unchanged buffer |
| `1` | Load, terminal, encoding, or runtime error |
| `2` | Command-line usage error |
| `130` | Exited through cancellation or `N`; any earlier `Ctrl-S` save remains on disk |

Normal editor-controlled exits retain the bounded text viewport and replace
the status bar with the plain summary described above. Abnormal termination
paths erase the region. Both policies restore terminal modes, bracketed paste,
signal handlers, and cursor visibility before returning control to the caller.

## Current limitations

- No direct system-clipboard integration.
- Search and replace is planned but not yet implemented.
- No syntax highlighting, mouse selection, multiple files, or automatic
  recovery-file selection.

## Contributing

Issues and focused pull requests are welcome. Please discuss substantial
changes first; contributions are submitted under the MIT License.

## License

`inedit` is available under the [MIT License](LICENSE.md).
