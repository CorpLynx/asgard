"""Your desktop UI preferences: %LOCALAPPDATA%\\Asgard\\settings\\ui.json.

    {
      "mode": "system",                       light, dark, or system (follow Windows)
      "theme": {"fontSize": 14},              overrides for every app
      "apps": {"heimdall": {"accent": "#6B4FBF"}, "odin": {"dark": {"accent": "#7FA7E8"}}},
      "banner": {"text": "...", "enabled": true, "animate": true, "speed": 80, "font": ""}
    }

"banner" is the yellow warning banner along the top and bottom of Asgard's home window (the Qt
window and the tkinter fallback both read it). Empty text hides it; "animate": false stops the
scrolling and the red pulse (for anyone who finds motion distracting); "font" picks a family,
otherwise the first of BANNER_FONTS this computer has.

Rules it keeps:
- A file that won't parse is never overwritten (same rule as Heimdall's templates): the window
  opens with defaults and says so, and saving refuses until the file is fixed or moved aside.
- Writes go to a temporary file and replace the old one in one step.
- Values are checked by theme.clean() when used, so a bad colour warns instead of crashing.

Standard library only.
"""
from __future__ import annotations

import copy
import json
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from asgard import paths

MODE_CHOICES = ("system", "light", "dark")

BANNER_DEFAULT_TEXT = "ASGARD  //  EDIT THIS MESSAGE IN SETTINGS > BANNER"
BANNER_MAX_TEXT = 300
BANNER_SPEEDS = (20, 400)            # pixels per second
# Techno / warning faces first; Windows has Bahnschrift and Consolas, Office adds OCR A and Agency FB.
BANNER_FONTS = ("OCR A Extended", "OCR A Std", "Agency FB", "Bahnschrift SemiBold Condensed", "Bahnschrift",
                "Consolas", "Menlo", "DejaVu Sans Mono", "Courier New")
# The pulse runs between dim and bright red. Both reach 4.5:1 or better on the yellow
# (tests/test_ui.py checks), so the text is readable at every point of the pulse.
BANNER_COLORS = {"background": "#FFD60A", "stripe": "#1A1A1A", "dim": "#6A0000", "bright": "#BE0000",
                 "still": "#9A0000"}
BANNER_PULSE_MS = 1600               # one full dim-bright-dim cycle: well under 3 flashes a second


@dataclass
class Banner:
    text: str = BANNER_DEFAULT_TEXT
    enabled: bool = True
    animate: bool = True
    speed: int = 80
    font: str = ""

    @property
    def shown(self) -> bool:
        return self.enabled and bool(self.text.strip())

    def to_json(self) -> Dict[str, Any]:
        return {"text": self.text, "enabled": self.enabled, "animate": self.animate, "speed": self.speed,
                "font": self.font}


def clean_banner_text(text: str) -> str:
    """One line, outer spaces trimmed, at most BANNER_MAX_TEXT characters."""
    return str(text).replace("\r", " ").replace("\n", " ").strip()[:BANNER_MAX_TEXT]


def pick_banner_font(available: Any, wanted: str = "") -> str:
    """The family to draw the banner in: the one asked for if installed, else the first of BANNER_FONTS."""
    have = {f.casefold(): f for f in available}
    for name in ((wanted,) if wanted else ()) + BANNER_FONTS:
        if name and name.casefold() in have:
            return have[name.casefold()]
    return ""


def prefs_path() -> Path:
    return paths.data_dir() / "settings" / "ui.json"


@dataclass
class Prefs:
    path: Path
    mode: str = "system"
    theme: Dict[str, Any] = field(default_factory=dict)
    apps: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    banner: Banner = field(default_factory=Banner)
    warnings: List[str] = field(default_factory=list)
    broken: Optional[str] = None

    def app_theme(self, app_id: str) -> Dict[str, Any]:
        return self.apps.get(app_id, {})

    def set_token(self, app_id: Optional[str], key: str, value: Any, mode: Optional[str] = None) -> None:
        """Set one token, for every app (app_id None or "") or one app, in both modes or one."""
        target = self.theme if not app_id else self.apps.setdefault(app_id, {})
        if mode:
            target = target.setdefault(mode, {})
        target[key] = value

    def clear_token(self, app_id: Optional[str], key: str) -> None:
        """Remove a token from a layer in both modes, then drop layers left empty."""
        target = self.theme if not app_id else self.apps.get(app_id, {})
        target.pop(key, None)
        for mode in ("light", "dark"):
            if isinstance(target.get(mode), dict):
                target[mode].pop(key, None)
                if not target[mode]:
                    del target[mode]
        if app_id and app_id in self.apps and not self.apps[app_id]:
            del self.apps[app_id]

    def save(self) -> None:
        if self.broken:
            raise PrefsError(f"{self.path} can't be used ({self.broken}), so it wasn't changed. Fix it, "
                             "or move it aside to start again from the defaults.")
        body: Dict[str, Any] = {"mode": self.mode}
        if self.theme:
            body["theme"] = self.theme
        if self.apps:
            body["apps"] = {k: v for k, v in sorted(self.apps.items()) if v}
        if self.banner != Banner():
            body["banner"] = self.banner.to_json()
        _write_json(self.path, body)


