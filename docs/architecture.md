# `inedit.py` architecture

## Purpose

Build `inedit.py` as a small, importable Python program whose version-1
contract is recorded in [roadmap.md](roadmap.md). `prompt_toolkit` owns terminal input,
Unicode-aware rendering, scrolling, and basic editing. `inedit.py` owns the
command line, document format, file-conflict policy, atomic save transaction,
editor state, status line, and process exit status.

The central safety rule is simple: loading and editing are read-only. The only
code allowed to mutate the target is the final commit step in
`save_document()`. Cancellation, validation errors, conflicts, signals, and
exceptions never call that step.

## Delivery shape

Keep version 1 in two source files at the repository root:

- `inedit.py`: executable entry point and importable implementation.
- `test_inedit.py`: unit and terminal-integration tests.

Use only the Python standard library and
`prompt_toolkit>=3.0.36,<4`. Do not add Rich, Textual, a packaging framework,
or a source-package hierarchy for the first version. Give `inedit.py` a
`#!/usr/bin/env python3` shebang, but keep all behavior behind `main()` so
tests can import it without side effects.

## Data model

Use small data objects so filesystem policy can be tested without constructing
a terminal application:

```python
class NewlineStyle(Enum):
    LF = "lf"
    CRLF = "crlf"

@dataclass(frozen=True)
class Fingerprint:
    device: int
    inode: int
    size: int
    mtime_ns: int

@dataclass(frozen=True)
class Document:
    display_path: str
    requested_path: Path
    target_path: Path
    text: str
    newline_style: NewlineStyle
    has_bom: bool
    mode: int | None
    fingerprint: Fingerprint | None
    entry_fingerprint: Fingerprint | None

@dataclass
class EditorState:
    document: Document
    original_text: str
    message: str | None = None
    discard_armed: bool = False
    help_visible: bool = False
    exit_prompt: bool = False
    effective_height: int = 20

    def is_modified(self, current_text: str) -> bool: ...

class ExitReason(Enum):
    SAVED = "saved"
    CANCELED = "canceled"
    ERROR = "error"
```

`display_path` preserves the spelling supplied by the caller for diagnostics
and the status line. `requested_path` is its absolute, non-symlink-resolved
form. `target_path` is the real path whose directory receives the temporary
file. Keeping all three is necessary to follow a symlink while replacing its
target rather than the symlink itself.

A target `Fingerprint` comes from `os.stat()` and contains exactly the identity,
size, and nanosecond modification time required for conflict checks. The entry
fingerprint comes from `os.lstat()` and also detects replacement or retargeting
of the caller-visible path. Compare the resolved target path again at save
time. These extra route checks make symlink handling conservative without
claiming to provide file locking.

Compute modified state as `buffer.text != original_text`; do not maintain a
one-way dirty bit. Undoing all changes must return the buffer to the unmodified
state.

## Command-line and startup flow

`parse_args(argv, environ)` should build an `argparse.ArgumentParser` for:

```text
inedit.py [--height ROWS] [--vi] [--no-line-numbers] FILE
```

Implement startup in this order:

1. Read `INEDIT_HEIGHT`, defaulting to `20`, and let an explicit `--height`
   override it. Parse both through one integer validator that rejects values
   below 4. `argparse` reports these failures and returns status 2.
2. Let `argparse` handle `-h`, `--help`, one required path, extra operands, and
   the `--` separator.
3. Require both stdin and stdout to be TTYs. Print a concise diagnostic to
   stderr and return 1 before constructing an application if either is not.
4. Call `load_document()` before terminal setup. Load errors therefore cannot
   leave terminal modes changed.
5. Read the output terminal size. If it has fewer than five rows, report that
   a four-row editor plus one outside row cannot fit and return 1.
6. Set the initial effective height to
   `min(configured_height, terminal_rows - 1)`.
7. Build and run the application, map its result to 0, 1, or 130, and keep
   successful operation silent.

Pass dependencies such as `environ`, terminal size, input, and output into
small helpers where doing so makes edge cases testable. Do not read global
process state at module import time.

## Loading a document

Implement `load_document(path: Path) -> Document` as a deterministic sequence:

1. Preserve the caller's spelling for display, separately convert it to an
   absolute path without resolving symlinks, and capture `os.lstat()` when a
   directory entry exists.
