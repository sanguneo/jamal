from __future__ import annotations

import argparse
import asyncio
import base64
import json
import mimetypes
import sys
from collections.abc import Sequence
from pathlib import Path

from jamal.client import ClientError, SubscribeClient
from jamal.codec import CodecError, Notification, decode_stream
from jamal.config import Config, ConfigValidationError, load_config_snapshot
from jamal.credentials import CredentialError, Session, load_session
from jamal.subscribe import DeviceHeaders


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="jamal", description="Low-level RCS Oasis receive client (no automation)."
    )
    commands = parser.add_subparsers(dest="command", required=True)

    credentials = commands.add_parser("credentials", help="Inspect an explicit session export")
    inspect = credentials.add_subparsers(dest="action", required=True).add_parser("inspect")
    inspect.add_argument("--credentials", required=True)

    config = commands.add_parser("config", help="Fetch verified bootstrap configuration (deferred)")
    fetch = config.add_subparsers(dest="action", required=True).add_parser("fetch")
    fetch.add_argument("--credentials", required=True)
    fetch.add_argument("--out", required=True)

    decode = commands.add_parser("decode", help="Decode an offline captured fixture")
    decode.add_argument("--input", required=True)
    decode.add_argument("--type", required=True, choices=["notification"])

    subscribe = commands.add_parser("subscribe", help="Subscribe to notifications (read-only)")
    subscribe.add_argument("--credentials", required=True)
    subscribe.add_argument("--config", required=True)
    subscribe.add_argument("--device", required=True)
    subscribe.add_argument("--epoch-micros", type=int)
    subscribe.add_argument("--limit", type=int)

    capability = commands.add_parser("capability", help="Query identifiers (not in receive MVP)")
    query = capability.add_subparsers(dest="action", required=True).add_parser("query")
    query.add_argument("--request", required=True)

    send = commands.add_parser("send", help="Send a message (real network dispatch, gated)")
    variants = send.add_subparsers(dest="action", required=True)
    xms = variants.add_parser("xms", help="Send an XMS text message to one recipient")
    xms.add_argument("--credentials", required=True)
    xms.add_argument("--config")
    xms.add_argument("--device", required=True)
    xms.add_argument("--to", required=True)
    xms.add_argument("--text", required=True)
    xms.add_argument("--client-ip")
    xms.add_argument("--confirm-send", action="store_true")
    image = variants.add_parser("xms-image", help="Upload and send an image XMS to one recipient")
    image.add_argument("--credentials", required=True)
    image.add_argument("--config")
    image.add_argument("--device", required=True)
    image.add_argument("--to", required=True)
    image.add_argument("--file", required=True)
    image.add_argument("--file-token-file", required=True)
    image.add_argument("--content-type")
    image.add_argument("--caption", default="")
    image.add_argument("--client-ip")
    image.add_argument("--confirm-send", action="store_true")
    variants.add_parser("rcs", help="SendRcs encoding is not captured yet").add_argument(
        "--request", required=True
    )
    return parser


def _notification_json(note: Notification) -> dict[str, object]:
    return {
        "push_epoch": note.push_epoch,
        "has_control": note.control_bytes is not None,
        "restart_token": note.restart_token,
        "messages": [
            {
                "from": message.from_,
                "to": list(message.to),
                "conversation_id": message.conversation_id,
                "date": message.date,
                "direction": message.direction,
                "content_type": message.content_type,
                "text": message.text,
            }
            for message in note.messages
        ],
    }


def _cmd_credentials_inspect(credentials_path: str) -> int:
    session = load_session(credentials_path)
    report = {
        "environment": session.environment,
        "app_version": session.app_version,
        "exported_at": session.exported_at,
        "expires_at": session.expires_at,
        "is_expired": session.is_expired(),
        "has_access_token": bool(session.access_token),
        "has_grpc_restart_token": bool(session.grpc_restart_token),
        "roots": sorted(session.roots.keys()),
    }
    print(json.dumps(report, ensure_ascii=False))
    return 0


def _read_frame_bytes(input_path: str) -> bytes:
    text = Path(input_path).read_text(encoding="utf-8").strip()
    if text.startswith("{"):
        payload = json.loads(text)
        encoded = payload.get("grpc_frame_b64")
        if not isinstance(encoded, str):
            raise CodecError("input JSON has no grpc_frame_b64 string")
        return base64.b64decode(encoded)
    return base64.b64decode(text)


