# Emacs brainspace

`inedit` is not an Emacs emulator. “Brainspace compatibility” means that
Emacs habits transfer when they fit a one-file inline editor, and that every
departure is deliberate and easy to remember.

## Policy

1. Let prompt-toolkit own ordinary buffer editing.
2. Preserve Emacs movement, mark, region, kill, yank, and fill vocabulary.
3. Let `inedit` own save, exit, help, height, and external-editor lifecycle.
4. Borrow Nano's application shell only where the inline lifecycle needs it.

This gives editing compatibility, not command-language compatibility.

## Current key decisions

| Idea | Emacs | `inedit` default mode | Assessment |
|---|---|---|---|
| Character, line, word movement | `C-b/f`, `C-p/n`, `M-b/f`, `C-a/e` | Same | Strong match |
| Mark and region | `C-SPC`, `C-w`, `M-w` | Same while a region exists | Strong match |
| Kill and yank | `C-k`, `C-y` | Same, but one clipboard entry | Familiar subset |
| Kill backward | `C-u` is a prefix argument | `C-u` kills to line start | prompt-toolkit/readline behavior, not Emacs |
| Search | `C-s` forward, `C-r` backward | `C-w` forward without a region, `C-r` backward | `C-s` is reserved for Save |
| Save and exit | `C-x C-s`, `C-x C-c` | `C-s`, `C-x` or `C-c` | Nano-like lifecycle |
| Help and cancel | `C-h` help, `C-g` cancel | `C-g` help; it cancels an active search | Deliberate conflict |
| Undo and redo | `C-_`/`C-/` undo; undo history supplies redo | `C-_` or `C-z` undo, `M-e` redo | Convenience bindings |
| Fill paragraph | `M-q` | `M-q` | Direct match |
| `M-v` | Previous screen | External editor | Deliberate conflict |

The clipboard is intentionally CUA-like: the latest cut or copy replaces the
previous value. There is no kill-ring history or `M-y` yank-pop. The useful
brainspace is “mark, operate on region, yank,” not Emacs's full history model.

## The Emacs mode line

