from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest

from jamal.cli import main

FIXTURE = Path(__file__).parent / "fixtures" / "receive" / "notification_synthetic.json"
CANARY = "CANARY-cli-secret-never-print"
FAR_FUTURE = 4102444800.0


def _jwt(payload: object) -> str:
    encoded = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    return f"synthetic-header.{encoded}.synthetic-signature"


def _write_session(tmp_path: Path) -> Path:
    export = {
        "format_version": 1,
        "environment": "stg",
        "app_version": "1.5.4+6",
        "exported_at": "2026-10-15T12:00:00Z",
        "account_hash": "abcdef123456",
        "tokens": {"access_token": _jwt({"exp": FAR_FUTURE}), "grpc_restart_token": CANARY},
        "identity": {"client_uuid": "12345678-1234-4234-8234-123456789abc", "device_code": CANARY},
        "roots": {
            "grpc": "grpc.example.test:1520",
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
    path = tmp_path / "session.json"
    path.write_text(json.dumps(export), encoding="utf-8")
    return path


def test_credentials_inspect_reports_redacted(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = main(["credentials", "inspect", "--credentials", str(_write_session(tmp_path))])
    out = capsys.readouterr().out
    assert code == 0
    report = json.loads(out)
    assert report["environment"] == "stg"
    assert report["has_access_token"] is True
    assert report["is_expired"] is False
    assert report["roots"] == ["grpc", "large_file", "server", "small_file", "wss"]
    assert CANARY not in out


def test_decode_fixture_emits_ndjson(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code = main(["decode", "--input", str(FIXTURE), "--type", "notification"])
    out = capsys.readouterr().out
    assert code == 0
    lines = [line for line in out.splitlines() if line]
    assert len(lines) == 1
    note = json.loads(lines[0])
    expected = json.loads(FIXTURE.read_text(encoding="utf-8"))["expected"]
    assert note["push_epoch"] == expected["push_epoch"]
    assert note["messages"][0]["from"] == expected["from"]
    assert note["messages"][0]["text"] == expected["text"]


def test_decode_raw_base64_input(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    frame_b64 = json.loads(FIXTURE.read_text(encoding="utf-8"))["grpc_frame_b64"]
    raw = tmp_path / "frame.b64"
    raw.write_text(frame_b64, encoding="utf-8")
    code = main(["decode", "--input", str(raw), "--type", "notification"])
    assert code == 0
    assert json.loads(capsys.readouterr().out.splitlines()[0])["push_epoch"] == 1792000000


def test_missing_credentials_file_is_exit_one(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = main(["credentials", "inspect", "--credentials", str(tmp_path / "absent.json")])
    captured = capsys.readouterr()
    assert code == 1
    assert captured.out == ""
    assert captured.err.startswith("error:")


def test_config_fetch_is_deferred(capsys: pytest.CaptureFixture[str]) -> None:
    code = main(["config", "fetch", "--credentials", "x.json", "--out", "y.json"])
    captured = capsys.readouterr()
    assert code == 2
    assert "deferred" in captured.err


def test_capability_not_in_receive_mvp(capsys: pytest.CaptureFixture[str]) -> None:
    code = main(["capability", "query", "--request", "x.json"])
    assert code == 2
    assert "receive MVP" in capsys.readouterr().err
