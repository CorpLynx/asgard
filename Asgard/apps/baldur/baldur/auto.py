"""Auto-approving the engine's own figures, for someone who has trialled it and trusts the numbers.

This is the one place a figure reaches Jira without a person looking at that day, so it is written to
refuse rather than guess. It replaces an earlier "approval off" setting that was deliberately removed
(Baldur spec, "Four old settings are gone on purpose"); the decision is superseded, not forgotten,
and the guard rails below are the reason it can be.

Two ways in, and they consent differently (the shape on-prem Odin uses, decided 2026-10-11):
- **`plan` then `apply`.** `plan` shows exactly what would be approved and decides nothing; `apply`
  shows the same thing and approves it once you type YES. The typing is the approval, so this needs
  no setting: you are looking at the figures as you agree to them.
- **Unattended, for the scheduled run.** That one needs the `auto_approve` setting on **and** the
  caller to ask (`refresh --auto-approve`). The flag is what Baldur's agent guard matches, so an
  agent running a plain `refresh` can never approve, and the setting is what stops a stray flag
  mattering on a machine that never opted in.

What it will never do:
- **Take an AI's number.** A day with AI-assisted figures is left for you. An AI may only suggest,
  and suggesting a different number is exactly the signal a person should look (Asgard rule 4; the
  spec's "The AI-assisted method never replaces the engine's numbers").
- **Decide a day the report would flag**: a weekend, time outside your tour of duty, a day scaled
  down to the cap, a day whose sessions rest on an expired reflog. The spec already said weekends
  and leave are "flagged ... nothing is approved automatically".
- **Decide a day that is still settling.** A day younger than `auto_approve_min_age_days` waits, so
  a late commit or a key added tomorrow is in before the day is decided.
- **Decide more than `auto_approve_max_days_per_run` days at once**, oldest first, so switching it
  on with a year of history behind you can't approve hundreds of days in one go.
- **Raise anything, or approve untracked time.** It approves each ticket at the engine's own figure
  and nothing else; the database refuses untracked approvals anyway.

Approving is final and Odin posts it on its next run, with no second question. Nothing here can take
posted time back, which is why every rule above fails closed.
"""
from __future__ import annotations

import datetime as dt
import sqlite3
from dataclasses import dataclass, field
from typing import List, Optional

from . import desk
from . import estimate as E
from .settings import Settings

# A flag that means "a person should look at this day", so it is never decided automatically. Matched
# against the start of each flag in DayView.flags.
#
# The calendar ones are here because they raise the figure, not just annotate it: with no meetings
# known, meeting time gets no ambient discount, so the same commits can estimate at twice the
# minutes they would with the calendar in. Odin also logs that meeting time separately, so the day
# would be counted twice. Biasing down means missing data has to fail closed. The independent review
# of 2026-10-11 (F2) found a day auto-approved at 240m that was 120m once the calendar arrived.
HOLD_FLAGS = ("A weekend day", "Scaled down to the day cap", "Meetings (", "Git's reflog doesn't reach back",
              "A squash merge links sessions", "No calendar data for this day",
              "The calendar was last synced at", "Commit ")


class AutoApproveError(Exception):
    """Auto-approve can't run. The message says what to change."""


@dataclass
class Candidate:
    """One day auto-approve looked at, and what it decided."""
    day: dt.date
    minutes: int = 0
    keys: List[str] = field(default_factory=list)
    each: List[int] = field(default_factory=list)      # the engine's figure per key, in step with keys
    hold: str = ""                 # why it was left for the person; "" means it qualifies
    approved: List[int] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.hold

    def line(self) -> str:
        what = ", ".join(f"{k} {E.fmt(m)}" for k, m in zip(self.keys, self.each)) if self.each else "nothing to approve"
        return f"{self.day.isoformat()}  {what}" + (f"   left for you: {self.hold}" if self.hold else "")


def enabled(settings: Settings) -> bool:
    return bool(settings.values.get("auto_approve", False))


def waiting_days(con: sqlite3.Connection) -> List[dt.date]:
    """Every day with an open proposal for a ticket, oldest first."""
    return [dt.date.fromisoformat(r[0]) for r in con.execute(
        "SELECT DISTINCT local_date FROM day_proposals WHERE status = 'proposed' AND work_item_key IS NOT NULL "
        "ORDER BY local_date")]


def candidates(con: sqlite3.Connection, settings: Settings, today: Optional[dt.date] = None) -> List[Candidate]:
    """Every waiting day, oldest first, each with its figures or why it's being left for you."""
    today = today or dt.date.today()
    min_age = int(settings.values.get("auto_approve_min_age_days", 2))
    out: List[Candidate] = []
    for day in waiting_days(con):
        out.append(_look(con, settings, day, today, min_age))
    return out


