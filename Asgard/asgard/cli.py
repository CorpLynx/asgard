"""One command for all of Asgard:  asgard <app> <command> [args],  or  asgard <command> [args].

    asgard --help                      the apps and Asgard's own commands
    asgard <app> --help                that app's commands
    asgard baldur collect              any app command, exactly as baldur.cmd collect runs it
    asgard odin                        Odin's daily run, exactly as odin.cmd runs it
    asgard muninn status               Asgard's own commands: muninn, install, uninstall, version, selftest, ui

Setup puts a small asgard.cmd on your Path (%LOCALAPPDATA%\\Asgard\\bin, per user, no admin rights;
install.py, shim.py). It runs this file with the Python Asgard was installed with, or the packaged
build's asgard-cli.exe, which hands every command line that isn't a script to main() here.

The rules it keeps:
- It reads only the first word. Everything after it goes to the app's own main(argv), unchanged, so
  each app keeps its own commands, help, --json output and exit codes. An app is imported only once
  it's chosen, so `asgard muninn status` never loads PySide6, Playwright or the MCP SDK.
- Each app runs through its own apps/<app>/cli.py, loaded from Asgard's folder (asgard.paths), never
  from the current folder: the same file baldur.cmd runs, with sys.argv as it would see it.
- Odin's commands that live in odin.cmd (the bare daily run, preview, setup, doctor, schedule,
  unschedule, selftest, csv, run, help) go to odin.cmd itself, which stays the one place they're
  defined: the "Asgard Odin daily" task points at it. Its Python commands run in this process.
- A missing or unknown app lists the apps and Asgard's commands and exits 2.
- The older forms keep working: baldur.cmd, odin.cmd, heimdall.cmd, ysildir.cmd, and
  Asgard.pyw --muninn / --uninstall (also accepted here, for the packaged build's
  QuietUninstallString, which runs asgard-cli.exe --uninstall --yes).

Standard library only, like the launcher's start-up path: it imports asgard.paths and nothing else
of Asgard's until a command is chosen (tests/test_dependencies.py).
"""
from __future__ import annotations

import difflib
import importlib.util
import os
import subprocess
import sys
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple

if __name__ == "__main__":                     # run as a file (asgard.cmd): its own folder isn't a package
    _ROOT = str(Path(__file__).resolve().parent.parent)
    if sys.path and Path(sys.path[0] or ".").resolve() == Path(__file__).resolve().parent:
        sys.path.pop(0)                        # asgard\ itself would make paths.py importable as "paths"
    if _ROOT not in sys.path:
        sys.path.insert(0, _ROOT)

from asgard import paths  # noqa: E402

PROG = "asgard"
IS_WINDOWS = os.name == "nt"                   # odin.cmd's commands run only there

# The apps, in the order help lists them: (one line about it).
APPS: Dict[str, str] = {
    "baldur": "work-time estimates from git, for you to review and approve",
    "heimdall": "fill security change submissions from templates",
    "odin": "meetings to Jira sub-tasks, Jira into Muninn, approved days to Jira (bare: the daily run)",
    "ysildir": "Asgard's MCP server, so an approved AI client can use Baldur and Muninn",
}
COMMANDS: Dict[str, str] = {
    "muninn": "look after Muninn, Asgard's database (status, check, repair, backup, restore, ...)",
    "install": "install Asgard for your account, from the folder you extracted",
    "uninstall": "remove Asgard (--yes: without asking; --purge: delete your data too)",
    "version": "which Asgard this is, and where it runs from",
    "selftest": "check that every part of the packaged build loads on this computer",
    "ui": "open Asgard's window (--app ID: just that app's pages)",
    "notify": "choose what Asgard tells you about (list, on, off, quiet, test)",
}
HELP_WORDS = ("-h", "--help", "help", "/?")

