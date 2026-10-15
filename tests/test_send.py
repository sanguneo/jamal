from __future__ import annotations

import asyncio
import base64
import json
from collections.abc import AsyncIterator
from pathlib import Path

import grpc
import pytest
import pytest_asyncio
from grpc import aio

from chatplus_core.cli import main
from chatplus_core.client import SubscribeClient
from chatplus_core.credentials import Session
from chatplus_core.send import (
    SEND_RCS_METHOD,
    SEND_XMS_METHOD,
    build_xms_mms_request,
    build_xms_text_request,
    send_message,
)
from chatplus_core.subscribe import DeviceHeaders
from chatplus_core.transport.h2 import H2Transport

SERVICE = "oasis.client.api.SendMessageService"
FIXTURES = Path(__file__).parent / "fixtures" / "transport"

DEVICE = DeviceHeaders(
    device_id="synthetic-device",
    device_name="offline-test",
    device_type="SD",
    os_version="test-os",
    client_version="1.5.4.6",
    firmware_version="test-firmware",
)


def _session(target: str) -> Session:
    return Session(
        format_version=1,
        environment="prd",
        app_version="1.5.4+6",
        exported_at="2026-10-15T00:00:00Z",
        account_hash="acc123",
        access_token="synthetic-access-token",
        grpc_restart_token="restart123",
        client_uuid="client-uuid-1",
        device_code="devcode",
        roots={"grpc": target, "wss": "wss://x", "server": "https://x"},
        limits={},
        expires_at=None,
    )


class SendService:
    def __init__(self) -> None:
        self.received: asyncio.Future[tuple[bytes, dict[str, str]]] = (
            asyncio.get_running_loop().create_future()
        )

    async def rcs(self, request: bytes, context: aio.ServicerContext[bytes, bytes]) -> bytes:
        if not self.received.done():
            metadata = {str(k): str(v) for k, v in (context.invocation_metadata() or [])}
            self.received.set_result((request, metadata))
        return b"\x08\x01"


@pytest_asyncio.fixture
async def send_server() -> AsyncIterator[tuple[SendService, str]]:
    service = SendService()
    server = aio.server()
    server.add_generic_rpc_handlers(
        (
            grpc.method_handlers_generic_handler(
                SERVICE,
                {
                    "SendRcs": grpc.unary_unary_rpc_method_handler(service.rcs),
                    "SendXms": grpc.unary_unary_rpc_method_handler(service.rcs),
                },
            ),
        )
    )
    credentials = grpc.ssl_server_credentials(
        [
            (
                FIXTURES.joinpath("localhost-key.pem").read_bytes(),
                FIXTURES.joinpath("localhost.pem").read_bytes(),
            ),
        ]
    )
    port = server.add_secure_port("localhost:0", credentials)
    assert port
    target = f"localhost:{port}"
    await server.start()
    try:
        yield service, target
    finally:
        await server.stop(None)


def _transport(target: str) -> H2Transport:
    return H2Transport(
        target,
        stream_deadline=30,
        root_certificates=FIXTURES.joinpath("localhost.pem").read_bytes(),
    )


@pytest.mark.asyncio
async def test_send_message_dispatches_with_auth_metadata(
    send_server: tuple[SendService, str],
) -> None:
    service, target = send_server
    transport = _transport(target)
    try:
        response = await send_message(
            transport, _session(target), DEVICE, SEND_RCS_METHOD, b"\x0a\x03req"
        )
    finally:
        await transport.aclose()

    request, metadata = await asyncio.wait_for(service.received, 5)
    assert request == b"\x0a\x03req"
    assert response == b"\x08\x01"
    assert metadata["authorization"] == "Bearer synthetic-access-token"
    assert metadata["x-mcs-deviceid"] == "synthetic-device"
    assert metadata["x-mcs-clientversion"] == "1.5.4.6"


@pytest.mark.asyncio
async def test_client_send_xms_text_dispatches(send_server: tuple[SendService, str]) -> None:
    service, target = send_server
    client = SubscribeClient(_session(target), transport=_transport(target))
    try:
        response = await client.send_xms_text(
            DEVICE, "+821000000000", "hi", client_ip="203.0.113.9"
        )
    finally:
        await client.aclose()
    request, metadata = await asyncio.wait_for(service.received, 5)
    assert request == build_xms_text_request("+821000000000", "hi", "203.0.113.9")
    assert response == b"\x08\x01"
    assert metadata["authorization"] == "Bearer synthetic-access-token"