2. Resolve the path with symlinks followed. Capture `os.stat()` on the target.
   Reject a directory, socket, device, FIFO, or any other non-regular target.
   A missing ordinary path starts an empty document. A dangling symlink may
   point at a new target, but its symlink entry and resolved target path must be
   recorded and rechecked before saving.
3. Open an existing target in binary mode and read it completely. Treat an I/O
   error as a load error; never offer a partially loaded buffer.
4. Reject any NUL byte. Detect and remove one leading UTF-8 BOM, then decode
   with strict UTF-8. A BOM elsewhere is ordinary Unicode content.
5. Validate newlines before normalization. Reject any `\r` not paired with a
   following `\n`. If CRLF and standalone LF occur together, reject the file
   as mixed. Select CRLF when all newline tokens are CRLF; otherwise select LF,
   including for a file with no newline.
6. Replace CRLF with LF for the in-memory text. Do not strip or append a final
   newline.
7. Save `stat.S_IMODE(st_mode)` and the target fingerprint. For a missing
   target, store `None` for its mode and target fingerprint. Keep the entry
   fingerprint when that missing target is reached through a dangling symlink.

Keep decoding and newline analysis in pure helpers and test them with bytes.
Use domain exceptions such as `LoadError`, `SaveError`, and `ConflictError` so
the UI can show expected errors without exposing tracebacks.

## Application construction

`build_application(document, options)` returns the application plus the state
and text area needed by tests. Construct one persistent `TextArea` with:

```python
TextArea(
    text=document.text,
    multiline=True,
    wrap_lines=False,
    scrollbar=True,
    line_numbers=not options.no_line_numbers,
    height=lambda: state.effective_height - 1,
)
```

Do not install an accept handler: Enter must insert a newline. Construct a
second read-only, scrollable `TextArea` for help. A `DynamicContainer` selects
the editor or help body without replacing the editing buffer. Put that body
and a `Window(FormattedTextControl(...), height=1)` in an `HSplit` whose height
is `lambda: state.effective_height`. Focus the text area in the `Layout`.
Configure the application explicitly:

```python
Application(
    layout=layout,
    key_bindings=bindings,
    editing_mode=EditingMode.VI if options.vi else EditingMode.EMACS,
    on_reset=initialize_vi_mode,
    enable_page_navigation_bindings=True,
    full_screen=False,
    erase_when_done=True,
    terminal_size_polling_interval=0.5,
)
```

`full_screen=False` and `erase_when_done=True` are invariants, not configuration
choices. A later refactor should have a test that fails if either changes. In
vi mode, the `on_reset` handler must select `InputMode.NAVIGATION` because
prompt-toolkit otherwise resets every application run to Insert mode.

### Resize behavior

Before each render, obtain the current output size and update
`state.effective_height` to `min(configured_height, rows - 1)`. The callable
container heights let prompt_toolkit apply the new value without replacing the
buffer. Invalidate the application when the value changes. If a resize leaves
fewer than five rows, exit the application with an error result; report the
diagnostic only after prompt_toolkit has restored the terminal.

## State and key bindings

Register a buffer text-change callback. On every actual edit it must:

- recompute modified state by comparison with `original_text`;
- disarm discard confirmation;
- clear a transient save error; and
- invalidate the application so the status row redraws.

Let prompt_toolkit provide vertical movement, Home, End, deletion, newline
insertion, vi navigation, and viewport movement. Its horizontal cursor methods
stop at logical-line boundaries, so add narrow Left and Right bindings that
move one absolute buffer character in Emacs mode and vi insert mode. This makes
the newline traversable without changing vi normal-mode motions. Add eager
global bindings for Ctrl-S, Ctrl-X, Ctrl-C, and Ctrl-G so save, exit, and help
do not depend on editing mode. In the default mode, let prompt-toolkit own
selection, cutting, copying, and yanking. Add only `Ctrl-Z` undo and `Alt-E`
redo as editing conveniences.

Ctrl-S follows one path:

1. If the buffer equals the original text, display `No changes to save` and
   remain in the editor without touching the filesystem.
2. Otherwise call `save_document(document, buffer.text)` synchronously. These
   files are intentionally small, so a worker thread would add state races for
   no practical benefit.
