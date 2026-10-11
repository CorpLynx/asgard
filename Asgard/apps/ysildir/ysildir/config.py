"""Which Ysildir tools the person allows: %LOCALAPPDATA%\\Asgard\\settings\\ysildir.json.

Everything a tool returns goes to the AI client, and so to its model, so each tool's data flow is
the ISSO's decision and each tool has a switch (docs/integration/ysildir.md):
- No file: the defaults. Tools that return only Asgard's own text, or the agents' own reports,
  start on; tools that send commit subjects, Jira summaries or times start off.
- A broken file fails closed: every tool except asgard_guide is off, and the guide's tools topic
  says what's wrong, naming the bad entry.
- Names that aren't Ysildir tools are ignored, and listed in the tools topic.
The server reads the file when it starts and never writes it. `ysildir.cmd tools` writes it,
through a temporary file and a rename. The person runs that, not an agent: Kiro's guard hook
blocks it.

Python 3.9 syntax, but no `from __future__ import annotations`: pydantic reads the annotations.
"""
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional

from pydantic import BaseModel, ConfigDict, Field, StrictBool, ValidationError

from asgard import jsonfile, paths

from . import Refused
from .switches import ALWAYS_ON, HELP, NAMES, SWITCHES, VERSION, Switch  # noqa: F401 (re-exported)


class SwitchesFile(BaseModel):
    """ysildir.json, as `ysildir.cmd tools` writes it."""
    model_config = ConfigDict(extra="ignore")

    version: int = Field(VERSION, ge=1, le=VERSION)
    tools: Dict[str, StrictBool] = Field(default_factory=dict)


@dataclass
class Config:
    tools: Dict[str, bool]              # every switch: on or off
    path: Path
    problem: Optional[str] = None       # why the file couldn't be used; then only asgard_guide is on
    unknown: List[str] = field(default_factory=list)     # names in the file that aren't Ysildir tools

    def on(self, name: str) -> bool:
        return bool(self.tools.get(name, False))

    def on_names(self) -> List[str]:
        return [n for n in NAMES if self.on(n)]


def settings_path() -> Path:
    return paths.data_dir() / "settings" / "ysildir.json"


def defaults() -> Dict[str, bool]:
    return {s.name: s.default for s in SWITCHES}


def everything_on(path: Optional[Path] = None) -> Config:
    """Every tool on: for `ysildir.cmd check --all` and the tests, never for serving."""
    return Config({n: True for n in NAMES}, path or settings_path())


def _problem(exc: Exception) -> str:
    """What's wrong with the file, naming the entry and never repeating its values."""
    if isinstance(exc, ValidationError):
        parts = []
        for err in exc.errors()[:3]:
            where = ".".join(str(x) for x in err.get("loc", ())) or "the file"
            parts.append(f"{where}: {err.get('msg', 'not valid')}")
        return "; ".join(parts)
    if isinstance(exc, json.JSONDecodeError):
        return f"it isn't JSON ({exc.msg} at line {exc.lineno})"
    if isinstance(exc, OSError):
        return f"it couldn't be read ({exc.strerror or type(exc).__name__})"
    return str(exc)


def load(path: Optional[Path] = None) -> Config:
    path = path or settings_path()
    if not path.exists():
        return Config(defaults(), path)
    try:
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
        if not isinstance(raw, dict):
            raise Refused('the file must hold a JSON object, like {"version": 1, "tools": {...}}')
        data = SwitchesFile.model_validate(raw)
    except (OSError, ValueError) as exc:          # JSONDecodeError and ValidationError are ValueErrors
        return Config({n: n == ALWAYS_ON for n in NAMES}, path, problem=_problem(exc))
    tools = defaults()
    tools.update({k: v for k, v in data.tools.items() if k in NAMES})
    return Config(tools, path, unknown=sorted(set(data.tools) - set(NAMES)))


def save(tools: Dict[str, bool], path: Optional[Path] = None) -> Path:
    """Write every switch, through a temporary file and a rename so a crash can't leave half a file."""
    path = path or settings_path()
    data = {"_help": HELP, "version": VERSION, "tools": {n: bool(tools.get(n, d)) for n, d in defaults().items()}}
    path.parent.mkdir(parents=True, exist_ok=True)
    return jsonfile.write_json(path, data)


def switch(names: Iterable[str], on: bool, path: Optional[Path] = None) -> Config:
    """Turn tools on or off, keeping the other switches. The person runs this (`ysildir.cmd tools`)."""
    names = list(names)
    bad = [n for n in names if n not in NAMES]
    if bad:
        raise Refused(f"{', '.join(bad)} isn't a Ysildir tool. The tools are: {', '.join(NAMES)}.")
    current = load(path)
    if current.problem:
        raise Refused(f"{current.path} has a mistake in it ({current.problem}). Fix it, or delete it to start "
                      "again from the defaults.")
    tools = dict(current.tools)
    tools.update({n: on for n in names})
    save(tools, current.path)
    return load(current.path)
