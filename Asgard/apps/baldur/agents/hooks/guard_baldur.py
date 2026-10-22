#!/usr/bin/env python3
"""Agent hook (Kiro PreToolUse): keep an AI agent's hands off Baldur's decisions.

The person decides what time is logged (Asgard rules 4 and 5). An agent may record its own
estimates (ai record), read what Baldur has (ai list, ai show, report, days, keys SHA) and run
collection or an estimate (collect, estimate, a plain refresh, plan); it may not:
- approve, reject or change a figure, or take AI-assisted figures (approve --ai);
- approve what a plan shows (apply), or let scheduled runs do it (auto-approve, refresh --auto-approve);
- note real hours (actual), which calibration trusts as ground truth, or accept a calibration;
- change Baldur's settings, a commit's keys, which repositories count, the schedule or the
  GitHub token;
- touch Muninn's file directly (muninn.db), run Asgard's database maintenance, or uninstall Asgard;
- do any of these through the asgard command (asgard baldur approve, asgard muninn restore,
  asgard uninstall, asgard ysildir tools --on), however it's started;
- switch Ysildir's tools on or off, connect AI clients to it (ysildir.cmd tools --on/--off, setup),
  or touch its switches file (ysildir.json): which tools an agent may use is the person's choice,
  approved by their ISSO.

Exit code 2 blocks the tool call and stderr tells the agent why; anything else lets it run. A
bug in this script never blocks anything. Standard library only, Python 3.9+.
"""
from __future__ import annotations

import json
import re
import shlex
import sys
from typing import Any, Iterator, List, Optional, Set, Tuple

COMMAND_KEYS = {"command", "cmd", "script", "commandLine", "command_line"}
# Baldur and Ysildir are started as baldur.cmd (or baldur on PATH), or as their cli.py from the
# install or the repo. A cli.py with no other app's folder in its path is taken as Baldur's: an agent
# can cd into Baldur's folder and run .\cli.py.
OTHER_APPS = ("heimdall", "ysildir", "odin", "bifrost", "freya", "loki")
# The asgard command runs every app and Asgard's own commands: asgard baldur approve, asgard.cmd (the
# Path shim), asgard-cli.exe and Asgard.exe (the packaged build), asgard\cli.py with python, and
# python -m asgard.cli. Its first word that isn't an option names what runs.
DISPATCHER_NAMES = {"asgard", "asgard.cmd", "asgard.bat", "asgard.exe", "asgard-cli", "asgard-cli.exe"}
DISPATCH_WORDS = {"baldur", "heimdall", "odin", "ysildir", "muninn", "install", "uninstall", "version",
                  "selftest", "ui", "notify"}
MUNINN_FILE = re.compile(r"muninn\.db", re.I)
ASGARD_DB = re.compile(r"asgard[\\/][^\s\"']*\.db\b", re.I)          # any database file in Asgard's folder
SETTINGS_FILE = re.compile(r"baldur\.json", re.I)
SWITCHES_FILE = re.compile(r"ysildir\.json", re.I)
MUNINN_MAINTENANCE = {"restore", "repair", "retention"}
# Free text follows these options (a commit message, a report's summary, a note). It's words, not a
# command or a file, so the guard doesn't read it: a summary may well mention muninn.db.
TEXT_OPTIONS = {"-m", "--message", "--summary", "--note"}
MAX_DEPTH = 4                         # quoted commands inside quoted commands

DECISIONS = {"approve", "reject", "change", "actual", "schedule", "auto-approve", "auto_approve", "apply"}
MESSAGE = ("Blocked by Baldur's agent guard: {what} is the person's decision, not an agent's. "
           "Record your estimate with `baldur.cmd ai record ...` and tell the person what you found; "
           "they approve in Baldur. (Baldur agent guide: baldur.cmd ai guide)")
YSILDIR_MESSAGE = ("Blocked by Baldur's agent guard: {what} is the person's decision, not an agent's. Which "
                   "Ysildir tools an agent may use is theirs, approved by their ISSO; tell them what you would "
                   "need and why. (ysildir.cmd tools lists the switches; Ysildir's asgard_guide, topic tools, too.)")


def read_event() -> Any:
    try:
        raw = sys.stdin.read() if not sys.stdin.isatty() else ""
    except Exception:
        return {}
    if not raw.strip():
        return {}
    try:
        return json.loads(raw)
    except ValueError:
        return {"command": raw}


def commands(event: Any) -> Iterator[str]:
    if isinstance(event, dict):
        for key, value in event.items():
            if key in COMMAND_KEYS and isinstance(value, str):
                yield value
            elif key in COMMAND_KEYS and isinstance(value, list) and value and all(isinstance(v, str) for v in value):
                yield " ".join(f'"{v}"' if (" " in v and not _quoted(v)) else v for v in value)   # an argv list
            else:
                yield from commands(value)
    elif isinstance(event, list):
        for item in event:
            yield from commands(item)


