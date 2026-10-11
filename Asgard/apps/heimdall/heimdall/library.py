"""Libraries: named lists a template's LIBRARY('name') variables pick from, kept encrypted on this computer.

A library is something like a firewall's address groups: entries with a name people pick
("web-servers"), an optional description, and the value that goes into the form
("10.20.0.0/24"). Whoever looks after the list publishes it as a JSON file (in a repository, on a
share); `heimdall library import FILE` reads it and keeps an encrypted copy in
%LOCALAPPDATA%\\Asgard\\heimdall\\libraries\\<name>.library. Importing the same library again
replaces it.

    {"library": "ip_library_12", "description": "Data centre address groups", "renders": "value",
     "version": "2026-10-01",
     "entries": [{"name": "web-servers", "value": "10.20.0.0/24", "description": "DMZ web tier"},
                 {"name": "db-servers", "value": ["10.30.0.5", "10.30.0.6"]}]}

"entries" may also be an object of name to value. A value given as a list is filled in as its
items separated by ", ".

"renders" says what a picked entry fills in: "value" (the default) or "name". A "name" library
is for lists whose values people shouldn't see: Heimdall drops the values when it imports one, so
they are never on this computer at all.

Rules it keeps, and what the encryption is and isn't:
- On Windows the copy is encrypted with DPAPI for the signed-in user (asgard/dpapi.py), so the
  file is unreadable to other users, on other computers and in backups. It is NOT hidden from the
  user it belongs to: anything running as them can decrypt it. Heimdall itself never shows a
  library's values in a list or a search, only the value of an entry someone picked, because that
  value is about to be typed into a form they review.
- Searching matches names and descriptions, never values.
- Elsewhere there's no DPAPI. Libraries are refused unless HEIMDALL_UNPROTECTED_LIBRARIES=1 says
  to store them unencrypted, for trying Heimdall on a development machine. Windows refuses that
  setting and refuses to read an unencrypted library.

Standard library only.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from asgard import dpapi, paths
from asgard.jsonfile import replace_with_retry

MAGIC = b"ASGARD HEIMDALL LIBRARY 1\n"
RENDERS = ("value", "name")
UNPROTECTED_ENV = "HEIMDALL_UNPROTECTED_LIBRARIES"
MAX_BYTES = 20 * 1024 * 1024
MAX_ENTRIES = 50000
_ENTROPY = b"Asgard Heimdall library"
_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\-]{0,63}$")
SUFFIX = ".library"


class LibraryError(ValueError):
    """A library is missing, can't be read, or a file can't be imported. The message says what to do."""


@dataclass(frozen=True)
class Entry:
    name: str
    value: str = ""
    description: str = ""


@dataclass(frozen=True)
class Library:
    name: str
    description: str
    renders: str
    entries: Tuple[Entry, ...]
    version: str = ""
    source: str = ""
    imported_at: str = ""

    def find(self, entry: str) -> Optional[Entry]:
        want = _fold(entry)
        for e in self.entries:
            if _fold(e.name) == want:
                return e
        return None

    def search(self, query: str, limit: int = 50) -> List[Entry]:
        """Entries whose name or description holds every word of query; names that start with it first."""
        words = _fold(query).split()
        hits = [e for e in self.entries if all(w in _fold(e.name + " " + e.description) for w in words)]
        start = _fold(query)
        hits.sort(key=lambda e: (not _fold(e.name).startswith(start), _fold(e.name)))
        return hits[:max(0, limit)]

    def fill(self, entry: str) -> str:
        """The text a picked entry puts in the form, or LibraryError naming close matches."""
        found = self.find(entry)
        if found is None:
            near = ", ".join(e.name for e in self.search(entry, 5))
            raise LibraryError(f'"{entry}" isn\'t in the library {self.name}.'
                               + (f" Did you mean: {near}?" if near else " Search it with 'heimdall library show "
                                  f"{self.name} --search TEXT'."))
        return found.value if self.renders == "value" else found.name

    def info(self) -> Dict[str, Any]:
        return {"name": self.name, "description": self.description, "renders": self.renders,
                "entries": len(self.entries), "version": self.version, "source": self.source,
                "imported_at": self.imported_at}

    def entry_json(self, e: Entry) -> Dict[str, Any]:
        """An entry for a list or a picker: its name and description, never its value."""
        return {"name": e.name, "description": e.description}


# ------------------------------------------------------------------ how files are protected


@dataclass(frozen=True)
class Protector:
    kind: bytes                                  # the second line of the file
    protect: Callable[[bytes], bytes]
    unprotect: Callable[[bytes], bytes]


DPAPI = Protector(b"dpapi\n", lambda data: dpapi.protect(data, "Asgard Heimdall library", _ENTROPY),
                  lambda data: dpapi.unprotect(data, _ENTROPY))
UNPROTECTED = Protector(b"unprotected\n", lambda data: data, lambda data: data)


def default_protector() -> Optional[Protector]:
    """DPAPI on Windows; elsewhere unencrypted only when asked for; otherwise None (refuse)."""
    if dpapi.AVAILABLE:
        return DPAPI
    if os.environ.get(UNPROTECTED_ENV, "").strip() == "1":
        return UNPROTECTED
    return None


def library_dir() -> Path:
    return paths.data_dir() / "heimdall" / "libraries"


# ------------------------------------------------------------------ the store


class Store:
    """The libraries on this computer. Each is read and decrypted once per Store, when first asked for."""

    _DEFAULT: Any = object()

    def __init__(self, folder: Optional[Path] = None, protector: Any = _DEFAULT) -> None:
        self.folder = Path(folder) if folder else library_dir()
        self.protector: Optional[Protector] = default_protector() if protector is Store._DEFAULT else protector
        self._cache: Dict[str, Library] = {}

    @property
    def encrypted(self) -> bool:
        return self.protector is not None and self.protector.kind != UNPROTECTED.kind

    def _path(self, name: str) -> Path:
        return self.folder / (_fold(name) + SUFFIX)

    def names(self) -> List[str]:
        """The libraries here, by the name they're used with (lower case; matching ignores case)."""
        if not self.folder.is_dir():
            return []
        return sorted(p.name[:-len(SUFFIX)] for p in self.folder.iterdir()
                      if p.is_file() and p.name.endswith(SUFFIX))

    def has(self, name: str) -> bool:
        return self._path(name).is_file()

    def load(self, name: str) -> Library:
        key = _fold(name)
        if key in self._cache:
            return self._cache[key]
        path = self._path(name)
        try:
            raw = path.read_bytes()
        except FileNotFoundError:
            have = ", ".join(self.names()) or "none imported yet"
            raise LibraryError(f"No library named {name} on this computer (libraries: {have}). "
                               "Import it with 'heimdall library import FILE'.") from None
        lib = self._decode(raw, path)
        self._cache[key] = lib
        return lib

    def _decode(self, raw: bytes, path: Path) -> Library:
        again = "Import it again with 'heimdall library import FILE'."
        if not raw.startswith(MAGIC):
            raise LibraryError(f"{path} isn't a library Heimdall saved. Delete it, then import the library again.")
        kind, _, body = raw[len(MAGIC):].partition(b"\n")
        kind += b"\n"
        p = self._usable()
        if kind != p.kind:
            said = "unencrypted" if kind == UNPROTECTED.kind else "encrypted with Windows DPAPI"
            raise LibraryError(f"{path} was saved {said}, which this computer doesn't read. {again}")
        try:
            data = json.loads(p.unprotect(body).decode("utf-8"))
        except dpapi.DpapiError as exc:
            raise LibraryError(f"{path} can't be decrypted: {exc} {again}") from None
        except (ValueError, UnicodeDecodeError):
            raise LibraryError(f"{path} is damaged. {again}") from None
        return _from_json(data, str(path))

    def _usable(self) -> Protector:
        if self.protector is None:
            raise LibraryError("Libraries are encrypted with Windows DPAPI, so Heimdall keeps them only on "
                               f"Windows. To try them on another computer, set {UNPROTECTED_ENV}=1; Heimdall then "
                               "stores them unencrypted.")
        if self.protector.kind == UNPROTECTED.kind and os.name == "nt":
            raise LibraryError(f"Heimdall doesn't store libraries unencrypted on Windows. Unset {UNPROTECTED_ENV}.")
        return self.protector

    def import_file(self, source: Path) -> Tuple[Library, Optional[int]]:
        """Read a published library file and keep it encrypted. Returns it and the old entry count, if any."""
        p = self._usable()
        source = Path(source).expanduser()
        try:
            size = source.stat().st_size
            if size > MAX_BYTES:
                raise LibraryError(f"{source} is {size // (1024 * 1024)} MB; libraries can be up to "
                                   f"{MAX_BYTES // (1024 * 1024)} MB.")
            raw = json.loads(source.read_text(encoding="utf-8-sig"))
        except FileNotFoundError:
            raise LibraryError(f"No file at {source}.") from None
        except json.JSONDecodeError as exc:
            raise LibraryError(f"{source} is not valid JSON (line {exc.lineno}): {exc.msg}.") from None
        except UnicodeDecodeError:
            raise LibraryError(f"{source} isn't UTF-8 text. Save it as UTF-8 and import it again.") from None
        lib = parse_published(raw, source.name)
        lib = Library(name=lib.name, description=lib.description, renders=lib.renders, entries=lib.entries,
                      version=lib.version, source=source.name, imported_at=_now())
        before: Optional[int] = None
        if self.has(lib.name):
            try:
                before = len(self.load(lib.name).entries)
            except LibraryError:
                before = 0          # a damaged copy is exactly what importing again fixes
        body = json.dumps(_to_json(lib), ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self._write(self._path(lib.name), MAGIC + p.kind + p.protect(body))
        self._cache[_fold(lib.name)] = lib
        return lib, before

    def _write(self, path: Path, data: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        handle, name = tempfile.mkstemp(dir=str(path.parent), prefix=path.name + ".", suffix=".tmp")
        tmp = Path(name)
        try:
            with os.fdopen(handle, "wb") as fh:
                fh.write(data)
            replace_with_retry(tmp, path)
        except BaseException:
            try:
                tmp.unlink()
            except OSError:
                pass
            raise

    def remove(self, name: str) -> str:
        path = self._path(name)
        if not path.is_file():
            raise LibraryError(f"No library named {name} on this computer.")
        path.unlink()
        self._cache.pop(_fold(name), None)
        return _fold(name)

    def resolver(self) -> Callable[[str, str], str]:
        """For variables.check: (library, entry) -> what the entry fills in."""
        return lambda library, entry: self.load(library).fill(entry)


# ------------------------------------------------------------------ the published file


def parse_published(raw: Any, where: str) -> Library:
    """Check a published library file and turn it into a Library, or say exactly what to fix."""
    if not isinstance(raw, dict):
        raise LibraryError(f'{where} must hold a JSON object with "library" and "entries".')
    name = str(raw.get("library") or "").strip()
    if not _NAME_RE.match(name):
        raise LibraryError(f'{where}: "library" must be the name templates use, 1 to 64 letters, digits, '
                           "'.', '_' or '-' (like ip_library_12).")
    renders = raw.get("renders", "value")
    if renders not in RENDERS:
        raise LibraryError(f'{where}: "renders" must be "value" or "name".')
    for key in ("description", "version"):
        if raw.get(key) is not None and not isinstance(raw.get(key), str):
            raise LibraryError(f'{where}: "{key}" must be text.')
    items = raw.get("entries")
    if isinstance(items, dict):
        items = [{"name": k, "value": v} for k, v in items.items()]
    if not isinstance(items, list) or not items:
        raise LibraryError(f'{where}: "entries" must list the entries, like '
                           '[{"name": "web-servers", "value": "10.20.0.0/24"}].')
    if len(items) > MAX_ENTRIES:
        raise LibraryError(f"{where} has {len(items)} entries; a library can hold up to {MAX_ENTRIES}.")
    entries: List[Entry] = []
    seen: Dict[str, int] = {}
    for i, item in enumerate(items, 1):
        if not isinstance(item, dict):
            raise LibraryError(f'{where}: entry {i} must be an object with a "name".')
        entry = str(item.get("name") or "").strip()
        if not entry or any(ch in entry for ch in "\r\n"):
            raise LibraryError(f'{where}: entry {i} needs a "name" on one line.')
        if _fold(entry) in seen:
            raise LibraryError(f'{where}: entries {seen[_fold(entry)]} and {i} are both named "{entry}".')
        seen[_fold(entry)] = i
        description = item.get("description") or ""
        if not isinstance(description, str):
            raise LibraryError(f'{where}: entry "{entry}": "description" must be text.')
        value = ""
        if renders == "value":
            value = _value(item.get("value"), f'{where}: entry "{entry}"')
        entries.append(Entry(name=entry, value=value, description=" ".join(description.split())))
    return Library(name=name, description=str(raw.get("description") or "").strip(), renders=renders,
                   entries=tuple(entries), version=str(raw.get("version") or "").strip())


def _value(value: Any, where: str) -> str:
    if isinstance(value, list) and value and all(isinstance(v, str) and v.strip() for v in value):
        return ", ".join(v.strip() for v in value)
    if isinstance(value, str) and value.strip():
        return value.strip()
    raise LibraryError(f'{where} needs a "value": text, or a list of text. A library whose values '
                       'people shouldn\'t see can set "renders": "name" instead.')


def _to_json(lib: Library) -> Dict[str, Any]:
    out = lib.info()
    del out["entries"]
    out["entries"] = [{"name": e.name, "value": e.value, "description": e.description} for e in lib.entries]
    return out


def _from_json(data: Dict[str, Any], where: str) -> Library:
    try:
        return Library(name=data["name"], description=data.get("description", ""), renders=data["renders"],
                       entries=tuple(Entry(e["name"], e.get("value", ""), e.get("description", ""))
                                     for e in data["entries"]),
                       version=data.get("version", ""), source=data.get("source", ""),
                       imported_at=data.get("imported_at", ""))
    except (KeyError, TypeError):
        raise LibraryError(f"{where} is damaged. Import the library again with 'heimdall library import FILE'.") from None


def _fold(text: str) -> str:
    return " ".join(str(text).split()).casefold()


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
