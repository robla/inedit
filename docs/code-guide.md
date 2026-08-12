# Reading the `inedit` code

`inedit.py` is the executable and compatibility facade. The implementation is
in the deliberately internal `_inedit` package. Read it in this order:

1. `_inedit/model.py` defines errors, enums, and data classes. It imports only
   the standard library and contains no file or terminal operations.
2. `_inedit/storage.py` owns decoding, loading, conflicts, explicit saves, and
   recovery files. Start with `Document`, `load_document()`, and
   `save_document()`; none require a terminal.
3. `_inedit/presentation.py` contains Help text and pure height, summary, and
   status calculations.
4. `_inedit/application.py` contains `EditorController`. Its constructor
   creates widgets, named methods implement lifecycle transitions, and
   `install_bindings()` is the complete application-level keymap.
5. `_inedit/terminal.py` owns TTY checks, signals, the cursor guard,
   application execution, diagnostics, and process exit status.
6. `inedit.py` re-exports the established import surface, wires replaceable
   side effects into the controller, and supplies `main()`.

`build_application()` remains a small public factory around the controller.
`EditorDependencies` makes save, recovery, and timer seams explicit without
letting the controller know about the facade. Prompt-toolkit continues to own
ordinary buffer editing, selection, undo, search state, vi state, rendering,
and terminal input decoding.

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
| Data shape or shared expected error | `_inedit/model.py` |
| Arguments or `INEDIT_HEIGHT` | `_inedit/cli.py` |
| Decoding, paths, conflicts, permissions, saving | `_inedit/storage.py` |
| Help, status text, truncation, final summary | `_inedit/presentation.py` |
| Save, exit, Help, Ex, height, external editor | `_inedit/application.py` |
| Application-level key | `EditorController.install_bindings()` |
| Ordinary text editing behavior | prompt-toolkit configuration or binding |
| Startup, signals, process status | `_inedit/terminal.py` |
| Re-export or replaceable dependency wiring | `inedit.py` |

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

The next maintenance step is to make the implemented command registry drive
the mode-specific Help text, then split the test module along these same
boundaries. Neither change should alter the public facade or editor behavior.
