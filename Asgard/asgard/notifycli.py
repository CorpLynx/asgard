"""asgard notify: see what Asgard can tell you about, choose what it does, and try one.

    asgard notify list                      every event, and whether it is on
    asgard notify on|off [app[.event]]      switch everything, one app or one event
    asgard notify quiet 18:00 08:00         no notifications between those times
    asgard notify quiet off
    asgard notify test [app.event]          show one now (the first event if none is named)

Choices are kept in settings\\notifications.json (asgard/notify.py says what is in it). Output is ASCII.
Standard library only.
"""
from __future__ import annotations

import argparse
import re
import sys
from typing import Optional, Sequence

from asgard import notify

_CLOCK = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


class CliError(Exception):
    pass


def _find(target: str):
    """(app, event or None) for 'baldur' or 'baldur.days_waiting', or CliError saying what exists."""
    declared = notify.events()
    app, _, event = target.partition(".")
    if app not in declared:
        raise CliError(f'No app "{app}" declares notifications. Try: {", ".join(sorted(declared)) or "(none)"}.')
    if event and event not in declared[app].events:
        raise CliError(f'{app} has no event "{event}". Try: {", ".join(sorted(declared[app].events))}.')
    return declared[app], declared[app].events[event] if event else None


def cmd_list(prefs: notify.Prefs, args: argparse.Namespace) -> int:
    print("Notifications are " + ("on" if prefs.enabled else "OFF (asgard notify on)") + ".")
    if prefs.quiet_start:
        print(f"Quiet hours: {prefs.quiet_start} to {prefs.quiet_end}.")
    for app in notify.events().values():
        for ev in app.events.values():
            why = prefs.allows(ev)
            print(f"  {'off' if why else 'on ':3}  {ev.key:28} {ev.label}")
    return 0


def cmd_switch(prefs: notify.Prefs, args: argparse.Namespace) -> int:
    on = args.command == "on"
    if not args.target:
        prefs.enabled = on
        what = "Notifications"
    else:
        app, ev = _find(args.target)
        if ev is None:
            prefs.apps[app.id] = on
            what = app.name
        else:
            prefs.events[ev.key] = on
            what = ev.key
    notify.save_prefs(prefs)
    print(f"{what} {'on' if on else 'off'}.")
    return 0


def cmd_quiet(prefs: notify.Prefs, args: argparse.Namespace) -> int:
    times = args.times
    if times == ["off"]:
        prefs.quiet_start = prefs.quiet_end = ""
    elif len(times) == 2 and all(_CLOCK.match(t) for t in times):
        prefs.quiet_start, prefs.quiet_end = times
    else:
        raise CliError("Give two 24-hour times like: asgard notify quiet 18:00 08:00 (or: quiet off).")
    notify.save_prefs(prefs)
    print("Quiet hours off." if not prefs.quiet_start else f"Quiet from {prefs.quiet_start} to {prefs.quiet_end}.")
    return 0


def cmd_test(prefs: notify.Prefs, args: argparse.Namespace) -> int:
    """Send past the person's choices, because the question is whether this computer can show one at all."""
    backend = notify._backend or notify.default_backend()
    if backend is None:
        raise CliError("This computer has no way to show a notification (ASGARD_NOTIFY=off, or no notify-send).")
    app = _find(args.target)[0] if args.target else next(iter(notify.events().values()))
    title = app.name
    try:
        backend(title, "This is a test notification from Asgard.", "info")
    except notify.NotifyError as exc:
        raise CliError(f"It couldn't be shown: {exc}") from None
    print("Sent. If you saw nothing, check Focus assist / Do not disturb and Windows' notification settings.")
    return 0


def build_parser(prog: str) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog=prog, description="Choose what Asgard tells you about.")
    sub = parser.add_subparsers(dest="command", metavar="command", required=True)
    sub.add_parser("list", help="every event, and whether it is on").set_defaults(run=cmd_list)
    for name in ("on", "off"):
        p = sub.add_parser(name, help=f"switch notifications {name}: all, one app or one event")
        p.add_argument("target", nargs="?", help="an app (baldur) or an event (baldur.days_waiting)")
        p.set_defaults(run=cmd_switch)
    p = sub.add_parser("quiet", help="no notifications between two times")
    p.add_argument("times", nargs="+", metavar="START END|off")
    p.set_defaults(run=cmd_quiet)
    p = sub.add_parser("test", help="show one now")
    p.add_argument("target", nargs="?", help="an app or app.event")
    p.set_defaults(run=cmd_test)
    return parser


def main(argv: Optional[Sequence[str]] = None, prog: str = "asgard notify") -> int:
    args = build_parser(prog).parse_args(list(sys.argv[1:] if argv is None else argv))
    prefs = notify.load_prefs()
    for warning in prefs.warnings:
        print(f"{prog}: {warning}", file=sys.stderr)
    try:
        return args.run(prefs, args)
    except (CliError, notify.NotifyError) as exc:
        print(f"{prog}: {exc}", file=sys.stderr)
        return 1