3. On success, replace the current document snapshot with the returned
   snapshot, set `original_text` to the saved text, display `Saved`, and remain
   in the editor.
4. On `SaveError`, remain in the editor, put the concise error in the status
   state, disarm discard confirmation, and redraw.

Ctrl-X and main-screen Ctrl-C share the same exit request. They exit
immediately with `SAVED` when the buffer is unchanged. When it is modified,
set `exit_prompt` and make the editing `TextArea` temporarily read-only. Render
`Save modified buffer? Y Yes | N No | ^C Cancel` in the status row. `Y` uses
the Ctrl-S save path and exits with `SAVED`, `N` exits with `CANCELED` without
writing, and Ctrl-C clears the prompt and returns to the same editing buffer.
Ignore other printable answers. In particular, Ctrl-C Ctrl-C must never
discard changes.

A real SIGINT remains a separate safe-cancellation transition:

```text
unmodified              -> CANCELED
modified, not armed     -> modified, armed; show discard reminder
modified, already armed -> CANCELED
```

Any buffer edit moves the armed state back to not armed. Cursor movement does
not, so a second SIGINT remains usable after inspecting nearby text.

### Prompt-toolkit-aligned keymap

Prompt-toolkit's Emacs mode is the editing substrate and the default editing
contract. Nano remains a precedent for application-level help and the
implemented exit interaction. Do not reimplement buffer-editing commands merely to align
with another editor's keymap; doing so creates unnecessary cursor, selection,
newline, undo, and clipboard edge cases.

`inedit` explicitly owns:

- `Ctrl-G` help;
- equivalent `Ctrl-X`/Ctrl-C prompted exit and `Ctrl-S` save-without-exit;
- conditional `Ctrl-W` forward-search entry when no selection exists;
- Left/Right traversal across logical-line boundaries;
- `Ctrl-Z` undo and `Alt-E` redo; and
- the `inedit` save and safe-exit operations that protect the file lifecycle.

Configure prompt-toolkit's `InMemoryClipboard` with `max_size=1`. Its native
`Ctrl-Space`, `Alt-W`, `Ctrl-K`, `Ctrl-U`, and `Ctrl-Y` handlers provide
selection and clipboard operations with a single latest value rather than a
kill-ring history. Its native `Ctrl-W` still cuts when a selection exists. Do
not advertise `Alt-Y` yank-pop. Keep internal yank and external
system-clipboard paste as separate operations even if a later integration
lets a cut populate both.

Keep the explicit binding surface small. Test the prompt-toolkit commands on
which the public guide relies, but avoid wrapping or copying their handlers.
Generate or validate status-line and `Ctrl-G` help entries from a small
intentional-binding registry if documentation drift becomes a problem.

Build the read-only help area from one of two static references. Default mode
documents the Emacs-oriented bindings, including the custom formatting and
external-editor commands. Vi mode instead documents prompt-toolkit's supported
mode changes, motions, operators, Visual selections, and Insert-mode keys. It
must omit Emacs-only commands and document only the implemented Ex subset.

In vi Normal mode, `:` replaces the status row with a focused, one-line
prompt-toolkit `TextArea` and temporarily uses vi Insert mode for command-line
editing. `Escape` or Ctrl-C restores Normal mode without executing. Enter
trims and dispatches only these commands:

| Command | Required transition |
|---|---|
| `w`, `write` | Use the Ctrl-S save path, then return to Normal mode |
| `q`, `quit` | Exit only if unchanged; otherwise show `No write since last change` |
| `wq` | Use the save path and exit only after a successful save |
| `h`, `help` | Open the mode-specific vi help view |
| `external` | Invoke the same external-editor action as default-mode Alt-V |

Also bind Normal-mode `ZZ` directly to vi's `:xit` semantics: exit immediately
when unchanged, save and exit when modified, and remain open if saving fails.
It must not enter the interactive `Ctrl-X` save/discard prompt.

Unknown commands return to Normal mode and show a concise error. Do not parse
filenames, bang variants, options, command separators, or the broader Ex
language. Plain `:` remains insertable text in default Emacs mode.

### Incremental search

Attach one prompt-toolkit `SearchToolbar` to the editing `TextArea` through
its `search_field` parameter. Keep that toolbar permanently in the layout tree
so prompt-toolkit can focus its `SearchBufferControl`; its own conditional
container gives it zero height when inactive. Put the Ex command area and
ordinary status window in complementary conditional containers. Exactly one
of the search toolbar, Ex prompt, or status window then occupies the footer
row at a time.