def _cmd_decode(input_path: str) -> int:
    for note in decode_stream(_read_frame_bytes(input_path)):
        print(json.dumps(_notification_json(note), ensure_ascii=False))
    return 0


def _load_device(device_path: str) -> DeviceHeaders:
    data = json.loads(Path(device_path).read_text(encoding="utf-8"))
    return DeviceHeaders(
        device_id=data["device_id"],
        device_name=data["device_name"],
        device_type=data["device_type"],
        os_version=data["os_version"],
        client_version=data["client_version"],
        firmware_version=data["firmware_version"],
        client_ip=data.get("client_ip", "127.0.0.1"),
        user_agent=data.get("user_agent", "Dart/3.10 (dart:io)"),
    )


async def _run_send_xms_image(args: argparse.Namespace) -> int:
    session = load_session(args.credentials)
    config = load_config_snapshot(args.config) if args.config else None
    device = _load_device(args.device)
    path = Path(args.file)
    data = path.read_bytes()
    content_type = (
        args.content_type or mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    )
    client_ip = args.client_ip or device.client_ip
    if not args.confirm_send:
        preview = {
            "would_send": "xms_image",
            "to": args.to,
            "file": path.name,
            "content_type": content_type,
            "size": len(data),
            "hint": "re-run with --confirm-send to dispatch a real message",
        }
        print(json.dumps(preview, ensure_ascii=False), file=sys.stderr)
        return 2
    file_token = Path(args.file_token_file).read_text(encoding="utf-8").strip()
    async with SubscribeClient(session, config=config) as client:
        response = await client.send_xms_image(
            device,
            args.to,
            path.name,
            content_type,
            data,
            file_token,
            caption=args.caption,
            client_ip=client_ip,
        )
    print(json.dumps({"sent": True, "response_bytes": len(response)}, ensure_ascii=False))
    return 0


async def _run_send_xms(args: argparse.Namespace) -> int:
    session = load_session(args.credentials)
    config = load_config_snapshot(args.config) if args.config else None
    device = _load_device(args.device)
    client_ip = args.client_ip or device.client_ip
    if not args.confirm_send:
        preview = {
            "would_send": "xms_text",
            "to": args.to,
            "text_len": len(args.text),
            "client_ip": client_ip,
            "hint": "re-run with --confirm-send to dispatch a real message",
        }
        print(json.dumps(preview, ensure_ascii=False), file=sys.stderr)
        return 2
    async with SubscribeClient(session, config=config) as client:
        response = await client.send_xms_text(device, args.to, args.text, client_ip=client_ip)
    print(json.dumps({"sent": True, "response_bytes": len(response)}, ensure_ascii=False))
    return 0


async def _run_subscribe(
    session: Session,
    config: Config,
    device: DeviceHeaders,
    epoch_micros: int | None,
    limit: int | None,
) -> int:
    count = 0
    async with SubscribeClient(session, config=config) as client:
        async for note in client.subscribe(device, epoch_micros=epoch_micros):
            print(json.dumps(_notification_json(note), ensure_ascii=False), flush=True)
            count += 1
            if limit is not None and count >= limit:
                break
    return 0


def _cmd_subscribe(args: argparse.Namespace) -> int:
    session = load_session(args.credentials)
    config = load_config_snapshot(args.config)
    device = _load_device(args.device)
    return asyncio.run(_run_subscribe(session, config, device, args.epoch_micros, args.limit))


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "credentials":
            return _cmd_credentials_inspect(args.credentials)
        if args.command == "decode":
            return _cmd_decode(args.input)
        if args.command == "subscribe":
            return _cmd_subscribe(args)
        if args.command == "send" and args.action == "xms":
            return asyncio.run(_run_send_xms(args))
        if args.command == "send" and args.action == "xms-image":
            return asyncio.run(_run_send_xms_image(args))
        if args.command == "config":
            print("config fetch: deferred (encrypted bootstrap not implemented)", file=sys.stderr)
            return 2
        if args.command == "send":
            print("send rcs: SendRcs encoding is not captured yet", file=sys.stderr)
            return 2
        print(f"{args.command}: not available in the receive MVP", file=sys.stderr)
        return 2
    except (
        CredentialError,
        ConfigValidationError,
        CodecError,
        ClientError,
        OSError,
        ValueError,
    ) as error:
        print(f"error: {type(error).__name__}: {error}", file=sys.stderr)
        return 1
