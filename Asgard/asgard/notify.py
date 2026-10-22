"""Desktop notifications for Asgard's apps: one place to say "tell the person", and one place that decides.

An app declares the events it can raise in apps/<id>/notify.json, the way it declares its window's
pages in ui/manifest.json, and raises one where it happens:

    {
      "app": "baldur",
      "name": "Baldur",
      "events": [
        {"id": "days_waiting", "label": "Days are waiting for your review", "default": true,
         "level": "info", "repeat_minutes": 720},
        {"id": "refresh_failed", "label": "A scheduled refresh had a problem", "level": "error"}
      ]
    }

    from asgard import notify
    notify.send("baldur", "days_waiting", "3 days are waiting for your review.", key="3")

send() looks the event up, asks the person's choices (settings\\notifications.json), and only then
shows anything. It returns an Outcome saying what happened and why, and never raises: a
notification is never worth failing a run over.

    {
      "enabled": true,                              the master switch
      "quiet_hours": {"start": "18:00", "end": "08:00"},   nothing shown in between; "" turns it off
      "apps": {"odin": false},                      mute one app
      "events": {"baldur.days_waiting": false}      mute (or, with true, force on) one event
    }

Rules it keeps:
- **Nothing sensitive in the text.** Callers send counts and plain words: never a token, a diff, a
  commit body or a Jira summary (docs/security-and-data.md). Text is cut to what a balloon can hold.
- **Per user, no admin, nothing new on disk.** Windows shows a balloon from the notification area
  through ctypes (user32 and shell32 are already there). No PowerShell, so Constrained Language
  Mode doesn't matter; no WinRT, no module from the Gallery, no helper program. Odin's reasons for
  not using toasts are in odin/config.py.
- **A quiet event stays quiet.** A muted event, quiet hours and a repeat of the same news are all
  dropped, not queued: a notification that arrives hours late is worse than none.
- **A repeat is the same event with the same key inside its repeat_minutes.** Pass a key that
  changes when the news does (the count of days waiting), so a 5-minute poll tells you once.
- **Unknown events are refused**, so a typo can't become a notification nobody can turn off.
- **A damaged preferences or state file never stops a notification and is never overwritten.**
  Defaults apply (everything on that the manifest defaults on) until it is fixed.
- ASGARD_NOTIFY=off shows nothing and ASGARD_NOTIFY=log only writes the log; tests use set_backend().

Standard library only: every app imports this, and so can Odin's daily run.
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from asgard import jsonfile, paths

log = logging.getLogger("asgard.notify")

CODE_ROOT = Path(__file__).resolve().parent.parent      # the folder holding asgard/ and apps/
LEVELS = ("info", "warning", "error")
MAX_TITLE = 60
MAX_TEXT = 200                  # a Windows balloon holds 255 characters; leave room
HOLD_SECONDS = 4.0              # how long the Windows notification-area icon stays so the balloon shows
STATE_KEEP = 500                # remembered repeats; the oldest go first
_ID = re.compile(r"^[a-z][a-z0-9]{0,31}$")
_EVENT_ID = re.compile(r"^[a-z][a-z0-9_]{0,47}$")
_CLOCK = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


class NotifyError(Exception):
    """A backend couldn't show a notification. The message says why; send() turns it into an Outcome."""


# --------------------------------------------------------------------------
# Manifests: which events an app can raise
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class Event:
    app: str
    id: str
    label: str
    default: bool = True
    level: str = "info"
    repeat_minutes: int = 0         # 0: every send is shown

    @property
    def key(self) -> str:
        return f"{self.app}.{self.id}"


@dataclass
class AppEvents:
    id: str
    name: str
    events: Dict[str, Event] = field(default_factory=dict)


