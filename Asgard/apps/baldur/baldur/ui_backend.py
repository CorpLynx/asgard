"""What Baldur's pages in Asgard's window show, with no Qt: the week, one day, and the reviews waiting.

Everything here goes through desk.py, the same plain-Python layer Baldur's tkinter window uses, so the
two windows can't drift apart on what a figure means. Rules it keeps:

- **Read and decide, never estimate.** The numbers come from `desk.load_week` and `desk.load_day`;
  this module never computes one.
- **Approving is the person's.** `approve_day` and `reject_day` are offered because a window is where
  consent happens, and they go straight to `muninn.baldur`, which refuses to edit a decision already
  made. Nothing here posts to Jira: Odin does that, from what was approved.
- **Nothing slow on the window's thread.** Collecting reads git and syncing GitHub reads the network,
  so the page calls both through the shell's `callAsync`, and estimating is offered as a button, not
  done on open.
- **A figure you type is yours, and only yours.** Staged figures are what the person typed, parsed by
  the CLI's own parser; nothing here picks a number. Changing an approved figure supersedes it through
  `desk.change_figure`, never edits it.
"""
from __future__ import annotations

import contextlib
import datetime as dt
import sqlite3
import time
from typing import Any, Dict, List, Optional

from asgard import muninn

from . import collect as collector
from . import desk, github, gitread, settings as settings_mod
from .cli import SCHEMA, CliError, parse_day_minutes
from .estimate import fmt

# Baldur's own expected failures. Its CLI prints these as a line and exits; the window shows them on
# the page. They aren't all ValueErrors, which is what the shell treats as "expected", so they are
# raised as one (see _expected).
EXPECTED = (CliError, collector.CollectError, desk.DeskError, gitread.GitError, muninn.MuninnError)


def _expected(fn, *args: Any, **kwargs: Any) -> Any:
    """Run fn, turning Baldur's and Muninn's own messages into ValueError so the page shows them."""
    try:
        return fn(*args, **kwargs)
    except EXPECTED as exc:
        raise ValueError(str(exc)) from None


def _date(text: Optional[str]) -> dt.date:
    if not text:
        return dt.date.today()
    try:
        return dt.date.fromisoformat(str(text)[:10])
    except ValueError:
        raise ValueError(f"{text!r} isn't a date like 2026-10-09") from None


def _decided(t: desk.TicketRow) -> bool:
    """Approved, with nothing newer waiting: a figure to change, not one to stage."""
    return t.approved_id is not None and t.open_id is None


def _stageable(t: desk.TicketRow) -> bool:
    """What approve_day would approve: the tkinter window's rule, so the two can't disagree."""
    return t.approvable and (t.open_id is not None or t.approved_id is None)