Emacs calls its status row the **mode line**. Each window has one. A separate
**echo area** below it carries messages and command prompts. The mode line
reports state; it does not advertise commands. See the GNU Emacs Manual's
[mode-line description](https://www.gnu.org/software/emacs/manual/html_node/emacs/Mode-Line.html).

The manual summarizes the default grammar as:

```text
cs:ch-dfr  buf      pos line   (major minor)
```

| Field | Meaning |
|---|---|
| `cs:` | Buffer/file encoding and newline convention; a text terminal adds keyboard-input and terminal-output encodings before `cs` |
| `ch` | `--` unchanged, `**` modified, `%%` read-only, `%*` read-only and modified |
| `d` | Dedicated-window marker, normally absent |
| `fr` | Frame name on a text terminal |
| `buf` | Buffer name, padded so it remains visually stable |
| `pos` | `All`, `Top`, `Bot`, or the percentage above the viewport |
| `line` | `L` and the one-based line number |
| `major` | Principal editing mode, such as Text or Python |
| `minor` | Active optional modes and exceptional state such as `Narrow` |

Columns are optional. When enabled, Emacs normally labels the column `C` and
counts from zero; line-plus-column may instead appear as `(line,column)`. See
[optional mode-line features](https://www.gnu.org/software/emacs/manual/html_node/emacs/Optional-Mode-Line.html).
Square brackets around the mode names mean a recursive edit is active. On a
text terminal, dashes fill the unused width. Those marks are grammar, not
decoration.

### Reading a terminal mode line

Consider this real example:

```text
-UUU:**--F1  moo            All (1,4)      (Fundamental Wrap) --------
```

| Text | Meaning |
|---|---|
| first `-` | The standard mode line's leading delimiter |
| first `U` | Keyboard-input coding system is UTF-8 |
| second `U` | Terminal-output coding system is UTF-8 |
| third `U` | Buffer/file coding system is UTF-8 |
| `:` | Unix newline convention; DOS or classic Mac files use another mark |
| `**` | Writable and modified; `--` would mean writable and clean |
| first `-` after `**` | The buffer's default directory is local; `@` means remote |
| `-F1` | Frame `F1`; text-terminal frame names include the leading hyphen |
| `moo` | Buffer name |
| `All` | The entire buffer fits in the window |
| `(1,4)` | Point is on line 1, column 4; columns are zero-based by default |
| `Fundamental` | The buffer's major mode |
| `Wrap` | Visual Line minor mode is active, so long logical lines wrap at word boundaries |
| final dashes | Unused mode-line width |

Thus `-UUU:` is not one opaque file property. It is a leading delimiter,
three UTF-8 coding-system mnemonics (keyboard, terminal, file), and the Unix
newline marker. `C-h C RET` describes the coding systems currently in use;
`M-x list-coding-systems` lists their mode-line letters.

The durable reading order is:

1. Is the buffer modified or read-only?
2. Which buffer is this?
3. Where is the viewport and point?
4. Which editing behavior is active?

The terseness works because normal state is quiet (`--`) and exceptional state
gets a visible token (`**`, `%%`, `Narrow`). Help is available elsewhere via
`C-h`; transient save feedback appears in the echo area. Emacs therefore spends
no permanent space on “Save” or “Exit” reminders. Its default line number is
enabled, while its column is optional.

## The mode-line-shaped `inedit` footer

Literal imitation would expose meaningless fields: `inedit` has one buffer,
one window, no major/minor modes in default mode, and a deliberately narrow
file-format contract. Borrow the information design, not every glyph.

The implemented default-mode forms combine Emacs-shaped telemetry with
permanent help for occasional users:

```text
-- COMMIT_EDITMSG   All L1 C1 | ^G Help | ^S Save | ^X/^C Exit | unchanged
** COMMIT_EDITMSG   Top L12 C7 | ^G Help | ^S Save | ^X/^C Exit | modified
```

- Lead with `--` or `**` instead of making the full state word primary.
- Keep the filename and one-based line/column numbers.
- Use `All`/`Top`/`Bot`/percentage for viewport position.
- Omit invariant encoding and mode fields.
- Keep only the application-level Help, Save, and Exit hints; `C-g` opens the
  full command reference.
- Repeat `unchanged` or `modified` at the end as a legend for the state glyph;
  omit it first when the row becomes crowded.
- Keep `[NORMAL]`, `[INSERT]`, and related labels in vi mode because they carry
  essential, changing state.
- Let search, Ex input, exit questions, errors, saves, and height feedback
  temporarily replace the footer, then restore it. That is the one-row
  `inedit` analogue of Emacs's separate echo area.

This would make the footer brainspace-compatible without pretending that
`inedit` has Emacs's buffer, window, mode, or command systems.

## Boundary to preserve

Do not chase compatibility by overriding more prompt-toolkit handlers. The
current movement and region vocabulary carries most of the benefit. The
remaining conflicts are memorable because they cluster around the inline
editor's application lifecycle.

Primary references: GNU Emacs Manual sections on the
[mode line](https://www.gnu.org/software/emacs/manual/html_node/emacs/Mode-Line.html),
[coding systems](https://www.gnu.org/software/emacs/manual/html_node/emacs/Coding-Systems.html),
[Visual Line mode](https://www.gnu.org/software/emacs/manual/html_node/emacs/Visual-Line-Mode.html),
[mark](https://www.gnu.org/software/emacs/manual/html_node/emacs/Setting-Mark.html),
[incremental search](https://www.gnu.org/software/emacs/manual/html_node/emacs/Incremental-Search.html),
[saving](https://www.gnu.org/software/emacs/manual/html_node/emacs/Save-Commands.html),
and [help](https://www.gnu.org/software/emacs/manual/html_node/emacs/Help.html).
