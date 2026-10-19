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

from jamal.client import ClientError, SubscribeClient, _resolve_grpc_root
from jamal.credentials import Session
from jamal.subscribe import (
    DeviceHeaders,
    build_subscribe_request,
    subscription_key,
)

SERVICE = "oasis.client.api.SubscribeNotificationService"
FIXTURES = Path(__file__).parent / "fixtures" / "transport"

DEVICE = DeviceHeaders(
    device_id="synthetic-device",
    device_name="offline-test",
    device_type="SD",
    os_version="test-os",
    client_version="1.5.4.6",
    firmware_version="test-firmware",
)


def _varint(value: int) -> bytes:
    out = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        if value:
            out.append(byte | 0x80)
        else:
            out.append(byte)
            return bytes(out)


def _len_field(field_number: int, payload: bytes) -> bytes:
    return _varint((field_number << 3) | 2) + _varint(len(payload)) + payload


def _varint_field(field_number: int, value: int) -> bytes:
    return _varint((field_number << 3) | 0) + _varint(value)


def _message_notification(text: str) -> bytes:
    nms = json.dumps(
        {
            "mdn": "+820000000000",
            "nmsEventList": {
                "index": 1,
                "restartToken": "cmVzdGFydA==",
                "nmsEvent": [
                    {
                        "changedObject": {
                            "attributes": {
                                "attribute": [
                                    {"name": "From", "value": "tel:+820000000001"},
                                    {"name": "To", "value": ["tel:+820000000000"]},
                                    {"name": "Direction", "value": "In"},
                                    {"name": "TextContent", "value": text},
                                ]
                            }
                        }
                    }
                ],
            },
        }
    )
    return _varint_field(3, 1792000000) + _len_field(2, nms.encode("utf-8"))


def _control_notification() -> bytes:
    return _varint_field(3, 1792000001) + _len_field(1, b"\x0a\x04ctrl")


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


class NotifyService:
    def __init__(self) -> None:
        self.received: asyncio.Future[tuple[bytes, dict[str, str]]] = (
            asyncio.get_running_loop().create_future()
        )

    async def stream(
        self, request: bytes, context: aio.ServicerContext[bytes, bytes]
    ) -> AsyncIterator[bytes]:
        if not self.received.done():
            metadata = {str(k): str(v) for k, v in (context.invocation_metadata() or [])}
            self.received.set_result((request, metadata))
        yield _message_notification("hello there")
        yield _control_notification()

    def client(self, target: str) -> SubscribeClient:
        from jamal.transport.h2 import H2Transport

        transport = H2Transport(
            target,
            stream_deadline=30,
            root_certificates=FIXTURES.joinpath("localhost.pem").read_bytes(),
        )
        return SubscribeClient(_session(target), transport=transport)


@pytest_asyncio.fixture
async def notify() -> AsyncIterator[tuple[NotifyService, str]]:
    service = NotifyService()
    server = aio.server()
    server.add_generic_rpc_handlers(
        (
            grpc.method_handlers_generic_handler(
                SERVICE,
                {"SubscribeNotification": grpc.unary_stream_rpc_method_handler(service.stream)},
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


def test_subscription_key_round_trip() -> None:
    key = subscription_key("acc", "cid", 123456)
    assert base64.b64decode(key).decode("ascii") == "acc:cid:123456"


def test_build_subscribe_request_is_field_two_string() -> None:
    request = build_subscribe_request("acc", "cid", 123456)
    assert request[0] == (2 << 3) | 2
    length = request[1]
    assert request[2 : 2 + length].decode("ascii") == subscription_key("acc", "cid", 123456)


def test_resolve_grpc_root_requires_grpc() -> None:
    session = _session("")
    object.__setattr__(session, "roots", {})
    with pytest.raises(ClientError):
        _resolve_grpc_root(session, None)


@pytest.mark.asyncio
async def test_subscribe_end_to_end(notify: tuple[NotifyService, str]) -> None:
    service, target = notify
    client = service.client(target)
    try:
        notifications = [note async for note in client.subscribe(DEVICE, epoch_micros=999)]
    finally:
        await client.aclose()

    request, metadata = await asyncio.wait_for(service.received, 5)
    assert request == build_subscribe_request("acc123", "client-uuid-1", 999)
    assert metadata["authorization"] == "Bearer synthetic-access-token"
    assert metadata["x-mcs-deviceid"] == "synthetic-device"

    assert len(notifications) == 2
    message_note, control_note = notifications
    assert len(message_note.messages) == 1
    assert message_note.messages[0].text == "hello there"
    assert message_note.messages[0].from_ == "+820000000001"
    assert message_note.messages[0].direction == "In"
    assert control_note.messages == ()
    assert control_note.control_bytes is not None


@pytest.mark.asyncio
async def test_expired_session_rejected(notify: tuple[NotifyService, str]) -> None:
    _, target = notify
    session = _session(target)
    object.__setattr__(session, "expires_at", 1.0)
    with pytest.raises(ClientError):
        SubscribeClient(session)
