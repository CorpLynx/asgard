"""Template variables: the fill-in-the-blanks language template authors write into field values.

    We need access to {SYSTEM_NAME:TEXTBOX('string'):"System Name"} from
    {SRC_ADDR:LIBRARY('ip_library_12'):'Source'} to {DEST_ADDR:LIBRARY('ip_library_12'):'Destination'}.

A definition is {NAME:FORMAT(args):"Friendly name"}. The arguments and the friendly name are
optional, so {SOURCE_ADDRESS:TEXTBOX} is a complete definition. {NAME} on its own repeats a
variable defined elsewhere in the same template. {{ and }} are a literal brace, the way $$ is a
literal dollar sign. ${today} is still the date placeholder, not a variable.

    CHECKBOX("description")    a checkbox; the description sits beside it. Fills in Yes or No
    TEXTBOX('ip_v4', ...)      typed text, optionally checked: ip_v4, ip_v6, string, bool, int, float.
                               Listing several accepts any of them (TEXTBOX('ip_v4','ip_v6'))
    DROPDOWN('a', 'b', ...)    one of the values listed
    LIBRARY('name')            one entry from a library Heimdall keeps locally (library.py)

Rules it keeps:
- A definition that can't be read fails when the template is saved, naming what to change; a
  typo never reaches the form looking like text.
- One variable means one thing: two definitions of the same name must agree.
- Values people type are inserted exactly as given. Only the template's own text is searched for
  $today and braces, so a value holding "$5" or "{x}" is never misread.
- Every variable but a checkbox needs a value; a blank never reaches the form looking complete.

Standard library only, and no Heimdall imports: form.py and templates.py use it, and so can a
copy of Heimdall that fills forms some other way.
"""
from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass
from string import Template
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple, Union

FORMATS = ("CHECKBOX", "TEXTBOX", "DROPDOWN", "LIBRARY")
VALIDATORS = ("ip_v4", "ip_v6", "string", "bool", "int", "float")
CHECKED, UNCHECKED = "Yes", "No"

_NAME_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_WORD_RE = re.compile(r"[A-Za-z_]+")
_LIBRARY_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\-]{0,63}$")
_INT_RE = re.compile(r"^[+-]?\d+$")
_FLOAT_RE = re.compile(r"^[+-]?(\d+(\.\d*)?|\.\d+)([eE][+-]?\d+)?$")
_TRUE = ("true", "yes", "y", "1", "on", "checked")
_FALSE = ("false", "no", "n", "0", "off", "unchecked")


class VariableError(ValueError):
    """A definition can't be read, or a value doesn't suit its variable. The message says what to fix."""


@dataclass(frozen=True)
class Variable:
    name: str
    kind: str                    # one of FORMATS
    args: Tuple[str, ...] = ()
    label: str = ""

    @property
    def description(self) -> str:
        """The text beside a checkbox."""
        return self.args[0] if self.kind == "CHECKBOX" and self.args else ""

    @property
    def library(self) -> str:
        return self.args[0] if self.kind == "LIBRARY" else ""

    @property
    def required(self) -> bool:
        return self.kind != "CHECKBOX"

    def to_json(self) -> Dict[str, Any]:
        return {"name": self.name, "label": self.label, "kind": self.kind.lower(), "required": self.required,
                "description": self.description, "validators": list(self.args) if self.kind == "TEXTBOX" else [],
                "options": list(self.args) if self.kind == "DROPDOWN" else [], "library": self.library,
                "hint": self.hint()}

    def hint(self) -> str:
        """What to type, in plain words, for a placeholder or a help line."""
        if self.kind == "TEXTBOX":
            words = _describe(self.args or ("string",))
            return words[:1].upper() + words[1:]
        if self.kind == "DROPDOWN":
            return "One of " + ", ".join(self.args)
        if self.kind == "LIBRARY":
            return f"An entry from {self.library}"
        return "Checked or not"

    def definition(self) -> str:
        """The variable written back as a template author would."""
        args = "(" + ", ".join(_quote(a) for a in self.args) + ")" if self.args else ""
        return "{" + f"{self.name}:{self.kind}{args}:{_quote(self.label)}" + "}"


