"""Windows DPAPI for any Asgard app: bytes in, bytes out, readable only by this Windows user here.

CryptProtectData ties the result to the signed-in user's Windows credentials, so a copy of the
file is useless on another machine or to another user, and nothing has to keep a key. It does not
hide data from the user it belongs to: anything running as that user can call CryptUnprotectData
too. Say that wherever this is used; it is protection at rest, not access control.

Odin's credstore.py predates this and keeps its own copy (Odin's daily run may import only a
short list of asgard modules); new callers use this one.

Standard library only (ctypes). On anything but Windows, AVAILABLE is False and both calls raise.
"""
from __future__ import annotations

import os
from typing import Optional

AVAILABLE = os.name == "nt"


class DpapiError(OSError):
    """DPAPI refused, or isn't here. The message says what to do."""


if AVAILABLE:
    import ctypes
    from ctypes import wintypes

    class _Blob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    _PBLOB = ctypes.POINTER(_Blob)
    _crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _crypt32.CryptProtectData.argtypes = [_PBLOB, wintypes.LPCWSTR, _PBLOB, ctypes.c_void_p,
                                          ctypes.c_void_p, wintypes.DWORD, _PBLOB]
    _crypt32.CryptProtectData.restype = wintypes.BOOL
    _crypt32.CryptUnprotectData.argtypes = [_PBLOB, ctypes.c_void_p, _PBLOB, ctypes.c_void_p,
                                            ctypes.c_void_p, wintypes.DWORD, _PBLOB]
    _crypt32.CryptUnprotectData.restype = wintypes.BOOL
    _kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    _kernel32.LocalFree.restype = ctypes.c_void_p
    _UI_FORBIDDEN = 0x01

    def _to_blob(data: bytes):
        buf = ctypes.create_string_buffer(data, len(data))
        return _Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))), buf

    def _take(blob: "_Blob") -> bytes:
        try:
            return ctypes.string_at(blob.pbData, blob.cbData)
        finally:
            _kernel32.LocalFree(ctypes.cast(blob.pbData, ctypes.c_void_p))


def protect(data: bytes, description: str, entropy: Optional[bytes] = None) -> bytes:
    """Encrypt for this Windows user. entropy, when given, must be given again to unprotect."""
    if not AVAILABLE:
        raise DpapiError("Windows DPAPI is only available on Windows.")
    blob_in, _keep = _to_blob(data)
    extra, _keep2 = _to_blob(entropy) if entropy else (None, None)
    blob_out = _Blob()
    if not _crypt32.CryptProtectData(ctypes.byref(blob_in), description, ctypes.byref(extra) if extra else None,
                                     None, None, _UI_FORBIDDEN, ctypes.byref(blob_out)):
        raise DpapiError(f"Windows couldn't encrypt the data (error {ctypes.get_last_error()}).")
    return _take(blob_out)


def unprotect(data: bytes, entropy: Optional[bytes] = None) -> bytes:
    if not AVAILABLE:
        raise DpapiError("This file is encrypted with Windows DPAPI and can only be read on Windows.")
    blob_in, _keep = _to_blob(data)
    extra, _keep2 = _to_blob(entropy) if entropy else (None, None)
    blob_out = _Blob()
    if not _crypt32.CryptUnprotectData(ctypes.byref(blob_in), None, ctypes.byref(extra) if extra else None,
                                       None, None, _UI_FORBIDDEN, ctypes.byref(blob_out)):
        raise DpapiError(f"Windows couldn't decrypt the data (error {ctypes.get_last_error()}). DPAPI data "
                         "only opens for the Windows user who saved it, on the computer they saved it on.")
    return _take(blob_out)
