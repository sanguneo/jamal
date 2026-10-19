from __future__ import annotations

import base64
import json
import time
from collections.abc import Mapping
from pathlib import Path

import pytest

from jamal import refresh, serve


def _jwt(exp: float) -> str:
    def seg(obj: object) -> str:
        return base64.urlsafe_b64encode(json.dumps(obj).encode()).rstrip(b"=").decode()

    return f"{seg({'alg': 'HS256', 'typ': 'JWT'})}.{seg({'exp': exp})}.sig"


def _export(access_token: str, refresh_token: str) -> dict[str, object]:
    return {
        "format_version": 1,
        "environment": "prd",
        "app_version": "1.5.4.6",
        "exported_at": "2026-10-19T00:00:00Z",
        "account_hash": "deadbeef",
        "tokens": {
            "access_token": access_token,
            "grpc_restart_token": "g.r.t",
            "refresh_token": refresh_token,
        },
        "identity": {
            "client_uuid": "11111111-2222-3333-4444-555555555555",
            "device_code": "dev",
        },
        "roots": {
            "grpc": "host.test:443",
            "wss": "wss://host.test",
            "server": "https://host.test",
            "small_file": "https://host.test",
            "large_file": "https://host.test",
        },
        "limits": {
            "max_group_xms_users": 30,
            "max_small_file_size": 100,
            "max_upload_file_size": 100,
        },
        "device_info": {
            "client_id": "11111111-2222-3333-4444-555555555555",
            "device_id": "D-ID",
            "device_name": "PC",
            "os_version": "10.0.26300",
            "client_version": "1.5.4.6",
        },
    }


def test_ensure_fresh_session_refreshes_expired(tmp_path: Path) -> None:
    path = tmp_path / "session.json"
    document = json.dumps(_export(_jwt(time.time() - 100), "old.refresh"))
    _ = path.write_text(document, encoding="utf-8")
    new_access = _jwt(time.time() + 86400)
    new_tokens = {"access_token": new_access, "refresh_token": "new.refresh"}
    blob = refresh.oasis_encrypt(json.dumps(new_tokens).encode(), "prd", nonce=b"7" * 12)

    def post(url: str, headers: Mapping[str, str], body: bytes) -> tuple[int, bytes]:
        return 200, json.dumps({"encrypted_data": blob}).encode()

    session = serve.ensure_fresh_session(str(path), "secret", post=post)
    assert not session.is_expired()
    on_disk = json.loads(path.read_text(encoding="utf-8"))
    assert on_disk["tokens"]["access_token"] == new_access
    assert on_disk["tokens"]["refresh_token"] == "new.refresh"
    assert on_disk["device_info"]["device_id"] == "D-ID"
    assert list(tmp_path.glob("*.tmp")) == []


def test_ensure_fresh_session_keeps_valid_session(tmp_path: Path) -> None:
    path = tmp_path / "session.json"
    document = json.dumps(_export(_jwt(time.time() + 86400), "r"))
    _ = path.write_text(document, encoding="utf-8")

    def fail(url: str, headers: Mapping[str, str], body: bytes) -> tuple[int, bytes]:
        raise AssertionError("a valid session must not be refreshed")

    session = serve.ensure_fresh_session(str(path), "secret", post=fail)
    assert not session.is_expired()


def test_ensure_fresh_session_raises_without_secret(tmp_path: Path) -> None:
    path = tmp_path / "session.json"
    document = json.dumps(_export(_jwt(time.time() - 100), "r"))
    _ = path.write_text(document, encoding="utf-8")

    def fail(url: str, headers: Mapping[str, str], body: bytes) -> tuple[int, bytes]:
        raise AssertionError("no secret means no refresh attempt")

    with pytest.raises(Exception):
        _ = serve.ensure_fresh_session(str(path), "", post=fail)
