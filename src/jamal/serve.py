"""Executable Oasis adapter; credentials and state stay outside the checkout."""

from __future__ import annotations

import argparse
import asyncio
import os
from collections.abc import Sequence
from pathlib import Path
from urllib.parse import urlsplit

import httpx
import uvicorn

from jamal.api import Adapter, create_app
from jamal.cache import CacheSource
from jamal.cli import _load_device
from jamal.client import SubscribeClient
from jamal.config import load_config_snapshot
from jamal.credentials import load_session
from jamal.history import History
from jamal.register import Registrar, registration_from_env


def validate_cache_url(value: str) -> str:
    """Accept a local listener or an HTTPS Tailscale address for the cache reader."""
    try:
        parsed = urlsplit(value)
        local = parsed.scheme == "http" and parsed.hostname in ("127.0.0.1", "::1")
        tailnet = (
            parsed.scheme == "https"
            and parsed.hostname is not None
            and parsed.hostname.endswith(".ts.net")
        )
        if (
            not (local or tailnet)
            or parsed.port is None or not 1 <= parsed.port <= 65535
            or parsed.username is not None or parsed.password is not None
            or parsed.path not in ("", "/") or parsed.query or parsed.fragment
        ):
            raise ValueError
    except ValueError:
        raise ValueError("CHATPLUS_CACHE_URL must be loopback HTTP or tailnet HTTPS") from None
    return value


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Serve the tsuba Oasis adapter")
    parser.add_argument("command", choices=["serve"])
    parser.add_argument("--host", default=os.environ.get("CHATPLUS_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("CHATPLUS_PORT", "8767")))
    parser.add_argument("--session", default=os.environ.get("CHATPLUS_SESSION"))
    parser.add_argument("--device", default=os.environ.get("CHATPLUS_DEVICE"))
    parser.add_argument("--config", default=os.environ.get("CHATPLUS_CONFIG"))
    parser.add_argument("--state", default=os.environ.get(
        "CHATPLUS_STATE", str(Path.home() / ".chatplus-oasis" / "history.sqlite3")
    ))
    parser.add_argument("--file-token-file", default=os.environ.get("CHATPLUS_FILE_TOKEN_FILE"))
    parser.add_argument("--cache-url", default=os.environ.get("CHATPLUS_CACHE_URL"))
    args = parser.parse_args(argv)
    if not args.session or not args.device:
        parser.error("--session/CHATPLUS_SESSION and --device/CHATPLUS_DEVICE are required")
    token = os.environ.get("CHATPLUS_TOKEN", "")
    if len(token.encode()) < 32:
        parser.error("CHATPLUS_TOKEN must contain at least 32 bytes")
    session = load_session(args.session)
    device = _load_device(args.device)
    if any(not value for value in device.as_metadata().values()):
        parser.error("Device metadata must contain nonempty captured values")
    config = load_config_snapshot(args.config) if args.config else None
    file_token = Path(args.file_token_file).read_text(encoding="utf-8").strip() \
        if args.file_token_file else ""
    registration = registration_from_env(os.environ, port=args.port, token=token)
    cache_token = os.environ.get("CHATPLUS_CACHE_TOKEN") or token
    if not args.cache_url and os.environ.get("CHATPLUS_CACHE_TOKEN"):
        parser.error("CHATPLUS_CACHE_TOKEN requires CHATPLUS_CACHE_URL")
    if args.cache_url:
        try:
            validate_cache_url(args.cache_url)
        except ValueError as exc:
            parser.error(str(exc))
    registrar = Registrar(registration) if registration else None
    # grpc.aio channels must be created on uvicorn's running event loop.
    async def app_factory() -> None:
        client = SubscribeClient(session, config=config)
        history = History(Path(args.state), session.account_hash)
        adapter = Adapter(
            client, device, history, file_token=file_token,
            max_bytes=min(session.limits.get("max_small_file_size", 0), 10 * 1024 * 1024),
        )
        cache = CacheSource(httpx.AsyncClient(base_url=args.cache_url, timeout=5), cache_token) \
            if args.cache_url else None
        app = create_app(adapter, token, cache=cache)
        if registrar:
            registrar.start()
        try:
            await uvicorn.Server(uvicorn.Config(
                app, host=args.host, port=args.port, access_log=False, log_level="warning"
            )).serve()
        finally:
            if registrar:
                registrar.stop()

    asyncio.run(app_factory())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
