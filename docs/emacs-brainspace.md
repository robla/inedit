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
| `cs:` | Character encoding and newline convention |
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

A schematic line:

```text
U:**-F1  COMMIT_EDITMSG   Top L12   (Text)------------------------
  ^^     ^                ^   ^     ^
  dirty  buffer           view line editing mode
```

The exact encoding prefix varies. The durable reading order is:

1. Is the buffer modified or read-only?
2. Which buffer is this?
3. Where is the viewport and point?
4. Which editing behavior is active?

The terseness works because normal state is quiet (`--`) and exceptional state
gets a visible token (`**`, `%%`, `Narrow`). Help is available elsewhere via
`C-h`; transient save feedback appears in the echo area. Emacs therefore spends
no permanent space on “Save” or “Exit” reminders. Its default line number is
enabled, while its column is optional.

## A mode-line-shaped footer for `inedit`

Literal imitation would expose meaningless fields: `inedit` has one buffer,
one window, no major/minor modes in default mode, and a deliberately narrow
file-format contract. Borrow the information design, not every glyph.

Recommended normal forms:

```text
-- COMMIT_EDITMSG   All L1 C1
** COMMIT_EDITMSG   Top L12 C7
```

- Replace the words `unchanged` and `modified` with `--` and `**`.
- Keep the filename and one-based line/column numbers.
- Add `All`/`Top`/`Bot`/percentage only if viewport position proves useful.
- Omit invariant encoding and mode fields.
- Omit permanent key hints; `C-g` help remains the command reference.
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
[mark](https://www.gnu.org/software/emacs/manual/html_node/emacs/Setting-Mark.html),
[incremental search](https://www.gnu.org/software/emacs/manual/html_node/emacs/Incremental-Search.html),
[saving](https://www.gnu.org/software/emacs/manual/html_node/emacs/Save-Commands.html),
and [help](https://www.gnu.org/software/emacs/manual/html_node/emacs/Help.html).
