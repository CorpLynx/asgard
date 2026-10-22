# Changelog

Format follows [Keep a Changelog](https://keepachangelog.com); versions are the payload's `VERSION`.

**Two copies of Asgard exist.** This repository (GitHub, `CorpLynx/meeting2jira`) and the on-premises
copy on the workstation have drifted: the on-prem copy has changes this one doesn't, and this one has
changes that aren't on-prem yet. Each entry below says where it lives. The "On-prem only" section is for
the owner to fill in from the workstation (see "Reconciling" at the end); nothing in it was written from
the on-prem code, because it hasn't been read.

## Unreleased (toward 0.4.0, `VERSION` still 0.3.1)

Drafted on 2026-10-22 from `git log` since `VERSION` last changed (2026-10-05) and from `HANDOFF.md`.
Everything here is **in this repo, and not known to be on-prem** unless marked.

### Added
- **Notifications.** `asgard/notify.py`: apps declare events in `apps/<id>/notify.json` and raise them with `notify.send()`; your choices, quiet hours and repeat suppression apply first. `asgard notify list|on|off|quiet|test`. Wired into Baldur's scheduled refresh, Odin's runs and Heimdall's fill. Windows shows a tray balloon through ctypes; not yet seen on a Windows desktop.
- **AI review evals (draft).** `evals/`: a DeepEval test file and a Promptfoo config over the same synthetic cases, graded by Baldur's own `check_review`. Dev only, never shipped. `tests/test_evals_grading.py` keeps the grader honest in the ordinary suite.
- **`asgard` command.** `asgard <app> <command>` and `asgard <command>` (issue #10); `asgard.cmd` returns the program's exit code.
- **Baldur:** scheduled refresh (`asgard baldur schedule`, `refresh --quiet`), `plan` and `apply`, auto-approve with its own switch and warning (#7), GitHub review-request sync, calibration, AI-assisted figures at three tiers (API, MCP, clipboard), agent estimates and their hooks.
- **Heimdall:** command line (`asgard heimdall ...`), template variables and libraries (#11), a template configuration language, a Qt window split into **Submit a change** and **Templates**, settings, and `asgard.secrets` (Credential Manager) for the ServiceNow password.
- **Ysildir:** Asgard's MCP server, with Baldur, Heimdall and Muninn tools and teaching text for AI clients (#12). Needs Python 3.10+ and the pinned `mcp` SDK.
- **Odin on Muninn.** Odin is an Asgard app: issue and worklog sync, Assigned to Me, tracked parents, posting Baldur's approved days, `odin settle`, moved one-off meetings recognised by calendar id.
- **Muninn hardening.** Triggers that stop edits to decided proposals and agent estimates (the Oct 9 review's R14 and P7), integrity checks, backups, retention switch.
- **Shared Qt window** (`asgard.ui`, PySide6-Essentials 6.10.3): Dashboard, Settings built from each app's own schema, the warning banner, light and dark themes. The launcher's Apps page runs on it; tkinter stays as the fallback.
- **Packaged build** (PyInstaller; `packaging/build.py`), update design (`docs/updates.md`), CI on Linux (Python 3.9, 3.11, 3.13) and Windows.
- A local fake Jira (`tools/fake_jira_server.py`) to try Odin against.

### Changed
- Baldur's tile opens the Qt window; the tkinter window (`baldur/window.py`) is removed.
- Packages are allowed when declared and pinned (`MODULES.md`); standard-library-only is no longer a rule, except for Muninn and the launcher's start-up path.
- Docs moved to the repo's `docs/` folder; the live Claude Docs are the source and the snapshot follows.

### Fixed
- Windows build: no cached Muninn connection across calls, and renames that Windows refuses are retried (`asgard/jsonfile.py`).
- Baldur window: a connection per call, and settings writes that wait their turn.
- Odin finds the on-premises data folder (`%LOCALAPPDATA%\odin`) and moves it, instead of creating an empty one and re-creating every meeting.

### Known
- Three Baldur `RefreshTests` fail on any date more than 14 days after 2026-10-01: they re-estimate only the last 14 days of a fixture dated 1 Oct. They need a `--days` argument or a fixed clock.
- `AGENTS.md` links `docs/dependency-policy.md`, which is not in this repo. It is probably on-prem only (or never pushed).

## On-prem only (not in this repo)

*Owner to fill in.* One line each, with the on-prem file or commit, whether this repo should get it, and
whether it conflicts with something above.

- [ ] _example: Odin's `Register-MeetingSyncTask.ps1` runs as ... (on-prem `windows/...`) -- port? yes/no_

## Reconciling

1. On the workstation, list what differs: `git log --oneline` there against this repo's, or compare the two
   trees (`diff -rq` over `asgard/`, `apps/`, `docs/`, `tools/`, ignoring `__pycache__`).
2. For each difference decide: port to this repo, keep on-prem only, or drop. Put it in the section above.
3. `docs/dependency-policy.md` and `VERSION` are the first things to compare; the on-prem `VERSION` says
   which of the two is ahead.
4. Keep this file the same on both sides, with the on-prem-only section holding the real difference.