@pytest.mark.asyncio
async def test_client_send_xms_image_uploads_then_sends(
    send_server: tuple[SendService, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    from chatplus_core.send import build_xms_mms_request
    from chatplus_core.upload import FileRef

    service, target = send_server
    ref = FileRef(
        href="https://s-ft.example/oasis/oapi/v1/file?id=1",
        size=322,
        file_name="pic.png",
        content_type="image/png",
    )

    async def fake_upload(
        root: str, token: str, name: str, ctype: str, data: bytes, **_: object
    ) -> FileRef:
        assert root == "https://s-ft.example/oasis"
        assert token == "file-token"
        return ref

    monkeypatch.setattr("chatplus_core.client.upload_mms_file", fake_upload)
    client = SubscribeClient(_session(target), transport=_transport(target))
    try:
        response = await client.send_xms_image(
            DEVICE,
            "+821000000000",
            "pic.png",
            "image/png",
            b"\x89PNG",
            "file-token",
            small_file_root="https://s-ft.example/oasis",
            client_ip="203.0.113.9",
        )
    finally:
        await client.aclose()
    request, _ = await asyncio.wait_for(service.received, 5)
    assert request == build_xms_mms_request(
        "+821000000000",
        "pic.png",
        "image/png",
        322,
        "https://s-ft.example/oasis/oapi/v1/file?id=1",
        "203.0.113.9",
    )
    assert response == b"\x08\x01"


def _write_session(tmp_path: Path) -> Path:
    exp = base64.urlsafe_b64encode(json.dumps({"exp": 4102444800.0}).encode()).decode().rstrip("=")
    export = {
        "format_version": 1,
        "environment": "prd",
        "app_version": "1.5.4+6",
        "exported_at": "2026-10-15T00:00:00Z",
        "account_hash": "abcdef123456",
        "tokens": {
            "access_token": f"h.{exp}.s",
            "grpc_restart_token": "restart",
        },
        "identity": {"client_uuid": "12345678-1234-4234-8234-123456789abc", "device_code": "dc"},
        "roots": {
            "grpc": "grpc.example.test:1520",
            "wss": "wss://ws.example.test",
            "server": "https://api.example.test",
            "small_file": "https://s.example.test",
            "large_file": "https://l.example.test",
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


def _write_device(tmp_path: Path) -> Path:
    path = tmp_path / "device.json"
    path.write_text(
        json.dumps(
            {
                "device_id": "d",
                "device_name": "n",
                "device_type": "SD",
                "os_version": "o",
                "client_version": "1.5.4.6",
                "firmware_version": "f",
                "client_ip": "203.0.113.9",
            }
        ),
        encoding="utf-8",
    )
    return path


def test_cli_send_xms_without_confirm_is_dry_run(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = main(
        [
            "send", "xms",
            "--credentials", str(_write_session(tmp_path)),
            "--device", str(_write_device(tmp_path)),
            "--to", "+821000000000",
            "--text", "hello",
        ]
    )
    captured = capsys.readouterr()
    assert code == 2
    assert captured.out == ""
    assert "would_send" in captured.err


def test_build_xms_mms_matches_captured_layout() -> None:
    request = build_xms_mms_request(
        "+821000000000", "pic.png", "image/png", 322, "https://s-ft.example/f?id=1", "203.0.113.9"
    )
    top = _fields(request)
    assert [(f, wt) for f, wt, _ in top] == [(3, 2), (10, 2)]
    mms_bytes = top[0][2]
    assert isinstance(mms_bytes, bytes)
    mms = _fields(mms_bytes)
    assert [(f, wt) for f, wt, _ in mms] == [(1, 2), (31, 2), (33, 2)]
    file_bytes = mms[2][2]
    assert isinstance(file_bytes, bytes)
    file_info = _fields(file_bytes)
    assert [(f, wt) for f, wt, _ in file_info] == [(1, 0), (2, 2), (3, 2), (4, 2)]
    assert file_info[0][2] == 322


def test_cli_send_xms_image_without_confirm_is_dry_run(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    image = tmp_path / "pic.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16)
    token = tmp_path / "ftok.txt"
    token.write_text("file-token", encoding="utf-8")
    code = main(
        [
            "send", "xms-image",
            "--credentials", str(_write_session(tmp_path)),
            "--device", str(_write_device(tmp_path)),
            "--to", "+821000000000",
            "--file", str(image),
            "--file-token-file", str(token),
        ]
    )
    captured = capsys.readouterr()
    assert code == 2
    assert captured.out == ""
    assert "xms_image" in captured.err
    assert "image/png" in captured.err


def test_send_method_paths_are_fixed() -> None:
    assert SEND_RCS_METHOD == "/oasis.client.api.SendMessageService/SendRcs"
    assert SEND_XMS_METHOD == "/oasis.client.api.SendMessageService/SendXms"


def _varint(b: bytes, p: int) -> tuple[int, int]:
    result = shift = 0
    while True:
        byte = b[p]
        p += 1
        result |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return result, p
        shift += 7


def _fields(b: bytes) -> list[tuple[int, int, bytes | int]]:
    out: list[tuple[int, int, bytes | int]] = []
    p = 0
    while p < len(b):
        tag, p = _varint(b, p)
        field, wt = tag >> 3, tag & 7
        if wt == 0:
            value, p = _varint(b, p)
            out.append((field, wt, value))
        else:
            ln, p = _varint(b, p)
            out.append((field, wt, b[p : p + ln]))
            p += ln
    return out


def test_build_xms_text_matches_captured_layout() -> None:
    request = build_xms_text_request("+821000000000", "테스트냥", "203.0.113.9")
    top = _fields(request)
    assert [(f, wt) for f, wt, _ in top] == [(1, 2), (10, 2)]
    message, client_ip = top[0][2], top[1][2]
    assert isinstance(message, bytes) and isinstance(client_ip, bytes)
    assert client_ip.decode() == "203.0.113.9"
    inner = _fields(message)
    assert [(f, wt) for f, wt, _ in inner] == [(1, 2), (4, 0), (31, 2)]
    recipient, kind, body = inner[0][2], inner[1][2], inner[2][2]
    assert isinstance(recipient, bytes) and recipient.decode() == "+821000000000"
    assert kind == 1
    assert isinstance(body, bytes) and body.decode() == "테스트냥"
