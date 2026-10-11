# Asgard[cite: 1]

Asgard is a suite of small Python apps for a federal software engineer on a locked-down Windows 11 workstation[cite: 1]. A launcher shows one tile per app, and the apps share one SQLite database, Muninn[cite: 1]. 

## The Suite[cite: 1]

* **Asgard**: Launcher, installer (`setup-Asgard.cmd`), and tiles with Muninn badge counts[cite: 1].
* **Muninn**: Shared SQLite database and `asgard.muninn` package[cite: 1].
* **Baldur**: Work-time estimates from git history and pull request alerts[cite: 1].
* **Odin**: Converts meetings to Jira sub-tasks; syncs Jira issues, calendars, and worklogs into Muninn; posts approved Baldur days to Jira[cite: 1].
* **Heimdall**: Security change submissions via Playwright and Edge[cite: 1].
* **Ysildir**: MCP server allowing approved AI tools to use Asgard[cite: 1].
* **Valhalla**: Uninstaller that replays the install ledger to remove the suite[cite: 1].

## Planned Apps[cite: 1]

* **Freya**: Yearly review drafts with citations[cite: 1].
* **Loki**: BLUFs for meetings and code changes[cite: 1].
* **Bifrost**: Entitlements workbooks and Confluence uploads[cite: 1].
* **Valkyrie**: Setup scripts for new engineers[cite: 1].

## Environment & Constraints[cite: 1]

* Windows 11, standard user, no admin rights[cite: 1].
* AppLocker or App Control enforcing; unsigned PowerShell runs in Constrained Language Mode[cite: 1].
* TLS inspection and PIV smart-card sign-in[cite: 1].
* Software must come from the agency catalog (Python 3.9+ required)[cite: 1].
* Everything is installed per-user under `%LOCALAPPDATA%\Asgard`[cite: 1].