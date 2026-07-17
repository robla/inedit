# `inedit` user guide

`inedit.py` is a small editor that stays in the terminal's normal screen. It
uses a bounded region below the shell prompt, leaves earlier shell output
visible, and returns when the file is saved or the edit is canceled.

This page documents the current behavior. Proposed changes are tracked in
[roadmap.md](roadmap.md). Text editing follows prompt-toolkit's default Emacs
bindings wherever practical. Nano inspires application-level controls such as
help and the planned exit flow, rather than replacing the editing engine.

## Starting the editor

```text
inedit.py [--height ROWS] [--vi] [--no-line-numbers] FILE
```

- `--height ROWS` changes the total editor height. The default is
  `${INEDIT_HEIGHT:-20}`.
- `--vi` selects prompt-toolkit's vi editing mode. Emacs mode is the default.
- `--no-line-numbers` hides the line-number gutter.
- `--` permits a filename beginning with `-`.

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
| `Ctrl-C` | Cancel using the current safe-cancel behavior |
| `Ctrl-G` | Open or close the inline help view |
| `Enter` | Insert a newline |
| `Ctrl-Z`, `Ctrl-_` | Undo |
| `Alt-E` | Redo |
| `Ctrl-Y` | Paste/yank the latest internal clipboard value |
| Arrow keys | Move by character or logical line |
| `Home`, `End` | Move to the start or end of the logical line |
| `PageUp`, `PageDown` | Move by a viewport |
| `Backspace`, `Delete` | Delete without changing the internal clipboard |

If the buffer is unchanged, `Ctrl-C` cancels immediately. After an edit, press
`Ctrl-C` twice to discard changes. The first press displays `Unsaved changes;
Ctrl-C again to discard`. Editing the buffer disarms that confirmation.

`Ctrl-X` is the normal exit command. An unchanged buffer exits immediately. A
modified buffer displays `Save modified buffer? Y Yes | N No | ^C Cancel`:

- `Y` saves and exits;
- `N` exits without saving; and
- `Ctrl-C` cancels the prompt and returns to editing.

The main editor's `Ctrl-C` behavior is intentionally unchanged for now. The
key is reserved, however, and may later adopt Nano's cursor-position behavior.

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
| `Ctrl-W` without a selection | Kill the whitespace-delimited word before the cursor |
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

## Keymap direction

Prompt-toolkit is the editing baseline. `inedit` should not reimplement a
prompt-toolkit editing command merely to resemble Nano or Emacs more closely.
This keeps selection boundaries, final-newline handling, cursor placement,
undo grouping, and clipboard behavior inside the library that owns the
buffer.

`inedit` currently adds `Ctrl-G` help, `Ctrl-X` prompted exit, `Ctrl-S` save,
`Ctrl-C` safe cancel, `Ctrl-Z` undo, and `Alt-E` redo. System-clipboard support
is a separate concern and must not silently replace the one-entry internal
clipboard.

## Inline help

Press `Ctrl-G` to replace the editing area with a scrollable key reference.
Press `Ctrl-G` again to return to the same buffer, cursor, and selection. The
status bar shows `^G Help` while editing and `^G Close` in the help view. Arrow
keys and `PageUp`/`PageDown` navigate the help text.

## Movement in default Emacs mode

| Key | Action |
|---|---|
| `Ctrl-A`, `Ctrl-E` | Start or end of the logical line |
| `Ctrl-B`, `Ctrl-F` | Backward or forward one character |
| `Ctrl-P`, `Ctrl-N` | Previous or next logical line |
| `Alt-B`, `Alt-F` | Backward or forward one word |
| `Ctrl-Left`, `Ctrl-Right` | Backward or forward one word |
| `Ctrl-Home`, `Ctrl-End` | Start or end of the buffer |

Long lines scroll horizontally rather than wrapping.

## Vi mode

With `--vi`, prompt-toolkit supplies vi insert and normal modes. `Escape`
returns to normal mode. `Ctrl-S` and `Ctrl-C` keep their global save and cancel
meanings, and `Ctrl-X` remains the global prompted exit command. Version 1 has
no Ex command line, so commands such as `:wq` are not available.

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
| `0` | Exited with `Ctrl-X` after saving or with an unchanged buffer |
| `1` | Load, terminal, encoding, or runtime error |
| `2` | Command-line usage error |
| `130` | Exited through cancellation or `N`; any earlier `Ctrl-S` save remains on disk |

## Current limitations

- No direct system-clipboard integration.
- No search and replace, syntax highlighting, mouse selection, multiple files,
  or crash-recovery file.
