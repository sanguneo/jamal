from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest

from jamal.codec import (
    CodecError,
    decode_notification,
    decode_stream,
    iter_grpc_frames,
)

FIXTURE = Path(__file__).parent / "fixtures" / "receive" / "notification_synthetic.json"


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


def _frame(message: bytes) -> bytes:
    return b"\x00" + len(message).to_bytes(4, "big") + message


def _nms(events: list[dict[str, object]], *, index: int = 1, restart: str = "cmVzdGFydA==") -> str:
    return json.dumps(
        {
            "mdn": "+820000000000",
            "nmsEventList": {"index": index, "restartToken": restart, "nmsEvent": events},
        }
    )


def _event(attributes: list[dict[str, object]]) -> dict[str, object]:
    return {"changedObject": {"attributes": {"attribute": attributes}}}


def test_synthetic_fixture_decodes_to_expected() -> None:
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    frame = base64.b64decode(data["grpc_frame_b64"])
    notifications = decode_stream(frame)
    assert len(notifications) == 1
    note = notifications[0]
    expected = data["expected"]
    assert note.push_epoch == expected["push_epoch"]
    assert len(note.messages) == 1
    message = note.messages[0]
    assert message.from_ == expected["from"]
    assert message.to == (expected["to"],)
    assert message.conversation_id == expected["conversation_id"]
    assert message.direction == expected["direction"]
    assert message.text == expected["text"]
    assert message.content_type == expected["content_type"]


def test_multi_frame_stream_splits() -> None:
    body = _varint_field(3, 100) + _len_field(2, _nms([]).encode("utf-8"))
    stream = _frame(body) + _frame(body) + _frame(body)
    assert len(decode_stream(stream)) == 3


def test_truncated_trailing_frame_ignored() -> None:
    body = _varint_field(3, 100) + _len_field(2, _nms([]).encode("utf-8"))
    stream = _frame(body) + b"\x00\x00\x00\x10\x01\x02"
    assert len(list(iter_grpc_frames(stream))) == 1


def test_control_only_notification_has_no_messages() -> None:
    body = _varint_field(3, 42) + _len_field(1, b"\x0a\x04ctrl")
    note = decode_notification(body)
    assert note.push_epoch == 42
    assert note.control_bytes is not None
    assert note.messages == ()


def test_unknown_attribute_preserved() -> None:
    event = _event(
        [
            {"name": "From", "value": "tel:+820000000001"},
            {"name": "To", "value": ["tel:+820000000000"]},
            {"name": "TextContent", "value": "hi"},
            {"name": "X-Custom-Flag", "value": "weird"},
        ]
    )
    note = decode_notification(_varint_field(3, 1) + _len_field(2, _nms([event]).encode("utf-8")))
    assert note.messages[0].raw_attributes["X-Custom-Flag"] == "weird"


def test_missing_text_content_is_none_not_empty() -> None:
    event = _event(
        [
            {"name": "From", "value": "tel:+820000000001"},
            {"name": "To", "value": ["tel:+820000000000"]},
        ]
    )
    note = decode_notification(_varint_field(3, 1) + _len_field(2, _nms([event]).encode("utf-8")))
    assert note.messages[0].text is None


def test_to_accepts_single_string() -> None:
    event = _event(
        [{"name": "To", "value": "tel:+820000000000"}, {"name": "TextContent", "value": "hi"}]
    )
    note = decode_notification(_varint_field(3, 1) + _len_field(2, _nms([event]).encode("utf-8")))
    assert note.messages[0].to == ("+820000000000",)


def test_restart_token_and_index_exposed() -> None:
    note = decode_notification(
        _varint_field(3, 1) + _len_field(2, _nms([], index=7, restart="dG9rZW4=").encode("utf-8"))
    )
    assert note.nms_index == 7
    assert note.restart_token == "dG9rZW4="


def test_malformed_json_raises_codec_error() -> None:
    with pytest.raises(CodecError):
        decode_notification(_varint_field(3, 1) + _len_field(2, b"{not json"))


def test_unknown_protobuf_field_ignored() -> None:
    body = _varint_field(9, 123) + _varint_field(3, 55) + _len_field(2, _nms([]).encode("utf-8"))
    note = decode_notification(body)
    assert note.push_epoch == 55