def discover(root: Optional[Path] = None) -> Tuple[Dict[str, AppEvents], List[str]]:
    """Apps with an apps/<id>/notify.json: ({app id: AppEvents}, warnings). JSON is read; nothing is imported.

    A broken manifest skips that app with a warning, so one app's typo can't silence the others.
    """
    root = Path(root) if root else CODE_ROOT
    found: Dict[str, AppEvents] = {}
    warnings: List[str] = []
    for manifest in sorted((root / "apps").glob("*/notify.json")):
        try:
            app = parse_manifest(manifest)
        except ValueError as exc:
            warnings.append(str(exc))
            continue
        found[app.id] = app
    return found, warnings


def parse_manifest(path: Path) -> AppEvents:
    where = f"{path.parent.name}/{path.name}"
    try:
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"{where} can't be read ({exc}); that app's notifications are off.") from None
    if not isinstance(raw, dict):
        raise ValueError(f"{where} must hold a JSON object; that app's notifications are off.")
    app_id = raw.get("app")
    if not isinstance(app_id, str) or not _ID.match(app_id) or app_id != path.parent.name:
        raise ValueError(f'{where}: "app" must be the folder\'s lower-case id, "{path.parent.name}".')
    listed = raw.get("events")
    if not isinstance(listed, list) or not listed:
        raise ValueError(f'{where}: "events" must list at least one event.')
    events: Dict[str, Event] = {}
    for i, e in enumerate(listed, 1):
        if not isinstance(e, dict):
            raise ValueError(f"{where}: event {i} must be an object.")
        eid, label = e.get("id"), e.get("label")
        if not isinstance(eid, str) or not _EVENT_ID.match(eid):
            raise ValueError(f'{where}: event {i} needs a lower-case "id" (letters, digits, underscores).')
        if eid in events:
            raise ValueError(f'{where}: two events have the id "{eid}".')
        if not isinstance(label, str) or not label.strip():
            raise ValueError(f'{where}: event "{eid}" needs a "label" saying what it tells the person.')
        level = e.get("level", "info")
        if level not in LEVELS:
            raise ValueError(f'{where}: event "{eid}" has level "{level}"; use {", ".join(LEVELS)}.')
        repeat = e.get("repeat_minutes", 0)
        if isinstance(repeat, bool) or not isinstance(repeat, int) or repeat < 0:
            raise ValueError(f'{where}: event "{eid}": "repeat_minutes" must be 0 or more.')
        default = e.get("default", True)
        if not isinstance(default, bool):
            raise ValueError(f'{where}: event "{eid}": "default" must be true or false.')
        events[eid] = Event(app_id, eid, label.strip(), default, level, repeat)
    return AppEvents(id=app_id, name=str(raw.get("name") or app_id.title()), events=events)


# --------------------------------------------------------------------------
# The person's choices
# --------------------------------------------------------------------------

def prefs_path() -> Path:
    return paths.data_dir() / "settings" / "notifications.json"


def state_path() -> Path:
    return paths.data_dir() / "settings" / "notify-state.json"


@dataclass
class Prefs:
    enabled: bool = True
    quiet_start: str = ""
    quiet_end: str = ""
    apps: Dict[str, bool] = field(default_factory=dict)
    events: Dict[str, bool] = field(default_factory=dict)
    warnings: List[str] = field(default_factory=list)

    def allows(self, event: Event) -> Optional[str]:
        """None when the person wants this event, else the reason it's off."""
        if not self.enabled:
            return "notifications are switched off"
        if self.apps.get(event.app) is False:
            return f"{event.app} is muted"
        wanted = self.events.get(event.key)
        if wanted is None:
            wanted = event.default
        return None if wanted else f"{event.key} is muted"

    def quiet_at(self, minutes: int) -> bool:
        """Whether a time (minutes after local midnight) is inside quiet hours, which may cross midnight."""
        if not (self.quiet_start and self.quiet_end):
            return False
        start, end = _minutes(self.quiet_start), _minutes(self.quiet_end)
        if start == end:
            return False
        return start <= minutes < end if start < end else (minutes >= start or minutes < end)


def _minutes(clock: str) -> int:
    return int(clock[:2]) * 60 + int(clock[3:])


