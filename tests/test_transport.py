"""Offline native gRPC integration tests; all sockets are TLS on localhost.

The checked-in key/certificate are synthetic test-only material, valid 2020-2040.
No production trust override or service connection is used.
"""

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

import grpc
import pytest
import pytest_asyncio
from grpc import aio

from jamal.transport.base import TransportOverflowError
from jamal.transport.h2 import H2Transport

SERVICE = "oasis.client.api.SubscribeNotificationService"
METHOD = f"/{SERVICE}/SubscribeNotification"
UNARY = f"/{SERVICE}/Echo"
FRAMES = [b"\x12\x00", b"\xff\x00opaque-not-protobuf", b""]
METADATA = {
    "authorization": "Bearer synthetic-test-token",
    "x-mcs-deviceid": "synthetic-device",
    "x-mcs-devicename": "offline-test",
    "x-mcs-devicetype": "SD",
    "x-mcs-osversion": "test-os",
    "x-mcs-clientversion": "1.5.4.6",
    "x-mcs-firmwareversion": "test-firmware",
    "x-mcs-clientip": "127.0.0.1",
    "user-agent": "Dart/3.10",
}
FIXTURES = Path(__file__).parent / "fixtures" / "transport"


class LocalService:
    def __init__(self) -> None:
        self.received = asyncio.get_running_loop().create_future()
        self.finished = asyncio.Event()
        self.release = asyncio.Event()
        self.calls = 0
        self.target = ""

    def capture(self, request: bytes, context: aio.ServicerContext) -> None:
        self.calls += 1
        if not self.received.done():
            self.received.set_result(
                (request, dict(context.invocation_metadata()), context.time_remaining())
            )

    async def stream(
        self, request: bytes, context: aio.ServicerContext
    ) -> AsyncIterator[bytes]:
        self.capture(request, context)
        try:
            if request == b"blocked":
                await self.release.wait()
            elif request == b"burst":
                yield FRAMES[0]
                await self.release.wait()
                for _ in range(3):
                    yield FRAMES[1]
                await asyncio.get_running_loop().create_future()
            elif request == b"error":
                context.set_trailing_metadata((("test-status", "finished"),))
                await context.abort(grpc.StatusCode.UNAUTHENTICATED, "synthetic rejection")
            elif request == b"large":
                yield b"x" * 512
            else:
                for frame in FRAMES:
                    yield frame
        finally:
            self.finished.set()

    async def unary(self, request: bytes, context: aio.ServicerContext) -> bytes:
        self.capture(request, context)
        try:
            if request == b"blocked":
                await self.release.wait()
            return request
        finally:
            self.finished.set()

    def transport(
        self,
        *,
        stream_deadline: float = 30,
        max_buffer_messages: int = 16,
        max_message_bytes: int = 4 * 1024 * 1024,
    ) -> H2Transport:
        return H2Transport(
            self.target,
            stream_deadline=stream_deadline,
            root_certificates=FIXTURES.joinpath("localhost.pem").read_bytes(),
            max_buffer_messages=max_buffer_messages,
            max_message_bytes=max_message_bytes,
        )


@pytest_asyncio.fixture
async def local_service() -> AsyncIterator[LocalService]:
    service = LocalService()
    server = aio.server()
    server.add_generic_rpc_handlers(
        (
            grpc.method_handlers_generic_handler(
                SERVICE,
                {
                    "SubscribeNotification": grpc.unary_stream_rpc_method_handler(service.stream),
                    "Echo": grpc.unary_unary_rpc_method_handler(service.unary),
                },
            ),
        )
    )
    credentials = grpc.ssl_server_credentials(
        (
            (
                FIXTURES.joinpath("localhost-key.pem").read_bytes(),
                FIXTURES.joinpath("localhost.pem").read_bytes(),
            ),
        )
    )
    port = server.add_secure_port("localhost:0", credentials)
    assert port
    service.target = f"localhost:{port}"
    await server.start()
    try:
        yield service
    finally:
        await server.stop(None)


@pytest.mark.asyncio
async def test_stream_raw_bytes_metadata_deadline_and_eof(local_service: LocalService) -> None:
    transport = local_service.transport()
    request = b"\x12\x02\xff\x00"

    async def consume() -> list[bytes]:
        return [frame async for frame in transport.server_stream(METHOD, request, METADATA)]

    task = asyncio.create_task(consume())
    received, metadata, deadline = await asyncio.wait_for(local_service.received, 5)
    assert received == request
    for key, value in METADATA.items():
        if key == "user-agent":
            assert metadata[key].startswith(value)
        else:
            assert metadata[key] == value
    # grpc-timeout is rounded on the wire; exact expiration is tested separately.
    assert deadline is not None and deadline > 0
    assert "oasis-encrypt" not in metadata
    assert "grpc-encoding" not in metadata
    assert await asyncio.wait_for(task, 5) == FRAMES
    assert transport.closed
    assert local_service.calls == 1
    with pytest.raises(RuntimeError, match="closed"):
        await transport.unary(UNARY, request, METADATA, 1)


