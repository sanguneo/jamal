"""Offline explicit-session validation with synthetic tokens and identities only."""

import base64
import json
import socket
import traceback
from collections.abc import MutableMapping
from dataclasses import FrozenInstanceError
from pathlib import Path
from typing import Never, cast

import pytest

from jamal.credentials import CredentialError, CredentialValidationError, load_session

CANARY = "CANARY-session-secret-never-print"
CLIENT_UUID = "12345678-1234-4234-8234-123456789abc"
NOW = 1800000000.0


def jwt(payload: object) -> str:
    encoded = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    return f"synthetic-header.{encoded}.synthetic-signature"


@pytest.fixture(autouse=True)
def offline_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*_args: object, **_kwargs: object) -> Never:
        raise AssertionError("credentials attempted network access")

    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr("jamal.credentials.time.time", lambda: NOW)


@pytest.fixture
def export() -> dict[str, object]:
    return {
        "format_version": 1,
        "environment": "stg",
        "app_version": "1.5.4+6",
        "exported_at": "2026-10-15T12:00:00Z",
        "account_hash": "abcdef123456",
        "tokens": {"access_token": jwt({"exp": NOW + 60}), "grpc_restart_token": CANARY},
        "identity": {"client_uuid": CLIENT_UUID, "device_code": CANARY},
        "roots": {
            "grpc": "grpc.example.test:443",
            "wss": "wss://ws.example.test/oasis",
            "server": "https://api.example.test",
            "small_file": "https://small.example.test",
            "large_file": "https://large.example.test",
        },
        "limits": {
            "max_group_xms_users": 30,
            "max_small_file_size": 5242880,
            "max_upload_file_size": 314572800,
        },
    }


def write_export(tmp_path: Path, export: object) -> Path:
    path = tmp_path / "session.json"
    _ = path.write_text(json.dumps(export), encoding="utf-8")
    return path


@pytest.mark.parametrize("environment", ["prd", "stg", "dev"])
def test_valid_load(tmp_path: Path, export: dict[str, object], environment: str) -> None:
    export["environment"] = environment
    path = write_export(tmp_path, export)
    before = path.read_bytes()
    session = load_session(str(path))
    assert session.format_version == 1
    assert session.environment == environment
    assert session.app_version == export["app_version"]
    assert session.exported_at == export["exported_at"]
    assert session.account_hash == export["account_hash"]
    assert session.access_token == jwt({"exp": NOW + 60})
    assert session.grpc_restart_token == CANARY
    assert session.client_uuid == CLIENT_UUID
    assert session.device_code == CANARY
    assert session.roots == export["roots"]
    assert session.limits == export["limits"]
    assert session.expires_at == NOW + 60
    assert not session.is_expired()
    assert not session.is_expired(NOW + 59)
    assert session.is_expired(NOW + 60)
    assert path.read_bytes() == before
    assert list(tmp_path.iterdir()) == [path]
    with pytest.raises(FrozenInstanceError):
        setattr(session, "access_token", "replacement")
    with pytest.raises(TypeError):
        cast(MutableMapping[str, str], session.roots)["grpc"] = "changed"
    with pytest.raises(TypeError):
        cast(MutableMapping[str, int], session.limits)["max_small_file_size"] = 0


@pytest.mark.parametrize(
    "field",
    [
        "format_version", "environment", "app_version", "exported_at", "account_hash",
        "tokens", "identity", "roots", "limits",
        "tokens.access_token", "tokens.grpc_restart_token",
        "identity.client_uuid", "identity.device_code",
        "roots.grpc", "roots.wss", "roots.server", "roots.small_file", "roots.large_file",
        "limits.max_group_xms_users", "limits.max_small_file_size", "limits.max_upload_file_size",
    ],
)
def test_missing_field(tmp_path: Path, export: dict[str, object], field: str) -> None:
    parts = field.split(".")
    target = export if len(parts) == 1 else cast(dict[str, object], export[parts[0]])
    del target[parts[-1]]
    with pytest.raises(CredentialValidationError):
        _ = load_session(write_export(tmp_path, export))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("format_version", True), ("format_version", 2), ("environment", ["prd"]),
        ("environment", "production"), ("app_version", ""), ("exported_at", CANARY),
        ("account_hash", CANARY), ("tokens", []), ("identity", None), ("roots", []),
        ("limits", {}), ("tokens.access_token", ""), ("tokens.access_token", 42),
        ("tokens.grpc_restart_token", " "), ("identity.client_uuid", CANARY),
        ("identity.device_code", False), ("roots.grpc", "grpc.example.test"),
        ("roots.grpc", "grpc.example.test:65536"), ("roots.grpc", "::1:443"),
        ("roots.wss", "ws://ws.example.test"), ("roots.wss", "wss://user:pass@host.test"),
        ("roots.wss", "wss://bad_host.test"), ("roots.server", "https://host.test:"),
        ("roots.server", "https://host.test:0"), ("roots.server", "https://host.test/#secret"),
        ("roots.small_file", "https://host.test/?secret"), ("roots.large_file", CANARY),
        ("limits.max_upload_file_size", True), ("limits.max_small_file_size", -1),
    ],
)
def test_invalid_field(
    tmp_path: Path, export: dict[str, object], field: str, value: object
) -> None:
    parts = field.split(".")
    target = export if len(parts) == 1 else cast(dict[str, object], export[parts[0]])
    target[parts[-1]] = value
    with pytest.raises(CredentialValidationError) as error:
        _ = load_session(write_export(tmp_path, export))
    assert CANARY not in str(error.value)
    assert CANARY not in "".join(traceback.format_exception(error.value))