def _look(con: sqlite3.Connection, settings: Settings, day: dt.date, today: dt.date, min_age: int) -> Candidate:
    age = (today - day).days
    if age < min_age:
        return Candidate(day, hold=f"it is {age} day(s) old; auto-approve waits {min_age}")
    view = desk.load_day(con, settings, day)
    open_rows = [t for t in view.tickets if t.open_id is not None and t.estimate]
    found = Candidate(day, keys=[t.key for t in open_rows], each=[int(t.estimate) for t in open_rows])
    found.minutes = sum(found.each)
    if not open_rows:
        found.hold = "nothing is waiting on it"
        return found
    # A ticket the window itself would refuse to approve (its key isn't in Jira, or Jira can't take
    # the time) is not one to decide unseen: review F6.
    blocked = [t for t in open_rows if not t.approvable or t.problem]
    if blocked:
        found.hold = f"{blocked[0].key}: {blocked[0].problem or 'it cannot be approved yet'}"
        return found
    # An explicit "no" from the person is not an invitation to decide the day once the number moves.
    # Review F5: reject a day, let one more commit land, and it would have been approved.
    if _was_rejected(con, day):
        found.hold = "you rejected this day before; decide it yourself"
        return found
    # An AI figure is a suggestion, and a suggestion that differs is the signal a person should look.
    if view.ai is not None and view.ai.changed():
        found.hold = f"AI-assisted figures differ ({view.ai.moves()}); approve it yourself"
        return found
    held = [f for f in view.flags if any(f.startswith(h) for h in HOLD_FLAGS)]
    if held:
        found.hold = held[0]
        return found
    if _outside_tour(settings, view):
        found.hold = "its work is outside your tour of duty"
    return found


def _was_rejected(con: sqlite3.Connection, day: dt.date) -> bool:
    return bool(con.execute("SELECT 1 FROM day_proposals WHERE local_date = ? AND status = 'rejected' LIMIT 1",
                            (day.isoformat(),)).fetchone())


def _outside_tour(settings: Settings, view: desk.DayView) -> bool:
    """Whether the day's development time lands outside the hours you work.

    A day's figure is built from sessions; the report flags the ones that sit outside the tour. Here
    the whole day is held if its total passes the paid minutes in the tour, which is the same bound
    the estimator caps on and needs no session detail.
    """
    total = sum(int(t.estimate or 0) for t in view.tickets) + int(view.untracked or 0)
    return total > settings.tour_minutes()


def run(con: sqlite3.Connection, settings: Settings, today: Optional[dt.date] = None,
        dry_run: bool = False, unattended: bool = False) -> List[Candidate]:
    """Approve the days that qualify, oldest first, at the engine's figures. Returns every candidate.

    `unattended` is the scheduled path, where nobody is watching: it needs the `auto_approve`
    setting as well as the caller asking. `apply` passes False, because the person typing YES to
    the plan in front of them is the approval. dry_run decides nothing either way.
    """
    if unattended and not enabled(settings):
        raise AutoApproveError(
            "Auto-approve is off, so nothing was approved. It is the one setting that lets a figure "
            "reach Jira without you looking at that day, and Odin can't take posted time back. To "
            "read what it means and switch it on: asgard baldur auto-approve --on")
    cap = int(settings.values.get("auto_approve_max_days_per_run", 5))
    found = candidates(con, settings, today)
    taken = 0
    for candidate in found:
        if not candidate.ok:
            continue
        if taken >= cap:
            candidate.hold = f"more than {cap} days qualified this run; it waits for the next one"
            continue
        if dry_run:
            taken += 1
            continue
        # The engine's figures only: no figures= override, and take_ai stays None by not being passed.
        candidate.approved = desk.approve_day(con, settings, candidate.day)
        taken += 1
    return found


def summary(found: List[Candidate], dry_run: bool = False) -> List[str]:
    """What happened, for the command line and the log."""
    did = [c for c in found if (c.approved or (dry_run and c.ok))]
    held = [c for c in found if not c.ok]
    verb = "Would approve" if dry_run else "Approved"
    lines = [f"{verb} {len(did)} day(s)" + (f", {E.fmt(sum(c.minutes for c in did))} in total" if did else "")
             + ("." if not did else ":")]
    lines += [f"  {c.line()}" for c in did]
    if held:
        lines.append(f"Left for you: {len(held)} day(s).")
        lines += [f"  {c.line()}" for c in held]
    if did and not dry_run:
        lines.append("Odin posts these on its next run. Baldur can't change a decision afterwards.")
    return lines