@pytest.mark.asyncio
async def test_unary_raw_bytes(local_service: LocalService) -> None:
    request = b"\x00\xffnot-a-grpc-envelope"
    async with local_service.transport() as transport:
        task = asyncio.create_task(transport.unary(UNARY, request, METADATA, 5))
        received, _, deadline = await asyncio.wait_for(local_service.received, 5)
        assert received == request
        assert deadline is not None and deadline > 0
        assert await task == request
    assert transport.closed


@pytest.mark.asyncio
@pytest.mark.parametrize("streaming", [True, False])
async def test_cancellation_closes_rpc_and_channel(
    local_service: LocalService, streaming: bool
) -> None:
    transport = local_service.transport()
    stream = transport.server_stream(METHOD, b"blocked", METADATA)
    task = asyncio.create_task(
        anext(stream) if streaming else transport.unary(UNARY, b"blocked", METADATA, 30)
    )
    await asyncio.wait_for(local_service.received, 5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await asyncio.wait_for(local_service.finished.wait(), 5)
    assert transport.closed
    assert local_service.calls == 1


@pytest.mark.asyncio
async def test_slow_consumer_overflow_is_explicit(local_service: LocalService) -> None:
    transport = local_service.transport(max_buffer_messages=1)
    stream = transport.server_stream(METHOD, b"burst", METADATA)
    first = asyncio.create_task(anext(stream))
    await asyncio.wait_for(local_service.received, 5)
    assert await first == FRAMES[0]
    # No next() is outstanding. The producer must fill the one-message buffer,
    # cancel the server RPC, and signal overflow before the consumer resumes.
    local_service.release.set()
    await asyncio.wait_for(local_service.finished.wait(), 5)
    with pytest.raises(TransportOverflowError):
        await anext(stream)
    assert transport.closed
    assert local_service.calls == 1


@pytest.mark.asyncio
async def test_close_while_iterator_is_paused(local_service: LocalService) -> None:
    transport = local_service.transport()
    stream = transport.server_stream(METHOD, b"burst", METADATA)
    first = asyncio.create_task(anext(stream))
    await asyncio.wait_for(local_service.received, 5)
    assert await first == FRAMES[0]
    await transport.aclose()
    await asyncio.wait_for(local_service.finished.wait(), 5)
    await stream.aclose()
    assert transport.closed


@pytest.mark.asyncio
@pytest.mark.parametrize("streaming", [True, False])
async def test_deadline_is_enforced(local_service: LocalService, streaming: bool) -> None:
    transport = local_service.transport(stream_deadline=5)
    async with transport:
        task = asyncio.create_task(
            anext(transport.server_stream(METHOD, b"blocked", METADATA))
            if streaming
            else transport.unary(UNARY, b"blocked", METADATA, 5)
        )
        with pytest.raises(aio.AioRpcError) as error:
            await task
        assert error.value.code() == grpc.StatusCode.DEADLINE_EXCEEDED
    assert transport.closed


@pytest.mark.asyncio
async def test_status_trailers_are_not_treated_as_messages(local_service: LocalService) -> None:
    transport = local_service.transport()
    task = asyncio.create_task(anext(transport.server_stream(METHOD, b"error", METADATA)))
    await asyncio.wait_for(local_service.received, 5)
    with pytest.raises(aio.AioRpcError) as error:
        await task
    assert error.value.code() == grpc.StatusCode.UNAUTHENTICATED
    assert error.value.trailing_metadata()["test-status"] == "finished"
    assert local_service.calls == 1
    assert transport.closed


@pytest.mark.asyncio
async def test_message_size_is_bounded(local_service: LocalService) -> None:
    transport = local_service.transport(max_message_bytes=64)
    task = asyncio.create_task(anext(transport.server_stream(METHOD, b"large", METADATA)))
    await asyncio.wait_for(local_service.received, 5)
    with pytest.raises(aio.AioRpcError) as error:
        await task
    assert error.value.code() == grpc.StatusCode.RESOURCE_EXHAUSTED
    assert transport.closed


@pytest.mark.asyncio
async def test_tls_does_not_trust_unknown_certificate(local_service: LocalService) -> None:
    async with H2Transport(local_service.target, stream_deadline=5) as transport:
        with pytest.raises(aio.AioRpcError) as error:
            await transport.unary(UNARY, b"test", METADATA, 5)
        assert error.value.code() == grpc.StatusCode.UNAVAILABLE
    assert local_service.calls == 0


@pytest.mark.asyncio
async def test_missing_metadata_fails_before_connection(local_service: LocalService) -> None:
    async with local_service.transport() as transport:
        with pytest.raises(ValueError, match="metadata"):
            await transport.unary(UNARY, b"test", {}, 5)
    assert local_service.calls == 0