@dataclass(frozen=True)
class Ref:
    """Where a variable's value goes. definition is None for a bare {NAME}."""
    name: str
    definition: Optional[Variable]


Segment = Union[str, Ref]


# ------------------------------------------------------------------ reading a template's text


def parse(text: str) -> List[Segment]:
    """Split text into literal pieces (braces unescaped, $today left for later) and variables."""
    out: List[Segment] = []
    literal: List[str] = []
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if ch == "$":
            # ${today} is a placeholder, not a variable; $$ is a literal dollar, so count the run.
            run = _dollar_run(text, i)
            literal.append(text[i:i + run])
            i += run
            if run % 2 and text.startswith("{", i):
                close = text.find("}", i)
                if close < 0:
                    raise VariableError(f"a ${{ at character {i} is never closed: write ${{today}}, or $$ for a "
                                        "literal dollar sign.")
                literal.append(text[i:close + 1])
                i = close + 1
            continue
        if ch == "{":
            if text.startswith("{{", i):
                literal.append("{")
                i += 2
                continue
            if literal:
                out.append("".join(literal))
                literal = []
            ref, i = _Reader(text, i).read()
            out.append(ref)
            continue
        if ch == "}":
            if text.startswith("}}", i):
                literal.append("}")
                i += 2
                continue
            raise VariableError(f"a lone }} at character {i + 1}: write }}}} for a literal brace.")
        literal.append(ch)
        i += 1
    if literal:
        out.append("".join(literal))
    return out


def has_variables(text: str) -> bool:
    return any(isinstance(s, Ref) for s in parse(text))


def escape(text: str) -> str:
    """Text with its braces doubled, so parse() reads it back unchanged. ${placeholders} stay as they are."""
    out: List[str] = []
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if ch == "$":
            run = _dollar_run(text, i)
            out.append(text[i:i + run])
            i += run
            close = text.find("}", i) if run % 2 and text.startswith("{", i) else -1
            if close >= 0:
                out.append(text[i:close + 1])
                i = close + 1
            continue
        out.append(ch * 2 if ch in "{}" else ch)
        i += 1
    return "".join(out)


def _dollar_run(text: str, i: int) -> int:
    j = i
    while j < len(text) and text[j] == "$":
        j += 1
    return j - i


def literals(segments: Sequence[Segment]) -> List[str]:
    return [s for s in segments if isinstance(s, str)]


def collect(texts: Sequence[Tuple[str, str]]) -> List[Variable]:
    """The variables used across (where, text) pairs, in order of first appearance.

    Raises when a name is defined two different ways, or used with no definition anywhere.
    """
    found: Dict[str, Variable] = {}
    order: List[str] = []
    seen_where: Dict[str, str] = {}
    used: Dict[str, Tuple[str, str]] = {}     # key -> (where first used, the name as written there)
    for where, text in texts:
        try:
            segments = parse(text)
        except VariableError as exc:
            raise VariableError(f"{where}: {exc}") from None
        for s in segments:
            if not isinstance(s, Ref):
                continue
            key = s.name.casefold()
            if key not in used:
                used[key] = (where, s.name)
                order.append(key)
            if s.definition is None:
                continue
            had = found.get(key)
            if had is None:
                found[key] = s.definition
                seen_where[key] = where
            elif had != s.definition:
                raise VariableError(f"{s.name} is defined twice, differently: {had.definition()} in "
                                    f"{seen_where[key]} and {s.definition.definition()} in {where}. "
                                    f"Define it once and write {{{s.name}}} where it's used again.")
    missing = [k for k in order if k not in found]
    if missing:
        where, name = used[missing[0]]
        raise VariableError(f"{where}: {{{name}}} is used but never defined. "
                            "Define it once, like {" + name + ":TEXTBOX:\"Friendly name\"}.")
    return [found[k] for k in order]


# ------------------------------------------------------------------ checking a value


Resolver = Callable[[str, str], str]   # (library, entry) -> the text to fill in; raises VariableError


