# Handoff: finish Baldur's Qt pages, then retire its tkinter window

Written 2026-10-10 for whoever picks this up next (Claude Code). Read `Asgard/AGENTS.md` and
`Asgard/HANDOFF.md` first; this file is the one task, not the project.

## Where things stand

Baldur has **two** windows right now:

| | Where | What it can do |
| --- | --- | --- |
| tkinter (the old one) | `apps/baldur/baldur/window.py`, 612 lines, opened by `baldur.pyw` | everything below |
| Qt pages (new, Oct 10) | `apps/baldur/ui/Week.qml`, `Day.qml` over `apps/baldur/baldur/ui_backend.py`, in Asgard's shared window | read, approve, reject, collect, estimate, copy |

Both can approve, and only the old one can change a figure. That is the problem to fix: **two
windows that write decisions, with different abilities**, is worse than either alone.

The Qt pages are already the house style (they match `apps/odin/ui/*.qml` and
`apps/heimdall/ui/*.qml`: `PageHeader` actions, a `MetricCard` grid, `Card` + `Repeater` rows with
`Pill` statuses, `EmptyState`). Don't restyle them. Add the four missing abilities, then delete the
tkinter window.

## What's missing, in the order worth doing

### 1. Edit a figure (the real gap)

The tkinter window lets you select a ticket, type a length, and either stage it for the approval or
change a figure you already approved. Nothing in the Qt pages does this, so a number you approve
can't be corrected except from the CLI.

Three behaviours, all of them already in `desk.py`:

- **Stage a figure before approving.** `window.py` keeps `self.figures: Dict[(date, key), int]`
  (line 115), shows a staged value in place of the estimate (line 390), clears one with Delete
  (`clear_figure`, line 451), and passes the day's staged figures to
  `desk.approve_day(con, settings, day, figures)` (line 475). `approve_day` already takes
  `figures: Optional[Dict[str, int]]`; `ui_backend.approve()` never passes it.
- **Change an approved figure.** `desk.change_figure(con, approved_id, minutes) -> int` writes a new
  approved row (it supersedes; triggers refuse an edit). It needs `TicketRow.approved_id`, and
  `ui_backend.day()` does **not** pass that through today: each ticket dict has
  `key, estimate, estimate_text, figure, figure_text, approved, approved_text, held, held_text,
  review, problem, open, approvable` (line 121). Add `approved_id`, or have the backend look it up
  from the day and key so the id never travels through QML.
- **Parse what the person typed.** `cli.parse_day_minutes(text) -> int` accepts `1h15m`, `75` and
  `1:15`, and raises `CliError` for anything else or more than a day. Use it; don't write another
  parser. Note `CliError` is a plain `Exception`, so it needs turning into a `ValueError` for the
  page (see `_expected` and `EXPECTED` in `ui_backend.py`).

Suggested backend surface (keep the existing style: plain data in, plain data out, `ValueError` for
anything the person should read):

```python
def parse_minutes(self, text: str) -> Dict[str, Any]:      # {"minutes": 75, "text": "1h15m"}
def stage_figure(self, date: str, key: str, text: str) -> Dict[str, Any]
def clear_figure(self, date: str, key: str) -> Dict[str, Any]
def staged(self, date: str) -> Dict[str, int]              # for the day page to show
def change_approved(self, date: str, key: str, text: str) -> Dict[str, Any]
```