class Backend:
    """Called from QML through the shell's bridge. Returns plain data, or raises ValueError for the page."""

    def __init__(self) -> None:
        # Which day the day page shows. The shell keeps one backend per app, so this survives moving
        # between pages; QML pages are built again each time and can't hold it themselves.
        self._chosen: Optional[str] = None
        # Figures the person typed for a day, not approved yet: {date: {key: minutes}}. Here for the
        # same reason as _chosen, and cleared for a day once approving it succeeds.
        self._staged: Dict[str, Dict[str, int]] = {}
        # The last GitHub pass (time.monotonic(), its reply). The week page asks each time it opens;
        # within poll_minutes of the last pass it gets that answer again instead of another pass.
        self._github: Optional[tuple] = None

    # ---- plumbing ---------------------------------------------------------
    def _open(self, readonly: bool = True) -> sqlite3.Connection:
        """A connection to Muninn, which the caller closes. Nothing is cached, deliberately.

        A connection held open keeps muninn.db open, and on Windows an open file can't be replaced or
        deleted: that breaks an Asgard upgrade, and the temporary folder a test runs in (the shell
        calls in from its thread pool as well as the window's thread, so a per-thread cache leaves a
        handle open for every pool thread that ever served a page).

        Muninn's errors already say what to do ("Open Asgard once, then start baldur again"), but they
        aren't ValueErrors, so the shell would log them as unexpected and show a generic line instead.
        Raised as ValueError, the page shows the real message.
        """
        return _expected(muninn.open_app, "baldur", supported=SCHEMA, readonly=readonly)

    @contextlib.contextmanager
    def _read(self):
        """Muninn for one read, closed afterwards."""
        con = self._open(readonly=True)
        try:
            yield con
        finally:
            con.close()

    def _settings(self) -> settings_mod.Settings:
        settings = settings_mod.load()
        if settings.broken:
            raise ValueError(f"{settings_mod.settings_path()} has a mistake in it ({settings.warnings[0]}). "
                             "Fix it, or delete it to start again from the defaults.")
        return settings

    def close(self) -> None:
        """Nothing to close: every connection is opened and closed around one read or write.

        Kept because the shell closes a backend that offers it, so this stays true if one is ever
        held open again.
        """

    # ---- the week ---------------------------------------------------------
    def week(self, monday: Optional[str] = None) -> Dict[str, Any]:
        """One week of days. monday defaults to the week of today; any date in a week will do."""
        start = desk.week_of(_date(monday))
        settings = self._settings()
        with self._read() as con:
            rows = _expected(desk.load_week, con, settings, start)
        return {
            "monday": start.isoformat(),
            "previous": (start - dt.timedelta(days=7)).isoformat(),
            "next": (start + dt.timedelta(days=7)).isoformat(),
            "is_this_week": start == desk.week_of(dt.date.today()),
            "policy": settings.policy,        # how coding during meetings counts
            "poll_minutes": int(settings.values.get("poll_minutes", 5) or 5),
            "github": bool(settings.github_host()),
            "days": [{"date": r.day.isoformat(), "name": r.day.strftime("%a %d %b"), "dev": r.dev,
                      "dev_text": fmt(r.dev), "tickets": r.tickets, "review": r.review,
                      "has_work": r.has_work, "today": r.day == dt.date.today()} for r in rows],
            "total_text": fmt(sum(r.dev for r in rows)),
        }

    # ---- one day ----------------------------------------------------------
    def choose_day(self, date: str) -> Dict[str, Any]:
        """Remember which day the day page should open at (the week page calls this)."""
        self._chosen = _date(date).isoformat()
        return {"date": self._chosen}

    def day(self, date: Optional[str] = None) -> Dict[str, Any]:
        settings = self._settings()
        chosen = _date(date or self._chosen)
        self._chosen = chosen.isoformat()
        with self._read() as con:
            view = _expected(desk.load_day, con, settings, chosen)
        ai = view.ai
        staged = self._prune(view)
        return {
            "date": view.day.isoformat(),
            "name": view.day.strftime("%A %d %B %Y"),
            "report": view.report,
            "untracked": view.untracked,
            "untracked_text": fmt(view.untracked),
            "flags": list(view.flags),
            "timesheet": desk.timesheet(view),
            # As the tkinter window had it: a ticket not proposed yet can be approved too, because
            # approve_day stores the estimate before it approves.
            "approvable": any(_stageable(t) for t in view.tickets),
            "has_approved": any(t.approved_id is not None for t in view.tickets),
            "tickets": [{"key": t.key, "estimate": t.estimate, "estimate_text": fmt(t.estimate),
                         "figure": t.figure, "figure_text": fmt(t.figure),
                         "approved": t.approved, "approved_text": fmt(t.approved) if t.approved is not None else "",
                         "held": t.held, "held_text": fmt(t.held) if t.held is not None else "",
                         "review": t.review, "problem": t.problem or "",
                         "open": t.open_id is not None, "approvable": t.approvable,
                         # What the row's figure box does: stage a figure, change an approved one, or nothing.
                         "edit": "stage" if _stageable(t) else ("change" if _decided(t) else ""),
                         "staged": staged.get(t.key),
                         "staged_text": fmt(staged[t.key]) if t.key in staged else ""} for t in view.tickets],
            "staged": len(staged),
            "ai": None if ai is None else {
                "source": ai.source,              # "agent estimates (kiro, 2 reports)"
                "moves": ai.moves(),              # PROJ-42 1h30m -> 45m, ...
                "digest": ai.digest(),            # approve takes these figures only while they're these
                "flags": list(ai.flags),
                "changed": len(ai.changed()),
            },
        }

    # ---- decisions (the person's) -----------------------------------------
    def approve(self, date: str, take_ai: str = "") -> Dict[str, Any]:
        """Approve a whole day as it stands. muninn.baldur refuses to touch a decision already made.

        take_ai is the `digest` the day page showed for the AI-assisted figures. Passing it approves
        those instead of the plain estimate, and it is refused if a new agent report has changed them
        since you looked.
        """
        settings = self._settings()
        day = _date(date)
        figures = dict(self._staged.get(day.isoformat(), {}))
        con = self._open(readonly=False)
        try:
            ids = _expected(desk.approve_day, con, settings, day, figures or None, take_ai=(take_ai or None))
        except ValueError as exc:
            # "Nothing to approve on DATE" is an answer, not a failure: the page says so and stays put.
            # Only that one: "PROJ-42 has nothing to approve" is a typed figure refused, and must be seen.
            if str(exc).startswith("Nothing to approve"):
                return {"message": "Nothing on that day was waiting to be approved.", "count": 0}
            raise
        finally:
            con.close()
        self._staged.pop(day.isoformat(), None)
        return {"message": f"Approved {len(ids)} figure(s). Odin posts them on its next run.", "count": len(ids)}

    def reject(self, date: str) -> Dict[str, Any]:
        settings = self._settings()
        con = self._open(readonly=False)
        try:
            ids = _expected(desk.reject_day, con, settings, _date(date))
        except ValueError as exc:
            if str(exc).startswith("Nothing to reject"):
                return {"message": "Nothing on that day to reject.", "count": 0}
            raise
        finally:
            con.close()
        # Rejected, a day's typed figures have nothing left to approve.
        self._staged.pop(_date(date).isoformat(), None)
        return {"message": f"Rejected {len(ids)} figure(s).", "count": len(ids)}

    # ---- figures you type -------------------------------------------------
    def parse_minutes(self, text: str) -> Dict[str, Any]:
        """1h15m, 75 or 1:15 as minutes, with the CLI's parser and its words when it refuses."""
        minutes = _expected(parse_day_minutes, str(text or ""))
        return {"minutes": minutes, "text": fmt(minutes)}

    def _ticket(self, date: str, key: str) -> desk.TicketRow:
        with self._read() as con:
            view = _expected(desk.load_day, con, self._settings(), _date(date))
        key = muninn.normalize_key(key)
        found = next((t for t in view.tickets if t.key == key), None)
        if found is None:
            raise ValueError(f"{key} has no time on {view.day.isoformat()}.")
        return found

    def _prune(self, view: desk.DayView) -> Dict[str, int]:
        """The day's staged figures, less any whose ticket was decided elsewhere (the CLI, or the old window)."""
        day = view.day.isoformat()
        staged = self._staged.get(day, {})
        keep = {t.key for t in view.tickets if _stageable(t)}
        for key in [k for k in staged if k not in keep]:
            del staged[key]
        if not staged:
            self._staged.pop(day, None)
        return dict(staged)

    def stage_figure(self, date: str, key: str, text: str) -> Dict[str, Any]:
        """Hold a figure the person typed for one ticket until they approve the day."""
        minutes = self.parse_minutes(text)["minutes"]
        t = self._ticket(date, key)
        if _decided(t):
            raise ValueError(f"{t.key} is already approved at {fmt(t.approved or 0)}. Change it instead.")
        if not _stageable(t):
            raise ValueError(f"{t.key} has nothing to approve on this day.")
        day = _date(date).isoformat()
        if minutes == t.estimate:
            # The same as the estimate: nothing to hold, and the row shows the estimate again.
            self._drop(day, t.key)
            return {"key": t.key, "minutes": None, "text": "", "message": f"{t.key} is back to the estimate."}
        self._staged.setdefault(day, {})[t.key] = minutes
        return {"key": t.key, "minutes": minutes, "text": fmt(minutes),
                "message": f"{t.key} will be approved at {fmt(minutes)}."}

    def _drop(self, day: str, key: str) -> bool:
        staged = self._staged.get(day, {})
        dropped = staged.pop(key, None) is not None
        if not staged:
            self._staged.pop(day, None)
        return dropped

    def clear_figure(self, date: str, key: str) -> Dict[str, Any]:
        key = muninn.normalize_key(key)
        dropped = self._drop(_date(date).isoformat(), key)
        return {"key": key, "cleared": dropped,
                "message": f"{key} is back to the estimate." if dropped else ""}

    def staged(self, date: str) -> Dict[str, int]:
        """The figures typed for a day and not approved yet, by ticket."""
        return dict(self._staged.get(_date(date).isoformat(), {}))

    def change_approved(self, date: str, key: str, text: str) -> Dict[str, Any]:
        """A different figure for an approved ticket: a new approved row supersedes it; Odin posts the difference.

        The page asks first. The approved row's id is looked up here, so it never travels through QML.
        """
        minutes = self.parse_minutes(text)["minutes"]
        t = self._ticket(date, key)
        if not _decided(t) or t.approved_id is None:
            raise ValueError(f"{t.key} has no approved figure on this day to change.")
        if minutes == t.approved:
            return {"key": t.key, "count": 0, "message": f"{t.key} is already {fmt(minutes)}."}
        con = self._open(readonly=False)
        try:
            _expected(desk.change_figure, con, t.approved_id, minutes)
        finally:
            con.close()
        return {"key": t.key, "count": 1,
                "message": f"{t.key} is now {fmt(minutes)}. Odin posts the difference on its next run."}

    # ---- the slow jobs, for callAsync -------------------------------------
    def collect(self) -> Dict[str, Any]:
        """Read git into Muninn. Reads every repository, so the page calls this off the UI thread."""
        settings = self._settings()
        if not settings.repo_roots:
            raise ValueError("Tell Baldur where your repositories are first: baldur.cmd setup --root C:\\src")
        con = self._open(readonly=False)
        try:
            result = _expected(desk.collect, con, settings)
        finally:
            con.close()
        new = sum(r.commits_new for r in result.repos)
        return {"message": f"{len(result.repos)} repositor(ies), {new} new commit(s).",
                "problems": [f"{r.name}: {r.error}" for r in result.repos if r.error]
                            + [f"{r.name}: {r.error}" for r in getattr(result, "failed", []) or []]}

    def estimate(self, first: Optional[str] = None, last: Optional[str] = None) -> Dict[str, Any]:
        settings = self._settings()
        start = _date(first)
        end = _date(last) if last else start + dt.timedelta(days=6)
        con = self._open(readonly=False)
        try:
            result = _expected(desk.estimate, con, settings, start, end)
        finally:
            con.close()
        days = sorted({c.local_date for c in result.changes})
        return {"message": f"{len(days)} day(s) changed. Review them, then approve." if days
                           else "Nothing changed; the estimates were already current."}

    def sync_github(self, force: bool = False) -> Dict[str, Any]:
        """One GitHub pass, for the week page's timer through callAsync. Never loud.

        GitHub being off, having no token, or being out of reach is a state to show beside the list,
        not an error: it must never get in the way of estimating or approving. poll_minutes comes back
        so the page's timer follows the setting. Within poll_minutes of the last pass, the last reply
        comes back instead (unless force), so opening the week page doesn't cost a pass each time.
        """
        settings = self._settings()
        poll = int(settings.values.get("poll_minutes", 5) or 5)
        last = self._github
        if not force and last is not None and time.monotonic() - last[0] < poll * 60 - 5:
            return dict(last[1], poll_minutes=poll)
        reply = self._sync_github(settings, poll)
        self._github = (time.monotonic(), reply)
        return reply

    def _sync_github(self, settings: settings_mod.Settings, poll: int) -> Dict[str, Any]:
        at = dt.datetime.now().strftime("%H:%M")
        if not settings.github_host():
            return {"state": "off", "at": at, "poll_minutes": poll, "problems": [],
                    "message": "GitHub is off; set github_api to see pull requests."}
        con = self._open(readonly=False)
        try:
            result = desk.sync_github(con, settings)
        except (github.GitHubError, muninn.MuninnError, sqlite3.Error) as exc:
            return {"state": "failed", "at": at, "poll_minutes": poll, "problems": [],
                    "message": f"Couldn't check GitHub at {at}: {exc}"}
        finally:
            con.close()
        if result is None:
            return {"state": "no token", "at": at, "poll_minutes": poll, "problems": [],
                    "message": "No GitHub token yet: baldur.cmd github token"}
        return {"state": "fresh", "at": at, "poll_minutes": poll, "problems": list(result.problems),
                "message": f"Checked GitHub at {at}."}

    # ---- reviews waiting --------------------------------------------------
    def reviews(self) -> List[Dict[str, Any]]:
        with self._read() as con:
            return [{"kind": i.kind, "label": i.label, "title": i.title, "who": i.who, "state": i.state,
                     "url": i.url, "draft": i.draft} for i in desk.review_items(con)]

    # ---- settings, edited in the window ------------------------------------
    # By key into baldur.json. settings.validate() has the last word, so a bad value is refused with
    # its message and nothing is written. Left out on purpose: github_api and the hook and schedule
    # switches, which do their work through commands that have to run anyway.
    SETTINGS: List[Dict[str, Any]] = [
        {"title": "Where your work is", "fields": [
            {"key": "repo_roots", "label": "Folders holding repositories", "kind": "list",
             "help": "One per line. Baldur reads git in each, and never writes to it."},
            {"key": "max_depth", "label": "How deep to look", "kind": "number", "min": 1, "max": 8,
             "unit": "folders"},
            {"key": "project_keys", "label": "Jira projects that count", "kind": "list",
             "help": "One per line, like PROJ. A key outside these is ignored."},
            {"key": "history_days", "label": "How far back to read", "kind": "number",
             "min": 1, "max": 3650, "unit": "days"},
        ]},
        {"title": "How a day is estimated",
         "help": "Every one of these biases the figure down, never up. Change them with `calibrate` if you "
                 "have noted real hours.", "fields": [
            {"key": "idle_gap_minutes", "label": "A gap this long splits a session", "kind": "number",
             "min": 15, "max": 480, "unit": "minutes"},
            {"key": "lead_in_minutes", "label": "Thinking time before the first commit", "kind": "number",
             "min": 0, "max": 180, "unit": "minutes"},
            {"key": "min_session_minutes", "label": "Shortest session", "kind": "number",
             "min": 1, "max": 120, "unit": "minutes"},
            {"key": "max_session_minutes", "label": "Longest session", "kind": "number",
             "min": 30, "max": 720, "unit": "minutes"},
            {"key": "max_daily_dev_minutes", "label": "Most development time in a day", "kind": "number",
             "min": 60, "max": 1440, "unit": "minutes"},
            {"key": "round_to_minutes", "label": "Round each figure down to", "kind": "number",
             "min": 1, "max": 60, "unit": "minutes"},
            {"key": "policy", "label": "Coding during a meeting", "kind": "choice",
             "options": [{"value": "ambient", "label": "Counts at a fraction (ambient)"},
                         {"value": "overlap", "label": "Counts, capped by the day (overlap)"},
                         {"value": "independent", "label": "Counts in full, and is flagged"}]},
            {"key": "ambient_weight", "label": "That fraction", "kind": "number",
             "min": 0, "max": 1, "step": 0.1, "help": "Used by the ambient policy. 0.5 is half."},
            {"key": "concurrent_fraction", "label": "Two tickets at once count as", "kind": "number",
             "min": 0, "max": 1, "step": 0.1},
        ]},
        {"title": "Your working hours",
         "help": "Used to keep an evening commit from stretching a day, and to stay quiet outside them.",
         "fields": [
            {"key": "tour_of_duty.start", "label": "Starts", "kind": "time"},
            {"key": "tour_of_duty.end", "label": "Ends", "kind": "time"},
            {"key": "tour_of_duty.unpaid_minutes", "label": "Unpaid break", "kind": "number",
             "min": 0, "max": 180, "unit": "minutes"},
            {"key": "quiet_outside_tour", "label": "No alerts outside them", "kind": "bool"},
        ]},
        {"title": "Pull requests and alerts", "fields": [
            {"key": "alerts", "label": "Tell me when a review is asked of me", "kind": "bool"},
            {"key": "poll_minutes", "label": "Check GitHub every", "kind": "number",
             "min": 1, "max": 120, "unit": "minutes"},
        ]},
        {"title": "AI-assisted figures",
         "help": "An AI may only lower a day or move time between tickets, and must cite evidence.", "fields": [
            {"key": "review_mode", "label": "What an AI review may see", "kind": "choice",
             "options": [{"value": "off", "label": "Off"},
                         {"value": "metadata", "label": "Metadata: subjects, times, line counts"}],
             "help": "There is a third mode in the file, content, and Baldur refuses to use it: code never "
                     "leaves this machine. It isn't offered here."},
        ]},
    ]

    def settings_schema(self) -> List[Dict[str, Any]]:
        return [dict(group) for group in self.SETTINGS]

    def settings_values(self) -> Dict[str, Any]:
        loaded = settings_mod.load()
        values = loaded.values if not loaded.broken else dict(settings_mod.DEFAULTS)
        out: Dict[str, Any] = {}
        for group in self.SETTINGS:
            for field in group["fields"]:
                here: Any = values
                for part in field["key"].split("."):
                    here = here.get(part) if isinstance(here, dict) else None
                out[field["key"]] = "" if here is None else here
        return out

    def set_settings(self, changes: Dict[str, Any]) -> Dict[str, Any]:
        """Save some settings. Baldur's validator decides; nothing is written if it refuses."""
        fields = {f["key"]: f for g in self.SETTINGS for f in g["fields"]}
        unknown = sorted(set(changes) - set(fields))
        if unknown:
            return {"error": f"{', '.join(unknown)} isn't a setting the window edits."}
        ready: Dict[str, Any] = {}
        for key, value in changes.items():
            kind, step = fields[key]["kind"], fields[key].get("step")
            if kind == "number":
                try:
                    value = float(value) if step else int(value)
                except (TypeError, ValueError):
                    return {"error": f"{fields[key]['label']} must be a number."}
            elif kind == "bool":
                value = bool(value)
            elif kind == "list":
                value = [str(v).strip() for v in (value or []) if str(v).strip()]
            elif isinstance(value, str):
                value = value.strip()
            parts = key.split(".")
            if len(parts) == 1:
                ready[key] = value
            else:                               # tour_of_duty.start and friends: merge, keep the rest
                nested = dict(self.settings_values_group(parts[0]))
                nested[parts[1]] = value
                ready[parts[0]] = nested
        try:
            settings_mod.update(ready)
        except settings_mod.SettingsError as exc:
            return {"error": str(exc)}
        return {"message": "Saved."}

    def settings_values_group(self, name: str) -> Dict[str, Any]:
        """One nested settings section as it stands, so changing one key keeps its neighbours."""
        loaded = settings_mod.load()
        values = loaded.values if not loaded.broken else dict(settings_mod.DEFAULTS)
        current = values.get(name)
        if isinstance(current, dict):
            return dict(current)
        fallback = settings_mod.DEFAULTS.get(name)
        return dict(fallback) if isinstance(fallback, dict) else {}

    # ---- what the shell asks for ------------------------------------------
    def dashboard(self) -> List[Dict[str, Any]]:
        cards: List[Dict[str, Any]] = []
        try:
            week = self.week()
        except (ValueError, sqlite3.Error) as exc:
            return [{"label": "Baldur", "value": "!", "tone": "error", "detail": str(exc), "view": "week"}]
        waiting = sum(1 for d in week["days"] if "to review" in d["review"])
        cards.append({"label": "Days to review this week", "value": str(waiting), "view": "week",
                      "tone": "warning" if waiting else "", "detail": f"{week['total_text']} estimated"})
        try:
            asked = [r for r in self.reviews() if r["kind"] == "requested"]
        except (ValueError, sqlite3.Error):
            asked = []
        if asked:
            cards.append({"label": "Reviews asked of you", "value": str(len(asked)), "view": "week",
                          "detail": ", ".join(r["label"] for r in asked[:3])})
        return cards

    def settings_files(self) -> List[Dict[str, str]]:
        return [{"label": "Settings (emails, repositories, policy)", "path": str(settings_mod.settings_path())}]