def check(var: Variable, value: Any, library: Optional[Resolver] = None) -> str:
    """The text a value fills in for var, or VariableError saying what it should be."""
    label = var.label
    if var.kind == "CHECKBOX":
        if value is None or value == "":
            return UNCHECKED
        if isinstance(value, bool):
            return CHECKED if value else UNCHECKED
        word = str(value).strip().lower()
        if word in _TRUE:
            return CHECKED
        if word in _FALSE:
            return UNCHECKED
        raise VariableError(f"{label} is a checkbox: use true or false, not {value!r}.")
    if isinstance(value, (dict, list, bool)):
        raise VariableError(f"{label} takes text, not {value!r}.")
    text = "" if value is None else str(value).strip()
    if not text:
        raise VariableError(f"{label} needs a value.")
    if var.kind == "TEXTBOX":
        rules = var.args or ("string",)
        if not any(_VALID[r](text) for r in rules):
            raise VariableError(f"{label} must be {_describe(rules)}; {text!r} isn't.")
        return text
    if var.kind == "DROPDOWN":
        for option in var.args:
            if option.casefold() == text.casefold():
                return option
        raise VariableError(f"{label} must be one of: {', '.join(var.args)}; {text!r} isn't.")
    if library is None:
        raise VariableError(f"{label} comes from the library {var.library}, which isn't available here.")
    return library(var.library, text)


def resolve(variables: Sequence[Variable], given: Dict[str, Any],
            library: Optional[Resolver] = None) -> Dict[str, str]:
    """Each variable's text, keyed by name as defined. Raises naming every variable that's missing."""
    by_key = {v.name.casefold(): v for v in variables}
    unknown = [k for k in given if k.casefold() not in by_key]
    if unknown:
        known = ", ".join(v.name for v in variables) or "none"
        raise VariableError(f"{unknown[0]} isn't a variable in this template (its variables: {known}).")
    values = {k.casefold(): v for k, v in given.items()}
    missing = [v for v in variables if v.required and _blank(values.get(v.name.casefold()))]
    if missing:
        raise VariableError("Give a value for " + ", ".join(f"{v.label} ({v.name})" for v in missing) + ".")
    return {v.name: check(v, values.get(v.name.casefold()), library) for v in variables}


def render(text: str, filled: Dict[str, str], placeholders: Dict[str, str]) -> str:
    """The text with $today and friends filled into its own words, and each variable's value as given."""
    by_key = {k.casefold(): v for k, v in filled.items()}
    out: List[str] = []
    for s in parse(text):
        if isinstance(s, Ref):
            out.append(by_key[s.name.casefold()])
        else:
            out.append(Template(s).substitute(placeholders))
    return "".join(out)


def valid_name(name: Any) -> bool:
    """Whether name could be a variable's name (letters, digits and underscores, not starting with a digit)."""
    return isinstance(name, str) and bool(_NAME_RE.fullmatch(name.strip()))


def parse_assignments(items: Sequence[str]) -> Dict[str, str]:
    """--var "NAME=value" pairs into a dict. Splits on the first '=' so values may contain one."""
    out: Dict[str, str] = {}
    for item in items:
        name, sep, value = item.partition("=")
        if not sep or not _NAME_RE.fullmatch(name.strip()):
            raise VariableError(f'--var needs NAME=VALUE, like --var "SYSTEM_NAME=Payroll"; got {item!r}.')
        out[name.strip()] = value
    return out


# ------------------------------------------------------------------ the reader


