"""Heimdall's command line. Every command a UI needs has a --json form with a stable shape.

    heimdall init                       create the form file from the example
    heimdall fields                     list the form's fields
    heimdall template list | show | save | edit | delete
    heimdall library import | list | show | remove
    heimdall fill [--template NAME] [--var NAME=VALUE]   open Edge, fill the form, stop before Submit

Exit codes: 0 done, 1 an expected problem (the message says what to do), 2 usage, 130 stopped.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path
from typing import Any, Optional, Sequence

from . import browser   # imports Playwright only inside browser.run()
from . import form as forms
from . import library as libs
from . import templates as tpl
from . import variables as tvars
from .form import Form, FormError
from .library import LibraryError
from .templates import Plan, Store, TemplateError
from .variables import VariableError

EXPECTED = (FormError, TemplateError, LibraryError, VariableError)


def _say(text: str) -> None:
    print(text, flush=True)


def _emit(data: Any) -> None:
    print(json.dumps(data, indent=2, ensure_ascii=False))


def _show_value(value: Any) -> str:
    if value is None:
        return "(left as is)"
    if value is True:
        return "checked"
    if value is False:
        return "unchecked"
    return '""' if value == "" else str(value)


def _load_form(args: argparse.Namespace) -> Form:
    return forms.load(args.form)


def _store(args: argparse.Namespace) -> Store:
    return Store(args.templates)


def _libraries(args: argparse.Namespace) -> libs.Store:
    return libs.Store(args.libraries)


# ------------------------------------------------------------------ commands


def cmd_init(args: argparse.Namespace) -> int:
    target = Path(args.form) if args.form else forms.form_path()
    if target.exists() and not args.force:
        raise FormError(f"{target} already exists. Edit it, or run 'heimdall init --force' to start again "
                        "from the example (your templates are kept).")
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(forms.EXAMPLE, target)
    _say(f"Created {target}.")
    _say("Edit instance_url, catalog_sys_id and the fields to match the security change form, then run 'heimdall fields'.")
    return 0


def cmd_fields(args: argparse.Namespace) -> int:
    form = _load_form(args)
    if args.json:
        _emit({"form": str(form.path), "item_url": _item_url(form),
               "fields": [f.to_json() for f in form.fields]})
        return 0
    _say(f"Form: {form.path}")
    for f in form.fields:
        extras = []
        if f.required:
            extras.append("required")
        if f.default is not None:
            extras.append(f"default {_show_value(f.default)}")
        tail = f"  ({', '.join(extras)})" if extras else ""
        _say(f"  {f.label}  [{f.kind}]{tail}")
    return 0


def cmd_template_list(args: argparse.Namespace) -> int:
    form = _load_form(args)
    store = _store(args)
    store.check_usable()
    rows = []
    for name in store.names():
        saved = store.get(name)
        rows.append({"name": saved.name, "description": saved.description, "updated": saved.updated,
                     "fields_set": len(saved.values), "attachment": saved.attachment,
                     "problems": tpl.problems(form, saved)})
    if args.json:
        _emit({"templates": rows})
        return 0
    if not rows:
        _say("No templates yet. Save one with: heimdall template save NAME --set \"LABEL=VALUE\" ...")
        return 0
    for row in rows:
        note = f"  - {row['description']}" if row["description"] else ""
        flag = "  [needs fixing]" if row["problems"] else ""
        _say(f"{row['name']}  ({row['fields_set']} of {len(form.fields)} fields){note}{flag}")
    return 0


def cmd_template_show(args: argparse.Namespace) -> int:
    form = _load_form(args)
    saved = _store(args).get(args.name)
    bad = tpl.problems(form, saved, _libraries(args))
    defined = [] if bad else tpl.variables_of(form, saved)
    if args.json:
        _emit({"name": saved.name, "description": saved.description, "updated": saved.updated,
               "attachment": saved.attachment, "values": saved.values, "problems": bad,
               "variables": [v.to_json() for v in defined]})
        return 0
    _say(f"{saved.name}" + (f"  - {saved.description}" if saved.description else ""))
    for f in form.fields:
        value = saved.values.get(f.label)
        source = ""
        if value is None and f.default is not None:
            value, source = f.default, "  (form default)"
        _say(f"  {f.label}: {_show_value(value)}{source}")
    _say(f"  Attachment: {saved.attachment or '(none)'}")
    if defined:
        _say("  Asks for (give each with --var NAME=VALUE when you fill):")
        for v in defined:
            _say(f"    {v.name}: {v.label}, {_describe_variable(v)}")
    for problem in bad:
        _say(f"  Problem: {problem}")
    return 1 if bad else 0


def _describe_variable(v: tvars.Variable) -> str:
    if v.kind == "CHECKBOX":
        return "true or false" + (f' ("{v.description}")' if v.description else "")
    if v.kind == "TEXTBOX":
        return "text" + (f" ({', '.join(v.args)})" if v.args else "")
    if v.kind == "DROPDOWN":
        return "one of " + ", ".join(v.args)
    return f"an entry from the library {v.library}"


def cmd_template_save(args: argparse.Namespace) -> int:
    form = _load_form(args)
    store = _store(args)
    saved = store.put(form, args.name, tpl.parse_assignments(args.set), args.attachment,
                      args.description or "", replace=args.replace)
    store.save()
    _say(f'Saved template "{saved.name}" ({len(saved.values)} of {len(form.fields)} fields).')
    return 0


def cmd_template_edit(args: argparse.Namespace) -> int:
    form = _load_form(args)
    store = _store(args)
    if not (args.set or args.unset or args.attachment or args.no_attachment
            or args.description is not None or args.rename):
        raise TemplateError("Nothing to change. Use --set, --unset, --attachment, --no-attachment, "
                            "--description or --rename.")
    saved = store.edit(form, args.name, tpl.parse_assignments(args.set), args.unset,
                       attachment=args.attachment, clear_attachment=args.no_attachment,
                       description=args.description, rename=args.rename)
    store.save()
    _say(f'Updated template "{saved.name}".')
    return 0


def cmd_template_delete(args: argparse.Namespace) -> int:
    store = _store(args)
    gone = store.delete(args.name)
    store.save()
    _say(f'Deleted template "{gone.name}".')
    return 0


def cmd_fill(args: argparse.Namespace) -> int:
    form = _load_form(args)
    saved = _store(args).get(args.template) if args.template else None
    overrides = tpl.parse_assignments(args.set)
    given = tvars.parse_assignments(args.var)
    if not given and (saved is None or not tpl.problems(form, saved)):    # plan() explains a drifted template
        asks = tpl.variables_of(form, saved, overrides)
        if any(v.required for v in asks):
            hint = " ".join(f'--var "{v.name}=..."' for v in asks if v.required)
            raise TemplateError(f"This template asks for {', '.join(v.label for v in asks if v.required)}. "
                                f"Give them with {hint} ('heimdall template show NAME' says what each takes).")
    the_plan = tpl.plan(form, saved, overrides, args.attachment, args.no_attachment, given=given,
                        libraries=_libraries(args))
    if args.dry_run:
        if args.json:
            body = the_plan.to_json()
            body["item_url"] = _item_url(form)
            _emit(body)
        else:
            _print_plan(form, the_plan)
        return 0

    try:
        browser.run(form, the_plan, lambda m: _say(f"heimdall: {m}"), trace=args.trace)
    except browser.BrowserError as exc:
        print(f"Heimdall: {exc}", file=sys.stderr)
        return 1
    return 0


def cmd_library_import(args: argparse.Namespace) -> int:
    store = _libraries(args)
    lib, before = store.import_file(args.file)
    what = "Updated" if before is not None else "Imported"
    was = f", was {before}" if before is not None else ""
    _say(f"{what} the library {lib.name}: {len(lib.entries)} entries{was}"
         + (f", version {lib.version}" if lib.version else "") + ".")
    if lib.renders == "name":
        _say("It fills in entry names; Heimdall didn't keep the values.")
    if store.encrypted:
        _say(f"Heimdall keeps its own encrypted copy, so you can delete {args.file}.")
    else:
        _say(f"Stored UNENCRYPTED ({libs.UNPROTECTED_ENV}=1). Use this only to try Heimdall out.")
    return 0


def cmd_library_list(args: argparse.Namespace) -> int:
    store = _libraries(args)
    rows = [store.load(name).info() for name in store.names()]
    if args.json:
        _emit({"libraries": rows})
        return 0
    if not rows:
        _say("No libraries yet. Import one with: heimdall library import FILE")
        return 0
    for row in rows:
        note = f"  - {row['description']}" if row["description"] else ""
        version = f", version {row['version']}" if row["version"] else ""
        _say(f"{row['name']}  ({row['entries']} entries, fills in the {row['renders']}{version}){note}")
    return 0


def cmd_library_show(args: argparse.Namespace) -> int:
    if args.limit < 1:
        raise LibraryError("--limit must be 1 or more.")
    lib = _libraries(args).load(args.name)
    matches = lib.search(args.search, len(lib.entries)) if args.search else list(lib.entries)
    entries = matches[:args.limit]
    if args.json:
        body = lib.info()
        body["shown"] = [lib.entry_json(e) for e in entries]
        _emit(body)
        return 0
    _say(f"{lib.name}" + (f"  - {lib.description}" if lib.description else ""))
    _say(f"  {len(lib.entries)} entries; a pick fills in the entry's {lib.renders}. Imported {lib.imported_at}"
         + (f" from {lib.source}" if lib.source else "") + ".")
    for e in entries:
        _say(f"  {e.name}" + (f"  - {e.description}" if e.description else ""))
    hidden = len(matches) - len(entries)
    if hidden > 0:
        _say(f"  ... and {hidden} more. Narrow it with --search TEXT, or show more with --limit N.")
    if args.search and not entries:
        _say("  Nothing matches. Searches look at names and descriptions.")
    return 0


def cmd_library_remove(args: argparse.Namespace) -> int:
    gone = _libraries(args).remove(args.name)
    _say(f"Removed the library {gone}. Templates that use it can't be filled until it's imported again.")
    return 0


def _print_plan(form: Form, the_plan: Plan) -> None:
    _say(f"Would open {_item_url(form)}")
    _say(f"Template: {the_plan.template or '(none; form defaults and --set only)'}")
    for var, text in the_plan.variables:
        _say(f"  {{{var.name}}} {var.label}: {text}")
    for f, value in the_plan.steps:
        _say(f"  {f.label}: {_show_value(value)}")
    _say(f"  Attachment: {the_plan.attachment or '(none)'}")
    _say("Heimdall stops before Submit; you review and submit in Edge.")


def _item_url(form: Form) -> str:
    return browser.item_url(form)


# ------------------------------------------------------------------ parser


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="heimdall", description="Heimdall: fill security change submissions from templates.")
    parser.add_argument("--form", type=Path, help="form file (default: settings\\heimdall.json under Asgard's data folder)")
    parser.add_argument("--templates", type=Path, help="templates file (default: settings\\heimdall-templates.json)")
    parser.add_argument("--libraries", type=Path, help="libraries folder (default: heimdall\\libraries)")
    sub = parser.add_subparsers(dest="command", metavar="command")

    def add(group: Any, name: str, func: Any, help_text: str) -> argparse.ArgumentParser:
        p = group.add_parser(name, help=help_text, description=help_text)
        p.set_defaults(func=func)
        return p

    def setting(p: argparse.ArgumentParser) -> None:
        p.add_argument("--set", action="append", default=[], metavar="LABEL=VALUE",
                       help="a field value; repeat for each field. Checkboxes take true or false")

    def json_flag(p: argparse.ArgumentParser) -> None:
        p.add_argument("--json", action="store_true", help="machine-readable output for a UI")

    p = add(sub, "init", cmd_init, "Create the form file from the example")
    p.add_argument("--force", action="store_true", help="replace an existing form file")

    json_flag(add(sub, "fields", cmd_fields, "List the form's fields"))

    t = sub.add_parser("template", help="List, show, save, edit or delete templates",
                       description="Templates are saved combinations of field values.")
    t.set_defaults(func=None, group=t)
    tsub = t.add_subparsers(dest="template_command", metavar="action")

    json_flag(add(tsub, "list", cmd_template_list, "List saved templates"))

    p = add(tsub, "show", cmd_template_show, "Show a template's values for every field")
    p.add_argument("name")
    json_flag(p)

    p = add(tsub, "save", cmd_template_save, "Save a new template")
    p.add_argument("name")
    setting(p)
    p.add_argument("--attachment", metavar="PATH", help="file to attach (checked when you fill)")
    p.add_argument("--description", metavar="TEXT")
    p.add_argument("--replace", action="store_true", help="overwrite a template with this name")

    p = add(tsub, "edit", cmd_template_edit, "Change part of a template")
    p.add_argument("name")
    setting(p)
    p.add_argument("--unset", action="append", default=[], metavar="LABEL", help="stop setting this field")
    group = p.add_mutually_exclusive_group()
    group.add_argument("--attachment", metavar="PATH")
    group.add_argument("--no-attachment", action="store_true", help="remove the attachment")
    p.add_argument("--description", metavar="TEXT")
    p.add_argument("--rename", metavar="NEW_NAME")

    p = add(tsub, "delete", cmd_template_delete, "Delete a template")
    p.add_argument("name")

    lib = sub.add_parser("library", help="Import, list, show or remove the libraries LIBRARY variables pick from",
                         description="Libraries are named lists, kept encrypted on this computer.")
    lib.set_defaults(func=None, group=lib)
    lsub = lib.add_subparsers(dest="library_command", metavar="action")
    p = add(lsub, "import", cmd_library_import, "Import a published library file, or update one already here")
    p.add_argument("file", type=Path)
    json_flag(add(lsub, "list", cmd_library_list, "List the libraries on this computer"))
    p = add(lsub, "show", cmd_library_show, "Show a library's entries (names and descriptions)")
    p.add_argument("name")
    p.add_argument("--search", metavar="TEXT", help="only entries whose name or description holds TEXT")
    p.add_argument("--limit", type=int, default=50, metavar="N", help="show at most N entries (default 50)")
    json_flag(p)
    p = add(lsub, "remove", cmd_library_remove, "Remove a library from this computer")
    p.add_argument("name")

    p = add(sub, "fill", cmd_fill, "Open Edge, fill the form from a template, and stop before Submit")
    p.add_argument("--template", "-t", metavar="NAME")
    setting(p)
    p.add_argument("--var", action="append", default=[], metavar="NAME=VALUE",
                   help="a value for one of the template's variables; repeat for each")
    group = p.add_mutually_exclusive_group()
    group.add_argument("--attachment", metavar="PATH", help="attach this file instead of the template's")
    group.add_argument("--no-attachment", action="store_true", help="don't attach anything this time")
    p.add_argument("--dry-run", action="store_true", help="show what would be filled; don't open Edge")
    p.add_argument("--trace", action="store_true", help="record a Playwright trace (holds session data)")
    json_flag(p)
    return parser


def _tolerant_output() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")   # type: ignore[union-attr]
        except (AttributeError, ValueError, OSError):
            pass


def main(argv: Optional[Sequence[str]] = None) -> int:
    _tolerant_output()
    parser = build_parser()
    args = parser.parse_args(argv)
    func = getattr(args, "func", None)
    if func is None:
        (getattr(args, "group", None) or parser).print_help()
        return 2
    if getattr(args, "json", False) and func is cmd_fill and not args.dry_run:
        print("Heimdall: --json works with --dry-run only.", file=sys.stderr)
        return 2
    try:
        return int(func(args) or 0)
    except EXPECTED as exc:
        print(f"Heimdall: {exc}", file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"Heimdall: couldn't read or write {exc.filename or 'a file'} ({exc.strerror}).", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Heimdall: stopped.", file=sys.stderr)
        return 130
