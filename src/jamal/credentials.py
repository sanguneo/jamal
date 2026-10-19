"""Read short-lived session exports without local-store, login, or network access.

JWT expiry is an untrusted scheduling hint, never authentication or signature
verification. Unknown expiry does not imply validity; the server authenticates it.
"""

import base64
import binascii
import json
import math
import re
import time
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from ipaddress import IPv6Address
from pathlib import Path
from types import MappingProxyType
from typing import Literal, cast, override
from urllib.parse import urlsplit
from uuid import UUID


class CredentialError(ValueError):
    """An explicit credential export could not be loaded safely."""


class CredentialValidationError(CredentialError):
    """The export violates the short-lived session contract."""


@dataclass(frozen=True, repr=False)
class Session:
    """Immutable session values; repr/str intentionally reveal no field values.

    roots uses the export's grpc/wss/server/small_file/large_file names.
    expires_at is a Unix timestamp hint, or None when absent/unparseable.
    """

    format_version: int
    environment: Literal["prd", "stg", "dev"]
    app_version: str
    exported_at: str
    account_hash: str
    access_token: str
    grpc_restart_token: str
    client_uuid: str
    device_code: str
    roots: Mapping[str, str]
    limits: Mapping[str, int]
    expires_at: float | None
    refresh_token: str | None = None
    device_info: Mapping[str, str] | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "roots", MappingProxyType(dict(self.roots)))
        object.__setattr__(self, "limits", MappingProxyType(dict(self.limits)))
        if self.device_info is not None:
            object.__setattr__(self, "device_info", MappingProxyType(dict(self.device_info)))

    @override
    def __repr__(self) -> str:
        return "Session(<redacted>)"

    def is_expired(self, now: float | None = None) -> bool:
        """Compare the untrusted expiry hint to Unix time; unknown returns False."""
        return self.expires_at is not None and self.expires_at <= (
            time.time() if now is None else now
        )


def _session_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    # The hook sees every object, including objects inside lists, and checks keys
    # before duplicate JSON keys could hide a forbidden value.
    if any(key == "OASIS_EXP_REFRESH_TOKEN" for key, _ in pairs):
        raise CredentialValidationError(
            "OASIS_EXP_REFRESH_TOKEN is forbidden; export the standard refresh token only"
        )
    return dict(pairs)


