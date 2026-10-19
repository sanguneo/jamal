from __future__ import annotations

from typing import Final

from jamal.credentials import Session
from jamal.subscribe import DeviceHeaders, build_metadata
from jamal.transport.base import Transport

SEND_RCS_METHOD: Final = "/oasis.client.api.SendMessageService/SendRcs"
SEND_XMS_METHOD: Final = "/oasis.client.api.SendMessageService/SendXms"
SEND_BULK_IMDN_METHOD: Final = "/oasis.client.api.SendMessageService/SendBulkImdn"

_XMS_TEXT_MESSAGE: Final = 1
_XMS_MMS_MESSAGE: Final = 3
_XMS_CLIENT_IP: Final = 10
_TEXT_RECIPIENT: Final = 1
_TEXT_KIND: Final = 4
_TEXT_BODY: Final = 31
_TEXT_KIND_VALUE: Final = 1
_MMS_RECIPIENT: Final = 1
_MMS_CAPTION: Final = 31
_MMS_FILE_INFO: Final = 33
_FILE_SIZE: Final = 1
_FILE_NAME: Final = 2
_FILE_CONTENT_TYPE: Final = 3
_FILE_HREF: Final = 4


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


def _len_field(field: int, payload: bytes) -> bytes:
    return _varint((field << 3) | 2) + _varint(len(payload)) + payload


def _varint_field(field: int, value: int) -> bytes:
    return _varint((field << 3) | 0) + _varint(value)


def build_xms_text_request(recipient: str, text: str, client_ip: str) -> bytes:
    inner = (
        _len_field(_TEXT_RECIPIENT, recipient.encode("utf-8"))
        + _varint_field(_TEXT_KIND, _TEXT_KIND_VALUE)
        + _len_field(_TEXT_BODY, text.encode("utf-8"))
    )
    return _len_field(_XMS_TEXT_MESSAGE, inner) + _len_field(
        _XMS_CLIENT_IP, client_ip.encode("utf-8")
    )


def build_xms_mms_request(
    recipient: str,
    file_name: str,
    content_type: str,
    size: int,
    href: str,
    client_ip: str,
    *,
    caption: str = "",
) -> bytes:
    file_info = (
        _varint_field(_FILE_SIZE, size)
        + _len_field(_FILE_NAME, file_name.encode("utf-8"))
        + _len_field(_FILE_CONTENT_TYPE, content_type.encode("utf-8"))
        + _len_field(_FILE_HREF, href.encode("utf-8"))
    )
    mms = (
        _len_field(_MMS_RECIPIENT, recipient.encode("utf-8"))
        + _len_field(_MMS_CAPTION, caption.encode("utf-8"))
        + _len_field(_MMS_FILE_INFO, file_info)
    )
    return _len_field(_XMS_MMS_MESSAGE, mms) + _len_field(
        _XMS_CLIENT_IP, client_ip.encode("utf-8")
    )


async def send_message(
    transport: Transport,
    session: Session,
    device: DeviceHeaders,
    method: str,
    request: bytes,
    *,
    deadline: float = 30.0,
) -> bytes:
    """Dispatch one prepared unary send and return the raw response bytes.

    This performs a real network send; the caller owns request encoding and the
    explicit send approval, so it must run only behind that confirmation.
    """
    metadata = build_metadata(session, device)
    return await transport.unary(method, request, metadata, deadline)