# odin.cmd's own commands: they run PowerShell (Outlook, Task Scheduler) or walk you through setup,
# so they stay in odin.cmd, and Asgard's dispatcher hands them over to it unchanged.
ODIN_CMD_ACTIONS = ("", "preview", "setup", "doctor", "schedule", "unschedule", "selftest", "csv", "run",
                    "help", "-h", "--help", "/?")
# Characters cmd.exe would read inside a quoted argument (% expands variables), or that end the quote.
_CMD_UNSAFE = ('"', "%", "\r", "\n", "\x00")


class Refused(Exception):
    """The command can't run as given; the message says why and what to do."""


def say(text: str = "") -> None:
    print(text)


def warn(text: str) -> None:
    print(text, file=sys.stderr)


# --------------------------------------------------------------------------
# Help
# --------------------------------------------------------------------------

def usage() -> str:
    width = max(len(n) for n in list(APPS) + list(COMMANDS)) + 2
    lines = [f"usage: {PROG} <app> <command> [args]",
             f"       {PROG} <command> [args]",
             "",
             "Apps:"]
    lines += [f"  {name:<{width}}{about}" for name, about in APPS.items()]
    lines += ["", "Asgard's commands:"]
    lines += [f"  {name:<{width}}{about}" for name, about in COMMANDS.items()]
    lines += ["",
              f"{PROG} <app> --help lists that app's commands; {PROG} odin with no command is Odin's daily run.",
              "The older forms still work: baldur.cmd, odin.cmd, heimdall.cmd, ysildir.cmd, "
              "Asgard.pyw --muninn and --uninstall."]
    return "\n".join(lines)


def unknown(word: str) -> int:
    near = difflib.get_close_matches(word.lower(), list(APPS) + list(COMMANDS), n=1)
    warn(f"{PROG}: there's no app or command called {word!r}." + (f" Did you mean {near[0]}?" if near else ""))
    warn("")
    warn(usage())
    return 2


# --------------------------------------------------------------------------
# Running a main() and passing its exit code through
# --------------------------------------------------------------------------

def _code(result: object) -> int:
    """An exit code the way the Python interpreter turns main()'s result or SystemExit into one."""
    if result is None:
        return 0
    if isinstance(result, bool):
        return int(result)
    if isinstance(result, int):
        return result
    warn(str(result))
    return 1


def call(main: Callable[..., object], argv: List[str], argv0: Optional[str] = None) -> int:
    """main(argv), with sys.argv as `python argv0 ARGS` would set it, and the exit code it means."""
    saved = list(sys.argv)
    sys.argv = [argv0 or PROG] + list(argv)
    try:
        return _code(main(list(argv)))
    except SystemExit as exc:                  # argparse: --help is 0, a usage error 2
        return _code(exc.code)
    except KeyboardInterrupt:
        return 130
    finally:
        sys.argv = saved


# --------------------------------------------------------------------------
# Apps
# --------------------------------------------------------------------------

def app_script(app: str) -> Path:
    """apps/<app>/cli.py in Asgard's own folder: the file the app's .cmd runs."""
    return paths.CODE_ROOT / "apps" / app / "cli.py"


def load_app(app: str) -> Callable[..., object]:
    """The app's main(), from its cli.py. That file puts the app's folder (and Asgard's) on sys.path."""
    script = app_script(app)
    if not script.is_file():
        raise Refused(f"{app} isn't in this copy of Asgard ({script} is missing). Run Asgard's setup again.")
    spec = importlib.util.spec_from_file_location(f"asgard_app_{app}_cli", str(script))
    if spec is None or spec.loader is None:
        raise Refused(f"Couldn't load {script}.")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module.main


def run_app(app: str, args: List[str]) -> int:
    if app == "odin":
        return run_odin(args)
    if args == ["help"]:                       # baldur.cmd help, heimdall.cmd help, ysildir.cmd help
        args = ["--help"]
    main = load_app(app)
    return call(main, args, str(app_script(app)))


def odin_cmd() -> Path:
    return paths.CODE_ROOT / "apps" / "odin" / "odin.cmd"