def _tokens(text: str) -> List[str]:
    """Words and the shell's separators (; & | && ||), quoted strings kept whole with their quotes."""
    lexer = shlex.shlex(text, posix=False, punctuation_chars=";&|")
    lexer.whitespace_split = True
    lexer.commenters = ""
    try:
        return list(lexer)
    except ValueError:                    # an unbalanced quote: plain words will do
        return re.findall(r"[;&|]+|[^\s;&|]+", text)


def _unquote(word: str) -> str:
    return word[1:-1] if len(word) >= 2 and word[0] == word[-1] and word[0] in "\"'" else word


def _quoted(word: str) -> bool:
    return len(word) >= 2 and word[0] == word[-1] and word[0] in "\"'"


def _segments(command: str) -> List[List[str]]:
    """The command's simple commands, each a list of words."""
    out: List[List[str]] = [[]]
    for token in _tokens(command):
        if token and set(token) <= set(";&|"):
            out.append([])
        else:
            out[-1].append(token)
    return [s for s in out if s]


def _expanded(words: List[str]) -> List[str]:
    """Arguments with quoted lists opened up: -ArgumentList "approve --date 1" or 'approve','--date'."""
    out: List[str] = []
    for word in words:
        out.extend(piece.strip("\"'") for piece in re.split(r"[,\s]+", _unquote(word)) if piece.strip("\"'"))
    return out


def _program(word: str, app: str) -> bool:
    path = _unquote(word).replace("\\", "/")
    name = path.rsplit("/", 1)[-1].lower()
    if name in (app, f"{app}.cmd", f"{app}.bat", f"{app}.exe"):
        return True
    if name != "cli.py":
        return False
    folders = path.lower().split("/")[:-1]
    if folders[-1:] == ["asgard"]:                 # asgard\cli.py is the asgard command, not an app's
        return False
    if app == "baldur":
        return "baldur" in folders or not any(other in folders for other in OTHER_APPS)
    return app in folders


def _first_word(args: List[str]) -> Optional[int]:
    """Where the first argument that isn't an option is."""
    return next((i for i, a in enumerate(args) if not a.startswith("-")), None)


def _dispatcher(words: List[str], n: int) -> bool:
    """Whether words[n] starts the asgard command (asgard, asgard.cmd, asgard-cli.exe, asgard\\cli.py, -m asgard.cli)."""
    path = _unquote(words[n]).replace("\\", "/")
    name = path.rsplit("/", 1)[-1].lower()
    if name in DISPATCHER_NAMES:
        return True
    if name == "cli.py":
        folders = [f for f in path.lower().split("/")[:-1] if f not in ("", ".")]
        if folders[-1:] == ["asgard"]:
            return True
        if not folders:                            # a bare .\cli.py: the asgard command if its next word is one
            rest = _expanded(words[n + 1:])
            first = _first_word(rest)
            return first is not None and rest[first].lower() in DISPATCH_WORDS
        return False
    return name in ("asgard.cli", "asgard") and n > 0 and _unquote(words[n - 1]).lower() == "-m"


def dispatched(command: str) -> List[Tuple[str, List[str]]]:
    """(what it runs, its arguments) for each use of the asgard command in a shell command line."""
    out = []
    for words in _segments(command):
        for n in range(len(words)):
            if _dispatcher(words, n):
                rest = _expanded(words[n + 1:])
                first = _first_word(rest)
                if first is not None:
                    out.append((rest[first].lower(), rest[first + 1:]))
                break
    return out


def app_args(command: str, app: str = "baldur") -> List[List[str]]:
    """The arguments of each invocation of an Asgard app's command line (baldur, ysildir) in a shell command:
    its own program or cli.py, or the asgard command (asgard baldur ...)."""
    out = []
    for words in _segments(command):
        for n, word in enumerate(words):
            if _program(word, app) and not _dispatcher(words, n):
                out.append(_expanded(words[n + 1:]))
                break
    out.extend(rest for name, rest in dispatched(command) if name == app)
    return out


def baldur_args(command: str) -> List[List[str]]:
    """The arguments of each Baldur invocation in a shell command line."""
    return app_args(command, "baldur")


def _flag(flags: Set[str], *names: str) -> bool:
    """Whether an option is given, also as an abbreviation argparse would once have taken (--acc, --of)."""
    return any(f == n or (len(f) >= 4 and n.startswith(f)) for f in flags for n in names)


def _not_text(words: List[str]) -> List[str]:
    """The words of a simple command, without the free text that follows -m, --summary and the like."""
    out, skip = [], False
    for word in words:
        if skip:
            skip = False
            continue
        low = word.lower()
        if low in TEXT_OPTIONS:
            skip = True
            continue
        if any(low.startswith(o + "=") for o in TEXT_OPTIONS if o.startswith("--")):
            continue
        out.append(word)
    return out


