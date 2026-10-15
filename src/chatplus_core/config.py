"""Load the donor's non-secret config snapshot, the supported MVP config route.

Live REST bootstrap returns {"encrypted_data": "<hex>"} with oasis-encrypt: true.
Its decryption key/algorithm is unverified, so live fetching is deferred. No network
or decryption is attempted here, nor any URL fallback or environment inference.
Snapshots use format_version 1, roots, and a map of nonnegative integer limits.
Environment/app_version are explicit provenance, not inferred from hostnames;
callers must compare them to the selected session before connecting.
"""

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from ipaddress import IPv6Address
from pathlib import Path
from types import MappingProxyType
from typing import Literal, cast
from urllib.parse import urlsplit

type Environment = Literal["prd", "stg", "dev"]


class ConfigValidationError(ValueError):
    """The explicit snapshot does not satisfy the supported format."""


class ConfigFetchNotImplemented(NotImplementedError):
    """Live bootstrap is deferred until REST decryption is verified."""


@dataclass(frozen=True)
class Config:
    """Exact configured roots and file provenance, with immutable numeric limits."""

    snapshot_path: Path
    environment: Environment
    app_version: str
    oasis_grpc_root: str
    oasis_wss_root: str
    oasis_server_root: str
    oasis_small_file_server_root: str
    oasis_large_file_server_root: str
    limits: Mapping[str, int]

    def __post_init__(self) -> None:
        object.__setattr__(self, "limits", MappingProxyType(dict(self.limits)))


def _object(value: object, field: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ConfigValidationError(f"{field} must be a JSON object")
    return cast(dict[str, object], value)


def _text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ConfigValidationError(f"{field} must be a nonempty string without edge whitespace")
    return value


def _root(value: object, field: str, scheme: str) -> str:
    root = _text(value, field)
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
    except ValueError as exc:
        expected = "host:port" if scheme == "grpc" else f"{scheme}:// URL"
        raise ConfigValidationError(f"{field} must be a well-formed {expected}") from exc
    return root


def load_config_snapshot(path: str | Path) -> Config:
    """Load only the selected snapshot; invalid content raises ConfigValidationError.

    File I/O failures propagate as OSError. Roots and app_version are retained
    exactly, and snapshot_path records the absolute file location.
    """
    snapshot_path = Path(path).resolve()
    try:
        data = _object(json.loads(snapshot_path.read_text(encoding="utf-8")), "snapshot")
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ConfigValidationError("snapshot must be UTF-8 JSON") from exc
    if type(data.get("format_version")) is not int or data["format_version"] != 1:
        raise ConfigValidationError("format_version must be 1")
    environment = data.get("environment")
    if environment not in ("prd", "stg", "dev"):
        raise ConfigValidationError("environment must be prd, stg, or dev")
    app_version = _text(data.get("app_version"), "app_version")
    roots = _object(data.get("roots"), "roots")
    raw_limits = _object(data.get("limits"), "limits")
    limits: dict[str, int] = {}
    for name, value in raw_limits.items():
        if type(value) is not int or value < 0:
            raise ConfigValidationError(f"limits.{name} must be a nonnegative integer")
        limits[name] = value
    return Config(
        snapshot_path=snapshot_path,
        environment=environment,
        app_version=app_version,
        oasis_grpc_root=_root(roots.get("oasis_grpc_root"), "oasis_grpc_root", "grpc"),
        oasis_wss_root=_root(roots.get("oasis_wss_root"), "oasis_wss_root", "wss"),
        oasis_server_root=_root(roots.get("oasis_server_root"), "oasis_server_root", "https"),
        oasis_small_file_server_root=_root(
            roots.get("oasis_small_file_server_root"), "oasis_small_file_server_root", "https"
        ),
        oasis_large_file_server_root=_root(
            roots.get("oasis_large_file_server_root"), "oasis_large_file_server_root", "https"
        ),
        limits=limits,
    )


async def fetch_config_live(session: object) -> Config:
    """Raise without accessing the session, network, or snapshot files."""
    raise ConfigFetchNotImplemented(
        "Live config fetch is deferred: oasis-encrypt encrypted_data decryption "
        "key/algorithm is unverified. Use load_config_snapshot(path) for the MVP."
    )
