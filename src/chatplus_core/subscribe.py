from __future__ import annotations

import base64
import time
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass

from chatplus_core.codec import Notification, decode_notification
from chatplus_core.credentials import Session
from chatplus_core.transport.base import Transport

SUBSCRIBE_METHOD = "/oasis.client.api.SubscribeNotificationService/SubscribeNotification"


@dataclass(frozen=True, slots=True)
class DeviceHeaders:
    device_id: str
    device_name: str
    device_type: str
    os_version: str
    client_version: str
    firmware_version: str
    client_ip: str = "127.0.0.1"
    user_agent: str = "Dart/3.10 (dart:io)"

    def as_metadata(self) -> dict[str, str]:
        return {
            "x-mcs-deviceid": self.device_id,
            "x-mcs-devicename": self.device_name,
            "x-mcs-devicetype": self.device_type,
            "x-mcs-osversion": self.os_version,
            "x-mcs-clientversion": self.client_version,
            "x-mcs-firmwareversion": self.firmware_version,
            "x-mcs-clientip": self.client_ip,
            "user-agent": self.user_agent,
        }


def subscription_key(account_hash: str, client_id: str, epoch_micros: int) -> str:
    raw = f"{account_hash}:{client_id}:{epoch_micros}".encode()
    return base64.b64encode(raw).decode("ascii")


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


def build_subscribe_request(account_hash: str, client_id: str, epoch_micros: int) -> bytes:
    key = subscription_key(account_hash, client_id, epoch_micros).encode("ascii")
    return _varint((2 << 3) | 2) + _varint(len(key)) + key


def build_metadata(session: Session, device: DeviceHeaders) -> dict[str, str]:
    metadata = device.as_metadata()
    metadata["authorization"] = f"Bearer {session.access_token}"
    return metadata


async def subscribe(
    transport: Transport,
    session: Session,
    device: DeviceHeaders,
    *,
    epoch_micros: int | None = None,
    metadata_extra: Mapping[str, str] | None = None,
) -> AsyncIterator[Notification]:
    if epoch_micros is None:
        epoch_micros = time.time_ns() // 1000
    request = build_subscribe_request(session.account_hash, session.client_uuid, epoch_micros)
    metadata = build_metadata(session, device)
    if metadata_extra:
        metadata.update(metadata_extra)
    async for message in transport.server_stream(SUBSCRIBE_METHOD, request, metadata):
        yield decode_notification(message)
