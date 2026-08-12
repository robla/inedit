# Repository Guidelines

## Purpose and Editing Stance

`inedit` is a bounded inline terminal editor for small UTF-8 files. It values
safe file handling, reliable terminal restoration, prompt-toolkit alignment,
and code a competent generalist can audit.

The production code already exceeds 2,000 lines, plus roughly 2,000 lines of
tests. That size is not itself a failure, but it is a reason to resist
architecture for architecture's sake. Line count is a warning signal, not a
quota:

- prefer one cohesive module to several pass-through classes;
- introduce a module only for a stable responsibility with a clear dependency
  boundary;
- introduce an abstraction only when it removes concrete duplication,
  coupling, or testing difficulty;
- do not add registries, service layers, protocols, factories, or dependency
  containers merely to make the design look systematic; and
- prefer deleting or consolidating code when that makes behavior easier to
  trace.

Keep refactors behavior-preserving unless the user also requested a behavior
change. Do not combine a large mechanical move with a policy change.

## Reference Documents

Start implementation work with `docs/code-guide.md`. Consult `README.md` for
shipped behavior, and read the relevant portion of `docs/architecture.md`
before changing storage, editor state, or terminal lifecycle. `tasks.org` and
`docs/roadmap.md` describe proposed work; they are plans, not permission to
implement adjacent tasks. `docs/llm-log.org` records substantive assisted
edits.

## Current Structure and Dependencies

`inedit.py` is the executable and compatibility facade. Keep it importable and
preserve its deliberate `__all__` surface and replaceable save/timer seams
unless the user explicitly accepts a compatibility change.

The internal package has these owners:

- `_inedit/model.py`: shared data, enums, constants, and expected errors;
- `_inedit/cli.py`: argument and environment parsing;
- `_inedit/storage.py`: decoding, loading, conflicts, explicit saves, and
  recovery files;
- `_inedit/presentation.py`: Help text and pure height/status/summary logic;
- `_inedit/application.py`: prompt-toolkit application and editor transitions;
- `_inedit/terminal.py`: TTY startup, signals, execution, and exit status.

Keep the dependency graph acyclic. `model.py` remains a standard-library leaf;
CLI, storage, and presentation may depend on it; application may depend on
those layers; terminal may depend on application; and the facade may assemble
everything. Internal modules must never import `inedit.py`.

A future `view.py` may depend on prompt-toolkit, model, and presentation, but
must not import the concrete controller. A future bindings registry should own
only inedit-installed commands. Prompt-toolkit-native commands may have Help
metadata, but must not be rebound simply to fit a local abstraction.

## Behavioral Boundaries

Prompt-toolkit owns ordinary editing: buffers, cursor motion, selection, undo,
search state, vi state, rendering mechanics, and terminal input decoding.
Add custom bindings only for inedit application behavior or a demonstrated
prompt-toolkit gap. Avoid recreating Emacs, Nano, or vi editing engines.

`EditorState.document` is the current on-disk snapshot. Modified state is a
comparison with `original_text`, not a one-way dirty flag. `EditorView` owns
mutually exclusive inedit views; do not reintroduce parallel modal booleans.

Only the explicit save transaction may replace the target. Loading, editing,
auto-saving, cancellation, and signals must not mutate it. Preserve conflict
checks, private temporary/recovery files, exact-path cleanup, and retained
terminal restoration behavior. Use temporary directories for destructive or
failure-injection tests; never test write behavior on a user's real file.

## Implementation Style

Use straightforward Python with explicit names, type hints at useful
boundaries, dataclasses for data, and small pure functions where they clarify
policy. Prefer dependency injection of the few existing side effects over
global patching inside internal modules, but do not build a general injection
framework.

Before adding a new file or class, be able to state:

1. which responsibility it owns;
2. which dependency it removes or prevents;
3. why a function or existing module is insufficient; and
4. whether total conceptual surface decreases after adapters and tests are
   counted.

Do not split `EditorController` merely because it is long. Binding metadata,
view construction, auto-save, or external-editor execution should move only
when the resulting API is narrower and easier to test. It is acceptable to
decide that no further split is currently justified.

Do not add dependencies beyond the standard library and
`prompt_toolkit>=3.0.36,<4` without explicit user agreement. Avoid broad
formatting or unrelated cleanup in a behavior change.

## Testing and Verification

Run the checks appropriate to every Python change:

```bash
ruff check .
python3 -m py_compile inedit.py _inedit/*.py test_inedit.py
pytest -q
```

Add focused unit tests for pure parsing, storage, and presentation policy. Use
pipe-input tests for controller transitions and PTY tests only for behavior
that depends on real terminal byte streams, screen retention, resizing, or
signals. Keep facade-level coverage even when a focused test imports an
internal owner directly.

For save changes, inject failures and assert target bytes, temporary siblings,
recovery files, modes, and editor usability. For terminal changes, verify no
alternate-screen entry, correct cleanup, and safe abnormal exits. A fragment
appearing in captured ANSI output is weaker evidence than reconstructed final
screen state; do not overclaim what a test proves.

## Documentation and Task Hygiene

Update mode-specific Help and `README.md` whenever shipped keys or behavior
change. Update `docs/architecture.md` when an invariant or dependency boundary
changes, and keep `docs/code-guide.md` accurate after file moves.

Task IDs in `tasks.org` are stable. Preserve their IDs, priorities, rationale,
and concrete completion criteria; do not mark a task done for partial work.
Keep roadmap prose concise and technical rather than reviewer-oriented.

For every substantive repository edit, append one concise entry to
`docs/llm-log.org` using its existing local-time Org format. In the final
handoff, report behavior changes, compatibility consequences, and the exact
checks run.