class PrefsError(ValueError):
    """The preferences file can't be saved. The message says what to do."""


def load(path: Optional[Path] = None) -> Prefs:
    path = Path(path) if path else prefs_path()
    prefs = Prefs(path=path)
    try:
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        return prefs
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        prefs.broken = f"it isn't valid JSON: {exc}"
    except OSError as exc:
        prefs.broken = f"it can't be read: {exc.strerror}"
    else:
        if not isinstance(raw, dict):
            prefs.broken = "it must hold a JSON object"
    if prefs.broken:
        prefs.warnings.append(f"Your UI preferences in {path} couldn't be used ({prefs.broken}), so the "
                              "defaults are showing. Fix the file, or move it aside to start again.")
        return prefs

    mode = raw.get("mode", "system")
    if mode in MODE_CHOICES:
        prefs.mode = mode
    else:
        prefs.warnings.append(f'{path.name}: "mode" must be one of {", ".join(MODE_CHOICES)}; using "system".')
    theme = raw.get("theme", {})
    if isinstance(theme, dict):
        prefs.theme = copy.deepcopy(theme)
    else:
        prefs.warnings.append(f'{path.name}: "theme" must be an object; ignored.')
    apps = raw.get("apps", {})
    if isinstance(apps, dict):
        for app_id, body in apps.items():
            if isinstance(body, dict):
                prefs.apps[app_id] = copy.deepcopy(body)
            else:
                prefs.warnings.append(f'{path.name}: the theme for "{app_id}" must be an object; ignored.')
    else:
        prefs.warnings.append(f'{path.name}: "apps" must be an object; ignored.')
    prefs.banner = _load_banner(raw.get("banner"), path.name, prefs.warnings)
    for key in raw:
        if key not in ("mode", "theme", "apps", "banner") and not key.startswith("_"):
            prefs.warnings.append(f'{path.name}: "{key}" isn\'t a UI setting; ignored.')
    return prefs


def _load_banner(raw: Any, where: str, warnings: List[str]) -> Banner:
    banner = Banner()
    if raw is None:
        return banner
    if not isinstance(raw, dict):
        warnings.append(f'{where}: "banner" must be an object; using the default banner.')
        return banner
    if "text" in raw:
        if isinstance(raw["text"], str):
            banner.text = clean_banner_text(raw["text"])
        else:
            warnings.append(f'{where}: banner "text" must be text; using the default.')
    for key in ("enabled", "animate"):
        if key in raw:
            if isinstance(raw[key], bool):
                setattr(banner, key, raw[key])
            else:
                warnings.append(f'{where}: banner "{key}" must be true or false; using {getattr(banner, key)}.')
    if "speed" in raw:
        low, high = BANNER_SPEEDS
        value = raw["speed"]
        if isinstance(value, int) and not isinstance(value, bool) and low <= value <= high:
            banner.speed = value
        else:
            warnings.append(f'{where}: banner "speed" must be a whole number from {low} to {high}; using {banner.speed}.')
    if "font" in raw:
        if isinstance(raw["font"], str):
            banner.font = raw["font"].strip()
        else:
            warnings.append(f'{where}: banner "font" must be a font name; using the default.')
    for key in raw:
        if key not in ("text", "enabled", "animate", "speed", "font") and not key.startswith("_"):
            warnings.append(f'{where}: "{key}" isn\'t a banner setting; ignored.')
    return banner


def _write_json(path: Path, body: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.stem}-", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps(body, indent=2, ensure_ascii=False) + "\n")
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
