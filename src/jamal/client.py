from __future__ import annotations

from collections.abc import AsyncIterator
from types import TracebackType
from typing import Self

from jamal.codec import Notification
from jamal.config import Config
from jamal.credentials import Session
from jamal.send import (
    SEND_XMS_METHOD,
    build_xms_mms_request,
    build_xms_text_request,
    send_message,
)
from jamal.subscribe import DeviceHeaders, subscribe
from jamal.transport.base import Transport
from jamal.transport.h2 import H2Transport
from jamal.upload import upload_mms_file


class ClientError(Exception):
    pass


def _resolve_grpc_root(session: Session, config: Config | None) -> str:
    if config is not None:
        if config.environment != session.environment:
            raise ClientError("session and config environments differ")
        return config.oasis_grpc_root
    root = session.roots.get("grpc")
    if not root:
        raise ClientError("session is missing the grpc root")
    return root


class SubscribeClient:
    def __init__(
        self,
        session: Session,
        *,
        config: Config | None = None,
        stream_deadline: float = 3600.0,
        root_certificates: bytes | None = None,
        transport: Transport | None = None,
    ) -> None:
        if session.is_expired():
            raise ClientError("session access token is expired; export again")
        self._session = session
        self._transport: Transport = transport or H2Transport(
            _resolve_grpc_root(session, config),
            stream_deadline=stream_deadline,
            root_certificates=root_certificates,
        )

    def subscribe(
        self, device: DeviceHeaders, *, epoch_micros: int | None = None
    ) -> AsyncIterator[Notification]:
        return subscribe(self._transport, self._session, device, epoch_micros=epoch_micros)

    async def send_xms_text(
        self, device: DeviceHeaders, recipient: str, text: str, *, client_ip: str | None = None
    ) -> bytes:
        request = build_xms_text_request(recipient, text, client_ip or device.client_ip)
        return await send_message(self._transport, self._session, device, SEND_XMS_METHOD, request)

    async def send_xms_image(
        self,
        device: DeviceHeaders,
        recipient: str,
        file_name: str,
        content_type: str,
        data: bytes,
        file_token: str,
        *,
        small_file_root: str | None = None,
        caption: str = "",
        client_ip: str | None = None,
    ) -> bytes:
        root = small_file_root or self._session.roots.get("small_file")
        if not root:
            raise ClientError("session is missing the small_file root")
        ref = await upload_mms_file(root, file_token, file_name, content_type, data)
        request = build_xms_mms_request(
            recipient,
            ref.file_name,
            ref.content_type,
            ref.size,
            ref.href,
            client_ip or device.client_ip,
            caption=caption,
        )
        return await send_message(self._transport, self._session, device, SEND_XMS_METHOD, request)

    async def aclose(self) -> None:
        await self._transport.aclose()

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.aclose()
