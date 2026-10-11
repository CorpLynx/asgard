"""Who Heimdall signs in to ServiceNow as, when it signs in with a name and a password.

Heimdall's own way in is Edge with your certificate and PIN (`browser.py`), which needs no stored
credentials at all and is what the workstation uses. A name and password are for the other case: a
ServiceNow developer instance, or the REST API work in HEIMDALL_HANDOFF.md, where basic auth is the
only thing on offer.

Where each half lives, and why they are apart:
- **The user name is in the form file** (`heimdall.json`, `username`). It isn't a secret, and having
  it in the settings you can read makes it obvious which account is configured.
- **The password is only in Windows Credential Manager** (`asgard.secrets`), per instance host, and
  never in the form file, Muninn or a log (`docs/security-and-data.md`). Off Windows there is no
  store, so `ASGARD_SECRET_HEIMDALL_<HOST>` is read instead; `status()` says which is in use.

Standard library only.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlsplit

from asgard import secrets

SERVICE = "heimdall"


class AuthError(ValueError):
    """A credential can't be stored or used. The message says what to do."""


def account(instance_url: str) -> str:
    """The instance's host, which is what a password is stored against."""
    host = urlsplit(str(instance_url or "").strip()).netloc or str(instance_url or "").strip()
    return host.split("@")[-1].strip("/").lower()


@dataclass(frozen=True)
class Status:
    host: str
    username: str
    saved: bool
    where: str                 # where a password would come from; never the password

    def line(self) -> str:
        if not self.host:
            return "No instance set yet, so there is nowhere to keep a password."
        who = self.username or "(no user name set)"
        return (f"{who} at {self.host}: " + ("a password is saved in " + self.where if self.saved
                else "no password saved (" + self.where + ")"))


def status(instance_url: str, username: str) -> Status:
    host = account(instance_url)
    saved = bool(host) and secrets.get(SERVICE, host) is not None
    return Status(host=host, username=str(username or "").strip(), saved=saved,
                  where=secrets.where(SERVICE, host) if host else "nowhere")


def password(instance_url: str) -> Optional[str]:
    host = account(instance_url)
    if not host:
        return None
    try:
        return secrets.get(SERVICE, host)
    except secrets.SecretError as exc:
        raise AuthError(str(exc)) from None


def save_password(instance_url: str, value: str) -> str:
    """Keep the password for this instance. Returns the host it was stored against."""
    host = account(instance_url)
    if not host:
        raise AuthError("Set the instance address first, so Heimdall knows which instance the password is for.")
    value = str(value or "")
    if not value.strip():
        raise AuthError("The password is empty. To remove the one that's saved, clear it instead.")
    if value != value.strip():
        # A pasted password often picks up a trailing newline; silently trimming it would be worse.
        raise AuthError("That password starts or ends with a space. Paste it again without it, or if the "
                        "space is really part of it, set it from the command line.")
    try:
        secrets.put(SERVICE, host, value, comment=f"Asgard Heimdall: ServiceNow password for {host}")
    except secrets.SecretError as exc:
        raise AuthError(str(exc)) from None
    return host


def clear_password(instance_url: str) -> bool:
    host = account(instance_url)
    if not host:
        return False
    try:
        return secrets.delete(SERVICE, host)
    except secrets.SecretError as exc:
        raise AuthError(str(exc)) from None
