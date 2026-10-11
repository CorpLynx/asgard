"""Which apps plug views into the shared window, found from apps/<id>/ui/manifest.json.

    {
      "app": "heimdall",
      "name": "Heimdall",
      "subtitle": "Security change submissions",
      "theme": {"accent": "#5B4B9A"},
      "backend": "heimdall.ui_backend:Backend",
      "views": [
        {"id": "templates", "title": "Templates", "icon": "Tp", "qml": "Templates.qml"},
        {"id": "form", "title": "Form", "icon": "Fm", "qml": "Form.qml"}
      ]
    }

The backend is a plain Python class: no Qt. The shell wraps it in a bridge, and the app's QML
calls its methods (bridge.call("state", [])). Optional methods the shell looks for:

    dashboard()        -> [{label, value, detail, tone, view}]       cards on the Dashboard
    settings_files()   -> [{label, path}]                            files to open from Settings
    settings_schema()  -> [{title, help, fields: [...]}]             the app's own Settings section
    settings_values()  -> {"jira.base_url": "...", ...}              what those fields hold now
    set_settings({key: value, ...}) -> {"message"} | {"error"}       save them

An app that offers the last three gets its settings edited in the window instead of by hand. Each
field is {"key", "label", "help", "kind"} plus what its kind needs:

    text      optional "placeholder"
    number    "min", "max", optional "step" and "unit"
    bool      -
    choice    "options": [{"value", "label"}]
    list      a list of strings, one per line in the window
    time      24-hour "HH:MM"
    secret    a password or token. Write-only: settings_values returns "saved" or "", never the
              secret, so it is never in the window's memory or a screenshot. set_settings gets the
              new secret as text, "" to leave the saved one alone, or None for "forget it". The app
              puts it somewhere made for secrets (asgard.secrets), never in its settings file.

Rules the shell relies on:
- The keys are dotted paths into the app's own settings file, and the app decides what is editable.
  Anything it leaves out (Odin's rules and templates, say) stays a file-only setting.
- set_settings validates through the app's own validator and writes nothing if it refuses, so the
  message can be shown and the control put back. It may be given one key or several.
- Reading is cheap and may be called on every repaint; set_settings is called when a control is
  used, so it does the writing.

Rules it keeps:
- A broken manifest skips that app with a warning; the window still opens for the others.
- Views must be QML files inside the app's own ui folder, so a manifest can't load code from
  anywhere else.
- Discovery reads JSON only; nothing is imported until a view is opened.

Standard library only.
"""
from __future__ import annotations

import importlib
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

CODE_ROOT = Path(__file__).resolve().parent.parent.parent      # the folder holding asgard/ and apps/
_ID = re.compile(r"^[a-z][a-z0-9]{0,31}$")
_BACKEND = re.compile(r"^[A-Za-z_][\w.]*:[A-Za-z_]\w*$")


class RegistryError(ValueError):
    """A manifest can't be used. The message names the file and what to fix."""


@dataclass(frozen=True)
class View:
    app: str
    id: str
    title: str
    icon: str
    qml: Path

    @property
    def key(self) -> str:
        return f"{self.app}:{self.id}"


@dataclass
class AppUI:
    id: str
    name: str
    subtitle: str
    folder: Path                 # apps/<id>
    theme: Dict[str, Any] = field(default_factory=dict)
    backend: Optional[str] = None
    views: List[View] = field(default_factory=list)

    def load_backend(self) -> Any:
        """Import and create the backend object, or None if the app has none."""
        if not self.backend:
            return None
        module_name, attr = self.backend.split(":")
        if str(self.folder) not in sys.path:
            sys.path.insert(0, str(self.folder))
        module = importlib.import_module(module_name)
        return getattr(module, attr)()


def discover(root: Optional[Path] = None, only: Optional[str] = None) -> Tuple[List[AppUI], List[str]]:
    """Apps with a ui/manifest.json, in folder order. Returns (apps, warnings)."""
    root = Path(root) if root else CODE_ROOT
    found: List[AppUI] = []
    warnings: List[str] = []
    apps_dir = root / "apps"
    if not apps_dir.is_dir():
        return found, warnings
    for manifest in sorted(apps_dir.glob("*/ui/manifest.json")):
        try:
            app = parse_manifest(manifest)
        except RegistryError as exc:
            warnings.append(str(exc))
            continue
        if only and app.id != only:
            continue
        found.append(app)
    if only and not found:
        warnings.append(f'No app "{only}" has views (looked for apps/{only}/ui/manifest.json).')
    return found, warnings


def parse_manifest(path: Path) -> AppUI:
    where = f"{path.parent.parent.name}/ui/{path.name}"
    try:
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        raise RegistryError(f"{where} can't be read ({exc}); that app's views are hidden.") from None
    if not isinstance(raw, dict):
        raise RegistryError(f"{where} must hold a JSON object; that app's views are hidden.")
    app_id = raw.get("app")
    if not isinstance(app_id, str) or not _ID.match(app_id):
        raise RegistryError(f'{where}: "app" must be the app\'s lower-case id, like "heimdall".')
    if app_id != path.parent.parent.name:
        raise RegistryError(f'{where}: "app" is "{app_id}" but the folder is "{path.parent.parent.name}".')
    backend = raw.get("backend")
    if backend is not None and (not isinstance(backend, str) or not _BACKEND.match(backend)):
        raise RegistryError(f'{where}: "backend" must look like "package.module:ClassName".')
    theme = raw.get("theme", {})
    if not isinstance(theme, dict):
        raise RegistryError(f'{where}: "theme" must be an object like {{"accent": "#5B4B9A"}}.')

    ui_dir = path.parent.resolve()
    views: List[View] = []
    seen = set()
    raw_views = raw.get("views")
    if not isinstance(raw_views, list) or not raw_views:
        raise RegistryError(f'{where}: "views" must list at least one view.')
    for i, v in enumerate(raw_views, 1):
        if not isinstance(v, dict):
            raise RegistryError(f"{where}: view {i} must be an object.")
        vid, title, qml = v.get("id"), v.get("title"), v.get("qml")
        if not isinstance(vid, str) or not _ID.match(vid):
            raise RegistryError(f'{where}: view {i} needs a lower-case "id".')
        if vid in seen:
            raise RegistryError(f'{where}: two views have the id "{vid}".')
        seen.add(vid)
        if not isinstance(title, str) or not title.strip():
            raise RegistryError(f'{where}: view "{vid}" needs a "title".')
        if not isinstance(qml, str) or not qml.endswith(".qml"):
            raise RegistryError(f'{where}: view "{vid}" needs a "qml" file name.')
        file = (ui_dir / qml).resolve()
        if ui_dir not in file.parents:
            raise RegistryError(f'{where}: view "{vid}" must be a QML file inside the app\'s ui folder.')
        if not file.is_file():
            raise RegistryError(f'{where}: view "{vid}" names {qml}, which doesn\'t exist.')
        icon = str(v.get("icon") or title[:2]).strip()[:2]
        views.append(View(app=app_id, id=vid, title=title.strip(), icon=icon, qml=file))

    return AppUI(id=app_id, name=str(raw.get("name") or app_id.title()), subtitle=str(raw.get("subtitle") or ""),
                 folder=path.parent.parent.resolve(), theme=theme, backend=backend, views=views)