def load_prefs() -> Prefs:
    """settings\\notifications.json, or defaults. Problems become prefs.warnings; the file is never rewritten here."""
    prefs = Prefs()
    path = prefs_path()
    if not path.exists():
        return prefs
    try:
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
        if not isinstance(raw, dict):
            raise ValueError("it should hold one JSON object")
    except (OSError, ValueError) as exc:
        prefs.warnings.append(f"{path} can't be used ({exc}); notifications use their defaults until it is fixed.")
        return prefs
    if isinstance(raw.get("enabled"), bool):
        prefs.enabled = raw["enabled"]
    quiet = raw.get("quiet_hours")
    if isinstance(quiet, dict):
        start, end = quiet.get("start", ""), quiet.get("end", "")
        if start == "" and end == "":
            pass
        elif all(isinstance(t, str) and _CLOCK.match(t) for t in (start, end)):
            prefs.quiet_start, prefs.quiet_end = start, end
        else:
            prefs.warnings.append('quiet_hours needs 24-hour "start" and "end" like "18:00"; ignored.')
    for name, target in (("apps", prefs.apps), ("events", prefs.events)):
        value = raw.get(name)
        if isinstance(value, dict):
            target.update({str(k): v for k, v in value.items() if isinstance(v, bool)})
    return prefs


def save_prefs(prefs: Prefs) -> Path:
    """Write the person's choices. Refuses to replace a file that won't parse (it may hold hand edits)."""
    path = prefs_path()
    if path.exists():
        try:
            json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            raise NotifyError(f"{path} isn't valid JSON, so it wasn't overwritten. Fix it or move it aside.") from None
    body: Dict[str, Any] = {"enabled": prefs.enabled,
                            "quiet_hours": {"start": prefs.quiet_start, "end": prefs.quiet_end},
                            "apps": dict(sorted(prefs.apps.items())),
                            "events": dict(sorted(prefs.events.items()))}
    return jsonfile.write_json(path, body)


# --------------------------------------------------------------------------
# Repeats
# --------------------------------------------------------------------------