def cmd_line(script: Path, args: Sequence[str]) -> str:
    """A cmd.exe /c command line that runs script with args, each quoted for cmd and for the program.

    cmd.exe reads its own syntax (& | < > ^ and %VAR%) on the whole line; inside double quotes only %
    still expands. So every argument is quoted, and one with a double quote or % is refused rather
    than guessed at: run odin.cmd yourself for that. Backslashes before a closing quote are doubled,
    as the Windows C runtime expects (C:\\folder\\ stays C:\\folder\\).
    """
    parts = [f'"{script}"']
    for arg in args:
        if any(c in arg for c in _CMD_UNSAFE):
            raise Refused(f"{arg!r} has a character cmd.exe would change (\" or %). Run odin.cmd with it "
                          "directly instead.")
        tail = len(arg) - len(arg.rstrip("\\"))
        parts.append(f'"{arg}{chr(92) * tail}"')
    return '"' + " ".join(parts) + '"'


def run_odin_cmd(args: Sequence[str]) -> int:
    """odin.cmd ARGS, as you'd type it, with its exit code (the daily run, preview, setup and the rest)."""
    script = odin_cmd()
    if not script.is_file():
        raise Refused(f"Odin isn't in this copy of Asgard ({script} is missing). Run Asgard's setup again.")
    comspec = os.environ.get("COMSPEC") or str(Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32"
                                                / "cmd.exe")
    # /d: no AutoRun commands from the registry; /s: strip exactly the outer quotes cmd_line() added.
    line = f'"{comspec}" /d /s /c {cmd_line(script, args)}'
    try:
        return subprocess.call(line)
    except KeyboardInterrupt:
        return 130


def run_odin(args: List[str]) -> int:
    action = args[0].lower() if args else ""
    if action in ODIN_CMD_ACTIONS:
        if IS_WINDOWS:
            return run_odin_cmd(args)
        if action in ("help", "-h", "--help", "/?"):
            return call(load_app("odin"), ["--help"], str(app_script("odin")))
        what = "daily run" if not action else f"{action} command"
        warn(f"Odin's {what} uses Outlook and Windows PowerShell, so it runs on Windows only. Its Python "
             f"commands work here: {PROG} odin --help.")
        return 2
    if action == "cli":                        # odin cli ARGS: the Python CLI, as odin.cmd passes it through
        args = args[1:]
    return call(load_app("odin"), args, str(app_script("odin")))


# --------------------------------------------------------------------------
# Asgard's own commands
# --------------------------------------------------------------------------

def run_muninn(args: List[str]) -> int:
    from asgard.muninn import cli as muninn_cli
    return call(lambda argv: muninn_cli.main(argv, prog=f"{PROG} muninn"), args)


def run_install(args: List[str]) -> int:
    from asgard import install
    return call(install.main, args)


def run_uninstall(args: List[str]) -> int:
    import argparse
    parser = argparse.ArgumentParser(prog=f"{PROG} uninstall", description="Remove Asgard for your account. "
                                     "Your tile settings, logs and Muninn database stay unless you add --purge.")
    parser.add_argument("--yes", action="store_true", help="don't ask: uninstall now (no window)")
    parser.add_argument("--purge", action="store_true", help="with --yes, delete your Asgard data too")
    try:
        parsed = parser.parse_args(args)
    except SystemExit as exc:
        return _code(exc.code)
    from asgard import valhalla
    return call(valhalla.main, ["--uninstall"] + (["--yes"] if parsed.yes else []) +
                (["--purge"] if parsed.purge else []))


