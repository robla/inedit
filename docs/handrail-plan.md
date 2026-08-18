# Handrail Planning Notes

Handrail is a framework of UX guidelines that may become a library one day.
For now, it is a set of nano-, Emacs-, and CUA-aligned guidelines for
full-featured TUI applications. A Handrail-style mini app normally renders in a
content-sized region below the shell prompt, preserves useful scrollback, and
exposes substantial behavior without taking over the full screen. Applications
may also offer a deliberate full-screen mode without maintaining a second UI.
This document is exploratory, not a commitment to create a package or to keep
the name.

## Scope and Design Contract

A prospective toolkit should make these properties easy to preserve:

- **Inline by default.** Do not enter the alternate screen unless the user
  explicitly selects a full-screen presentation mode. Leave prior shell output
  visible and make final-screen retention or erasure an explicit policy. An
  application whose primary view has no useful compact form — a structured-data
  tree editor, for example — may declare full-screen presentation at startup.
  That is a declared mode, not an exception to the contract.
- **Content-sized, not wasteful.** Fit compact views to their visible content,
  grow richer views to a configured inline ceiling, and clamp both to the
  terminal. Separate desired, requested, and effective height.
- **Bounded, not simplistic.** Allow scrolling, search, nested views, editing,
  background work, and contextual actions within the inline region; the same
  application views may use more space in full-screen mode.
- **Commands explain themselves.** One command definition should drive key
  bindings, the compact command bar, and expanded `Ctrl-G` help. Important
  operations should not exist only as undocumented keys.
- **Predictable context.** Opening help, a picker, or a detail view pushes a
  context; Back closes one context. Editing modes may override this rule where
  keys such as Escape already have established meaning.
- **Safe interruption.** Cancellation, terminal resize, signals, exceptions,
  and external-program handoff must restore the terminal and must not imply a
  successful write.
- **Backend-aware, not backend-fiction.** Start with prompt_toolkit directly.
  Keep application-facing models free of prompt_toolkit types where that is
  natural, but do not build a second backend until another toolkit is actually
  used by Handrail code. Jsonedit demonstrates related UX on Urwid, but it is
  evidence for the design vocabulary rather than an existing second backend.

The toolkit should not own application data, persistence rules, or domain
actions. It should provide an application shell and interaction components.

## Evidence in Inedit

Inedit exercises a deeper prompt_toolkit application lifecycle. Candidate
pieces include:

| Current symbol | Possible shared role | Assessment |
|---|---|---|
| `effective_height()`, `automatic_height()`, and `adjusted_height()` | Bounded-height policy | Strong pure-function candidates after making reserved rows and limits explicit inputs. |
| `_display_width()`, `_truncate_left()`, `_truncate_right()`, and `_one_line()` | Width-safe compact text | Strong low-risk candidates; they solve recurring Unicode and narrow-terminal problems. |
| `_guard_terminal_cursor_column()` | Safe inline first render | Valuable but low-level. Extract only with its PTY tests and documented platform limitations. |
| `_termination_handlers()` and parts of `run_editor()` | Signal-safe application runner | Potential shared lifecycle code after a second application needs exactly the same outcomes. |
| `EditorDependencies` | Injectable clocks, subprocesses, and environment behavior | A useful testing pattern, not necessarily a shared class. |
| `EditorView`, `toggle_help()`, and `build_layout()` | Modal view and Help transitions | Design input for a small view stack; the concrete editor layout should remain local. |
| `status_fragments()` and `before_render()` | Dynamic status and resize adaptation | Extract layout mechanics, not inedit's status content. |
| `install_bindings()` | Current command registration point | First centralize inedit-owned commands and Help metadata locally; then compare it with jsonedit's separate footer and dispatch declarations. |
| `run_external_editor()` | Suspend and resume around an external program | A later candidate if other apps need terminal-safe handoff. |

