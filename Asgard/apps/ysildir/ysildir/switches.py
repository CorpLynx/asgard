"""Which Ysildir tools exist, and which are on by default: the plain data behind config.py.

Kept apart from config.py because that module needs pydantic (the MCP SDK's). This one is standard
library, so Asgard's window can list the tools and say what each sends even on a Python where the SDK
isn't installed - which is exactly when the person needs to be told.
"""
from typing import NamedTuple

class Switch(NamedTuple):
    name: str
    default: bool
    sends: str          # what the tool sends to the AI client: what the ISSO approves
    fallback: str       # the command-line way to do the same, when the tool is off or MCP isn't there


# The allow-list, in the documented order. server.TOOLS has exactly these names (a test checks), so
# adding a tool means adding its switch, its data flow and a spec change.
SWITCHES = (
    Switch("asgard_guide", True, "Asgard's own guides", "asgard baldur ai guide"),
    Switch("muninn_catalog", True, "Muninn's table names, owners and row counts", "none"),
    Switch("baldur_record_estimate", True, "The id and status of the report it records",
           "asgard baldur ai record ... --json"),
    Switch("baldur_withdraw_estimate", True, "The id of the report it withdraws", "asgard baldur ai withdraw r12"),
    Switch("baldur_estimates", True, "The agents' own reports: dates, keys, minutes and summaries",
           "asgard baldur ai list --json"),
    Switch("baldur_day", False, "One day's keys, minutes, AI reasons and flags; the day report (commit subjects) "
           "when asked", "asgard baldur ai show DATE --json, asgard baldur report DATE"),
    Switch("baldur_review_pack", False, "One day's evidence: commit subjects, times, line counts, keys and agent "
           "reports", "asgard baldur ai pack DATE"),
    Switch("baldur_submit_review", False, "The checked figures and flags", "asgard baldur ai review DATE ANSWER.json"),
    Switch("muninn_what_changed", False, "Event kinds, times, keys and allow-listed payload fields", "none yet"),
    Switch("muninn_day_status", False, "Approved and logged minutes per day and ticket", "asgard baldur report DATE"),
    Switch("muninn_issue", False, "One Jira issue's key, summary, status, type and epic", "none yet"),
    Switch("muninn_search", False, "Jira keys and summaries, the person's commit subjects, pull request titles",
           "none yet"),
    Switch("heimdall_templates", False, "The person's template names and descriptions, and what each asks for "
           "(variable names, labels and kinds); never the values a template fills in",
           "asgard heimdall template list --json"),
    Switch("heimdall_template_variables", False, "One template's variables: name, label, kind, checks and options",
           "asgard heimdall template variables NAME --json"),
    Switch("heimdall_library_list", False, "Library names, descriptions, entry counts and versions; no entries",
           "asgard heimdall library list --json"),
    Switch("heimdall_library_search", False, "Entry names and descriptions that match a search; never values",
           "asgard heimdall library search NAME TEXT --json"),
    Switch("heimdall_plan", False, "What a fill would type into each field for values you give; a library pick "
           "shows as the entry's name, never its value", "asgard heimdall fill -t NAME --var ... --dry-run --json"),
)
NAMES = tuple(s.name for s in SWITCHES)
ALWAYS_ON = "asgard_guide"      # stays on when the file is broken, so the agent can say what's wrong
VERSION = 1
HELP = ("Which Ysildir tools your AI client may use. Each tool's data flow needs your ISSO's approval (Asgard "
        "docs/integration/ysildir.md). Restart the MCP server in your client after a change.")
