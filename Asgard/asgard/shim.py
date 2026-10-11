"""The asgard command on your Path: %LOCALAPPDATA%\\Asgard\\bin\\asgard.cmd, per user, no admin rights.

Setup (install.py) writes a two-line asgard.cmd into bin\\ that runs the installed copy's asgard
command (asgard/cli.py): with the Python Asgard was installed with, or the packaged build's
asgard-cli.exe. It then adds bin\\ to your own Path, the "Path" value under HKCU\\Environment, and
tells running programs the environment changed (WM_SETTINGCHANGE), so a terminal opened afterwards
finds `asgard`. Both are recorded in the install ledger, and Valhalla takes them out again.

The rules it keeps:
- Only the per-user Path. The machine Path (HKLM) needs admin rights and is never read or written.
- One entry. An entry that already names the folder (in any case, with or without a trailing
  backslash or %LOCALAPPDATA%) is left as it is; removing takes out every entry for the folder and
  nothing else.
- The value keeps its registry type: REG_EXPAND_SZ when it has one (Windows' default, so %VAR%
  entries keep working), and REG_EXPAND_SZ for a Path created here.
- A failure to change the Path never stops setup: the shim still works by its full path, and setup
  says how to add it by hand.

Standard library only, like the rest of setup's path (tests/test_dependencies.py). Tests pass a
stand-in for winreg; nothing here touches a real registry unless it's on Windows and asked to.
"""
from __future__ import annotations

import ntpath
import os
from pathlib import Path
from typing import Any, List, Optional, Tuple

SHIM_NAME = "asgard.cmd"
ENV_KEY = "Environment"
PATH_VALUE = "Path"
MAX_PATH_VALUE = 32_000          # Windows caps an environment block entry at 32,767 characters
WM_SETTINGCHANGE = 0x001A
HWND_BROADCAST = 0xFFFF
SMTO_ABORTIFHUNG = 0x0002


class PathError(Exception):
    """The Path couldn't be changed; the message says why and what to do by hand."""


def shim_text(program: str, script: Optional[str] = None) -> str:
    """asgard.cmd: run program (and script) with every argument, and keep the exit code.

    Everything happens on one line ending in `& exit /b`: cmd.exe reads a batch file a line at a time,
    so when `asgard uninstall` deletes this file mid-run, the line it's on has already been read and
    it exits cleanly instead of saying "The batch file cannot be found." `exit /b` with no number
    returns the program's own exit code.
    """
    for part in (program, script or ""):
        if any(c in part for c in '"%\r\n'):
            raise ValueError(f"{part!r} can't be written into asgard.cmd")
    target = f'"{program}"' + (f' "{script}"' if script else "")
    lines = ["@echo off",
             "rem asgard: Asgard's command, from any folder. Try: asgard --help",
             "rem Setup writes this file and Valhalla removes it; asgard.cmd isn't for editing.",
             f"{target} %* & exit /b"]
    text = "\r\n".join(lines) + "\r\n"
    text.encode("ascii")                       # .cmd files are ASCII (AGENTS.md); a non-ASCII path can't go here
    return text


def write_shim(folder: Path, program: str, script: Optional[str] = None) -> Path:
    """Write folder\\asgard.cmd through a temporary file and a rename, so it's never half written."""
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / SHIM_NAME
    tmp = target.with_suffix(".cmd.tmp")
    tmp.write_bytes(shim_text(program, script).encode("ascii"))
    os.replace(tmp, target)
    return target


def shim_command(app: Path, frozen: bool, python: str) -> Tuple[str, Optional[str]]:
    """(program, script) the shim runs for an installed copy in app."""
    if frozen:
        ext = ".exe" if os.name == "nt" else ""
        return str(app / f"asgard-cli{ext}"), None
    return python, str(app / "asgard" / "cli.py")


# --------------------------------------------------------------------------
# The per-user Path
# --------------------------------------------------------------------------

def _winreg(reg: Any = None) -> Any:
    if reg is not None:
        return reg
    import winreg
    return winreg


def same_folder(entry: str, folder: Path) -> bool:
    """Whether a Path entry names folder: case, quotes, a trailing slash and %VARIABLES% don't matter."""
    def norm(text: str) -> str:
        text = ntpath.expandvars(text.strip().strip('"')).replace("/", "\\").rstrip("\\")
        return text.lower()
    return bool(entry.strip()) and norm(entry) == norm(str(folder))


def read_user_path(reg: Any = None) -> Tuple[str, Optional[int]]:
    """(the per-user Path, its registry type), or ("", None) when it isn't set."""
    winreg = _winreg(reg)
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, ENV_KEY, 0, winreg.KEY_READ) as key:
            value, kind = winreg.QueryValueEx(key, PATH_VALUE)
    except FileNotFoundError:
        return "", None
    return (str(value) if value is not None else ""), kind


def _write_user_path(value: str, kind: Optional[int], reg: Any = None) -> None:
    winreg = _winreg(reg)
    if len(value) > MAX_PATH_VALUE:
        raise PathError(f"Your Path would be {len(value):,} characters, longer than Windows allows.")
    if kind not in (winreg.REG_SZ, winreg.REG_EXPAND_SZ):
        kind = winreg.REG_EXPAND_SZ
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, ENV_KEY, 0, winreg.KEY_WRITE) as key:
        winreg.SetValueEx(key, PATH_VALUE, 0, kind, value)


def entries(value: str) -> List[str]:
    return [e for e in value.split(";") if e.strip()]


def add_to_user_path(folder: Path, reg: Any = None, broadcast: bool = True) -> bool:
    """Add folder to the end of the per-user Path. Returns False when it was already there."""
    try:
        value, kind = read_user_path(reg)
        found = entries(value)
        if any(same_folder(e, folder) for e in found):
            return False
        _write_user_path(";".join(found + [str(folder)]), kind, reg)
    except OSError as exc:
        raise PathError(f"Couldn't change your Path ({exc}).") from exc
    if broadcast:
        broadcast_environment_change()
    return True


def remove_from_user_path(folder: Path, reg: Any = None, broadcast: bool = True) -> bool:
    """Take every entry for folder out of the per-user Path. Returns False when there was none."""
    try:
        value, kind = read_user_path(reg)
        found = entries(value)
        kept = [e for e in found if not same_folder(e, folder)]
        if len(kept) == len(found):
            return False
        _write_user_path(";".join(kept), kind, reg)
    except OSError as exc:
        raise PathError(f"Couldn't change your Path ({exc}).") from exc
    if broadcast:
        broadcast_environment_change()
    return True


def broadcast_environment_change() -> bool:
    """Tell running programs (Explorer, so new terminals) that the environment changed. Best effort."""
    if os.name != "nt":
        return False
    try:
        import ctypes
        from ctypes import wintypes
        result = wintypes.DWORD()
        sent = ctypes.windll.user32.SendMessageTimeoutW(HWND_BROADCAST, WM_SETTINGCHANGE, 0, ENV_KEY,
                                                        SMTO_ABORTIFHUNG, 5000, ctypes.byref(result))
        return bool(sent)
    except (OSError, AttributeError, ValueError):
        return False