Inedit's current `Alt-Up` and `Alt-Down` one-row height controls are useful
implementation evidence but not the intended Handrail keymap. They should
eventually scroll the active viewport, following nano's display-oriented use of
those keys. Manual height adjustment moves into the shared Resize mode
described below. This is a planned compatibility change for inedit, not a reason
for a Handrail library to own editor behavior.

Inedit's `Document`, `EditorState`, save/conflict checks, auto-save policy,
discard confirmation, Ex subset, search behavior, and prompt_toolkit `Buffer`
operations should stay in inedit. A common library must not become a new text
editing engine or wrap prompt_toolkit operations merely to rename them.

## Evidence in jsonwidget/jsonedit

[jsonwidget-python](https://github.com/robla/jsonwidget-python) has public
history dating to 2010. It ships `jsonedit`, a schema-driven JSON tree editor,
plus `jsonaddress`, `csvedit`, and a YAML example built on the same engine. Its
terminal UI uses Urwid rather than prompt_toolkit, making it useful evidence
that Handrail's UX vocabulary is not tied to one backend.

Jsonwidget is historical design evidence, not a Handrail reference
implementation. Its manual event loop predates modern Urwid APIs, current
modernization notes still call for deeper interactive testing, and the test
suite has only one focused terminal-widget test and no PTY lifecycle coverage.
Its generic tree layer is also LGPL-derived from Urwid while most of the project
is BSD-licensed. Handrail may learn from these interfaces, but should not copy
their implementation into a permissively licensed package without a separate
license and maintenance review.

The public implementation provides several concrete lessons:

| Observed mechanism | Handrail implication |
|---|---|
| [`PinotUserInterface`](https://github.com/robla/jsonwidget-python/blob/master/jsonwidget/pinot.py) owns a pine/pico/nano-inspired header, notifications, prompts, and two-row key-hint footer. | Application chrome, transient status, contextual prompts, and command bars are related framework concerns; save policy remains application-owned. |
| [`JsonFileEditor`](https://github.com/robla/jsonwidget-python/blob/master/jsonwidget/termedit.py) declares `^W Write/Save`, `^X Exit`, `^N Insert New Item`, and `^D Delete Item`, then dispatches the same keys in a separate `if`/`elif` chain. | One `Command` declaration should drive binding, availability, footer text, and expanded Help. |
| Footer prompts replace the normal notification and command rows, move focus, then restore the body and default footer. | A prompt is a temporary interaction context with its own commands, not a nested terminal application. |
| [`TreeNode`/`ParentNode`, `TreeWalker`, and `TreeWidget`](https://github.com/robla/jsonwidget-python/blob/master/jsonwidget/treetools.py) separate hierarchy, visible traversal, and rendering. | Tree data, its flattened visible projection, and focus belong to separate layers; a visible row should not become the domain model. |
| Insert and delete explicitly refresh child caches and move focus using the previous screen offset. | Structural mutation needs stable item identity plus a deliberate post-mutation focus anchor; index alone is insufficient. |
| [`JsonWidgetNode`](https://github.com/robla/jsonwidget-python/blob/master/jsonwidget/termwidgets.py) selects string, integer, number, boolean, and enum controls from the schema; add-field and edit-key actions are represented as synthetic tree nodes. | Views must compose backend-native focusable controls and action rows generated at runtime. Handrail should not invent a universal field-widget hierarchy. |
| Jsonedit starts full-screen and uses the same general chrome for file and in-memory editors. | Full-screen is an ordinary presentation choice, not a separate application architecture. |

The repository history reinforces the incremental extraction rule. The
pine/pico/nano chrome, generic tree tools, JSON-specific widgets, unhandled key
dispatch, and file wrapper were separated over months in response to concrete
reuse and coupling. The resulting layers are still imperfect and explicitly
marked for cleanup. Handrail should follow the sequence, not copy the class
hierarchy: extract a seam only after a working application proves it.

Handrail should not adopt jsonwidget's larger promise that a schema can
generate a complete editor. It should merely permit views whose rows and
controls are generated at runtime. A future jsonedit port could test tree,
field, and Urwid portability together, but combining all three would make it a
poor first experiment for validating a small shared library.

## Adaptive and Full-Screen Viewports

Inline views should fit compact content, grow when a subtree expands or a
larger view opens, and contract when content shrinks, all under a configured
inline ceiling. Help, selectors, prompts, and editors need to report minimum
and preferred body rows without embedding their concrete types in the
application shell. A structured editor with no useful compact form may instead
declare full-screen presentation at startup.

This suggests four independent concepts:

- a view's content measurement and minimum/preferred body rows;
- an automatic or explicit requested inline height;
- a terminal-clamped effective height and reserved shell row; and
- an inline or full-screen presentation mode.

Full-screen is not merely a very large requested height. It gives the
application all usable terminal rows and restores the prior shell display on
exit. Prompt_toolkit's `Application(full_screen=True)` implements this with the
alternate screen; another backend may provide a different lifecycle mechanism.
Inline mode may instead retain one factual final frame. Both modes should use
the same views, commands, application state, and persistence policy.

The first full-screen implementation should select its presentation mode when
constructing the application. Runtime switching can follow only after tests
show that rebuilding or reconfiguring the application preserves the active
view, focus, edits, and terminal state. It should not depend on a function key,
which can be hard to discover or generate on compact keyboards. Full-screen
switching belongs inside the Resize mode described below. See the
[prompt_toolkit full-screen application guide](https://python-prompt-toolkit.readthedocs.io/en/master/pages/full_screen_apps.html),
[nano command documentation](https://www.nano-editor.org/dist/v5/nano.html),
and [Emacs window resizing](https://www.gnu.org/software/emacs/manual/html_node/emacs/Change-Window.html).

### Viewport Key Profile

Handrail applications should use these commands consistently:

- `Alt-Up` and `Alt-Down` scroll the active viewport without changing its
  logical cursor or selection. Scrolling stops before that anchor would leave
  the visible region.
- `C-^` enters Resize mode. Traditional terminals encode `C-^` and `C-6` as
  the same `0x1e` control character, so command bars and Help should teach both
  spellings and tests should exercise the byte actually received.
- Resize-mode Up makes a bottom-anchored inline application taller; Down makes
  it shorter. The mode also offers `a` for automatic content-fit sizing, `m`
  for maximum inline height, and `f` for full-screen presentation when the
  application supports it.
- `C-^`, Enter, or the application's Back command leaves Resize mode while
  preserving the session-local presentation choice.

The first manual Up or Down disables automatic sizing until `a` restores it.
The active command bar should identify Resize mode and show only supported
commands, for example `Up taller  Down shorter  a auto  m max  f full  ^6 done`.
Applications without full-screen support omit `f`; applications may add
domain-neutral aliases, but should not give these keys unrelated meanings.

This assignment deliberately simplifies Emacs's `C-x ^` vertical-window
command without consuming `C-x`, which nano and inedit use for Exit. Nano uses
the indistinguishable `C-6` byte to set its mark; Handrail instead favors
CUA-style Shift selection and reserves the control character for the less
frequent layout mode. An application that must preserve nano's mark command
may override the binding, but should retain the same Resize-mode command
semantics under a documented alternative.

## Possible Public Vocabulary

The API should describe user-visible concepts rather than toolkit objects. A
tentative vocabulary is:

- `TerminalApp`: runs one application in its selected presentation mode and
  owns terminal lifecycle.
- `View`: a selector, editor, prompt, Help page, or application-defined view.
- `ViewStack`: pushes and pops nested interaction contexts.
- `Command`: stable ID, key sequences, short label, Help text, and availability.
- `CommandSet`: resolves active commands and produces bindings and Help models.
- `CommandBar`: width-aware one- or two-line command hints.
- `SelectorRow`: stable key plus styled label, description, and optional detail.
- `TreeRow`: a visible projection with a stable item key, depth, and disclosure
  state; the application still owns hierarchy and mutation.
- `SelectionResult`: selected row, command action, cancellation, or Back.
- `ViewportPolicy`: content measurement, minimum/preferred body rows, automatic
  ceiling, explicit height, terminal reserve, and effective-height clamping.
- `PresentationMode`: inline or full-screen terminal ownership and exit policy.
- `ResizeMode`: transient grow, shrink, auto-fit, maximize, and full-screen
  commands operating on `ViewportPolicy` and `PresentationMode`.
- `Theme`: semantic style tokens such as selected, warning, success, and dim.
- `AppOutcome`: completed, canceled, or failed, with optional final display.

An early API might look approximately like this:

```python
commands = CommandSet([
    Command("help", keys=["c-g"], label="Help", help="Show all commands"),
    Command("back", keys=["escape", "b", "q"], label="Back"),
    Command("resize", keys=["c-^"], label="Resize"),
])

result = run_app(
    Selector(rows, commands=commands),
    presentation=PresentationMode.INLINE,
    viewport=ViewportPolicy(mode="auto", minimum=5, maximum=20, reserve=1),
)
```

`TreeRow` keeps the model honest about jsonedit-like applications, but it is
only a rendering projection. Backend-native controls remain children of a
`View`; a framework-owned `Field` base class is not justified.

Handlers may remain in the application so command declarations stay testable
data. The first prompt_toolkit implementation can translate active commands
into `KeyBindings` and backend events into framework results without claiming
that those translations already form a portable backend protocol.

## Possible Package Layout

```text
handrail/
  commands.py       command metadata, key labels, Help models
  application.py    prompt_toolkit lifecycle and presentation mode
  viewport.py       content-fit and explicit height policy
  selector.py       row model and highlight-bar selection
  presentation.py   width, truncation, command-bar composition
  outcomes.py       typed completion/cancellation results
  testing.py        pipe-input and captured-output helpers
```

Do not add `editor.py` initially. Inedit should continue to use
prompt_toolkit's `TextArea`, buffers, search, clipboard, Emacs mode, and vi mode
directly. A later editor adapter is justified only by another real editor-like
application with matching requirements. Do not add `tree.py`, `fields.py`, or
a `backends/` package initially either. Hierarchical rows, editable controls,
and another backend are requirements to keep possible, not modules to write
before an application needs them. Introduce a backend boundary only during a
real second-backend port.

## Extraction Sequence

1. **Share vocabulary in documents first.** Use terms such as command, command
   bar, view, view stack, viewport, and application outcome consistently without
   introducing a dependency.
2. **Stabilize inedit locally.** Complete its command/Help registry, viewport
   controls, and view split without forecasting a shared package API.
3. **Validate the vocabulary in another public prompt_toolkit app.** Implement
   the same command and view semantics locally first, recording actual
   duplication and mismatches rather than forcing inheritance.
4. **Extract proven leaf behavior.** Width-aware truncation, height
   calculations, command metadata, and Help composition are early candidates
   only after two consumers use matching semantics.
5. **Extract selectors and view transitions after leaf adoption.** Keep domain
   rows, persistence, and mutation callbacks in each application.
6. **Extract terminal lifecycle only with PTY coverage.** Cursor guarding,
   signals, resize handling, retained output, and application suspension are
   valuable precisely because they are subtle; moving them without their tests
   would reduce confidence.
7. **Attempt another backend last.** A jsonedit/Urwid port is a possible test,
   but it also introduces hierarchy and schema-driven fields. Derive any
   backend protocol from that work rather than forecasting Urwid, Textual, or
   another toolkit's needs.

Creating a separate package becomes compelling when two public consumers need
the same tested behavior, the same terminal bug is fixed twice, or maintaining
a shared prompt_toolkit compatibility matrix is cheaper than independent code.

## Guide for New Mini Apps

Start a new terminal application by answering these questions:

1. What remains usable as an ordinary non-interactive CLI?
2. Must a non-TTY invocation fail, use a numbered fallback, or emit structured
   output?
3. How does each view calculate minimum and preferred height, and what is the
   normal inline ceiling and terminal row reserve?
4. Is full-screen available, and how do its startup, exit, and scrollback rules
   differ from inline mode?
5. Which views can be pushed, and what does Back mean in each editing mode?
6. Which commands must always be visible, and which belong in expanded Help?
7. Which state is in memory, when can disk writes happen, and how are conflicts
   detected?
8. What should remain in scrollback after success, cancellation, and failure?
9. Which behavior belongs to the terminal toolkit rather than application code?
10. Is the data flat or hierarchical, and what preserves selection across
    expand, collapse, insertion, and deletion?
11. Are views hand-written, or generated at runtime from data or a schema?

A practical application split is:

```text
cli.py           argument parsing and non-TTY policy
domain.py        application data and pure operations
application.py   state transitions and command handlers
views.py         selectors, editors, prompts, and Help composition
commands.py      application-owned command declarations
storage.py       explicit reads, writes, recovery, and conflicts
terminal.py      only behavior not supplied by the shared runner
```

Prefer one source of truth for command IDs, bindings, command-bar labels, and
Help descriptions. Keep view rendering pure where practical. Return typed
outcomes instead of using process exits deep in widgets. Treat navigation as
read-only and require explicit commands for mutation. Preserve stable row IDs
so filtering or editing does not move the highlight to unrelated data.

Tests should cover four layers:

- Pure tests for commands, layout policy, truncation, and state transitions.
- Backend-level input tests for keys, focus, Help, scrolling, and results;
  prompt_toolkit applications can use pipe input.
- Temporary-file tests for save, discard, recovery, and conflict behavior.
- PTY tests for inline alternate-screen avoidance, explicit full-screen entry
  and restoration, adaptive growth/contraction, `0x1e` Resize-mode input,
  Alt-arrow viewport scrolling, first-frame placement, terminal resize,
  signals, exception cleanup, Unicode width, and the next shell prompt.

## Overarchitecture Checks

Do not extract code merely because two functions both call prompt_toolkit.
Extraction should preserve matching semantics and remove duplicated policy or
terminal risk. Stop and reconsider when:

- shared wrappers mirror every prompt_toolkit argument;
- application callbacks require pervasive `Any` or backend-specific escape
  hatches;
- a generic view has more configuration than either concrete implementation;
- supporting a hypothetical backend weakens the prompt_toolkit implementation;
- file safety or editing behavior moves into a UI package; or
- adopting the package increases total code and test burden without producing
  a stable concept used by at least two applications.

It is acceptable for the outcome of this plan to be shared terminology,
copied test patterns, and independent application implementations. A library
should be the result of proven common behavior, not the prerequisite for
discovering it.

## Open Decisions

- Whether the project and import package keep the working name.
- Whether it lives in its own repository or incubates in one consumer.
- Whether Rich-backed static output belongs in the package or remains optional
  application presentation.
- Whether numbered fallback is a built-in component or an application policy.
- How command profiles reconcile menu-style Escape/Back with editor and vi
  semantics.
- Whether runtime full-screen transitions are worth application reconstruction,
  or startup-only presentation modes are sufficient.
- Which applications need a documented exception to the `C-^` Resize binding
  because they preserve nano's `C-6` mark command.
- Whether successful inline applications retain their final frame by default.
- How little tree projection is needed before a real hierarchical application
  adopts Handrail code.
- Whether a jsonedit port is a useful later portability test or an unhelpful
  combination of three experiments: Urwid, trees, and schema-generated fields.
- Which second public prompt_toolkit application can validate the initial API
  before any backend abstraction is attempted.