Prompt-toolkit owns query state, incremental cursor movement, acceptance,
cancellation, and vi repetition. In default Emacs mode, override `Ctrl-W`
only under these two conditions: with no selection and no active search, call
prompt-toolkit's forward-search entry binding; during a search, call its
forward-repeat binding. When a selection exists, allow the native region-cut
binding to win. Retain prompt-toolkit's native `Ctrl-R`, Up/Down, Enter,
Escape, `Ctrl-C`, and `Ctrl-G` search behavior. Application-level save, help,
and exit bindings must be filtered out while search has focus so they cannot
steal those search keystrokes. Outside search, `Ctrl-S` remains Save.

In vi mode, rely on prompt-toolkit's native `/` and `?` entry, `n`/`N`
repetition, and search-prompt controls. Do not reproduce vi's search state in
`inedit`. The accepted query already remains on the editing `BufferControl`;
default mode's `F3` binding applies that retained `SearchState` again in its
original direction. Do not advertise `Shift-F3`: prompt-toolkit 3.0.36 and
common terminal input protocols do not provide a portable shifted-F3 key.

## Status line

Generate status fragments on demand from the requested filename, the current
buffer document, modified state, message, and key reminder. Use one-based
logical line and column numbers. The stable right side is:

```text
Ln N, Col N | modified/unchanged | ^G Help | ^S Save | ^X/^C Exit
```

In vi mode, insert `[NORMAL]`, `[INSERT]`, `[REPLACE]`, or `[VISUAL]` after
the modified state. Derive it on every status render from prompt-toolkit's
current vi and selection state so transitions appear immediately. The mode
label is part of the stable suffix and is omitted in Emacs mode.

Place a save error or discard reminder before that stable suffix. Calculate
width in terminal cells with prompt_toolkit's Unicode-width helpers. When the
row is too narrow, left-truncate the filename first with an ellipsis, then the
transient message. Preserve cursor position and key hints as long as the
terminal width permits. The status `Window`, search toolbar, and Ex command
line must each remain one row and must not wrap. Their conditional containers
must be mutually exclusive without removing the search control from the
layout tree.

## Save transaction

`save_document(document, text)` must perform all fallible validation and byte
construction before it creates a temporary file:

1. Reject an in-memory NUL or text that cannot be strictly UTF-8 encoded.
2. Convert LF to the document's original newline style and prepend the UTF-8
   BOM only when the loaded file had one.
3. Re-resolve `requested_path` and compare it with `target_path`. Recheck the
   caller-visible entry with `lstat()` and the target with `stat()`.
   For an original file, device, inode, size, and `mtime_ns` must all match.
   For a new target, no target may now exist. Any mismatch is a conflict and
   leaves the editor open.
4. Create a randomly named temporary sibling with `os.open()` using
   `O_CREAT | O_EXCL | O_WRONLY`. Use mode `0o666` for a new file so the
   process umask applies normally. For an existing file, call `os.fchmod()`
   with the recorded permission bits.
5. Write all encoded bytes, checking for short writes, flush, call
   `os.fsync()`, and capture the descriptor's final fingerprint and mode before
   closing it. No target mutation has occurred yet.
6. Run the conflict check again immediately before commit to narrow the race
   window.
7. Commit with `os.replace(temp_path, target_path)`. Because `target_path` is
   resolved, an existing symlink remains in place and its target inode is
   replaced atomically.
8. Mark the transaction successful as soon as `os.replace()` succeeds. Do not
   perform a later operation whose failure would falsely report that an
   already-replaced file was unsaved.
9. Return a new `Document` containing the saved text, captured target
   fingerprint and mode, and the correct caller-visible entry fingerprint.
   This snapshot allows repeated Ctrl-S saves without falsely detecting the
   editor's own prior atomic replacement as an external conflict.

Wrap steps 4 through 7 in `try/finally`. If the temporary pathname still
exists, unlink only that exact sibling. Cleanup failure may be included in the
save error, but must never trigger a second replacement or remove the target.

