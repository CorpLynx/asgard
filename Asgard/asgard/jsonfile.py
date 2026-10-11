"""Writing a settings file so a crash, or a second writer, can't leave half of one behind.

Every app keeps its settings in JSON under %LOCALAPPDATA%\\Asgard, and more than one thing writes
them: the window's Settings page, the app's own CLI, and the window's worker threads reading while
one of those writes. Two rules come out of that, and both of them bit us:

1. **Its own temporary file.** A fixed name (`baldur.json.tmp`) means two writers share one path, and
   the first rename takes the file the second is about to rename. On POSIX that surfaced as
   FileNotFoundError while the Settings page was being drawn.
2. **Windows can refuse the rename.** os.replace onto a path another handle has open fails with
   PermissionError (WinError 5 or 32), where POSIX just does it. Antivirus and search indexers open
   files they see appear, so this happens on an idle machine too. The answer is to try again for a
   moment, not to give up: the write itself is finished, only the rename is contended.

Standard library only, and no Asgard imports, so the apps and the launcher can all use it.
"""
from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any, Optional

RETRY_SECONDS = 2.0


def replace_with_retry(source: Path, target: Path, *, total: float = RETRY_SECONDS) -> None:
    """os.replace(source, target), trying again briefly while Windows says the file is in use."""
    deadline = time.monotonic() + total
    pause = 0.01
    while True:
        try:
            os.replace(source, target)
            return
        except PermissionError:
            # Windows only: something holds the target open (another writer, an indexer, antivirus).
            if time.monotonic() >= deadline:
                raise
            time.sleep(pause)
            pause = min(pause * 2, 0.2)


def write_json(path: Path, data: Any, *, indent: int = 2, newline: Optional[str] = None) -> Path:
    """Write data as JSON to path, through a temporary file of its own and one rename.

    newline defaults to CRLF on Windows, so Notepad shows the file as its neighbours do.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if newline is None:
        newline = "\r\n" if os.name == "nt" else "\n"
    handle, name = tempfile.mkstemp(dir=str(path.parent), prefix=path.name + ".", suffix=".tmp")
    tmp = Path(name)
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline=newline) as fh:
            json.dump(data, fh, indent=indent)
            fh.write("\n")
        replace_with_retry(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return path
