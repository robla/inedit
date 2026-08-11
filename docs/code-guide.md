# Reading the `inedit` code

`inedit.py` is still one importable script, but it has four intentional layers.
Read them in this order:

1. `main()` owns process concerns: arguments, TTY checks, terminal setup,
   signals, application execution, diagnostics, and exit status.
2. The data classes and file functions own document policy. Start with
   `Document`, then read `load_document()`, `save_document()`, and the three
   auto-save functions. None of these require a terminal.
3. The height, summary, and status functions are pure calculations. Their
   return values depend only on their arguments and are tested directly.
4. `EditorController` owns the prompt-toolkit application. Its constructor
   creates widgets; named methods implement lifecycle transitions; and
   `install_bindings()` is the complete application-level keymap.

`build_application()` is deliberately a small factory around the controller.
Prompt-toolkit continues to own ordinary buffer editing, selection, undo,
search state, vi state, rendering, and terminal input decoding.

## State to keep straight

- `EditorState.document` is the only current on-disk snapshot. A successful
  explicit save replaces it with the `Document` returned by `save_document()`.
- Modified means `buffer.text != state.original_text`; it is not a one-way
  dirty flag, so undo can make a buffer clean again.
- `EditorView` has exactly one of `EDITOR`, `HELP`, `EXIT_PROMPT`, or
  `EX_COMMAND`. Search remains prompt-toolkit state. Do not add another modal
  boolean when a new view belongs in this enum.
- `requested_height` is the session's desired height; `effective_height` is
  the terminal-capped value currently displayed.
- `AutoSaveState` describes only the recovery `#filename#`. It is separate
  from the target-file transaction and never marks the buffer clean.

## Safety invariants

- Loading, editing, auto-saving, cancellation, and signals do not mutate the
  target. Only the commit in `save_document()` does.
- Every explicit save rechecks the caller-visible path and resolved target
  before replacing anything.
- A save error leaves the editor open and retains the last known-good
  `Document` snapshot.
- Controlled exits prepare the retained final view before calling
  `Application.exit()`; abnormal exits leave `erase_when_done` enabled.
- Raw terminal handling stays outside `EditorController`. Prompt-toolkit owns
  the live terminal lifecycle once the application begins.

## Where a patch should go

| Change | Primary location |
|---|---|
| Decoding, paths, conflicts, permissions, saving | document/file functions |
| Status text, truncation, final summary | pure formatting functions |
| Save, exit, Help, Ex, height, external editor | `EditorController` method |
| Application-level key | `install_bindings()` plus the named method |
| Ordinary text editing behavior | prompt-toolkit configuration or binding |
| Startup, signals, process status | `main()` and terminal helpers |

A key change is incomplete until both mode-specific Help text and the README
agree with `install_bindings()`. A filesystem change should be tested without
constructing a terminal application first, then through the controller if it
changes user-visible lifecycle behavior.

## Tests

`test_inedit.py` is grouped by responsibility:

- `ArgumentTests`: CLI and environment parsing.
- `DocumentTests`: load and explicit-save transactions.
- `AutoSaveTests`: recovery snapshots.
- `LayoutAndStateTests`: pure presentation plus prompt-toolkit pipe-input
  behavior.
- `PtyIntegrationTests`: terminal byte streams, retained display, and signals.

The next low-risk structural step is to move these established layers into a
small internal package while keeping `inedit.py` as the executable facade.
That split should follow, not precede, the controller boundary so it remains a
mostly mechanical move with behavior protected by the current tests.