class _Reader:
    """Reads one {...} starting at text[start]. Whitespace between the parts is allowed."""

    def __init__(self, text: str, start: int) -> None:
        self.text, self.start, self.i = text, start, start + 1

    def fail(self, what: str) -> VariableError:
        snippet = self.text[self.start:self.start + 40]
        return VariableError(f"{what} in {snippet!r}. Variables look like {{NAME:TEXTBOX:\"Friendly name\"}}; "
                             "write {{ for a literal brace.")

    def peek(self) -> str:
        return self.text[self.i] if self.i < len(self.text) else ""

    def skip(self) -> None:
        while self.peek() in (" ", "\t"):
            self.i += 1

    def expect(self, ch: str, what: str) -> None:
        self.skip()
        if self.peek() != ch:
            raise self.fail(what)
        self.i += 1

    def word(self, pattern: "re.Pattern[str]") -> str:
        self.skip()
        m = pattern.match(self.text, self.i)
        if not m:
            return ""
        self.i = m.end()
        return m.group(0)

    def string(self) -> str:
        self.skip()
        quote = self.peek()
        if quote not in ("'", '"'):
            raise self.fail("expected a quoted value")
        self.i += 1
        out: List[str] = []
        while True:
            ch = self.peek()
            if not ch:
                raise self.fail(f"a {quote} that is never closed")
            self.i += 1
            if ch == "\\" and self.peek() in ("\\", "'", '"'):
                out.append(self.peek())
                self.i += 1
            elif ch == quote:
                return "".join(out)
            else:
                out.append(ch)

    def read(self) -> Tuple[Ref, int]:
        name = self.word(_NAME_RE)
        if not name:
            raise self.fail("a variable needs a NAME of letters, digits and _ first")
        self.skip()
        if self.peek() == "}":
            return Ref(name, None), self.i + 1
        self.expect(":", f"expected : or }} after {name}")
        kind = self.word(_WORD_RE).upper()
        if kind not in FORMATS:
            raise self.fail(f"{name}: the format must be one of {', '.join(FORMATS)}")
        self.skip()
        if self.peek() == ":" and self.text[self.i + 1:].lstrip(" \t").startswith("("):
            raise self.fail(f"{name}: write {kind}('...') with no colon before the bracket")
        args: List[str] = []
        if self.peek() == "(":
            self.i += 1
            self.skip()
            if self.peek() != ")":
                while True:
                    args.append(self.string())
                    self.skip()
                    if self.peek() == ",":
                        self.i += 1
                        continue
                    break
            self.expect(")", f"{name}: expected , or ) in the list")
        self.skip()
        label = ""
        if self.peek() == ":":
            self.i += 1
            label = self.string().strip()
        self.expect("}", f"{name}: expected }} to close it")
        var = Variable(name=name, kind=kind, args=tuple(a.strip() for a in args) if kind != "CHECKBOX" else tuple(args),
                       label=label or _friendly(name))
        _check_definition(var, self.fail)
        return Ref(name, var), self.i


def _check_definition(var: Variable, fail: Callable[[str], VariableError]) -> None:
    n, args = var.name, var.args
    if var.kind == "CHECKBOX" and len(args) > 1:
        raise fail(f'{n}: CHECKBOX takes one description at most, like CHECKBOX("Emergency change")')
    if var.kind == "TEXTBOX":
        bad = [a for a in args if a not in VALIDATORS]
        if bad:
            raise fail(f"{n}: {bad[0]!r} isn't a check Heimdall knows ({', '.join(VALIDATORS)})")
    if var.kind == "DROPDOWN":
        if not args or any(not a for a in args):
            raise fail(f"{n}: DROPDOWN needs the values to offer, like DROPDOWN('prod', 'test')")
        folded = [a.casefold() for a in args]
        if len(set(folded)) != len(folded):
            raise fail(f"{n}: DROPDOWN lists the same value twice")
    if var.kind == "LIBRARY":
        if len(args) != 1 or not _LIBRARY_NAME_RE.match(args[0]):
            raise fail(f"{n}: LIBRARY needs one library name, like LIBRARY('ip_library_12')")


# ------------------------------------------------------------------ small helpers


def _ip(version: int) -> Callable[[str], bool]:
    def ok(text: str) -> bool:
        try:
            return ipaddress.ip_address(text).version == version
        except ValueError:
            return False
    return ok


_VALID: Dict[str, Callable[[str], bool]] = {
    "ip_v4": _ip(4),
    "ip_v6": _ip(6),
    "string": lambda t: bool(t),
    "bool": lambda t: t.lower() in _TRUE + _FALSE,
    "int": lambda t: bool(_INT_RE.match(t)),
    "float": lambda t: bool(_FLOAT_RE.match(t)),
}
_SAYS = {"ip_v4": "an IPv4 address", "ip_v6": "an IPv6 address", "string": "text", "bool": "true or false",
         "int": "a whole number", "float": "a number"}


def _describe(rules: Sequence[str]) -> str:
    words = [_SAYS[r] for r in rules]
    return words[0] if len(words) == 1 else ", ".join(words[:-1]) + " or " + words[-1]


def _blank(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def _friendly(name: str) -> str:
    words = name.replace("_", " ").split()
    return " ".join(words).capitalize() if words else name


def _quote(text: str) -> str:
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'
