"""Heimdall's views in Asgard's shared window, as plain Python: the shell's bridge calls these.

Every method returns JSON-like data (dicts, lists, text). Problems a person can fix raise
FormError or TemplateError (both ValueError), which the window shows as a message.

Rules it keeps:
- Same files and the same rules as the command line: it calls form.py and templates.py, never
  its own copy of them, so the window and `heimdall template ...` can't disagree.
- fill runs the command line in a child process (fill_command), so Playwright and Edge never
  load into the window, and a fill behaves exactly as `heimdall fill -t NAME` does.
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

from . import browser
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
        state = self.state()
        if state["form_error"]:
            return [{"label": "Form", "value": "Set up" if state["form_missing"] else "Fix",
                     "detail": "Create the form file" if state["form_missing"] else "The form file has a problem",
                     "tone": "warning", "view": "form"}]
        if state["templates_error"]:
            return [{"label": "Templates", "value": "Fix", "detail": "The templates file has a problem",
                     "tone": "error", "view": "templates"}]
        broken = sum(1 for t in state["templates"] if t["problems"])
        cards = [{"label": "Templates", "value": str(len(state["templates"])),
                  "detail": "Ready to fill" if state["templates"] else "Save one to get started", "view": "templates"},
                 {"label": "Form fields", "value": str(state["form"]["field_count"]),
                  "detail": state["form"]["instance_url"], "view": "form"}]
        if broken:
            cards.insert(1, {"label": "Need fixing", "value": str(broken),
                             "detail": "Templates that no longer match the form", "tone": "warning",
                             "view": "templates"})
        return cards

    def settings_files(self) -> List[Dict[str, str]]:
        return [{"label": "Form file", "path": str(self._form_file())},
                {"label": "Templates", "path": str(self._templates_file())}]


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
