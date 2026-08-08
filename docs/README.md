# `inedit` user guide

`inedit.py` is a small editor that stays in the terminal's normal screen. It
uses a bounded region below the shell prompt, leaves earlier shell output
visible, and returns control to the caller when the editing session exits. Git
commit messages are a typical use, but it can edit any one named UTF-8 text
file.

This page documents the current behavior. Proposed changes are tracked in
[roadmap.md](roadmap.md). Text editing follows prompt-toolkit's default Emacs
bindings wherever practical. Nano inspires application-level controls such as
help and the exit flow, rather than replacing the editing engine.

## Starting the editor

```text
inedit.py [--height ROWS] [--vi] [--no-line-numbers] FILE
```

- `--height ROWS` changes the total editor height. The default is
  `${INEDIT_HEIGHT:-20}`.
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

`Ctrl-X` and `Ctrl-C` are equivalent main-screen exit commands. An unchanged
buffer exits immediately. A modified buffer displays
`Save modified buffer? Y Yes | N No | ^C Cancel`:

- `Y` saves and exits;
- `N` exits without saving; and
- `Ctrl-C` cancels the prompt and returns to editing.

Thus, `Ctrl-C Ctrl-C` cannot discard the buffer: the first press opens the
prompt and the second returns to editing.

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
one-row status line with a search prompt. The editor remains the same fixed
height. Matching wraps at the beginning or end of the buffer.

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
exit, `Ctrl-S` save, `Ctrl-Z` undo, and `Alt-E` redo. System-clipboard support
is a separate concern and must not silently replace the one-entry internal
clipboard.

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
- Saving is atomic and preserves existing permission bits.
- If another process changes the file after it is opened, saving is refused
  and the editor remains open.

## Exit statuses

| Status | Meaning |
|---|---|
| `0` | Exited with `Ctrl-X` or `Ctrl-C` after saving or with an unchanged buffer |
| `1` | Load, terminal, encoding, or runtime error |
| `2` | Command-line usage error |
| `130` | Exited through cancellation or `N`; any earlier `Ctrl-S` save remains on disk |

The current version erases its bounded editor region on exit. Retaining the
final rendered editor view in terminal history—similar to the visible result
associated with `less -X`—is planned for evaluation; see
[roadmap.md](roadmap.md). Terminal modes and cursor visibility must be restored
regardless of which display policy is selected.

## Current limitations

- No direct system-clipboard integration.
- Search and replace is planned but not yet implemented.
- No syntax highlighting, mouse selection, multiple files, or crash-recovery
  file.
