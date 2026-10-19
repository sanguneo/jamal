"""Native gRPC with opaque protobuf payloads and no reconnect or resume.

grpc.aio owns HTTP/2, TLS, length-prefixed framing, deadlines and status trailers.
One transport owns one channel and at most one subscription. Stream termination
or cancellation closes that channel; use a new transport for an explicit restart.
"""

import asyncio
from collections import deque
from collections.abc import AsyncIterator, Mapping
from contextlib import suppress
from types import TracebackType
from typing import Self, final, override

import grpc
from grpc import aio

from jamal.transport.base import Transport, TransportOverflowError

REQUIRED_METADATA = frozenset(
    {
        "authorization",
        "x-mcs-deviceid",
        "x-mcs-devicename",
        "x-mcs-devicetype",
        "x-mcs-osversion",
        "x-mcs-clientversion",
        "x-mcs-firmwareversion",
        "x-mcs-clientip",
        "user-agent",
    }
)


def _identity(payload: bytes) -> bytes:
    return payload


@final
class H2Transport(Transport):
    """Verified TLS gRPC endpoint; construction and imports do not connect.

    ``target`` is the configured host:port, with no hardcoded service fallback.
    ``stream_deadline`` and unary ``deadline`` are positive timeout seconds.
    Optional root certificates configure TLS trust, never disable verification.
    Memory is bounded by max_buffer_messages * max_message_bytes plus the active
    gRPC read. A full buffer raises TransportOverflowError and cancels the RPC.
    Use ``aclose`` (or the context manager) when abandoning an iterator after yield.
    """

    def __init__(
        self,
        target: str,
        *,
        stream_deadline: float,
        root_certificates: bytes | None = None,
        max_buffer_messages: int = 16,
        max_message_bytes: int = 4 * 1024 * 1024,
    ) -> None:
        if stream_deadline <= 0 or max_buffer_messages <= 0 or max_message_bytes <= 0:
            raise ValueError("deadlines and buffer limits must be positive")
        self._target = target
        self._stream_deadline = stream_deadline
        self._root_certificates = root_certificates
        self._max_buffer_messages = max_buffer_messages
        self._max_message_bytes = max_message_bytes
        self._channel: aio.Channel | None = None
        self._user_agent: str | None = None
        self._closed = False
        self._stream_started = False

    @property
    def closed(self) -> bool:
        """Whether this transport has closed and can no longer start RPCs."""
        return self._closed

    def _connection(self, metadata: Mapping[str, str]) -> aio.Channel:
        if self._closed:
            raise RuntimeError("transport is closed")
        if REQUIRED_METADATA.difference(metadata):
            raise ValueError("required gRPC metadata is missing")
        if self._channel is None:
            self._user_agent = metadata["user-agent"]
            self._channel = aio.secure_channel(
                self._target,
                grpc.ssl_channel_credentials(root_certificates=self._root_certificates),
                options=(
                    ("grpc.primary_user_agent", self._user_agent),
                    ("grpc.max_receive_message_length", self._max_message_bytes),
                    ("grpc.max_send_message_length", self._max_message_bytes),
                    ("grpc.enable_retries", 0),
                ),
                compression=grpc.Compression.NoCompression,
            )
        elif metadata["user-agent"] != self._user_agent:
            raise ValueError("user-agent cannot change on an existing channel")
        return self._channel

    @staticmethod
    def _metadata(metadata: Mapping[str, str]) -> tuple[tuple[str, str], ...]:
        # C-core owns the reserved user-agent header; primary_user_agent sets its prefix.
        return tuple((key, value) for key, value in metadata.items() if key != "user-agent")

    @override
    async def unary(
        self, method: str, payload: bytes, metadata: Mapping[str, str], deadline: float
    ) -> bytes:
        if deadline <= 0:
            raise ValueError("deadline must be positive")
        channel = self._connection(metadata)
        rpc: aio.UnaryUnaryMultiCallable[bytes, bytes] = channel.unary_unary(
            method, request_serializer=_identity, response_deserializer=_identity
        )
        try:
            return await rpc(payload, metadata=self._metadata(metadata), timeout=deadline)
        except asyncio.CancelledError:
            await self.aclose()
            raise

    @override
    async def server_stream(
        self, method: str, payload: bytes, metadata: Mapping[str, str]
    ) -> AsyncIterator[bytes]:
        if self._stream_started:
            raise RuntimeError("only one subscription is allowed per transport")
        channel = self._connection(metadata)
        self._stream_started = True
        rpc: aio.UnaryStreamMultiCallable[bytes, bytes] = channel.unary_stream(
            method, request_serializer=_identity, response_deserializer=_identity
        )
        call = rpc(payload, metadata=self._metadata(metadata), timeout=self._stream_deadline)
        buffered: deque[bytes] = deque()
        ready = asyncio.Event()
        async def receive() -> Exception | None:
            try:
                async for message in call:
                    if len(buffered) == self._max_buffer_messages:
                        _ = call.cancel()
                        return TransportOverflowError("receive buffer is full")
                    buffered.append(message)
                    ready.set()
            except asyncio.CancelledError:
                return RuntimeError("transport stream was closed")
            except Exception as error:
                return error
            finally:
                await self.aclose()
                ready.set()
            return None

        receiver = asyncio.create_task(receive())
        try:
            while True:
                _ = await ready.wait()
                if receiver.done():
                    failure = receiver.result()
                    if failure is not None:
                        raise failure
                if buffered:
                    yield buffered.popleft()
                elif receiver.done():
                    return
                else:
                    ready.clear()
        finally:
            _ = call.cancel()
            _ = receiver.cancel()
            with suppress(asyncio.CancelledError):
                _ = await receiver
            await self.aclose()

    @override
    async def aclose(self) -> None:
        self._closed = True
        if self._channel is not None:
            await self._channel.close()

    async def __aenter__(self) -> Self:
        if self._closed:
            raise RuntimeError("transport is closed")
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        await self.aclose()
