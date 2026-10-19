from __future__ import annotations

import json
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from typing import Final

_GRPC_PREFIX: Final = 5
_WT_VARINT: Final = 0
_WT_FIXED64: Final = 1
_WT_LEN: Final = 2
_WT_FIXED32: Final = 5


class CodecError(ValueError):
    pass


def _read_varint(data: bytes, pos: int) -> tuple[int, int]:
    result = 0
    shift = 0
    while pos < len(data):
        byte = data[pos]
        pos += 1
        result |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return result, pos
        shift += 7
    raise CodecError("truncated varint")


def iter_grpc_frames(data: bytes) -> Iterator[bytes]:
    pos = 0
    total = len(data)
    while pos + _GRPC_PREFIX <= total:
        length = int.from_bytes(data[pos + 1 : pos + _GRPC_PREFIX], "big")
        start = pos + _GRPC_PREFIX
        end = start + length
        if end > total:
            return
        yield data[start:end]
        pos = end


@dataclass(frozen=True, slots=True)
class Message:
    from_: str | None
    to: tuple[str, ...]
    conversation_id: str | None
    date: str | None
    content_type: str | None
    direction: str | None
    text: str | None
    raw_attributes: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class Notification:
    push_epoch: int | None
    control_bytes: bytes | None
    messages: tuple[Message, ...]
    nms_index: int | None = None
    restart_token: str | None = None
    unparsed_events: tuple[Mapping[str, object], ...] = field(default_factory=tuple)


def _as_str(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _strip_tel(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    return value[4:] if value.startswith("tel:") else value


def _attributes(event: Mapping[str, object]) -> list[Mapping[str, object]] | None:
    changed = event.get("changedObject")
    if not isinstance(changed, Mapping):
        return None
    attrs = changed.get("attributes")
    if not isinstance(attrs, Mapping):
        return None
    attribute = attrs.get("attribute")
    if not isinstance(attribute, list):
        return None
    return [a for a in attribute if isinstance(a, Mapping)]


def _message_from_event(event: Mapping[str, object]) -> Message | None:
    attribute = _attributes(event)
    if attribute is None:
        return None
    named: dict[str, object] = {}
    for item in attribute:
        name = item.get("name")
        if isinstance(name, str):
            named[name] = item.get("value")
    to_value = named.get("To")
    if isinstance(to_value, list):
        to = tuple(_strip_tel(v) or "" for v in to_value if isinstance(v, str))
    else:
        single = _strip_tel(to_value)
        to = (single,) if single is not None else ()
    text = named.get("TextContent")
    return Message(
        from_=_strip_tel(named.get("From")),
        to=to,
        conversation_id=_as_str(named.get("Conversation-ID")),
        date=_as_str(named.get("Date")),
        content_type=_as_str(named.get("Content-Type")),
        direction=_as_str(named.get("Direction")),
        text=_as_str(text),
        raw_attributes=named,
    )


def _parse_nms(
    payload: str,
) -> tuple[list[Message], int | None, str | None, list[Mapping[str, object]]]:
    try:
        parsed = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise CodecError("invalid NMS JSON") from exc
    if not isinstance(parsed, Mapping):
        raise CodecError("NMS payload is not an object")
    event_list = parsed.get("nmsEventList")
    if not isinstance(event_list, Mapping):
        return [], None, None, []
    index = event_list.get("index")
    restart = event_list.get("restartToken")
    events = event_list.get("nmsEvent")
    messages: list[Message] = []
    unparsed: list[Mapping[str, object]] = []
    if isinstance(events, list):
        for event in events:
            if not isinstance(event, Mapping):
                continue
            message = _message_from_event(event)
            if message is None:
                unparsed.append(event)
            else:
                messages.append(message)
    return (
        messages,
        index if isinstance(index, int) else None,
        restart if isinstance(restart, str) else None,
        unparsed,
    )


def decode_notification(msg: bytes) -> Notification:
    pos = 0
    push_epoch: int | None = None
    control_bytes: bytes | None = None
    nms_json: str | None = None
    total = len(msg)
    while pos < total:
        tag, pos = _read_varint(msg, pos)
        field_number = tag >> 3
        wire_type = tag & 0x07
        if wire_type == _WT_VARINT:
            value, pos = _read_varint(msg, pos)
            if field_number == 3:
                push_epoch = value
        elif wire_type == _WT_LEN:
            length, pos = _read_varint(msg, pos)
            end = pos + length
            if end > total:
                raise CodecError("truncated length-delimited field")
            chunk = msg[pos:end]
            pos = end
            if field_number == 1:
                control_bytes = chunk
            elif field_number == 2:
                nms_json = chunk.decode("utf-8", "replace")
        elif wire_type == _WT_FIXED64:
            pos += 8
        elif wire_type == _WT_FIXED32:
            pos += 4
        else:
            raise CodecError(f"unsupported wire type {wire_type}")
    if nms_json is None:
        return Notification(push_epoch=push_epoch, control_bytes=control_bytes, messages=())
    messages, index, restart, unparsed = _parse_nms(nms_json)
    return Notification(
        push_epoch=push_epoch,
        control_bytes=control_bytes,
        messages=tuple(messages),
        nms_index=index,
        restart_token=restart,
        unparsed_events=tuple(unparsed),
    )


def decode_stream(data: bytes) -> list[Notification]:
    return [decode_notification(frame) for frame in iter_grpc_frames(data)]