@pytest.mark.parametrize("location", ["top", "tokens", "nested-list"])
def test_master_refresh_rejected(
    tmp_path: Path, export: dict[str, object], location: str
) -> None:
    key = "OASIS_EXP_REFRESH_TOKEN"
    if location == "top":
        export[key] = CANARY
    elif location == "tokens":
        cast(dict[str, object], export["tokens"])[key] = CANARY
    else:
        export["extra"] = [{"nested": [{key: None}]}]
    with pytest.raises(CredentialValidationError, match="OASIS_EXP_REFRESH_TOKEN"):
        _ = load_session(write_export(tmp_path, export))


def test_duplicate_keys_cannot_hide_master_refresh(tmp_path: Path) -> None:
    path = tmp_path / "session.json"
    _ = path.write_text(
        '{"extra":{"OASIS_EXP_REFRESH_TOKEN":null},"extra":{}}', encoding="utf-8"
    )
    with pytest.raises(CredentialValidationError, match="OASIS_EXP_REFRESH_TOKEN"):
        _ = load_session(path)


def test_refresh_token_accepted(tmp_path: Path, export: dict[str, object]) -> None:
    cast(dict[str, object], export["tokens"])["refresh_token"] = "r.e.f"
    session = load_session(write_export(tmp_path, export))
    assert session.refresh_token == "r.e.f"


def test_device_info_accepted(tmp_path: Path, export: dict[str, object]) -> None:
    export["device_info"] = {"client_id": "cid", "os_type": "win"}
    session = load_session(write_export(tmp_path, export))
    assert session.device_info is not None
    assert session.device_info["os_type"] == "win"


def test_expired_session_loads_when_not_required(
    tmp_path: Path, export: dict[str, object]
) -> None:
    cast(dict[str, object], export["tokens"])["access_token"] = jwt({"exp": NOW - 60})
    session = load_session(write_export(tmp_path, export), require_valid=False)
    assert session.is_expired()


@pytest.mark.parametrize("exp", [NOW - 60, NOW])
def test_expired_token(tmp_path: Path, export: dict[str, object], exp: float) -> None:
    cast(dict[str, object], export["tokens"])["access_token"] = jwt({"exp": exp})
    with pytest.raises(CredentialValidationError, match="re-export"):
        _ = load_session(write_export(tmp_path, export))


@pytest.mark.parametrize(
    "token",
    [
        CANARY, "header.!.signature", "header.eA.signature", jwt([]), jwt({}),
        jwt({"exp": "123"}), jwt({"exp": True}), jwt({"exp": None}),
        jwt({"exp": float("nan")}), jwt({"exp": float("inf")}), jwt({"exp": 10**400}),
        "header._w.signature",
    ],
)
def test_unknown_expiry(tmp_path: Path, export: dict[str, object], token: str) -> None:
    cast(dict[str, object], export["tokens"])["access_token"] = token
    session = load_session(write_export(tmp_path, export))
    assert session.expires_at is None
    assert not session.is_expired()


def test_redaction(
    tmp_path: Path, export: dict[str, object], caplog: pytest.LogCaptureFixture
) -> None:
    cast(dict[str, object], export["tokens"])["access_token"] = CANARY
    session = load_session(write_export(tmp_path, export))
    for value in (CANARY, CLIENT_UUID, session.account_hash):
        assert value not in repr(session)
        assert value not in str(session)
        assert value not in caplog.text
    cast(dict[str, object], export["roots"])["server"] = f"https://[{CANARY}]"
    with pytest.raises(CredentialValidationError) as error:
        _ = load_session(write_export(tmp_path, export))
    assert CANARY not in repr(error.value)
    assert CANARY not in "".join(traceback.format_exception(error.value))
    assert caplog.text == ""


@pytest.mark.parametrize("content", [f'{{"{CANARY}":', f"{CANARY}\x00"])
def test_malformed_json_redacted(tmp_path: Path, content: str) -> None:
    path = tmp_path / "session.json"
    _ = path.write_text(content, encoding="utf-8")
    with pytest.raises(CredentialValidationError) as error:
        _ = load_session(path)
    assert CANARY not in "".join(traceback.format_exception(error.value))


def test_invalid_utf8_redacted(tmp_path: Path) -> None:
    path = tmp_path / "session.json"
    _ = path.write_bytes(b"\xff" + CANARY.encode())
    with pytest.raises(CredentialValidationError) as error:
        _ = load_session(path)
    assert CANARY not in "".join(traceback.format_exception(error.value))


def test_read_failure_redacted(tmp_path: Path) -> None:
    with pytest.raises(CredentialError) as error:
        _ = load_session(tmp_path / CANARY)
    assert CANARY not in "".join(traceback.format_exception(error.value))
