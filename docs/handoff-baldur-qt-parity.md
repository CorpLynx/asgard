# Handoff: retire Baldur's tkinter window

Rewritten 2026-10-11. The first three quarters of this task are done; what is left is the deletion.
Read `Asgard/AGENTS.md` and `Asgard/HANDOFF.md` first; this file is the one task, not the project.

## Where things stand

Baldur still has **two** windows, but they are no longer unequal:

| | Where | What it can do |
| --- | --- | --- |
| tkinter (the old one) | `apps/baldur/baldur/window.py`, 612 lines, opened by `baldur.pyw` | everything below |
| Qt pages | `apps/baldur/ui/Week.qml`, `Day.qml` over `baldur/ui_backend.py`, in Asgard's shared window | everything below |

The abilities the Qt pages were missing on Oct 10 all landed in `c5ead53`, and Windows CI is green
on `34759b3` (812 tests, `OK (skipped=14)`):

- **Editing a figure.** `ui_backend` gained `parse_minutes`, `stage_figure`, `clear_figure`,
  `staged` and `change_approved`. Staged figures live on the backend instance (`self._staged`, by
  day then ticket), so they survive moving between pages; `_prune()` drops any whose ticket was
  decided elsewhere, and `approve()` passes the rest to `desk.approve_day(..., figures=)`. Typing
  the estimate again stages nothing. `change_approved` looks the approved row's id up itself, so it
  never travels through QML, and supersedes through `desk.change_figure`. `Day.qml` draws an
  editable `TextField` per row and confirms a change to an approved figure in an `AppDialog`
  ("Odin posts the difference").
- **GitHub freshness.** `ui_backend.sync_github(force=False)` returns a state rather than raising
  (`off`, `no token`, `failed`, `fresh`), and `Week.qml` calls it through `callAsync` on a `Timer`
  whose interval is `poll_minutes` and whose `running` is tied to `page.visible`, so nothing polls
  behind a closed page. A failure shows beside the list, never as a toast.
- **The small things.** A **This week** button, `Ctrl+Left` / `Ctrl+Right` as `Shortcut`s, and
  Reject now asks first ("Nothing is posted for it.").

Covered by `tests/test_app_ui_backends.py`: 10 tests for figures (staging, clearing, surviving
navigation, a bigger number being allowed, a ticket decided elsewhere, rejection dropping typed
figures) and 6 for GitHub, including a pass against the fake server.

## What is left: delete the tkinter window

Nothing blocks this on the code side. It wants one pass on Windows first (see below), because it
removes the fallback.

1. **Delete `apps/baldur/baldur/window.py`.**
2. **Rewrite `apps/baldur/baldur.pyw`** as the one-liner the other apps use. Copy
   `apps/heimdall/heimdall.pyw` and change `run(app="heimdall")` to `run(app="baldur")`; keep its
   `ASGARD_APP` / two-folders-up `sys.path` preamble verbatim, since the installed copy depends on
   it. **`asgard/apps.json` needs no change**: Baldur's `launch` target is already
   `{app}/apps/baldur/baldur.pyw`, so rewriting that file repoints the tile.
3. **`tests/test_baldur.py`: delete `WindowTests` (lines 1554-1658, 17 tests)** and the
   `from baldur import window` import and `messagebox` patches in its `setUp`. Check each test
   against `BaldurBackendTests` before dropping it; the figure and rejection behaviours are already
   there, but anything about *week paging* or *the day report text* that is only asserted here
   should move onto the backend rather than disappear. `DeskTests` stays as it is: it tests
   `desk.py`, which both windows use.
4. **Docs.** `MODULES.md` has two stale claims: the PySide6 row's "Stdlib alternative" calls tkinter
   "which Baldur's window uses", and there is a whole `Baldur's window` row saying moving it is
   optional. `HANDOFF.md` says the same in its Oct-10 section, in "Not done", and in the shared
   window's status row. All of them change, and this file gets deleted with them.

## Rules this work must keep

From `AGENTS.md`, and they are the reason the code looks as it does:

1. **Baldur biases down.** A person typing a bigger number is their decision; code choosing one is
   not. `test_a_person_may_type_a_bigger_number` is the line between the two.
2. **Decisions are final.** An approved figure is never edited; it is superseded through
   `muninn.baldur.change_approval`, and triggers refuse anything else. If a trigger refuses, its
   message is the answer, not something to work around.
3. **Baldur never writes to Jira.** Approving records a decision; Odin posts it.
4. **Backends stay plain Python.** No Qt import in `ui_backend.py`. The bridge turns a `ValueError`
   into a message for the page and logs anything else as unexpected, which is why `_expected()`
   exists.
5. **Nothing slow on the UI thread**, and never a Muninn write transaction across git or network.
6. **Reads open and close** (`_read()`); writes get their own connection. Don't cache either: a
   cached read connection is what broke the Windows build on `c5ead53`.

## How to check it

```
python tools/run_tests.py                      # from the repo root
python tools/run_tests.py Asgard/tests/test_app_ui_backends.py
bash Asgard/tools/baldur_smoke.sh              # must still end PROJ-42 1h30m, PROJ-51 30m
ruff check .
```

`test_ui_qt.py` skips without PySide6; with it, it loads every page and asserts
`qml_warnings == []`. Keep that true. There is no display in CI: `QT_QPA_PLATFORM=offscreen`, then
`shell.Shell(home=True)`, `load()`, `navigation.go("baldur:day")`, `grabWindow().save(...)`.

## What can't be checked away from Windows

Needs target-machine verification, and should be said plainly rather than implied:

- The editable figure fields at real DPI with Segoe UI, and Narrator reading them.
- Approving and then changing a figure against a real Muninn, with Odin posting the difference.
- The GitHub timer against Enterprise Server (host, token type and server version are still waiting
  on the owner; see `HANDOFF.md`, "Waiting on").

That last pass is the only real argument for keeping `window.py` another week. Once someone has
driven the Qt day page on the workstation, delete it.
