"""Heimdall's views in Asgard's shared window, as plain Python: the shell's bridge calls these.

Every method returns JSON-like data (dicts, lists, text). Problems a person can fix raise
FormError or TemplateError (both ValueError), which the window shows as a message.

Rules it keeps:
- Same files and the same rules as the command line: it calls form.py and templates.py, never
  its own copy of them, so the window and `asgard heimdall template ...` can't disagree.
- fill runs the command line in a child process (fill_command), so Playwright and Edge never
  load into the window, and a fill behaves exactly as `asgard heimdall fill -t NAME` does.
- It checks the plan before handing over the command, so a missing attachment or required
  field is reported in the window instead of after Edge opens.
- Template variables travel to the fill as --var NAME=VALUE. A library pick travels as the
  entry's name; its value is looked up again in the child, so it never sits on a command line.
- A library is listed and searched by name and description only (library.Entry), never by value.

Standard library only.
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import auth, browser
from . import form as forms
from . import library as libs
from . import templates as tpl
from .form import FormError

CLI = Path(__file__).resolve().parent.parent / "cli.py"


class Backend:
    def __init__(self, form_path: Optional[Path] = None, templates_path: Optional[Path] = None,
                 libraries_path: Optional[Path] = None) -> None:
        self.form_path = Path(form_path) if form_path else None
        self.templates_path = Path(templates_path) if templates_path else None
        self.libraries_path = Path(libraries_path) if libraries_path else None

    def _libraries(self) -> libs.Store:
        return libs.Store(self.libraries_path)

    # ---------------------------------------------------------------- reading

    def _form_file(self) -> Path:
        return self.form_path or forms.form_path()

    def _templates_file(self) -> Path:
        return self.templates_path or tpl.templates_path()

    def _form(self) -> forms.Form:
        return forms.load(self._form_file())

    def _store(self) -> tpl.Store:
        return tpl.Store(self._templates_file())

    def state(self) -> Dict[str, Any]:
        """The form, its fields and the saved templates, or what's stopping them loading."""
        out: Dict[str, Any] = {"form": None, "form_error": "", "form_missing": False,
                               "form_path": str(self._form_file()), "templates_path": str(self._templates_file()),
                               "fields": [], "templates": [], "templates_error": ""}
        try:
            form = self._form()
        except FormError as exc:
            out["form_error"] = str(exc)
            out["form_missing"] = not self._form_file().exists()
            return out
        out["form"] = {"instance_url": form.instance_url, "item_url": browser.item_url(form), "ui": form.ui,
                       "ready": form.ready_label or form.ready_selector or "", "field_count": len(form.fields)}
        out["fields"] = [f.to_json() for f in form.fields]
        out.update(self.libraries())
        store = self._store()
        try:
            store.check_usable()
        except tpl.TemplateError as exc:
            out["templates_error"] = str(exc)
            return out
        here = self._libraries()
        for name in store.names():
            saved = store.get(name)
            bad = tpl.problems(form, saved, here)
            out["templates"].append({"name": saved.name, "description": saved.description,
                                     "updated": saved.updated, "fields_set": len(saved.values),
                                     "attachment": saved.attachment or "", "problems": bad,
                                     "variables": 0 if bad else len(tpl.variables_of(form, saved))})
        return out

    def libraries(self) -> Dict[str, Any]:
        """The libraries here, or why none can be kept on this computer."""
        store = self._libraries()
        rows: List[Dict[str, Any]] = []
        problems: List[str] = []
        for name in store.names():
            try:
                rows.append(store.load(name).info())
            except libs.LibraryError as exc:
                problems.append(str(exc))
        unavailable = ""
        if store.protector is None:
            unavailable = ("Libraries are kept encrypted with Windows DPAPI, so this computer can't hold them. "
                           f"To try them anyway, start Asgard with {libs.UNPROTECTED_ENV}=1 (stored unencrypted).")
        return {"libraries": rows, "library_problems": problems, "libraries_unavailable": unavailable,
                "libraries_encrypted": store.encrypted, "libraries_path": str(store.folder)}

    def template(self, name: str) -> Dict[str, Any]:
        """One template, field by field, saying where each value comes from."""
        form = self._form()
        saved = self._store().get(name)
        rows: List[Dict[str, Any]] = []
        for f in form.fields:
            value, source = saved.values.get(f.label), "template"
            if value is None:
                value, source = (f.default, "default") if f.default is not None else (None, "none")
            rows.append({"label": f.label, "kind": f.kind, "required": f.required, "value": value,
                         "saved": saved.values.get(f.label), "source": source, "display": show(value)})
        bad = tpl.problems(form, saved, self._libraries())
        asks = [] if bad else [v.to_json() for v in tpl.variables_of(form, saved)]
        return {"name": saved.name, "description": saved.description, "updated": saved.updated,
                "attachment": saved.attachment or "", "fields": rows, "problems": bad, "variables": asks}

    def library_search(self, library: str, query: str, limit: int = 20) -> Dict[str, Any]:
        """Entries for a LIBRARY picker: names and descriptions only, best matches first."""
        lib = self._libraries().load(library)
        hits = lib.search(query or "", len(lib.entries))
        return {"library": lib.name, "total": len(hits),
                "entries": [lib.entry_json(e) for e in hits[:max(1, int(limit))]]}

    def import_library(self, path: str) -> Dict[str, Any]:
        if not str(path or "").strip():
            raise libs.LibraryError("Give the path of the library file to import.")
        lib, before = self._libraries().import_file(Path(str(path).strip().strip('"')))
        verb = "Updated" if before is not None else "Imported"
        return {"name": lib.name, "entries": len(lib.entries), "previous": before,
                "message": f"{verb} the library {lib.name} ({len(lib.entries)} entries)."}

    def remove_library(self, name: str) -> Dict[str, Any]:
        gone = self._libraries().remove(name)
        return {"name": gone, "message": f"Removed the library {gone}."}

    # ---------------------------------------------------------------- changing

    def save_template(self, original: str, data: Dict[str, Any]) -> Dict[str, Any]:
        """Create (original "") or replace a template from the editor.

        data: {"name", "description", "attachment", "values": {label: value}}. A value of None or
        "" means "leave this field as it is", so it isn't stored.
        """
        form = self._form()
        store = self._store()
        values = {k: v for k, v in dict(data.get("values") or {}).items() if v is not None and v != ""}
        if original:
            store.delete(original)          # in memory only until save(); a clash below loses nothing
        saved = store.put(form, str(data.get("name") or ""), values, str(data.get("attachment") or "") or None,
                          str(data.get("description") or ""))
        store.save()
        return {"name": saved.name}

    def delete_template(self, name: str) -> Dict[str, Any]:
        store = self._store()
        gone = store.delete(name)
        store.save()
        return {"name": gone.name}

    def init_form(self) -> Dict[str, Any]:
        target = self._form_file()
        if target.exists():
            raise FormError(f"{target} already exists. Open it to edit it.")
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(forms.EXAMPLE, target)
        return {"path": str(target)}

    # ---------------------------------------------------------------- filling

    def _plan(self, name: str, values: Optional[Dict[str, Any]] = None) -> tpl.Plan:
        form = self._form()
        return tpl.plan(form, self._store().get(name), {}, None, False, given=_given(values),
                        libraries=self._libraries())

    def dry_run(self, name: str, values: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        form = self._form()
        the_plan = self._plan(name, values)
        lines = [f"Would open {browser.item_url(form)}"]
        lines += [f"  {{{var.name}}} {var.label}: {text}" for var, text in the_plan.variables]
        lines += [f"  {f.label}: {show(v)}" for f, v in the_plan.steps]
        lines.append(f"  Attachment: {the_plan.attachment or '(none)'}")
        lines.append("Heimdall stops before Submit; you review and submit in Edge.")
        return {"lines": lines, "plan": the_plan.to_json()}

    def fill_command(self, name: str, values: Optional[Dict[str, Any]] = None) -> List[str]:
        """The command line for a fill, after checking the plan would work."""
        self._plan(name, values)
        argv = [sys.executable, str(CLI)]
        if self.form_path:
            argv += ["--form", str(self.form_path)]
        if self.templates_path:
            argv += ["--templates", str(self.templates_path)]
        if self.libraries_path:
            argv += ["--libraries", str(self.libraries_path)]
        argv += ["fill", "--template", name]
        for var, value in _given(values).items():
            argv += ["--var", f"{var}={value}"]
        return argv

    # ---------------------------------------------------------------- for the shell

    def dashboard(self) -> List[Dict[str, Any]]:
        """Two sides, kept apart: making templates (Templates), and submitting one (Submit a change)."""
        state = self.state()
        if state["form_error"]:
            return [{"label": "Form", "value": "Set up" if state["form_missing"] else "Fix",
                     "detail": "Create the form file" if state["form_missing"] else "The form file has a problem",
                     "tone": "warning", "view": "form"}]
        if state["templates_error"]:
            return [{"label": "Templates", "value": "Fix", "detail": "The templates file has a problem",
                     "tone": "error", "view": "templates"}]
        total = len(state["templates"])
        ready = sum(1 for t in state["templates"] if not t["problems"])
        broken = total - ready
        cards = [{"label": "Templates available", "value": str(total),
                  "detail": "Make and edit them here" if total else "Make one to get started", "view": "templates"},
                 {"label": "Ready to submit", "value": str(ready),
                  "detail": "Fill one into ServiceNow" if ready else "None ready yet", "view": "submit"}]
        if broken:
            cards.append({"label": "Need fixing", "value": str(broken),
                          "detail": "Templates that no longer match the form", "tone": "warning",
                          "view": "templates"})
        return cards

    def settings_files(self) -> List[Dict[str, str]]:
        return [{"label": "Form file", "path": str(self._form_file())},
                {"label": "Templates", "path": str(self._templates_file())}]

    # ---------------------------------------------------------------- settings in the window
    # By key into the form file, except "password", which is never in a file: it goes to Windows
    # Credential Manager through asgard.secrets (heimdall/auth.py). The form's fields stay file-only,
    # because they are a list of objects; Settings offers to open the file for those.
    SETTINGS: List[Dict[str, Any]] = [
        {"title": "The ServiceNow instance", "fields": [
            {"key": "instance_url", "label": "Address", "kind": "text",
             "placeholder": "https://yourinstance.servicenowservices.com",
             "help": "The https:// address of the instance. Heimdall never signs in to anything else."},
            {"key": "catalog_sys_id", "label": "Catalog item", "kind": "text",
             "placeholder": "0123456789abcdef0123456789abcdef",
             "help": "The 32 characters after sysparm_id= or sys_id= in the security change item's address."},
            {"key": "ui", "label": "Which page", "kind": "choice",
             "options": [{"value": "classic", "label": "Classic form"},
                         {"value": "portal", "label": "Service Portal"}]},
        ]},
        {"title": "Signing in",
         "help": "On the workstation Heimdall signs in through Edge with your certificate and PIN, and needs "
                 "neither of these. They are for an instance that wants a name and a password, such as a "
                 "developer instance.", "fields": [
            {"key": "username", "label": "User name", "kind": "text", "placeholder": "Leave empty to use Edge",
             "help": "Kept in the form file. It isn't a secret."},
            {"key": "password", "label": "Password", "kind": "secret",
             "help": "Kept in Windows Credential Manager for your account, never in a settings file, in "
                     "Muninn or in a log. Set the address first, because it is saved per instance."},
            {"key": "channel", "label": "Browser", "kind": "text", "placeholder": "msedge"},
            {"key": "sign_in_timeout_s", "label": "Wait for sign-in", "kind": "number",
             "min": 10, "max": 3600, "unit": "seconds"},
            {"key": "attempts", "label": "Tries per field", "kind": "number", "min": 1, "max": 10},
        ]},
    ]
    SECRETS = ("password",)

    def settings_schema(self) -> List[Dict[str, Any]]:
        return [dict(group) for group in self.SETTINGS]

    def settings_values(self) -> Dict[str, Any]:
        """What each field holds. A secret is never returned: "saved" or "" says whether there is one."""
        values = forms.values(self._form_file())
        out: Dict[str, Any] = {k: v for k, v in values.items()}
        out["password"] = "saved" if auth.status(values["instance_url"], values["username"]).saved else ""
        return {f["key"]: out.get(f["key"], "") for g in self.SETTINGS for f in g["fields"]}

    def set_settings(self, changes: Dict[str, Any]) -> Dict[str, Any]:
        """Save some settings. The form's own validator decides; nothing is written if it refuses."""
        fields = {f["key"]: f for g in self.SETTINGS for f in g["fields"]}
        unknown = sorted(set(changes) - set(fields))
        if unknown:
            return {"error": f"{', '.join(unknown)} isn't a setting the window edits."}
        said = []
        secret_changes = {k: v for k, v in changes.items() if k in self.SECRETS}
        ready: Dict[str, Any] = {}
        for key, value in changes.items():
            if key in self.SECRETS:
                continue
            if fields[key]["kind"] == "number":
                try:
                    value = int(value)
                except (TypeError, ValueError):
                    return {"error": f"{fields[key]['label']} must be a whole number."}
            elif isinstance(value, str):
                value = value.strip()
            ready[key] = value
        if ready:
            try:
                forms.set_values(ready, self._form_file())
            except FormError as exc:
                return {"error": str(exc)}
            said.append("Saved.")
        for key, value in secret_changes.items():
            # "" means leave the saved one alone (the box shows no secret to begin with); None is
            # the Forget button, and anything else is a new secret.
            label = fields[key]["label"]
            values = forms.values(self._form_file())
            try:
                if value is None:
                    said.append(f"{label} removed." if auth.clear_password(values["instance_url"])
                                else f"There was no saved {label.lower()}.")
                elif str(value).strip():
                    host = auth.save_password(values["instance_url"], str(value))
                    said.append(f"{label} saved for {host}.")
            except auth.AuthError as exc:
                return {"error": str(exc)}
        return {"message": " ".join(said) or "Nothing to change."}

    def credentials(self) -> Dict[str, Any]:
        """Who Heimdall would sign in as, and where a password would come from. Never the password."""
        values = forms.values(self._form_file())
        found = auth.status(values["instance_url"], values["username"])
        return {"host": found.host, "username": found.username, "saved": found.saved,
                "where": found.where, "line": found.line()}


def _given(values: Optional[Dict[str, Any]]) -> Dict[str, str]:
    """What the page typed, as --var would give it: text, with checkboxes as true or false."""
    out: Dict[str, str] = {}
    for name, value in dict(values or {}).items():
        if value is None or value == "":
            continue
        out[str(name)] = ("true" if value else "false") if isinstance(value, bool) else str(value)
    return out


def show(value: Any) -> str:
    if value is None:
        return "(left as is)"
    if value is True:
        return "checked"
    if value is False:
        return "unchecked"
    return '""' if value == "" else str(value)
