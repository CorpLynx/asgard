# How Muninn works

Oct 11, 2026 · schema v5 · code in `asgard/muninn/` · the design is [muninn-design.md](../../../docs/muninn-design.md), the runbook [muninn-operations.md](../../../docs/muninn-operations.md)

Muninn is Asgard's memory: one SQLite file per person, `%LOCALAPPDATA%\Asgard\muninn.db`, shared by every Asgard app and by Odin. It's not a server. Each app opens the file itself through the `asgard.muninn` package, which is standard library only, so any Python that runs Asgard can use it.

What makes it more than a file is the set of rules every connection carries: which tables an app may write, how a write happens, and which rows can never change.

## Opening it

There are two ways in, and which one you use decides what you can do.

| Who | Call | What it does |
| --- | --- | --- |
| Asgard, at start | `muninn.prepare()` | Creates the database or upgrades it, checks it for damage, takes the day's backup and runs housekeeping. Nothing else ever changes the schema |
| Every app | `muninn.open_app("baldur", supported=(4, 5))` | Opens the file as that app. It refuses if Muninn isn't set up yet, or if the schema version is outside the range the app was written for. It never migrates |

`supported` has no default on purpose: a default would quietly go stale the first time the schema moves. An app that's too old for the file says "Update baldur"; a file that's too old for the app says "Open Asgard to upgrade it".

Every connection gets the same settings (`db.connect`): foreign keys on, a 5-second busy timeout, WAL journaling so readers never block the writer, and `isolation_level=None`, so transactions happen only when code asks for one. `readonly=True` adds `PRAGMA query_only`, which makes the connection unable to write at all. Ysildir's reads use this.

A connection belongs to the thread that opened it, and an open one keeps the file locked on Windows. So the apps open a connection for each call and close it straight after, rather than keeping one around.

## The guard: each app writes only its own tables

`open_app` installs an SQLite *authorizer* on the connection (`guard.py`). SQLite asks it about every statement as it compiles, before anything runs. The guard answers from a table of owners:

| Owner | Tables (examples) |
| --- | --- |
| Baldur | `repos`, `commits`, `commit_work_items`, `reflog_entries`, `pull_requests`, `estimate_runs`, `day_proposals`, `time_actuals`, `agent_estimates` |
| Odin | `work_items`, `work_item_aliases`, `calendar_events`, `worklogs`, `meeting_subtasks` |
| Heimdall, Loki, Freya, Bifrost | their own tables, mostly not built yet |
| Shared | `sources`, `identities`, `sync_runs`, `sync_cursors`, `events` (insert only), `event_cursors` |

What the guard refuses, from any app but Asgard itself:

- writes to a table the app doesn't own;
- any schema change (`CREATE`, `DROP`, `ALTER`) and `ATTACH`;
- PRAGMAs that would switch the protections off, such as `foreign_keys = OFF`, `query_only` or `writable_schema`. An app may read any PRAGMA and set only `busy_timeout`, `cache_size` and `foreign_keys = ON`.

The shared tables need finer rules than "which table", so the guard also adds TEMP triggers on that connection (`row_rules`). They keep each app to its own rows: it adds events and sync runs only under its own name, moves only its own event cursor, and touches only the identity kinds it collects. That last rule matters because identities decide which commits are "yours". If Loki could add a git email, someone else's commits would start counting as your time.

Writes made by the schema's own triggers (the search index, status history) are always allowed, because only Asgard can change the schema.

The guard is a guard against mistakes and against one app's bug damaging another's data. It isn't a defence against someone opening the file with plain `sqlite3`; the triggers and CHECKs in the schema cover that case, and `--muninn check` reports anything that slipped through.

## Making a write

All writes go through `muninn.transaction(con)`, which runs `BEGIN IMMEDIATE … COMMIT` and rolls back if the block raises.