def _app_verdict(command: str) -> Optional[str]:
    for name, args in dispatched(command):
        first = _first_word(args)
        if name == "muninn" and first is not None and args[first].lower() in MUNINN_MAINTENANCE:
            return MESSAGE.format(what="Muninn maintenance (restore, repair, retention)")
        if name == "uninstall":
            return MESSAGE.format(what="Uninstalling Asgard")
    for args in app_args(command, "ysildir"):
        words = [a.lower() for a in args if not a.startswith("-")]
        flags = {a.split("=", 1)[0].lower() for a in args if a.startswith("-")}
        if words[:1] == ["setup"]:
            return YSILDIR_MESSAGE.format(what="Connecting AI clients to Ysildir (setup)")
        if words[:1] == ["tools"] and _flag(flags, "--on", "--off"):
            return YSILDIR_MESSAGE.format(what="Switching Ysildir's tools on or off")
    for args in baldur_args(command):
        words = [a for a in args if not a.startswith("-")]
        flags = {a.split("=", 1)[0].lower() for a in args if a.startswith("-")}
        sub = words[0].lower() if words else ""
        if sub in DECISIONS:
            return MESSAGE.format(what=f"`{sub}`")
        if sub == "calibrate" and _flag(flags, "--accept"):
            return MESSAGE.format(what="Accepting a calibration")
        # A plain refresh is collection and an estimate, which an agent may run. With --auto-approve
        # it can decide a day, so the flag is blocked: the person opts into that, not an agent.
        if sub == "refresh" and _flag(flags, "--auto-approve", "--auto_approve"):
            return MESSAGE.format(what="Approving days automatically (refresh --auto-approve)")
        if sub == "setup" and args[1:]:
            return MESSAGE.format(what="Changing Baldur's setup")
        if sub == "repos" and _flag(flags, "--on", "--off"):
            return MESSAGE.format(what="Switching a repository on or off")
        if sub == "keys" and len(words) > 2:
            return MESSAGE.format(what="Setting a commit's Jira keys")
        if sub == "github" and len(words) > 1 and words[1].lower() == "token":
            return MESSAGE.format(what="The GitHub token")
    return None


def verdict(command: str, depth: int = 0) -> Optional[str]:
    """Why this command is blocked, or None when it may run.

    It reads every simple command in the line, and the inside of every quoted string as a command of
    its own (cmd /c "...", powershell -Command "...", bash -lc "...", Invoke-Expression "..."), but
    not the free text after -m, --summary and the like.
    """
    if depth > MAX_DEPTH:
        return None
    why = _app_verdict(command)
    if why:
        return why
    for words in _segments(command):
        words = _not_text(words)
        plain = [_unquote(w) for w in words]
        for text in plain:
            if MUNINN_FILE.search(text) or ASGARD_DB.search(text):
                return MESSAGE.format(what="Reading or writing Muninn's file directly") + \
                    " Muninn changes only through Baldur's commands or Ysildir."
            if SETTINGS_FILE.search(text):
                return MESSAGE.format(what="Baldur's settings file (baldur.json)") + \
                    " The person changes settings in Baldur."
            if SWITCHES_FILE.search(text):
                return YSILDIR_MESSAGE.format(what="Ysildir's switches file (ysildir.json)")
        lowered = [w.lower() for w in plain]
        for n, word in enumerate(lowered[:-1]):
            if word == "--muninn" and lowered[n + 1] in MUNINN_MAINTENANCE:
                return MESSAGE.format(what="Muninn maintenance (restore, repair, retention)")
        if "--uninstall" in lowered:          # Asgard.pyw, Asgard.exe or asgard-cli.exe; --purge deletes Muninn
            return MESSAGE.format(what="Uninstalling Asgard")
        for word in words:
            if _quoted(word):
                why = verdict(_unquote(word), depth + 1)
                if why:
                    return why
    # Last pass: the same line with every quote taken out. A command can quote its inner quotes
    # with backslashes (cmd /c "baldur \"approve\" --date today"), which splits a word in the
    # middle, so the program and its subcommand are never seen together and nothing above matches.
    # Without the quotes they are plain words again. Found by the independent review of 2026-10-11
    # (F1), where it let every blocked command through, not only this change's.
    # Tokenise first, so a quoted commit message is still one word and _not_text drops all of it;
    # only then take the quotes out, which puts a program and its subcommand back beside each other.
    kept = " ".join(_not_text(_tokens(command)))
    bare = _DEQUOTE.sub(" ", kept)
    if bare != kept:
        why = _app_verdict(bare)
        if why:
            return why
    return None


_DEQUOTE = re.compile(r"""[\\]?["']""")


def main() -> int:
    for command in commands(read_event()):
        why = verdict(command)
        if why:
            print(why, file=sys.stderr)
            return 2
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:                    # never block on a bug in the guard itself
        print(f"Baldur agent guard error (not blocking): {exc}", file=sys.stderr)
        sys.exit(0)
