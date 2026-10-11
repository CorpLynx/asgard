"""One place for Asgard's passwords and tokens: Windows Credential Manager, through ctypes.

Planned in docs/platform-consolidation.md ("before a second Asgard app stores a token") and built
when Heimdall needed a ServiceNow password. Baldur's GitHub token predates this and keeps its own
copy in `baldur/github.py`; Odin's Jira token is a DPAPI file (`odin/credstore.py`) because Odin's
daily run may import almost nothing of Asgard. New callers use this.

    put("heimdall", "snow.example.gov", "the password")
    get("heimdall", "snow.example.gov")      -> the password, or None
    delete("heimdall", "snow.example.gov")

Rules it keeps:
- **A secret never goes into a settings file, Muninn or a log** (`docs/security-and-data.md`). The
  account name is not a secret and may live in the app's settings; the password only lives here.
- Credential Manager holds it against the signed-in Windows user, so another account on the machine
  can't read it and it isn't in any backup of the profile's files.
- `ASGARD_SECRET_<SERVICE>_<ACCOUNT>` overrides it, uppercased with anything that isn't a letter or
  digit turned into `_`. That is how this works off Windows (tests, and a development machine), and
  it is read first so a test can never touch the real store.
- Nothing here raises when a secret simply isn't there; `get` returns None and the caller says what
  to run. Storing on a machine with no Credential Manager raises `SecretError`, naming the variable.

Standard library only.
"""
from __future__ import annotations

import os
import re
from typing import Optional

AVAILABLE = os.name == "nt"
MAX_BYTES = 2560          # Credential Manager's limit on one blob (CRED_MAX_CREDENTIAL_BLOB_SIZE)


class SecretError(Exception):
    """A secret can't be stored or read. The message says what to do instead."""


def env_name(service: str, account: str) -> str:
    """ASGARD_SECRET_HEIMDALL_SNOW_EXAMPLE_GOV, the override for this service and account."""
    both = f"{service}_{account}" if account else service
    return "ASGARD_SECRET_" + re.sub(r"[^A-Za-z0-9]+", "_", both).strip("_").upper()


def target(service: str, account: str) -> str:
    """What the credential is called in Credential Manager, as the user will see it listed."""
    return f"Asgard {service}" + (f" {account}" if account else "")


if AVAILABLE:
    import ctypes
    from ctypes import wintypes

    class _CREDENTIAL(ctypes.Structure):
        _fields_ = [("Flags", wintypes.DWORD), ("Type", wintypes.DWORD), ("TargetName", wintypes.LPWSTR),
                    ("Comment", wintypes.LPWSTR), ("LastWritten", wintypes.FILETIME),
                    ("CredentialBlobSize", wintypes.DWORD), ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)),
                    ("Persist", wintypes.DWORD), ("AttributeCount", wintypes.DWORD),
                    ("Attributes", ctypes.c_void_p), ("TargetAlias", wintypes.LPWSTR),
                    ("UserName", wintypes.LPWSTR)]

    _advapi = ctypes.WinDLL("advapi32", use_last_error=True)
    _advapi.CredReadW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                  ctypes.POINTER(ctypes.POINTER(_CREDENTIAL))]
    _advapi.CredReadW.restype = wintypes.BOOL
    _advapi.CredWriteW.argtypes = [ctypes.POINTER(_CREDENTIAL), wintypes.DWORD]
    _advapi.CredWriteW.restype = wintypes.BOOL
    _advapi.CredDeleteW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD]
    _advapi.CredDeleteW.restype = wintypes.BOOL
    _advapi.CredFree.argtypes = [ctypes.c_void_p]
    _advapi.CredFree.restype = None
    _GENERIC, _PERSIST_LOCAL_MACHINE, _NOT_FOUND = 1, 2, 1168


def get(service: str, account: str = "") -> Optional[str]:
    """The secret, from the environment override first, then Credential Manager. None if unset."""
    override = os.environ.get(env_name(service, account), "")
    if override:
        return override
    if not AVAILABLE:
        return None
    found = ctypes.POINTER(_CREDENTIAL)()
    if not _advapi.CredReadW(target(service, account), _GENERIC, 0, ctypes.byref(found)):
        error = ctypes.get_last_error()
        if error == _NOT_FOUND:
            return None
        raise SecretError(f"Couldn't read {target(service, account)} from Credential Manager (error {error}).")
    try:
        cred = found.contents
        blob = ctypes.string_at(cred.CredentialBlob, cred.CredentialBlobSize)
        return blob.decode("utf-16-le").strip() or None
    finally:
        _advapi.CredFree(found)


def put(service: str, account: str, secret: str, comment: str = "") -> None:
    """Store the secret for this Windows user. Raises SecretError off Windows, naming the override."""
    secret = (secret or "").strip()
    if not secret:
        raise SecretError("There's nothing to save: the secret is empty.")
    data = secret.encode("utf-16-le")
    if len(data) > MAX_BYTES:
        raise SecretError(f"That is too long for Credential Manager ({len(data)} bytes of {MAX_BYTES}).")
    if not AVAILABLE:
        raise SecretError("Secrets are kept in Windows Credential Manager, so they can only be saved on "
                          f"Windows. Elsewhere, set the {env_name(service, account)} environment variable.")
    blob = (ctypes.c_ubyte * len(data)).from_buffer_copy(data)
    cred = _CREDENTIAL()
    cred.Type = _GENERIC
    cred.TargetName = target(service, account)
    cred.Comment = comment or f"Asgard {service}"
    cred.CredentialBlobSize = len(data)
    cred.CredentialBlob = ctypes.cast(blob, ctypes.POINTER(ctypes.c_ubyte))
    cred.Persist = _PERSIST_LOCAL_MACHINE
    cred.UserName = service
    if not _advapi.CredWriteW(ctypes.byref(cred), 0):
        raise SecretError(f"Couldn't save {target(service, account)} in Credential Manager "
                          f"(error {ctypes.get_last_error()}).")


def delete(service: str, account: str = "") -> bool:
    """Forget it. True when something was removed; False when there was nothing to remove."""
    if not AVAILABLE:
        return False
    if _advapi.CredDeleteW(target(service, account), _GENERIC, 0):
        return True
    if ctypes.get_last_error() == _NOT_FOUND:
        return False
    raise SecretError(f"Couldn't remove {target(service, account)} from Credential Manager "
                      f"(error {ctypes.get_last_error()}).")


def where(service: str, account: str = "") -> str:
    """Where a secret would come from, for a status line. Never the secret itself."""
    if os.environ.get(env_name(service, account), ""):
        return f"the {env_name(service, account)} environment variable"
    return "Windows Credential Manager" if AVAILABLE else "nowhere on this computer"