This is optimistic conflict detection, not locking. Another writer can still
race between the final check and `os.replace()`. File locking protocols are a
version-1 non-goal and this limitation should remain explicit. Atomic replace
also intentionally does not preserve hard-link identity, extended attributes,
ACLs, or ownership beyond what the process and operating system provide.

## Signals, exceptions, and exit statuses

Run the application inside a narrow exception boundary. Ctrl-C input is
handled by the shared exit binding. A real `SIGINT` follows the separate safe
signal-cancellation path; `KeyboardInterrupt` cancels without saving and
returns 130. Temporarily install `SIGTERM` and `SIGHUP`
handlers that raise a private termination exception; unwinding through
`Application.run()` lets prompt_toolkit's `finally` blocks restore terminal
modes and the cursor. Restore the process's previous handlers afterward.

Map results exactly:

- `SAVED` -> 0
- `CANCELED` or `KeyboardInterrupt` -> 130; an earlier successful Ctrl-S save
  is not rolled back
- load, terminal, encoding, save-independent runtime, termination-signal, or
  unexpected error -> 1
- `argparse` usage failure -> 2

For an unexpected exception, restore the terminal first, then print a concise
diagnostic to stderr. A traceback is appropriate only behind an explicit
future debug option. No exception handler may attempt an implicit save.

## Verification strategy

Use temporary directories for every filesystem test. Unit-test:

- argument and environment height parsing, including `--` filenames;
- initial and resized height capping;
- UTF-8, UTF-8 BOM, decode errors, and NUL rejection;
- LF, CRLF, mixed endings, bare CR, empty files, and final-newline retention;
- existing files, new files, symlinks, dangling symlinks, and rejected types;
- unchanged Ctrl-S avoiding a write and remaining in the editor;
- repeated saves, including through a symlink, refreshing conflict snapshots;
- permission preservation and umask-respecting new-file creation;
- conflict detection for content changes, replacement, deletion, creation,
  and symlink retargeting;
- temporary-file cleanup on write, fsync, and replace failures;
- dirty-state reversal through undo, the two-stage SIGINT state machine, and
  the shared Ctrl-X/Ctrl-C `Y`/`N`/Ctrl-C prompt;
- Left/Right traversal in both directions across logical-line boundaries,
  including vi insert mode;
- exact exit-status mapping.

Use prompt_toolkit pipe input and dummy output for key-binding tests. Send text,
Enter, selection, native cut/copy/yank, undo/redo, help, save, prompted exit,
and cancel as actual input bytes and assert the application result, buffer,
clipboard, and target bytes.

Add PTY tests for behavior that dummy output cannot prove:

1. Start with an 80x24 PTY containing a sentinel line above the editor.
2. Capture all terminal output and reject `CSI ? 1049 h`, `CSI ? 1047 h`, and
   `CSI ? 47 h`.
3. Verify a height request of 20 does not consume more than 20 rows and the
   sentinel remains visible.
4. Exercise horizontal scrolling, vertical scrolling, line numbers, and both
   editing modes.
5. Open help, verify it renders without an alternate screen, close it, and
   confirm the editing buffer is unchanged.
6. Open the Ctrl-X prompt, verify it renders inline, cancel it, and continue
   editing.
7. Save, exit, and cancel modified buffers and compare exact bytes and statuses.
8. Inject a conflict and a save failure while the UI is open; confirm the
   editor remains usable.
9. Resize the PTY, then send `SIGINT`, `SIGTERM`, and `SIGHUP`; verify cleanup,
   cursor restoration, and no implicit write.

Finally, run a manual smoke test as the editor for `dsedit` with more lines than
the viewport, spaces in paths, a long horizontal line, save, and cancel.

## Build order and completion gates

Implement in reviewable slices:

1. CLI parsing, data classes, decoding, and load tests.
2. Encoding, conflict checks, atomic replacement, and filesystem tests.
3. Editor state and pure status formatting tests.
4. Layout and key bindings with pipe-input tests.
5. Signal handling, PTY tests, and `dsedit` smoke testing.

A slice is complete only when its focused tests pass. Version 1 is complete
when the entire specification is covered, captured PTY output contains no
alternate-screen entry sequence, cancel and every tested failure preserve the
original bytes, a successful save preserves the specified format and mode,
and `python3 inedit.py --help` accurately documents the shipped behavior and
known atomic-replacement limitations.