def _object(value: object, field: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise CredentialValidationError(f"{field} must be a JSON object")
    return cast(dict[str, object], value)


def _text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise CredentialValidationError(
            f"{field} must be a nonempty string without edge whitespace"
        )
    return value


def _root(value: object, scheme: str) -> str:
    root = _text(value, "roots entry")
    try:
        if any(ord(char) <= 32 or char.isspace() for char in root) or "\\" in root:
            raise ValueError
        parsed = urlsplit("//" + root if scheme == "grpc" else root)
        host = parsed.hostname
        if not host or parsed.username is not None or parsed.password is not None:
            raise ValueError
        if ":" in host:
            _ = IPv6Address(host)
        elif not all(
            re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?", label)
            for label in host.removesuffix(".").split(".")
        ):
            raise ValueError
        port = parsed.port
        if port is not None and not 1 <= port <= 65535:
            raise ValueError
        if "?" in root or "#" in root:
            raise ValueError
        if scheme == "grpc":
            if parsed.path or not re.fullmatch(r"(?:\[[^]]+\]|[^:]+):[0-9]+", root):
                raise ValueError
            if port is None:
                raise ValueError
        elif parsed.scheme != scheme or not root.startswith(scheme + "://"):
            raise ValueError
        elif parsed.netloc.endswith(":"):
            raise ValueError
    except ValueError:
        raise CredentialValidationError("roots contains a malformed service endpoint") from None
    return root


def _expiry_hint(token: str) -> float | None:
    try:
        parts = token.split(".")
        if len(parts) != 3:
            return None
        payload = parts[1]
        decoded = base64.b64decode(
            payload + "=" * (-len(payload) % 4), altchars=b"-_", validate=True
        )
        claims = cast(object, json.loads(decoded))
        if not isinstance(claims, dict):
            return None
        exp = cast(dict[str, object], claims).get("exp")
        if isinstance(exp, bool) or not isinstance(exp, (int, float)):
            return None
        timestamp = float(exp)
        return timestamp if math.isfinite(timestamp) else None
    except (ValueError, UnicodeDecodeError, binascii.Error, OverflowError):
        return None


def load_session(path: str | Path, *, require_valid: bool = True) -> Session:
    """Load only the explicit UTF-8 export; never write files or refresh tokens.

    CredentialError wraps file-read errors without revealing the path.
    CredentialValidationError covers invalid content and expired access tokens.
    Required export metadata, device identity, roots, and limits are validated.
    Unparseable JWT expiry is allowed as unknown, not treated as verified.
    """
    try:
        content = Path(path).read_text(encoding="utf-8")
    except OSError:
        raise CredentialError("Cannot read the explicit session file") from None
    except UnicodeDecodeError:
        raise CredentialValidationError("Session file must be UTF-8 JSON") from None
    try:
        data = _object(
            cast(object, json.loads(content, object_pairs_hook=_session_object)), "session"
        )
    except json.JSONDecodeError:
        raise CredentialValidationError("Session file must be UTF-8 JSON") from None
    if type(data.get("format_version")) is not int or data["format_version"] != 1:
        raise CredentialValidationError("format_version must be 1")
    environment = data.get("environment")
    if environment not in ("prd", "stg", "dev"):
        raise CredentialValidationError("environment must be prd, stg, or dev")
    app_version = _text(data.get("app_version"), "app_version")
    exported_at = _text(data.get("exported_at"), "exported_at")
    try:
        _ = datetime.fromisoformat(exported_at)
    except ValueError:
        raise CredentialValidationError("exported_at must be ISO 8601") from None
    account_hash = _text(data.get("account_hash"), "account_hash")
    if re.fullmatch(r"[0-9a-fA-F]+", account_hash) is None:
        raise CredentialValidationError("account_hash must be hexadecimal")
    tokens = _object(data.get("tokens"), "tokens")
    access_token = _text(tokens.get("access_token"), "tokens.access_token")
    restart_token = _text(tokens.get("grpc_restart_token"), "tokens.grpc_restart_token")
    identity = _object(data.get("identity"), "identity")
    client_uuid = _text(identity.get("client_uuid"), "identity.client_uuid")
    try:
        _ = UUID(client_uuid)
    except ValueError:
        raise CredentialValidationError("identity.client_uuid must be a UUID") from None
    device_code = _text(identity.get("device_code"), "identity.device_code")
    raw_roots = _object(data.get("roots"), "roots")
    roots = {
        name: _root(raw_roots.get(name), scheme)
        for name, scheme in (
            ("grpc", "grpc"),
            ("wss", "wss"),
            ("server", "https"),
            ("small_file", "https"),
            ("large_file", "https"),
        )
    }
    raw_limits = _object(data.get("limits"), "limits")
    limits: dict[str, int] = {}
    for name in ("max_group_xms_users", "max_small_file_size", "max_upload_file_size"):
        value = raw_limits.get(name)
        if type(value) is not int or value < 0:
            raise CredentialValidationError("limits must contain nonnegative integer size limits")
        limits[name] = value
    raw_refresh = tokens.get("refresh_token")
    refresh_token = _text(raw_refresh, "tokens.refresh_token") if raw_refresh is not None else None
    raw_device = data.get("device_info")
    device_info: dict[str, str] | None = None
    if raw_device is not None:
        fields = _object(raw_device, "device_info")
        device_info = {str(name): _text(fields[name], f"device_info.{name}") for name in fields}
    session = Session(
        format_version=1,
        environment=environment,
        app_version=app_version,
        exported_at=exported_at,
        account_hash=account_hash,
        access_token=access_token,
        grpc_restart_token=restart_token,
        client_uuid=client_uuid,
        device_code=device_code,
        roots=roots,
        limits=limits,
        expires_at=_expiry_hint(access_token),
        refresh_token=refresh_token,
        device_info=device_info,
    )
    if require_valid and session.is_expired():
        raise CredentialValidationError("Access token has expired; re-export the session")
    return session
