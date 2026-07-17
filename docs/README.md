# `inedit` user guide

`inedit.py` is a small editor that stays in the terminal's normal screen. It
uses a bounded region below the shell prompt, leaves earlier shell output
visible, and returns when the file is saved or the edit is canceled.

This page documents the current behavior. Proposed changes are tracked in
[roadmap.md](roadmap.md). The default keymap is **Nano-first**, with a small
set of deliberate `mg`/Emacs exceptions for region selection and kill-ring
operations.

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
- A comma means a sequence. For example, `Ctrl-X`, `R`, `Y` means press and
  release Ctrl-X, then type R, then type Y.

## Essential keys

| Key | Current action |
|---|---|
| `Ctrl-S` | Save and exit |
| `Ctrl-C` | Cancel |
| `Ctrl-G` | Open or close the inline help view |
| `Enter` | Insert a newline |
| `Ctrl-Z`, `Alt-U` | Undo |
| `Alt-E` | Redo |
| `Ctrl-Y`, `Ctrl-U` | Paste/yank from the internal kill ring |
| Arrow keys | Move by character or logical line |
| `Home`, `End` | Move to the start or end of the logical line |
| `PageUp`, `PageDown` | Move by a viewport |
| `Backspace`, `Delete` | Delete without adding text to the kill ring |

If the buffer is unchanged, `Ctrl-C` cancels immediately. After an edit, press
`Ctrl-C` twice to discard changes. The first press displays `Unsaved changes;
Ctrl-C again to discard`. Editing the buffer disarms that confirmation.

## Cutting, copying, and yanking

The current clipboard behavior comes from prompt-toolkit's Emacs bindings. It
uses an in-memory kill ring that exists only for this invocation of `inedit`.
It is not the desktop or terminal system clipboard.

| Key | Current action |
|---|---|
| `Ctrl-Space` | Start a character selection |
| `Shift` + movement | Start or extend a selection when supported by the terminal |
| `Ctrl-G` | Open help without changing the buffer, cursor, or selection |
| `Ctrl-W` with a selection | Cut the selected text into the internal kill ring |
| `Ctrl-W` without a selection | Kill the whitespace-delimited word before the cursor |
| `Alt-W` with a selection | Copy the selection into the internal kill ring |
| `Ctrl-K` | Cut the selection, or the entire current line when nothing is selected |
| `Alt-6` | Copy the selection, or the entire current line when nothing is selected |
| `Alt-D` | Kill forward through the current or next word |
| `Ctrl-U`, `Ctrl-Y` | Yank the newest internal kill-ring entry at the cursor |
| `Alt-Y` after a yank | Replace that yank with the next kill-ring entry |

Consecutive `Ctrl-K` presses accumulate whole lines into one pasteable entry,
as in Nano. Any intervening key starts a new entry on the next `Ctrl-K`.
Earlier entries remain available through `Alt-Y` after a yank.

Your terminal's own paste command—often `Ctrl-Shift-V`, `Shift-Insert`, or a
middle-click—can still send system-clipboard text as terminal input. That
shortcut belongs to the terminal emulator, not to `inedit`, and varies by
environment. Text killed inside `inedit` is not copied to the system clipboard.

## Keymap direction

Nano is the primary user-interface precedent for the default mode. When a key
has no `inedit`-specific requirement and Nano and `mg` disagree, prefer Nano.
This is a usability baseline rather than a promise to emulate every Nano
command. Nano's compact, task-focused interaction model is closer to the
intended experience; `mg` is a reference for particular editing semantics,
not the overall user interface.

The intended exception is text selection and clipboard-like editing. Keep the
useful `mg`/Emacs model of a mark, a selected region, an internal kill ring,
yank, and yank rotation. This makes operations on arbitrary text possible
without giving up Nano's more discoverable exit, help, and ordinary line
editing conventions.

The current allocation is:

| Source | Planned behavior |
|---|---|
| Nano | `Ctrl-G` help, `Ctrl-K` line cut, `Alt-6` line copy, and `Ctrl-U` paste |
| Nano | `Alt-U`/`Alt-E` for undo/redo; `Ctrl-Z` remains an undo convenience |
| `mg`/Emacs exception | `Ctrl-Space` mark, `Ctrl-W` cut region, and `Alt-W` copy region |
| `mg`/Emacs exception | `Ctrl-Y` yank and `Alt-Y` rotate the kill ring |
| `inedit` convention | `Ctrl-S` saves and exits; `Ctrl-C` safely cancels |

Nano-style `Ctrl-X` exit is the most important remaining keymap change. System
clipboard support is a separate concern: it should complement the internal
kill ring rather than silently replace it.

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
meanings. Version 1 has no Ex command line, so commands such as `:wq` are not
available.

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
| `0` | Saved, or an unchanged file was accepted with `Ctrl-S` |
| `1` | Load, terminal, encoding, or runtime error |
| `2` | Command-line usage error |
| `130` | Canceled without saving |

## Current limitations

- No direct system-clipboard integration.
- `Ctrl-X` is currently an Emacs prefix rather than Nano-style exit.
- No search and replace, syntax highlighting, mouse selection, multiple files,
  or crash-recovery file.