- **IMMEDIATE** takes the write lock at the start, so a busy database makes the write wait at the beginning instead of failing halfway through.
- **Busy waiting.** SQLite waits 5 seconds inside each attempt. Several apps writing in bursts can still lose that race, so `retry_busy` keeps trying with short random pauses for up to 30 seconds, then says "Muninn is busy in another Asgard app… Nothing was lost; wait a moment and try again" instead of SQLite's "database is locked".
- **No nesting.** A transaction refuses to start inside another. This is deliberate: work that must be saved *before* a network call (Odin's "sending" row before it posts to Jira) can't end up waiting on an outer commit that comes after the call.
- **Lost transactions are caught.** If SQLite rolls back on its own (disk full, I/O error), the commit step notices and raises "nothing was saved" rather than pretending it worked.

Network calls never happen inside a transaction. A collector fetches first, with no lock held, then writes in a short batch.

## Runs: where every row came from

Anything that brings data in from outside (git, GitHub, Jira, the calendar) does it inside a `Run` (`sync.py`):

```python
with muninn.Run(con, "odin", jira, "issues") as run:
    for page in client.pages(updated_since=run.cursor):   # no lock held while fetching
        with run.batch():                                  # one short transaction per page
            for issue in page:
                odin.upsert_issue(run, issue, ctx)
        run.advance_cursor(newest_updated_in(page))
```

- Entering the run writes a `sync_runs` row (app, source, stream, start time) and loads the stream's saved **cursor**, such as "issues updated since 10:42" or an ETag.
- `run.batch()` is a transaction; nested inside another batch it becomes a savepoint, so a single item is all or nothing without losing the page.
- `run.problem("…")` records an item that failed while the rest worked. The run then ends `partial` and **keeps its old cursor**, so the next run reads the same items again. Upserts take a re-read without writing anything twice.
- Leaving the run saves the new cursor and the run's counts only if it ended without an error. A run that raises is recorded as `failed`, with its error scrubbed of anything that looks like a credential.
- A run still marked `running` after six hours belongs to an app that stopped mid-run. `prepare()` closes it as failed at the next start.

The `sync_runs` table is the provenance trail: every collected row can be traced to the run that brought it in.

## How the apps talk to each other

Apps never write each other's tables. They talk in two ways.

**Shared views** are the main way. A view is always current, needs no messages, and the app that owns the data defines what it means:

| View | Defined over | Who reads it, for what |
| --- | --- | --- |
| `v_busy_meetings` | Odin's `calendar_events` | Baldur: meeting time to leave out or weight. Busy or tentative, not declined, cancelled or all-day |
| `v_day_status` | Baldur's approved `day_proposals` + Odin's `worklogs` | Both: for each approved day and ticket, minutes approved against minutes Jira holds |
| `v_worklogs_to_post` | `v_day_status` | Odin: what's approved and not yet in Jira, minus anything already being sent |
| `v_unpostable_days` | `v_day_status` + Odin's issues and key lookups | Baldur: approved time Odin can't post, because the key isn't found in Jira or the issue was deleted |
| `v_tile_badges` | several | The launcher: counts on each app's tile |

`v_worklogs_to_post` is the whole hand-off from Baldur to Odin. It lists each approved figure where `approved_minutes > logged_minutes`, and it leaves out any figure that has a worklog already `sending`, so two Odin runs can't post the same figure. `logged_minutes` counts both Odin's posts and time you logged by hand in Jira, so Odin only ever posts the difference.

**Events** are for "something happened" notices. `muninn.emit(...)` appends a row to `events` inside the same transaction as the change it describes, so an event exists exactly when its change does. Events are never updated or deleted (triggers refuse it). An app reads the ones it hasn't handled with `consume`:

```python
with muninn.consume(con, "freya", ["work_item.done", "work_item.reopened"]) as batch:
    for event in batch:
        ...   # writes here commit together with the cursor
```

Each app has a single cursor (`event_cursors`). It moves forward in the same transaction as the app's writes, so a crash halfway through handling a batch handles it again rather than skipping it. Baldur emits `estimate_run.created`, `day_proposal.approved`, `calibration.accepted` and `agent_estimate.recorded`/`.withdrawn`. Odin emits `worklog.posted`, `worklog.failed` and `work_item.moved`, among others.

## Rules the schema itself enforces

The guard stops an app writing the wrong table. Triggers and CHECK constraints stop anyone writing a wrong *row*, whichever way they open the file. Some of the ones that matter most:

- **Decisions are final.** An approved or rejected `day_proposals` row can't be edited (except approved → superseded) or deleted. Changing a figure means a new approved row beside the old one.
- **One open and one approved proposal** per day and ticket (unique partial indexes).
- **Rounding only goes down** (`minutes_proposed <= minutes_raw`), and **untracked time is never approved** (an approved row needs a ticket).
- **Keys look like keys.** Every column where one app writes a Jira key that another resolves accepts only `PROJ-123` in capitals (v3).
- **At most a day.** An approval is at most 1,440 minutes, and so is a worklog.
- **Posted stays posted.** A worklog Odin posted can only become deleted, never sending or failed again, and a worklog row Asgard sent can't be deleted: that row is what stops the time being posted twice.
- **Agent reports are facts** (v4). Their figures, commits and text can't change; a report can only be withdrawn, and only with its report: commits can't be added to a sealed report later.

## The search index

`search` is an FTS5 full-text index over commit subjects, Jira summaries, pull request titles and the like, kept up to date by triggers on those tables. It holds no data of its own, so a damaged index is simply rebuilt (`prepare()` does it automatically; `--muninn repair` by hand).

## Upgrades: migrations

The schema is a numbered series of SQL files in `asgard/muninn/migrations/`, and `PRAGMA user_version` records how far a database has come:

| File | Version | What it added |
| --- | --- | --- |
| `0001_initial.sql` | 1 | Every table, view and trigger of the original design |
| `0002_copies_arent_activity.sql` | 2 | Squash copies of your commits (`is_merge = 1`) never count as activity |
| `0003_hardening.sql` | 3 | Rules that held only in Python now hold in the database: key shapes, day caps, posted worklogs stay posted, double-post detection |
| `0004_agent_estimates.sql` | 4 | `agent_estimates` and `agent_estimate_commits` for Baldur's AI-assisted method |
| `0005_odin_meetings.sql` | 5 | Odin's `meeting_subtasks`, replacing Odin's own `state.db` |

`prepare()` takes a `before-vN` backup, then applies each pending migration all or nothing. **A shipped migration is never edited**; a change is always a new file. After migrating, `integrity.repair_schema` compares the live schema with what the shipped migrations would build and puts back any protection someone removed, such as a dropped trigger, and warns about anything it can't.

## Looking after the file

`prepare()` runs every time Asgard starts. On the first start each day it also:

- checks the file for damage. A damaged file raises an error that names the newest backup and the restore command; nothing is moved until you ask;
- keeps a daily backup (7 kept), made with `VACUUM INTO`, so the copy is consistent and compact while other apps keep reading;
- runs housekeeping: refreshes the query planner's statistics, folds the write-ahead log back into the file, deletes half-written backups a crash left, and prunes old rows if retention is switched on (off by default, because how long records are kept is the records officer's call).

`asgard muninn status | check | repair | backup | restore | maintain` does the same by hand. [muninn-operations.md](../../../docs/muninn-operations.md) says what each message means and what to do.

## Keeping secrets out

Muninn keeps events for the life of the database, so anything that could hold a credential is cleaned on the way in (`redact.py`):

- `scrub()` masks tokens, passwords, authorization headers, cookies and private keys in free text such as error messages;
- `redact_url()` drops user names and passwords from git remote URLs;
- event payloads pass through `scrub_value()`, except fields copied verbatim from a source system's own record, such as a Jira summary.

Tokens themselves never go in Muninn: Baldur's GitHub token is in Windows Credential Manager, and Odin's Jira token in a DPAPI-protected file.
