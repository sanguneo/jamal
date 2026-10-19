#!/usr/bin/env python3
"""Export a jamal session.json from a logged-in ChattingPlus install.

Read-only. Run as the signed-in Windows user on the PC where ChattingPlus for
Windows is installed and logged in. Reads the flutter_secure_storage values
(AES-GCM sealed with a Windows Credential Manager key) and writes the explicit
short-lived session export that jamal's credential loader consumes.
The long-lived refresh token is deliberately never read or exported.

Requires the `cryptography` package:  pip install cryptography
"""

from __future__ import annotations

import argparse
import base64
import ctypes
import json
import os
import platform
import sys
import time
from ctypes import wintypes
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

PACKAGE = "com.sktelecom.torchlight_82zr7n7shan6r"
VENDOR = "SK Telecom Co., Ltd/ChattingPlus"
CRED_TARGET = "key_."
CRED_TYPE_GENERIC = 1
MASTER_KEY_BYTES = 16
GCM_IV = 12
GCM_TAG = 16
SECURE_PREFIX = ".sK2Nwk"
SECURE_SUFFIX = ".secure"

ROOT_MAP = {
    "oasis_grpc_root": "grpc",
    "oasis_wss_root": "wss",
    "oasis_server_root": "server",
    "oasis_small_file_server_root": "small_file",
    "oasis_large_file_server_root": "large_file",
}
LIMIT_KEYS = (
    "oasis_max_group_xms_users",
    "oasis_max_small_file_size",
    "oasis_max_upload_file_size",
)
ENVIRONMENTS = ("prd", "stg", "dev")
DART_VERSION = '3.10.8 (stable) (Tue Jan 27 00:02:04 2026 -0800) on "windows_x64"'
DEVICE_NAME = "B460 MB B460MDS3H"


class _Credential(ctypes.Structure):
    _fields_ = (
        ("flags", wintypes.DWORD),
        ("kind", wintypes.DWORD),
        ("target", wintypes.LPWSTR),
        ("comment", wintypes.LPWSTR),
        ("written", wintypes.FILETIME),
        ("size", wintypes.DWORD),
        ("blob", ctypes.POINTER(ctypes.c_ubyte)),
        ("persist", wintypes.DWORD),
        ("attribute_count", wintypes.DWORD),
        ("attributes", ctypes.c_void_p),
        ("alias", wintypes.LPWSTR),
        ("user", wintypes.LPWSTR),
    )


def master_key() -> bytes:
    api = ctypes.WinDLL("advapi32", use_last_error=True)
    api.CredReadW.argtypes = (
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.POINTER(ctypes.POINTER(_Credential)),
    )
    api.CredReadW.restype = wintypes.BOOL
    api.CredFree.argtypes = (ctypes.c_void_p,)
    api.CredFree.restype = None
    credential = ctypes.POINTER(_Credential)()
    if not api.CredReadW(CRED_TARGET, CRED_TYPE_GENERIC, 0, ctypes.byref(credential)):
        raise SystemExit("cannot read the Credential Manager key; run as the signed-in user")
    try:
        key = ctypes.string_at(credential.contents.blob, credential.contents.size)
    finally:
        api.CredFree(credential)
    if len(key) != MASTER_KEY_BYTES:
        raise SystemExit("unexpected storage key length")
    return key


def unseal(blob: bytes, key: bytes) -> bytes:
    if len(blob) <= GCM_IV + GCM_TAG:
        raise ValueError("truncated sealed value")
    nonce = blob[:GCM_IV]
    tag = blob[GCM_IV : GCM_IV + GCM_TAG]
    ciphertext = blob[GCM_IV + GCM_TAG :]
    return AESGCM(key).decrypt(nonce, ciphertext + tag, None).rstrip(b"\0")


def _package_root() -> Path:
    local = os.environ.get("LOCALAPPDATA")
    if not local:
        raise SystemExit("LOCALAPPDATA is not set")
    return Path(local) / "Packages" / PACKAGE / "LocalCache"


def account_hash(package_root: Path) -> str:
    edb = package_root / "Local" / VENDOR / "edb"
    profiles = [p for p in edb.iterdir() if p.is_dir()] if edb.exists() else []
    if len(profiles) != 1:
        raise SystemExit("expected exactly one logged-in profile")
    return profiles[0].name


def load_values(package_root: Path, key: bytes) -> dict[str, bytes]:
    secure = package_root / "Roaming" / VENDOR
    values: dict[str, bytes] = {}
    for path in secure.glob(f"{SECURE_PREFIX}*{SECURE_SUFFIX}"):
        try:
            encoded = path.name[len(SECURE_PREFIX) : -len(SECURE_SUFFIX)]
            name = base64.urlsafe_b64decode(encoded).decode()
        except (ValueError, UnicodeDecodeError):
            continue
        try:
            values[name] = unseal(path.read_bytes(), key)
        except (ValueError, OSError):
            continue
    return values


def firmware_version() -> str:
    import winreg

    try:
        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows NT\CurrentVersion"
        ) as handle:
            value, _ = winreg.QueryValueEx(handle, "DisplayVersion")
            return str(value)
    except OSError:
        return ""


def device_id(values: dict[str, bytes], account: str) -> str:
    prefix = f"cipher_key_{account}_"
    for name in values:
        if name.startswith(prefix):
            return name[len(prefix) :]
    raise SystemExit("cannot determine device_id from secure storage")


def build_session(values: dict[str, bytes], account: str) -> dict[str, object]:
    def text(name: str) -> str:
        raw = values.get(name)
        if raw is None:
            raise SystemExit(f"missing secure value: {name}")
        return raw.decode("utf-8")

    server_type = values.get("server_type")
    environment = server_type.decode("utf-8").lower() if server_type else "prd"
    if environment not in ENVIRONMENTS:
        environment = "prd"
    client_version = values.get(f"client_version_{account}")
    return {
        "format_version": 1,
        "environment": environment,
        "app_version": client_version.decode("utf-8") if client_version else "1.5.4+6",
        "exported_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "account_hash": account,
        "tokens": {
            "access_token": text("access_token"),
            "grpc_restart_token": text(f"grpc_restart_token_{account}"),
            "refresh_token": text("refresh_token"),
        },
        "identity": {"client_uuid": text("client_uuid"), "device_code": text("device_code")},
        "roots": {short: text(long) for long, short in ROOT_MAP.items()},
        "limits": {key.removeprefix("oasis_"): int(text(key)) for key in LIMIT_KEYS},
        "device_info": {
            "client_id": text("client_uuid"),
            "device_kind": "computer",
            "device_id": device_id(values, account),
            "device_name": DEVICE_NAME,
            "client_vendor": "SKTelecom",
            "mno": "SKT",
            "os_type": "win",
            "os_version": platform.version(),
            "device_type": "SD",
            "firmware_version": firmware_version(),
            "service_version": "1.0",
            "client_version": client_version.decode("utf-8") if client_version else "1.5.4.6",
            "dart_version": DART_VERSION,
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Export a jamal session.json (read-only).")
    parser.add_argument("--out", help="write the JSON here instead of stdout")
    args = parser.parse_args(argv)
    if sys.platform != "win32":
        raise SystemExit("this exporter supports ChattingPlus for Windows only")
    package_root = _package_root()
    account = account_hash(package_root)
    session = build_session(load_values(package_root, master_key()), account)
    document = json.dumps(session, ensure_ascii=False, indent=2)
    if args.out:
        Path(args.out).write_text(document, encoding="utf-8")
        print(f"wrote {args.out}")
    else:
        print(document)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
