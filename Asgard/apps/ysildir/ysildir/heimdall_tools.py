"""Ysildir's Heimdall tools: what the person's security change templates and libraries hold, and what a
fill would type. Read-only, and all off until the person switches them on.

Heimdall's own code answers (templates.py, variables.py, library.py), the way `asgard heimdall ... --json`
does, so a rule is Heimdall's whichever way it's asked. Nothing here fills a form, clicks Submit, or
changes a template or a library: the fill and the Submit click stay with the person
(docs/integration/heimdall.md).

Library values never leave this computer through Ysildir. Libraries hold what the person's organisation
chose to keep from users, and `renders: name` exists for libraries whose values must never be on the
machine at all. So the tools list entries by name and description only, and heimdall_plan resolves a
library variable to the entry's name (Store.resolver(names_only=True)): the plan shows which entry would
be picked, and the form gets its value only when the person runs the fill themselves.

Text from the person's templates and from libraries' publishers is cleaned to one capped line with
credential-shaped text masked, and each answer names it in untrusted_fields.
"""
import functools
from typing import Any, Callable, Dict, Union

from asgard.muninn.baldur import clean_line

from heimdall import form as forms
from heimdall import library as libs
from heimdall import templates as tpl
from heimdall import variables as tvars
from heimdall.form import FormError
from heimdall.library import LibraryError
from heimdall.templates import TemplateError
from heimdall.variables import VariableError

from . import Refused
from .models import (HeimdallLibraries, HeimdallPlan, HeimdallTemplates, LibraryMatch, LibraryName, LibraryQuery,
                     LibraryRow, LibrarySearch, PlanField, PlanValues, PlanVariable, TemplateName, TemplateRow,
                     TemplateVariables, VariableRow)

EXPECTED = (FormError, TemplateError, LibraryError, VariableError)
SEARCH_LIMIT = 200


def refusing(fn: Callable[..., Any]) -> Callable[..., Any]:
    """Heimdall's expected problems (no form file yet, no such template) are refusals with Heimdall's message."""
    @functools.wraps(fn)
    def run(*args: Any, **kwargs: Any) -> Any:
        try:
            return fn(*args, **kwargs)
        except EXPECTED as exc:
            raise Refused(str(exc)) from None
    return run


def _line(text: Any, size: int = 200) -> str:
    return clean_line(str(text or ""), size)


def _variable(v: tvars.Variable) -> VariableRow:
    j = v.to_json()
    return VariableRow(name=j["name"], label=_line(j["label"], 120), kind=j["kind"], required=j["required"],
                       description=_line(j["description"], 200), validators=[_line(x, 40) for x in j["validators"]],
                       options=[_line(x, 120) for x in j["options"][:50]], library=_line(j["library"], 64),
                       hint=_line(j["hint"], 300))


def _template_row(form: forms.Form, saved: tpl.Saved, libraries: libs.Store) -> TemplateRow:
    problems = tpl.problems(form, saved, libraries)
    try:
        defined = [] if tpl.problems(form, saved) else tpl.variables_of(form, saved)
    except TemplateError:
        defined = []
    return TemplateRow(name=_line(saved.name, 80), description=_line(saved.description, 300), updated=saved.updated,
                       fields_set=len(saved.values), has_attachment=bool(saved.attachment),
                       variables=[_variable(v) for v in defined], problems=[_line(p, 300) for p in problems])


@refusing
def heimdall_templates() -> HeimdallTemplates:
    form = forms.load(None)
    store = tpl.Store()
    store.check_usable()
    libraries = libs.Store()
    rows = [_template_row(form, store.get(name), libraries) for name in store.names()]
    return HeimdallTemplates(templates=rows, untrusted_fields=[
        "templates[].name", "templates[].description", "templates[].variables[].label",
        "templates[].variables[].options", "templates[].problems"])


@refusing
def heimdall_template_variables(template: TemplateName) -> TemplateVariables:
    form = forms.load(None)
    saved = tpl.Store().get(template)
    row = _template_row(form, saved, libs.Store())
    return TemplateVariables(template=row.name, variables=row.variables, problems=row.problems,
                             untrusted_fields=["template", "variables[].label", "variables[].options", "problems"])


@refusing
def heimdall_library_list() -> HeimdallLibraries:
    store = libs.Store()
    rows = []
    for name in store.names():
        info = store.load(name).info()
        rows.append(LibraryRow(name=_line(info["name"], 64), description=_line(info["description"], 300),
                               renders=info["renders"], entries=info["entries"], version=_line(info["version"], 40),
                               imported_at=info["imported_at"]))
    return HeimdallLibraries(libraries=rows, untrusted_fields=["libraries[].name", "libraries[].description"])


@refusing
def heimdall_library_search(library: LibraryName, query: LibraryQuery) -> LibrarySearch:
    lib = libs.Store().load(library)
    found = lib.search(query, len(lib.entries))
    matches = [LibraryMatch(name=_line(e.name, 120), description=_line(e.description, 300)) for e in found[:SEARCH_LIMIT]]
    return LibrarySearch(library=lib.name, query=_line(query, 200), total=len(found), matches=matches,
                         untrusted_fields=["matches[].name", "matches[].description"])


@refusing
def heimdall_plan(template: TemplateName, variables: PlanValues = None) -> HeimdallPlan:
    """What a fill of this template would type, for these values. Library entries come back by name."""
    form = forms.load(None)
    saved = tpl.Store().get(template)
    libraries = libs.Store()
    given: Dict[str, Union[str, bool]] = dict(variables or {})
    the_plan = tpl.plan(form, saved, {}, None, False, given=given, libraries=_NamesOnly(libraries))
    body = the_plan.to_json()                                   # the command line's own --json shape
    hidden = any(v["kind"] == "library" for v in body["variables"])
    attachment = body["attachment"].replace("\\", "/").rsplit("/", 1)[-1] if body["attachment"] else None
    return HeimdallPlan(
        template=_line(saved.name, 80),
        fields=[PlanField(label=_line(f["label"], 120), kind=f["kind"], value=_value(f["value"]), action=f["action"])
                for f in body["fields"]],
        variables=[PlanVariable(name=v["name"], label=_line(v["label"], 120), kind=v["kind"],
                                value=_line(v["value"], 500)) for v in body["variables"]],
        attachment=_line(attachment, 120) if attachment else None, library_values_hidden=hidden,
        message="This is what a fill would type; nothing was filled or submitted. The person runs the fill "
                "(asgard heimdall fill -t NAME) and clicks Submit in Edge themselves."
                + (" A library pick is shown as the entry's name; the form gets its value when they run the fill."
                   if hidden else ""),
        untrusted_fields=["template", "fields[].label", "fields[].value", "variables[].label",
                          "variables[].value", "attachment"])


def _value(value: Any) -> Any:
    return _line(value, 1000) if isinstance(value, str) else value


class _NamesOnly:
    """The Store, as templates.plan() uses it, with library entries resolved to their names."""

    def __init__(self, store: libs.Store) -> None:
        self.store = store

    def has(self, name: str) -> bool:
        return self.store.has(name)

    def resolver(self) -> Callable[[str, str], str]:
        return self.store.resolver(names_only=True)
