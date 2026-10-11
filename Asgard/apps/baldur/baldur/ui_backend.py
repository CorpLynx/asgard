"""What Baldur's pages in Asgard's window show, with no Qt: the week, one day, and the reviews waiting.

Everything here goes through desk.py, the same plain-Python layer Baldur's tkinter window uses, so the
two windows can't drift apart on what a figure means. Rules it keeps:

- **Read and decide, never estimate.** The numbers come from `desk.load_week` and `desk.load_day`;
  this module never computes one.
- **Approving is the person's.** `approve_day` and `reject_day` are offered because a window is where
  consent happens, and they go straight to `muninn.baldur`, which refuses to edit a decision already
  made. Nothing here posts to Jira: Odin does that, from what was approved.
- **Nothing slow on the window's thread.** Collecting reads git, so the page calls it through the
  shell's `callAsync`, and estimating is offered as a button, not done on open.
"""
from __future__ import annotations

import datetime as dt
import sqlite3
from typing import Any, Dict, List, Optional

from asgard import muninn

from . import collect as collector
from . import desk, gitread, settings as settings_mod
from .cli import SCHEMA
from .estimate import fmt

# Baldur's own expected failures. Its CLI prints these as a line and exits; the window shows them on
# the page. They aren't all ValueErrors, which is what the shell treats as "expected", so they are
# raised as one (see _expected).
EXPECTED = (collector.CollectError, desk.DeskError, gitread.GitError, muninn.MuninnError)


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


class Backend:
    """Called from QML through the shell's bridge. Returns plain data, or raises ValueError for the page."""

    def __init__(self) -> None:
        self._con: Optional[sqlite3.Connection] = None
        # Which day the day page shows. The shell keeps one backend per app, so this survives moving
        # between pages; QML pages are built again each time and can't hold it themselves.
        self._chosen: Optional[str] = None

    # ---- plumbing ---------------------------------------------------------
    def _open(self, readonly: bool = True) -> sqlite3.Connection:
        """Muninn, kept open for reads. A write gets its own connection, so the read one stays read-only.

        Muninn's errors already say what to do ("Open Asgard once, then start baldur again"), but they
        aren't ValueErrors, so the shell would log them as unexpected and show a generic line instead.
        Raised as ValueError, the page shows the real message.
        """
        if readonly:
            if self._con is None:
                self._con = _expected(muninn.open_app, "baldur", supported=SCHEMA, readonly=True)
            return self._con
        return _expected(muninn.open_app, "baldur", supported=SCHEMA)

    def _settings(self) -> settings_mod.Settings:
        settings = settings_mod.load()
        if settings.broken:
            raise ValueError(f"{settings_mod.settings_path()} has a mistake in it ({settings.warnings[0]}). "
                             "Fix it, or delete it to start again from the defaults.")
        return settings

    # ---- the week ---------------------------------------------------------
    def week(self, monday: Optional[str] = None) -> Dict[str, Any]:
        """One week of days. monday defaults to the week of today; any date in a week will do."""
        start = desk.week_of(_date(monday))
        settings = self._settings()
        con = self._open()
        rows = _expected(desk.load_week, con, settings, start)
        return {
            "monday": start.isoformat(),
            "previous": (start - dt.timedelta(days=7)).isoformat(),
            "next": (start + dt.timedelta(days=7)).isoformat(),
            "is_this_week": start == desk.week_of(dt.date.today()),
            "policy": settings.policy,        # how coding during meetings counts
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
        view = _expected(desk.load_day, self._open(), settings, chosen)
        ai = view.ai
        return {
            "date": view.day.isoformat(),
            "name": view.day.strftime("%A %d %B %Y"),
            "report": view.report,
            "untracked": view.untracked,
            "untracked_text": fmt(view.untracked),
            "flags": list(view.flags),
            "timesheet": desk.timesheet(view),
            "approvable": any(t.approvable and t.open_id is not None for t in view.tickets),
            "has_approved": any(t.approved_id is not None for t in view.tickets),
            "tickets": [{"key": t.key, "estimate": t.estimate, "estimate_text": fmt(t.estimate),
                         "figure": t.figure, "figure_text": fmt(t.figure),
                         "approved": t.approved, "approved_text": fmt(t.approved) if t.approved is not None else "",
                         "held": t.held, "held_text": fmt(t.held) if t.held is not None else "",
                         "review": t.review, "problem": t.problem or "",
                         "open": t.open_id is not None, "approvable": t.approvable} for t in view.tickets],
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
        con = self._open(readonly=False)
        try:
            ids = _expected(desk.approve_day, con, settings, _date(date), take_ai=(take_ai or None))
        except ValueError as exc:
            # "Nothing to approve on DATE" is an answer, not a failure: the page says so and stays put.
            if "nothing to approve" in str(exc).lower():
                return {"message": "Nothing on that day was waiting to be approved.", "count": 0}
            raise
        finally:
            con.close()
        return {"message": f"Approved {len(ids)} figure(s). Odin posts them on its next run.", "count": len(ids)}

    def reject(self, date: str) -> Dict[str, Any]:
        settings = self._settings()
        con = self._open(readonly=False)
        try:
            ids = _expected(desk.reject_day, con, settings, _date(date))
        except ValueError as exc:
            if "nothing to reject" in str(exc).lower():
                return {"message": "Nothing on that day to reject.", "count": 0}
            raise
        finally:
            con.close()
        return {"message": f"Rejected {len(ids)} figure(s).", "count": len(ids)}

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

    # ---- reviews waiting --------------------------------------------------
    def reviews(self) -> List[Dict[str, Any]]:
        return [{"kind": i.kind, "label": i.label, "title": i.title, "who": i.who, "state": i.state,
                 "url": i.url, "draft": i.draft} for i in desk.review_items(self._open())]

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
