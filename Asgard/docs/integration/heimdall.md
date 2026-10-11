# Heimdall and Muninn

Identity `heimdall` · developed on-prem and diverging; the copy in `apps/heimdall/` is a pattern sample only · this page is the contract it should meet if it writes Muninn

Heimdall prepares security change submissions and drives the browser to fill them, stopping before Submit. It shares `submissions` with Bifrost (`system = 'secchm'` for Heimdall).

## Tables

| Owns (shared with Bifrost) | Reads |
| --- | --- |
| `submissions` (`system = 'secchm'` rows), `submission_status_history` (filled by trigger only) | `work_items`, `work_item_aliases` (the related issue), `identities` |

Keep to your own `system` value: the guard is table-level, so the module (proposed `asgard.muninn.submissions`, shared with Bifrost) filters every write by system.

## The state machine

`draft → approved → submitted → in_review → accepted | returned`, and `withdrawn` from any state. The schema enforces the order's evidence: any state past draft needs `approved_at`; submitted or later needs `submitted_at`. Every state change adds a history row by trigger, so no code path can forget it.

| Step | Call | Notes |
| --- | --- | --- |
| Prepare | `submissions.create(con, "secchm", title, fields, work_item_key)` | `fields` is the values by field name; emits `submission.created` |
| Approve | `submissions.approve(con, id)` | Your approval of the content; emits `submission.approved` |
| Fill | browser automation, no Muninn lock held | Stops on the review page |
| Submit | the person clicks Submit; then `submissions.mark_submitted(con, id, external_id, url)` | Security submissions carry an attestation in your name, so the click stays human |
| Track | poll queue `next_poll_at` (`ix_submissions_poll`); `submissions.record_status(con, id, state, raw)` | `last_status_raw` keeps the status text as shown; emits `submission.status_changed` |

## The command line, and AI clients

Heimdall's commands run as `asgard heimdall ...` from any folder (`heimdall.cmd` still works). Besides `init`, `fields`, `template` and `fill`, since issue #12:

| Command | What it does |
| --- | --- |
| `template variables NAME [--json]` | What a template asks for: name, label, kind, checks, options, library |
| `template check NAME [--json]` | Reads the template, checks its libraries are here, and says what a fill would ask for; no values needed. Exit 1 if it can't be filled yet |
| `library search NAME TEXT [--json]` | The window's picker search: entry names and descriptions that match, never values |
| `fill -t NAME --vars FILE.json` | The variables' values as one JSON object; `--var` overrides it |
| `fill -t NAME` at a terminal | Asks for each value not given, one at a time, checking each as you type. A script or `--json` never gets a prompt: it fails naming what's missing |

The guides for the people who write [templates](../heimdall-templates.md) and publish [libraries](../heimdall-libraries.md) are separate.

Ysildir (docs/integration/ysildir.md) has five read-only Heimdall tools, each off until the person switches it on: `heimdall_templates`, `heimdall_template_variables`, `heimdall_library_list`, `heimdall_library_search` and `heimdall_plan`. They return names and descriptions, never a template's field values or a library's values (a plan shows a library pick by the entry's name), and no tool fills the form, clicks Submit, or changes a template or library: the fill and the Submit click stay with the person. Baldur's agent guard doesn't read `asgard heimdall fill`: the fill opens Edge and stops before Submit, and is the person's to run.

## Rules for the diverging copy

1. Open with `muninn.open_app("heimdall", supported=(3, 3))`; never migrate. If Heimdall needs columns, they come in an Asgard migration.
2. Never hold a transaction while the browser works.
3. `fields` holds the same sensitive detail as security change itself: same handling as its exports; never in events (put the id and state in payloads, not field values).
4. Templates, libraries and browser profiles stay in Heimdall's own folders (`settings\heimdall-templates.json`, `heimdall\libraries\`, the Edge profile); they are not Muninn data. Valhalla must list the Edge profile and libraries folders in its purge set (open item from the inventory).
5. Playwright is allowed under the dependency policy (declared and pinned), but stays out of `asgard.muninn`: only the `fill` command imports it, as today.
6. Template variables (`heimdall/variables.py`) and libraries (`heimdall/library.py`) import nothing from Heimdall apart from `asgard.dpapi`, `asgard.paths` and `asgard.jsonfile`, so the on-prem copy can lift them whole. `templates.plan()` is the one place they're resolved, and both the browser fill and a ServiceNow API fill should take values from its `Plan`. A library's values live only in its encrypted file and in the `Plan` of a fill that picked them. Like `fields`, they never go into events or Muninn.

## Tests the contract needs

A submission taken through every state produces one history row per change; withdrawn from approved keeps `approved_at`; a Bifrost connection can't change a `security change` row through the module.