def _read_state() -> Dict[str, float]:
    try:
        raw = json.loads(state_path().read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return {}
    return {k: float(v) for k, v in raw.items() if isinstance(k, str) and isinstance(v, (int, float))} \
        if isinstance(raw, dict) else {}


def _repeat_of(event: Event, key: Optional[str], now: float, state: Dict[str, float]) -> bool:
    if event.repeat_minutes <= 0:
        return False
    last = state.get(f"{event.key}|{key or ''}")
    return last is not None and 0 <= now - last < event.repeat_minutes * 60


def _remember(event: Event, key: Optional[str], now: float, state: Dict[str, float]) -> None:
    if event.repeat_minutes <= 0:
        return
    state[f"{event.key}|{key or ''}"] = now
    if len(state) > STATE_KEEP:
        for old in sorted(state, key=state.get)[:len(state) - STATE_KEEP]:
            del state[old]
    try:
        jsonfile.write_json(state_path(), state)
    except OSError as exc:
        log.debug("couldn't remember a notification: %s", exc)


# --------------------------------------------------------------------------
# Backends: how it reaches the screen
# --------------------------------------------------------------------------

Backend = Callable[[str, str, str], None]       # (title, text, level); raises NotifyError
_backend: Optional[Backend] = None
_discovered: Optional[Dict[str, AppEvents]] = None


def set_backend(backend: Optional[Backend]) -> None:
    """Replace how notifications are shown (None goes back to the computer's own). Tests use this."""
    global _backend
    _backend = backend


def _log_only(title: str, text: str, level: str) -> None:
    log.info("notification [%s] %s: %s", level, title, text)


def _windows_balloon(title: str, text: str, level: str) -> None:
    """A balloon from the notification area, which Windows 10 and 11 show as a toast and keep in the Action Center.

    Shell_NotifyIconW needs an icon and a window to own it: a message-only window of the built-in STATIC
    class. The icon is added, told to show the balloon, kept for HOLD_SECONDS (a balloon is dismissed with
    its icon) and removed. Untested on a real desktop as of 0.4.0: see HANDOFF.md.
    """
    import ctypes
    from ctypes import wintypes

    class NOTIFYICONDATAW(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("hWnd", wintypes.HWND), ("uID", wintypes.UINT),
                    ("uFlags", wintypes.UINT), ("uCallbackMessage", wintypes.UINT), ("hIcon", wintypes.HICON),
                    ("szTip", wintypes.WCHAR * 128), ("dwState", wintypes.DWORD),
                    ("dwStateMask", wintypes.DWORD), ("szInfo", wintypes.WCHAR * 256),
                    ("uTimeout", wintypes.UINT), ("szInfoTitle", wintypes.WCHAR * 64),
                    ("dwInfoFlags", wintypes.DWORD), ("guidItem", ctypes.c_byte * 16),
                    ("hBalloonIcon", wintypes.HICON)]

    user32, shell32 = ctypes.windll.user32, ctypes.windll.shell32      # type: ignore[attr-defined]
    user32.CreateWindowExW.restype = wintypes.HWND
    user32.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
                                       ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.HWND,
                                       wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID]
    user32.LoadIconW.restype = wintypes.HICON
    user32.LoadIconW.argtypes = [wintypes.HINSTANCE, wintypes.LPCWSTR]
    shell32.Shell_NotifyIconW.argtypes = [wintypes.DWORD, ctypes.POINTER(NOTIFYICONDATAW)]
    shell32.Shell_NotifyIconW.restype = wintypes.BOOL
    NIM_ADD, NIM_MODIFY, NIM_DELETE = 0, 1, 2
    NIF_ICON, NIF_TIP, NIF_INFO = 0x2, 0x4, 0x10
    flags = {"info": 0x1, "warning": 0x2, "error": 0x3}[level]            # NIIF_INFO / WARNING / ERROR

    hwnd = user32.CreateWindowExW(0, "STATIC", "AsgardNotify", 0, 0, 0, 0, 0, wintypes.HWND(-3), None, None, None)
    if not hwnd:
        raise NotifyError("Windows wouldn't create the window that owns a notification.")
    data = NOTIFYICONDATAW()
    data.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
    data.hWnd = hwnd
    data.uID = 1
    data.uFlags = NIF_ICON | NIF_TIP
    data.hIcon = user32.LoadIconW(None, ctypes.cast(ctypes.c_void_p(32512), wintypes.LPCWSTR))   # IDI_APPLICATION
    data.szTip = "Asgard"
    try:
        if not shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(data)):
            raise NotifyError("Windows wouldn't add the notification-area icon (is the taskbar running?).")
        data.uFlags = NIF_INFO
        data.szInfoTitle = title
        data.szInfo = text
        data.dwInfoFlags = flags
        if not shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(data)):
            raise NotifyError("Windows wouldn't show the notification.")
        time.sleep(HOLD_SECONDS)
    finally:
        shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(data))
        user32.DestroyWindow(hwnd)


def _run_tool(argv: List[str]) -> None:
    try:
        done = subprocess.run(argv, capture_output=True, timeout=10)
    except (OSError, subprocess.SubprocessError) as exc:
        raise NotifyError(f"{argv[0]} didn't run ({exc}).") from None
    if done.returncode != 0:
        raise NotifyError(f"{argv[0]} stopped with code {done.returncode}.")