def run_version(args: List[str]) -> int:
    if args and args[0] in HELP_WORDS:
        say(f"usage: {PROG} version\n\nWhich Asgard this is, and where it runs from.")
        return 0
    if args:
        warn(f"usage: {PROG} version  (it takes no arguments)")
        return 2
    from asgard import __version__
    say(f"Asgard {__version__}")
    if paths.FROZEN:
        say(f"Packaged build, Python {sys.version.split()[0]}: {paths.frozen_programs()[0]}")
    else:
        say(f"Python {sys.version.split()[0]}: {sys.executable}")
    say(f"Code: {paths.CODE_ROOT}")
    say(f"Data: {paths.data_dir()}")
    return 0


def run_selftest(args: List[str], selftest: Optional[Callable[..., int]] = None) -> int:
    import argparse
    parser = argparse.ArgumentParser(prog=f"{PROG} selftest", description="Check that every part of Asgard's "
                                     "packaged build loads on this computer (SQLite, Muninn, Tk, Qt, MCP, the "
                                     "apps, TLS). Your own data isn't touched.")
    parser.add_argument("--skip", metavar="PARTS", default="", help="leave parts out, like Qt,Tk")
    try:
        parsed = parser.parse_args(args)
    except SystemExit as exc:
        return _code(exc.code)
    skip = [s for s in parsed.skip.split(",") if s.strip()]
    if selftest is None:
        # A checkout has the packaged build's entry point beside the code; an installed copy with
        # Python doesn't ship it, and has no build to check.
        entry = paths.CODE_ROOT / "packaging" / "frozen_main.py"
        if not entry.is_file():
            warn("selftest checks the packaged build, and this is Asgard on Python. To check your data, run: "
                 f"{PROG} muninn check")
            return 2
        spec = importlib.util.spec_from_file_location("asgard_packaging_frozen_main", str(entry))
        if spec is None or spec.loader is None:
            raise Refused(f"Couldn't load {entry}.")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        selftest = module.self_test
    return _code(selftest(skip=skip))


def run_notify(args: List[str]) -> int:
    from asgard import notifycli
    return call(lambda argv: notifycli.main(argv, prog=f"{PROG} notify"), args)


def run_ui(args: List[str]) -> int:
    import argparse
    parser = argparse.ArgumentParser(prog=f"{PROG} ui", description="Open Asgard's desktop window.")
    parser.add_argument("--app", metavar="ID", help="show only this app's pages (for example heimdall)")
    try:
        parsed, rest = parser.parse_known_args(args)
    except SystemExit as exc:
        return _code(exc.code)
    from asgard import ui
    return _code(ui.run(app=parsed.app, argv=[PROG] + rest))


GLOBALS: Dict[str, Callable[[List[str]], int]] = {
    "muninn": run_muninn, "install": run_install, "uninstall": run_uninstall, "version": run_version,
    "ui": run_ui, "notify": run_notify,
}


# --------------------------------------------------------------------------
# Entry
# --------------------------------------------------------------------------

def split(argv: Sequence[str]) -> Tuple[str, List[str]]:
    """(the first word, lower-case, and everything after it unchanged)."""
    args = list(argv)
    return (args[0].lower(), args[1:]) if args else ("", [])


def main(argv: Optional[Sequence[str]] = None, *, selftest: Optional[Callable[..., int]] = None) -> int:
    """Dispatch one command line. selftest is the packaged build's self-test, which frozen_main passes in."""
    args = list(sys.argv[1:] if argv is None else argv)
    if args[:1] == ["--muninn"]:               # Asgard.pyw's own forms, kept for old commands
        return run_muninn(args[1:])
    if "--uninstall" in args[:1]:
        from asgard import valhalla
        return call(valhalla.main, args)
    first, rest = split(args)
    try:
        if not first:
            warn(usage())
            return 2
        if first in HELP_WORDS:
            say(usage())
            return 0
        if first in ("--version", "-v"):
            return run_version([])
        if first in APPS:
            return run_app(first, rest)
        if first == "selftest":
            return run_selftest(rest, selftest)
        if first in GLOBALS:
            return GLOBALS[first](rest)
        return unknown(args[0])
    except Refused as exc:
        warn(f"{PROG}: {exc}")
        return 2
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
