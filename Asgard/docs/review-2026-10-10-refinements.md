# Independent review of refinements 1a, 1b and 2 (2026-10-10)

One review by an agent with no part in writing the code, of branch `claude/ci-and-odin-tests` at `5e2835b`: the Muninn triggers that seal agent estimates (1b: `b953e58`, `2b45068`), Odin recognising a moved one-off meeting (1a: `81c499b`) and `odin settle` (2: `5e2835b`), as specified in [refinements-spec.md](refinements-spec.md). It tried to break them with scripts it ran; every finding below was reproduced, then fixed with a regression test that fails without the fix.

| Severity | Confirmed | Worst |
| --- | --- | --- |
| Blocker | 1 | CI was red: the OWA exporter's launcher didn't pass `settle` through, which its parity test requires |
| Should fix | 4 | A genuinely new meeting sharing a one-off's calendar id (a copy a week later, a one-off with a series' id) was taken for the meeting moved, so its time was never logged; an empty or garbled 200 from Jira read as "no worklogs", so a landed post was settled as not posted |
| Minor | 1 | A report written raw without its `agent_estimate.recorded` event was never sealed |

The reviewer's scripts were re-run against the fixed code. The moved-meeting and empty-answer scripts pass. Two settle scripts assert one of the fixes the reviewer offered (resend an old meeting; offer the day again after a mistyped id); the other offered fix was taken instead, and they now show it: the old meeting is reported as needing to be logged by hand, and a mistyped id is refused with the post left in doubt.

## Blocker

| # | Defect | Fix | Test |
| --- | --- | --- | --- |
| 1 | `Odin/playwright-app/tests/test_parity.py` requires `meeting2jira-owa.cmd` to offer every `odin.cmd` command, and `settle` was missing; `checks.yml` and `odin-checks.yml` failed on `5e2835b`. The Graph launcher lacked it too, untested | Both launchers delegate `settle` to Odin, like `forget`, and list it in their help (`d218418`) | `test_parity.EntryPointParityTests.test_command_surface_covers_the_com_app` |

## Should fix

| # | Defect | Fix | Test |
| --- | --- | --- | --- |
| 2 | 1a matched any record whose key started with the meeting's id. Its only guard (the old key still in this export) failed when the old time was outside the export (the daily run reads yesterday and today), so a copy made a week later was reported `MOVED`; a one-off sharing a series' id matched one of the series' records; an id containing `\|` matched a shorter one | `store.find_moved` matches only when exactly one record has the id and the rest of both keys is a timestamp. `store.left_old_time` then needs evidence the old time is gone: its calendar event was swept, the record already points at this meeting's event, or this export read the old time in full and it isn't there (`run(..., window=)`, passed by `cli`). Otherwise the meeting is pushed as before: a second sub-task can be deleted in Jira, a meeting taken for a moved one never has its time logged | `test_odin_moved`: `test_a_copy_outside_the_window_is_another_meeting`, `test_a_one_off_with_the_id_of_a_series_is_another_meeting`, `test_an_id_with_a_bar_in_it_doesnt_match_a_shorter_one`, `test_without_a_window_nothing_says_the_old_time_is_gone_so_it_is_pushed_as_before`, `test_once_linked_it_is_found_without_a_window` |
| 3 | `JiraClient.request` returns None for an empty body before checking the content type, and `issue_worklogs` read None, `{}`, `{"worklogs": null}` or `[]` as no worklogs. `odin settle` (and the start-of-run settle) then recorded a post that had landed as not posted | `issue_worklogs` raises `JiraError` unless the answer is an object with a `worklogs` list and an integer `total`, so a non-answer leaves the post in doubt | `test_odin_jira_client.RealClientTests.test_an_answer_without_a_worklog_list_is_a_failure_not_no_worklogs` |
| 4 | Settling a meeting post as not posted said its time was offered again, but meeting time is only retried for `store.RETRY_DAYS` (14) after the sub-task was made, and long-stuck posts are exactly the old ones | `posting.offered_again` answers whether a run will send it again (an approved day while it's due; a meeting while `store.will_retry`). When none will, `odin settle` and the start-of-run settle say so and give the length, issue and start to log by hand | `test_odin_settle.OldMeetingTests` |
| 5 | `--posted WORKLOG` stored any id unchecked, and the post then left `settle`'s reach. `'١٢٣'` passed `str.isdigit()` and was echoed back (non-ASCII output); `050001` left the post `deleted` beside a second row for the same worklog | Only ASCII digits, stored without leading zeros. When Jira can answer, the worklog has to be on the issue and carry the post's marker, or be the same length (time logged by hand); otherwise it's refused and nothing changes. When Jira can't answer, the person's word stands and the message says it wasn't checked | `test_odin_settle.PersonSettlesTests`: `test_posted_is_checked_in_jira_when_jira_can_answer`, `test_time_logged_by_hand_at_the_same_length_is_taken`, `test_when_jira_cant_answer_the_persons_word_stands_and_says_so`, `test_posted_needs_a_number` |

## Minor

| # | Defect | Fix | Test |
| --- | --- | --- | --- |
| 6 | The commit trigger seals a report by its `agent_estimate.recorded` event. A report inserted raw without one could have commits added from a connection whose `last_insert_rowid()` happened to match | `integrity.check` warns about recorded reports with no such event, with the command to withdraw them. `record_agent_estimate` always writes the event in the same transaction, so only a raw write gets here; the digest check still catches a changed one | `test_baldur_assist.ReviewFindingsTests.test_a_report_written_without_its_event_is_flagged` |

## What held up

- **1b.** A real three-commit report records through `record_agent_estimate`. Adding to a sealed report was refused from the same and another connection, by `INSERT ... SELECT`, multi-row inserts, a temp trigger, a temp-table rowid collision, an id given as text, and after a rolled-back report with the same id. `recorded_at` three minutes either side is refused and 90 seconds accepted; every other format is refused (the column's CHECK backs the trigger). Drift and repair work through the API and `Asgard.pyw --muninn check/repair`; the same on Python 3.9's SQLite.
- **1a.** Ids that are prefixes of each other, differ in case or by a combining character never match; moved twice and back is one sub-task and one worklog; the creation cap and the within-run dedupe hold; `odin forget` then a move creates again; COM and Graph keys never match each other; the v5 trigger is never tripped and `logged_as_key` is consistent.
- **2.** The run lock is held across the Jira call; a paged answer (1,201 worklogs) and a structured comment are searched; negative and huge ids are refused; the `<` to `<=` change in `stuck_posts` only moves the start-of-run cut-off by a second, inside the run lock, so a post still being sent can't be taken for a lost one; exit codes are consistent.