def _applescript(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _macos(title: str, text: str, level: str) -> None:
    _run_tool(["osascript", "-e", f"display notification {_applescript(text)} with title {_applescript(title)}"])


def _freedesktop(title: str, text: str, level: str) -> None:
    urgency = {"info": "normal", "warning": "normal", "error": "critical"}[level]
    _run_tool(["notify-send", "--app-name=Asgard", f"--urgency={urgency}", "--", title, text])


def default_backend() -> Optional[Backend]:
    """The computer's own way, or None when it has none (Asgard targets Windows; the others serve development)."""
    mode = os.environ.get("ASGARD_NOTIFY", "").strip().lower()
    if mode == "off":
        return None
    if mode == "log":
        return _log_only
    if os.name == "nt":
        return _windows_balloon
    if sys.platform == "darwin" and shutil.which("osascript"):
        return _macos
    if shutil.which("notify-send"):
        return _freedesktop
    return None


# --------------------------------------------------------------------------
# Sending
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class Outcome:
    shown: bool
    reason: str             # "shown", or why not, in plain words

    def __bool__(self) -> bool:
        return self.shown


def events(root: Optional[Path] = None) -> Dict[str, AppEvents]:
    """Every app's declared events. Read once per process for the real tree; a different root is read fresh."""
    global _discovered
    if root is not None:
        found, warnings = discover(root)
        for w in warnings:
            log.warning(w)
        return found
    if _discovered is None:
        _discovered, warnings = discover()
        for w in warnings:
            log.warning(w)
    return _discovered


def _clip(value: str, limit: int) -> str:
    one = " ".join(str(value).split())
    return one if len(one) <= limit else one[:limit - 1].rstrip() + "…"


def send(app: str, event: str, text: str, *, key: Optional[str] = None, title: Optional[str] = None,
         wait: bool = True, now: Optional[float] = None, root: Optional[Path] = None) -> Outcome:
    """Raise an event the app declared. Returns what happened; never raises.

    text is the plain-words message (counts, not content). key names the news for repeat suppression:
    the same event and key inside the event's repeat_minutes is shown once. wait=False shows it on a
    background thread, for a caller (the window) that mustn't stop for the seconds a balloon is held;
    don't use it from a program that exits right after, or the notification dies with it.
    """
    try:
        return _send(app, event, text, key, title, wait, now, root)
    except Exception as exc:    # noqa: BLE001 - nothing about a notification may break the caller
        log.warning("notification %s.%s failed: %s", app, event, exc)
        return Outcome(False, f"failed ({exc})")


def _send(app: str, event: str, text: str, key: Optional[str], title: Optional[str], wait: bool,
          now: Optional[float], root: Optional[Path]) -> Outcome:
    declared = events(root).get(app)
    ev = declared.events.get(event) if declared else None
    if ev is None:
        log.warning("notification %s.%s isn't declared in apps/%s/notify.json", app, event, app)
        return Outcome(False, f"{app}.{event} isn't declared in apps/{app}/notify.json")
    prefs = load_prefs()
    for w in prefs.warnings:
        log.warning(w)
    off = prefs.allows(ev)
    if off:
        return Outcome(False, off)
    stamp = time.time() if now is None else now
    local = time.localtime(stamp)
    if prefs.quiet_at(local.tm_hour * 60 + local.tm_min):
        return Outcome(False, "quiet hours")
    state = _read_state()
    if _repeat_of(ev, key, stamp, state):
        return Outcome(False, "already shown recently")
    backend = _backend or default_backend()
    if backend is None:
        return Outcome(False, "this computer has no way to show a notification")
    heading = _clip(title or (declared.name if declared else app.title()), MAX_TITLE)
    body = _clip(text, MAX_TEXT)
    if not body:
        return Outcome(False, "no text to show")

    def show() -> Optional[str]:
        try:
            backend(heading, body, ev.level)
        except NotifyError as exc:
            log.warning("notification %s couldn't be shown: %s", ev.key, exc)
            return str(exc)
        return None

    if not wait:        # remembered now: the thread's answer comes after this returns
        _remember(ev, key, stamp, state)
        threading.Thread(target=show, name="asgard-notify", daemon=True).start()
        return Outcome(True, "shown")
    problem = show()
    if problem:         # not remembered, so the next run tries again
        return Outcome(False, f"couldn't be shown ({problem})")
    _remember(ev, key, stamp, state)
    return Outcome(True, "shown")
