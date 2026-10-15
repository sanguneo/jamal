"""Synthetic, offline snapshot validation and deferred-bootstrap coverage."""

import json
import socket
from collections.abc import MutableMapping
from dataclasses import FrozenInstanceError
from pathlib import Path
from typing import Never, cast

import pytest

from chatplus_core.config import (
    ConfigFetchNotImplemented,
    ConfigValidationError,
    fetch_config_live,
    load_config_snapshot,
)

ROOTS = {
    "oasis_grpc_root": "grpc.example.test:1520",
    "oasis_wss_root": "wss://ws.example.test",
    "oasis_server_root": "https://api.example.test/oasis",
    "oasis_small_file_server_root": "https://small.example.test/oasis",
    "oasis_large_file_server_root": "https://large.example.test/oasis",
}


@pytest.fixture(autouse=True)
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*args: object, **kwargs: object) -> Never:
        raise AssertionError("config attempted network access")

    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)


@pytest.fixture
def snapshot() -> dict[str, object]:
    return {
        "format_version": 1,
        "environment": "stg",
        "app_version": "1.5.4+6",
        "roots": dict(ROOTS),
        "limits": {"oasis_max_small_file_size": 5242880},
    }


def write_snapshot(tmp_path: Path, snapshot: object) -> Path:
    path = tmp_path / "config.json"
    path.write_text(json.dumps(snapshot), encoding="utf-8")
    return path


@pytest.mark.parametrize("environment", ["prd", "stg", "dev"])
def test_valid_snapshot(
    tmp_path: Path, snapshot: dict[str, object], environment: str
) -> None:
    snapshot["environment"] = environment
    path = write_snapshot(tmp_path, snapshot)
    config = load_config_snapshot(str(path))
    assert config.snapshot_path == path.resolve()
    assert config.environment == environment
    assert config.app_version == snapshot["app_version"]
    assert config.limits == snapshot["limits"]
    for key, value in ROOTS.items():
        assert getattr(config, key) == value
    with pytest.raises(FrozenInstanceError):
        setattr(config, "environment", "dev")
    with pytest.raises(TypeError):
        cast(MutableMapping[str, int], config.limits)["oasis_max_small_file_size"] = 1


@pytest.mark.parametrize("key", ROOTS)
def test_missing_root(tmp_path: Path, snapshot: dict[str, object], key: str) -> None:
    roots = cast(dict[str, str], snapshot["roots"])
    del roots[key]
    with pytest.raises(ConfigValidationError):
        load_config_snapshot(write_snapshot(tmp_path, snapshot))


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("oasis_wss_root", "ws://ws.example.test"),
        ("oasis_wss_root", "wss:///missing-host"),
        ("oasis_wss_root", "wss://ws.example.test:"),
        ("oasis_server_root", "http://api.example.test"),
        ("oasis_small_file_server_root", "https://user:pass@small.example.test"),
        ("oasis_large_file_server_root", "https://large.example.test/#fragment"),
        ("oasis_server_root", "https://api.example.test/?redirect=https://other.test"),
        ("oasis_server_root", "https://bad host.test"),
        ("oasis_server_root", "https://bad_host.test"),
        ("oasis_server_root", "https://api.example.test:65536"),
        ("oasis_server_root", "https://api.example.test:0"),
        ("oasis_server_root", "https://api.example.test\\other"),
        ("oasis_server_root", "\nhttps://api.example.test"),
        ("oasis_server_root", None),
        ("oasis_server_root", 42),
        ("oasis_server_root", ""),
        ("oasis_grpc_root", "grpc.example.test"),
        ("oasis_grpc_root", "grpc.example.test:"),
        ("oasis_grpc_root", "grpc.example.test:abc"),
        ("oasis_grpc_root", "grpc.example.test:65536"),
        ("oasis_grpc_root", "grpc.example.test:0"),
        ("oasis_grpc_root", "grpc.example.test:1520/path"),
        ("oasis_grpc_root", "https://grpc.example.test:1520"),
        ("oasis_grpc_root", "user@grpc.example.test:1520"),
        ("oasis_grpc_root", ":1520"),
        ("oasis_grpc_root", "grpc.example.test:1520?"),
        ("oasis_grpc_root", "grpc.example.test:1520#"),
        ("oasis_grpc_root", "::1:1520"),
    ],
)
def test_invalid_root(
    tmp_path: Path, snapshot: dict[str, object], key: str, value: object
) -> None:
    roots = cast(dict[str, object], snapshot["roots"])
    roots[key] = value
    with pytest.raises(ConfigValidationError):
        load_config_snapshot(write_snapshot(tmp_path, snapshot))


@pytest.mark.parametrize("root", ["localhost:1520", "127.0.0.1:1520", "[::1]:1520"])
def test_valid_grpc_authorities(
    tmp_path: Path, snapshot: dict[str, object], root: str
) -> None:
    cast(dict[str, str], snapshot["roots"])["oasis_grpc_root"] = root
    assert load_config_snapshot(write_snapshot(tmp_path, snapshot)).oasis_grpc_root == root


@pytest.mark.parametrize("environment", ["production", "PRD", "", None, 1, ["prd"]])
def test_invalid_environment(
    tmp_path: Path, snapshot: dict[str, object], environment: object
) -> None:
    snapshot["environment"] = environment
    with pytest.raises(ConfigValidationError):
        load_config_snapshot(write_snapshot(tmp_path, snapshot))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("format_version", True),
        ("format_version", 2),
        ("app_version", None),
        ("app_version", ""),
        ("roots", []),
        ("limits", []),
        ("limits", {"size": True}),
        ("limits", {"size": -1}),
        ("limits", {"size": 1.5}),
    ],
)
def test_invalid_snapshot_fields(
    tmp_path: Path, snapshot: dict[str, object], field: str, value: object
) -> None:
    snapshot[field] = value
    with pytest.raises(ConfigValidationError):
        load_config_snapshot(write_snapshot(tmp_path, snapshot))


def test_malformed_json(tmp_path: Path) -> None:
    path = tmp_path / "invalid.json"
    path.write_text("{", encoding="utf-8")
    with pytest.raises(ConfigValidationError):
        load_config_snapshot(path)


@pytest.mark.asyncio
async def test_live_fetch_is_deferred(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*args: object, **kwargs: object) -> Never:
        raise AssertionError("live config attempted network access")

    # Windows creates loop-internal socket pairs before the test starts.
    with monkeypatch.context() as patch:
        patch.setattr(socket.socket, "connect", forbidden)
        patch.setattr(socket.socket, "connect_ex", forbidden)
        with pytest.raises(ConfigFetchNotImplemented):
            await fetch_config_live(object())