Staged figures belong on the backend instance, next to `self._chosen` (the shell keeps one backend
per app, so they survive moving between pages; a QML page is rebuilt each time and can't hold them).
Clear a day's staged figures once `approve()` succeeds, as `window.py` does at line 471.

In the QML, an editable figure fits the existing row: a `TextField` in place of the figure `Text`
when the ticket is `approvable`, saving on `onAccepted` and on focus loss, the way
`AsgardUI/SettingField.qml` does. Changing an **approved** figure must ask first; `window.py`'s
wording is right: "Approve 45m for PROJ-42 instead of 1h30m? Odin posts the difference."

### 2. Keep the GitHub list fresh

`window.py` calls `desk.sync_github(con, settings)` on a timer every `poll_minutes` (`github_tick`,
line 529) and shows whether the list it displays is fresh. `ui_backend.reviews()` only reads
`desk.review_items(con)` from Muninn, so the Qt page shows whatever the last `baldur.cmd github
sync` left behind.

- `desk.sync_github` returns `None` when GitHub is off or no token is saved, and otherwise a
  `github.SyncResult` with `login, repos, pulls_changed, reviews_new, requested, keyed_commits,
  unchanged, requests, problems`.
- It makes network calls, so it must go through `bridge.callAsync`, never `call` (the week page
  already does that for `collect` and `estimate`: see `run()` and `onAsyncDone`).
- A timer belongs in the page (`Timer { interval: ...; repeat: true }`) with the interval from
  `poll_minutes`, and it must stop when the page isn't shown. Don't start a Python thread for it.
- Say when the list was last refreshed, and don't make a failure loud: GitHub being unreachable
  must not stop estimates or approvals.

### 3. Small things the old window had

- A **This week** button, to come back from a week you paged to (`b_this`, line 190).
- **Ctrl+Left** and **Ctrl+Right** to step weeks (lines 169-170). `Shortcut` in the page.
- **Reject asks first.** `window.py` confirms ("Nothing is posted for it."); the Qt page doesn't.
  Use `AppDialog`, as Approve does.

### 4. Then delete the tkinter window

Only once 1-3 are done and checked on Windows:

- Delete `apps/baldur/baldur/window.py` and `apps/baldur/baldur.pyw`.
- Point Baldur's tile at the shared window: in `asgard/apps.json`, Baldur's `launch` is
  `{app}/apps/baldur/baldur.pyw` and Heimdall's is `{app}/apps/heimdall/heimdall.pyw`, which is a
  one-line file calling `asgard.ui.run(app="heimdall")`. Give Baldur the same one-line `.pyw`.
- `tests/test_baldur.py` mentions the window in 7 places; move what still applies onto the backend,
  in `tests/test_app_ui_backends.py`.
- `MODULES.md`'s PySide6 section says "Baldur's window" is tkinter under "Stdlib alternative", and
  `HANDOFF.md` says moving it is optional. Both change.

## Rules this work must keep

From `AGENTS.md`, and they are the reason the code looks as it does:

1. **Baldur biases down.** Nothing here may raise a figure on its own. A person typing a bigger
   number is fine (it is their decision); code choosing one is not.
2. **Decisions are final.** An approved figure is never edited: `change_figure` supersedes through
   `muninn.baldur.change_approval`, and database triggers refuse anything else. Don't work around a
   trigger; if one refuses, the message is the answer.
3. **Baldur never writes to Jira.** Approving records a decision. Odin posts it, and only what Jira
   is missing.
4. **Backends stay plain Python.** No Qt import in `ui_backend.py`, so it stays testable without a
   display. The bridge turns a `ValueError` into a message for the page and logs anything else as
   unexpected, which is why `_expected()` exists.
5. **Nothing slow on the UI thread.** Git and network work goes through `bridge.callAsync` or
   `bridge.spawn`. Never hold a Muninn write transaction across either.
6. **Muninn is opened read-only for reads** (`_open(readonly=True)` keeps one connection) and with
   its own connection for writes. Don't share one.

## How to check it

```
python tools/run_tests.py                      # from the repo root; 709 pass today, 3 known macOS
python tools/run_tests.py Asgard/tests/test_app_ui_backends.py
bash Asgard/tools/baldur_smoke.sh              # must still end PROJ-42 1h30m, PROJ-51 30m
ruff check .
```

- PySide6 isn't needed for the backend tests, and `test_ui_qt.py` skips without it. With it
  installed, `test_ui_qt.py` loads every page and asserts `qml_warnings == []`; keep that true.
- There is no display in CI. Offscreen rendering is how the pages are checked:
  `QT_QPA_PLATFORM=offscreen`, then `shell.Shell(home=True)`, `load()`, `navigation.go("baldur:day")`
  and `grabWindow().save(...)`. A small script for this is worth writing again if you want pictures.
- New behaviour needs a test, and every bug fix a regression test that fails without the fix.

## What can't be checked away from Windows

Say so plainly in the handoff note rather than implying it works:

- The pages at real DPI with Segoe UI, and Narrator reading the new fields.
- Approving against a real Muninn with real proposals, and Odin then posting them.
- The GitHub sync against Enterprise Server (the host, token type and server version are still
  waiting on the owner; see `HANDOFF.md`, "Waiting on").

## Files you will touch

```
apps/baldur/baldur/ui_backend.py     the five new methods, staged figures, sync_github
apps/baldur/ui/Day.qml               editable figures, the change-approved dialog, reject confirm
apps/baldur/ui/Week.qml              This week, Ctrl+arrows, the GitHub timer and freshness
tests/test_app_ui_backends.py        BaldurBackendTests: parsing, staging, clearing, change_approved
tests/test_ui_qt.py                  the pages still load with no warnings
asgard/apps.json, MODULES.md, HANDOFF.md    only in step 4, when the old window goes
```

Leave `desk.py`, `store.py`, `estimate.py` and `muninn/baldur.py` alone: everything needed is
already there, and estimation changes need the direction tests and an independent review.
