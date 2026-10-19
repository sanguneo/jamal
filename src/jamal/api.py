"""tsuba 0.2.7 HTTP adapter over the native Oasis client."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import re
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager, suppress
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import grpc
import httpx
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response, StreamingResponse
from starlette.datastructures import UploadFile
from starlette.exceptions import HTTPException

from jamal.cache import CacheSource
from jamal.client import SubscribeClient
from jamal.history import History
from jamal.icon import load_icon
from jamal.subscribe import DeviceHeaders

MAX_LENGTH = 3072
IMAGE_TYPES = ["image/jpeg", "image/png", "image/gif"]


class Refusal(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


def failure(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(
        {"ok": False, "error": {"code": code, "message": message}},
        status_code=status,
        headers={"WWW-Authenticate": "Bearer"} if status == 401 else None,
    )


def frame(event: str, **body: Any) -> str:
    return f"event: {event}\ndata: {json.dumps({'ok': True, **body})}\n\n"


class Adapter:
    def __init__(
        self, client: SubscribeClient, device: DeviceHeaders, history: History,
        *, file_token: str = "", max_bytes: int = 0,
    ) -> None:
        self.client, self.device, self.history = client, device, history
        self.file_token, self.max_bytes = file_token, max_bytes
        self.changed = asyncio.Event()
        self.write_lock = asyncio.Lock()
        self.writes = 0
        self.streams = 0
        self.receive_error: tuple[int, str, str] | None = None

    async def receive(self) -> None:
        try:
            async for note in self.client.subscribe(self.device):
                if self.history.ingest(note):
                    self.notify()
            self.receive_error = (
                503, "REAUTH_REQUIRED", "Oasis subscription ended; restart service"
            )
        except asyncio.CancelledError:
            raise
        except grpc.aio.AioRpcError as exc:
            self.receive_error = (
                (503, "REAUTH_REQUIRED", "Export a fresh Oasis session and restart")
                if exc.code() == grpc.StatusCode.UNAUTHENTICATED
                else (500, "INTERNAL", "Oasis subscription failed; restart service")
            )
        except Exception:
            self.receive_error = (500, "INTERNAL", "Oasis subscription failed; restart service")
        self.notify()

    def notify(self) -> None:
        previous, self.changed = self.changed, asyncio.Event()
        previous.set()

    def check_write(self, room: str) -> str:
        if self.receive_error:
            raise Refusal(*self.receive_error)
        try:
            recipient = self.history.recipient(room)
        except KeyError:
            raise Refusal(404, "NOT_FOUND", "No such observed room") from None
        if not recipient:
            raise Refusal(501, "NOT_SUPPORTED", "Only single-recipient XMS rooms support sending")
        return recipient

    @asynccontextmanager
    async def write(self) -> AsyncIterator[None]:
        if self.writes >= 4:
            raise Refusal(429, "RATE_LIMITED", "The adapter write line is full")
        self.writes += 1
        try:
            async with self.write_lock:
                yield
        finally:
            self.writes -= 1

    async def events(
        self, positions: dict[str, int], baseline: int, resumed: bool,
        cursor: dict[str, str],
    ) -> AsyncIterator[str]:
        self.streams += 1
        try:
            yield frame("ready", resumed=resumed, cursor=cursor)
            while True:
                changed = self.changed
                messages = self.history.after(positions, baseline)
                emitted: dict[str, str] = {}
                for seq, message in messages:
                    positions[message["roomId"]] = seq
                    emitted[message["roomId"]] = message["id"]
                    yield frame("message", message=message)
                if emitted:
                    yield frame("cursor", cursor=emitted)
                if self.receive_error:
                    _, code, detail = self.receive_error
                    yield (
                        "event: error\ndata: "
                        + json.dumps({"ok": False, "error": {"code": code, "message": detail}})
                        + "\n\n"
                    )
                    return
                try:
                    await asyncio.wait_for(changed.wait(), timeout=15)
                except TimeoutError:
                    yield frame("ping")
        finally:
            self.streams -= 1


def accepted(room: str, text: str, kind: str = "text") -> dict[str, Any]:
    # This is an accepted request, not a claim of a backend-stored message id.
    return {
        "ok": True, "message": {
            "id": f"accepted-{uuid4()}", "roomId": room, "sender": "",
            "text": text, "kind": kind, "sentAt": datetime.now(UTC).isoformat(), "fromMe": True,
        },
    }


def text_field(data: Any, name: str, *, optional: bool = False) -> str:
    value = data.get(name, "" if optional else None)
    if not isinstance(value, str) or (not optional and not value):
        raise Refusal(400, "INVALID_REQUEST", f"Invalid {name}")
    return value


def create_app(
    adapter: Adapter, token: str, *, receive: bool = True, cache: CacheSource | None = None,
) -> FastAPI:
    if len(token.encode()) < 32:
        raise ValueError("CHATPLUS_TOKEN must contain at least 32 bytes")
    icon = load_icon()

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        task = asyncio.create_task(adapter.receive()) if receive else None
        try:
            yield
        finally:
            if task:
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
            await adapter.client.aclose()
            if cache is not None:
                await cache.aclose()
            adapter.history.close()

    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)

    async def cache_capabilities() -> dict[str, Any]:
        if cache is None:
            raise Refusal(501, "NOT_SUPPORTED", "Local cache reader is not configured")
        try:
            return await cache.capabilities()
        except (httpx.HTTPError, ValueError):
            raise Refusal(503, "BUSY", "Local cache reader unavailable") from None

    async def cached_feature(room: str, feature: str) -> dict[str, Any]:
        declaration = await cache_capabilities()
        declared = declaration.get("features", {})
        if not isinstance(declared, dict) or feature not in declared:
            raise Refusal(501, "NOT_SUPPORTED", "Cached write feature is not available")
        available = declared[feature]
        if not isinstance(available, dict):
            raise Refusal(500, "INTERNAL", "Invalid local cache capability")
        if cache is None:
            raise Refusal(501, "NOT_SUPPORTED", "Local cache reader is not configured")
        try:
            listed = await cache.rooms()
        except (httpx.HTTPError, ValueError):
            raise Refusal(503, "BUSY", "Local cache reader unavailable") from None
        for item in listed.get("rooms", []):
            if isinstance(item, dict) and item.get("id") == room:
                own = item.get("features")
                if isinstance(own, dict) and own.get(feature) is False:
                    raise Refusal(501, "NOT_SUPPORTED", "Cached room cannot send this")
                return available
        raise Refusal(404, "NOT_FOUND", "No such cached room")

    def cached_result(response: httpx.Response) -> JSONResponse:
        try:
            body = response.json()
        except ValueError:
            raise Refusal(500, "INTERNAL", "Send outcome is unknown; do not retry") from None
        if isinstance(body, dict):
            if (
                response.is_success and body.get("ok") is True
                and isinstance(body.get("message"), dict)
                and isinstance(body["message"].get("id"), str)
            ):
                return JSONResponse(body, status_code=response.status_code)
            if (
                not response.is_success and body.get("ok") is False
                and isinstance(body.get("error"), dict)
                and isinstance(body["error"].get("code"), str)
            ):
                return JSONResponse(body, status_code=response.status_code)
        raise Refusal(500, "INTERNAL", "Send outcome is unknown; do not retry")

    @app.middleware("http")
    async def authenticate(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        if request.url.path != "/health":
            scheme, _, actual = request.headers.get("authorization", "").partition(" ")
            if scheme.lower() != "bearer" or not hmac.compare_digest(
                hashlib.sha256(actual.encode()).digest(), hashlib.sha256(token.encode()).digest()
            ):
                return failure(401, "UNAUTHORIZED", "Bearer token required")
        return await call_next(request)

    @app.exception_handler(Refusal)
    async def refusal(_request: Request, exc: Refusal) -> Response:
        return failure(exc.status, exc.code, exc.message)

    @app.exception_handler(RequestValidationError)
    async def validation(_request: Request, _exc: RequestValidationError) -> Response:
        return failure(400, "INVALID_REQUEST", "Invalid request")

    @app.exception_handler(HTTPException)
    async def routing(_request: Request, exc: HTTPException) -> Response:
        code = {
            404: "NOT_FOUND", 405: "METHOD_NOT_ALLOWED",
        }.get(exc.status_code, "INVALID_REQUEST")
        return failure(exc.status_code, code, "Route refused")

    @app.exception_handler(Exception)
    async def internal(_request: Request, _exc: Exception) -> Response:
        return failure(500, "INTERNAL", "Adapter failed; send outcome may be unknown")

    @app.get("/health")
    async def health() -> dict[str, bool]:
        return {"ok": True}

    @app.get("/capabilities")
    async def capabilities() -> dict[str, Any]:
        features: dict[str, Any] = {
            "rooms.list": {}, "messages.read": {"paging": "before-id", "maxLimit": 200},
            "messages.stream": {"resume": "cursor"},
            "messages.send": {"maxLength": MAX_LENGTH, "receipt": "accepted"},
            "x-chatplus.compose": {},
        }
        if adapter.file_token and adapter.max_bytes > 0:
            features["attachments.send"] = {
                "maxBytes": adapter.max_bytes, "caption": True,
                "captionMaxLength": MAX_LENGTH, "types": IMAGE_TYPES, "receipt": "accepted",
            }
        if cache is not None:
            available = (await cache_capabilities()).get("features", {})
            upload = available.get("attachments.send") if isinstance(available, dict) else None
            if isinstance(upload, dict) and "attachments.send" not in features:
                features["attachments.send"] = upload
            read_attach = available.get("attachments.read") if isinstance(available, dict) else None
            if isinstance(read_attach, dict):
                features["attachments.read"] = read_attach
        body: dict[str, Any] = {
            "ok": True, "contract": {"version": "0.2.7"},
            "adapter": {"name": "jamal", "version": "0.1.0"},
            "provider": {
                "id": "chatplus", "name": "\ucc44\ud305+",
                "icon": icon.version,
            },
            "account": {"kind": "user"}, "features": features,
        }
        body["revision"] = hashlib.sha256(
            json.dumps(body, sort_keys=True).encode()
        ).hexdigest()[:16]
        return body

    @app.get("/icon")
    async def provider_icon() -> Response:
        return Response(icon.bytes, media_type=icon.media_type, headers={
            "Content-Security-Policy": "default-src 'none'; sandbox",
            "X-Content-Type-Options": "nosniff",
        })

    @app.get("/rooms")
    async def rooms() -> dict[str, Any]:
        observed = adapter.history.rooms()
        if cache is None:
            return {"ok": True, "rooms": observed, "complete": False}
        try:
            cached = await cache.rooms()
        except (httpx.HTTPError, ValueError):
            raise Refusal(503, "BUSY", "Local cache reader unavailable") from None
        if not isinstance(cached.get("rooms"), list):
            raise Refusal(500, "INTERNAL", "Invalid local cache room list")
        available = (await cache_capabilities()).get("features", {})
        if not isinstance(available, dict):
            raise Refusal(500, "INTERNAL", "Invalid local cache capabilities")
        unavailable = {
            feature: False for feature in ("messages.send", "attachments.send")
            if feature not in available
        }
        ids = {room["id"] for room in observed}
        cache_rooms = [
            {
                **room,
                "features": {
                    **unavailable,
                    **({"attachments.send": available["attachments.send"]}
                       if adapter.file_token and "attachments.send" in available else {}),
                    **room.get("features", {}),
                },
            }
            for room in cached["rooms"]
            if isinstance(room, dict) and isinstance(room.get("id"), str)
            and room["id"] not in ids
        ]
        if not adapter.file_token:
            observed = [
                {**room, "features": {**room.get("features", {}), "attachments.send": False}}
                for room in observed
            ]
        combined = [*observed, *cache_rooms]
        combined.sort(key=lambda room: str(room.get("lastActivityAt") or ""), reverse=True)
        return {
            "ok": True, "rooms": combined,
            "complete": False,
        }

    @app.get("/messages")
    async def messages(roomId: str, limit: int = 50, before: str | None = None) -> dict[str, Any]:
        if not roomId or not 1 <= limit <= 200 or before == "":
            raise Refusal(400, "INVALID_REQUEST", "Invalid message window")
        try:
            return adapter.history.messages(roomId, limit, before)
        except KeyError:
            if cache is None:
                raise Refusal(404, "NOT_FOUND", "No such observed room") from None
            try:
                return await cache.messages(roomId, limit, before)
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == 404:
                    raise Refusal(404, "NOT_FOUND", "No such cached room or message") from None
                raise Refusal(503, "BUSY", "Local cache reader unavailable") from None
            except (httpx.RequestError, ValueError):
                raise Refusal(503, "BUSY", "Local cache reader unavailable") from None
        except ValueError:
            raise Refusal(404, "NOT_FOUND", "No such message in that room") from None

    @app.get("/stream")
    async def stream(request: Request) -> Response:
        if adapter.streams >= 8:
            raise Refusal(503, "BUSY", "Stream limit reached")
        baseline = adapter.history.latest()
        cursor = adapter.history.cursors()
        positions: dict[str, int] = {}
        since = request.query_params.getlist("since")
        for hint in since:
            room, separator, message_id = hint.rpartition(":")
            if not separator or not room or not message_id or room in positions:
                raise Refusal(400, "INVALID_REQUEST", "Invalid stream cursor")
            try:
                positions[room] = adapter.history.position(room, message_id)
            except ValueError:
                raise Refusal(400, "INVALID_REQUEST", "Unknown stream cursor") from None
            cursor[room] = message_id
        return StreamingResponse(
            adapter.events(positions, baseline, bool(since), cursor),
            media_type="text/event-stream", headers={"Cache-Control": "no-store"},
        )

    @app.post("/send")
    async def send(request: Request) -> Response:
        try:
            data = await request.json()
        except ValueError:
            raise Refusal(400, "INVALID_REQUEST", "Invalid JSON") from None
        if not isinstance(data, dict):
            raise Refusal(400, "INVALID_REQUEST", "Expected an object")
        if "replyTo" in data:
            raise Refusal(501, "NOT_SUPPORTED", "Replies are not supported")
        room, text = text_field(data, "roomId"), text_field(data, "text")
        if len(text) > MAX_LENGTH:
            raise Refusal(400, "INVALID_REQUEST", "Text exceeds maxLength")
        async with adapter.write():
            try:
                adapter.history.recipient(room)
            except KeyError:
                await cached_feature(room, "messages.send")
                if cache is None:
                    raise Refusal(501, "NOT_SUPPORTED", "Local cache reader is not configured")
                try:
                    result = await cache.send(room, text)
                except httpx.RequestError:
                    raise Refusal(
                        500, "INTERNAL", "Send outcome is unknown; do not retry"
                    ) from None
                return cached_result(result)
            recipient = adapter.check_write(room)
            # Once dispatch starts, every failure is INTERNAL: never suggest retry.
            try:
                await adapter.client.send_xms_text(adapter.device, recipient, text)
            except Exception:
                raise Refusal(500, "INTERNAL", "Send outcome is unknown; do not retry") from None
        return JSONResponse(accepted(room, text))

    @app.post("/x-chatplus/compose")
    async def compose(request: Request) -> dict[str, str | bool]:
        """Start a new single-recipient XMS without claiming the room exists yet."""
        try:
            data = await request.json()
        except ValueError:
            raise Refusal(400, "INVALID_REQUEST", "Invalid JSON") from None
        if not isinstance(data, dict) or "replyTo" in data:
            raise Refusal(400, "INVALID_REQUEST", "Invalid compose request")
        recipient, text = text_field(data, "to"), text_field(data, "text")
        if len(text) > MAX_LENGTH:
            raise Refusal(400, "INVALID_REQUEST", "Text exceeds maxLength")
        if re.fullmatch(r"010[1-9][0-9]{7}", recipient):
            recipient = "+82" + recipient[1:]
        elif re.fullmatch(r"\+8210[1-9][0-9]{7}", recipient) is None:
            raise Refusal(400, "INVALID_REQUEST", "Expected one Korean mobile number")
        async with adapter.write():
            if adapter.receive_error:
                raise Refusal(*adapter.receive_error)
            try:
                await adapter.client.send_xms_text(adapter.device, recipient, text)
            except Exception:
                raise Refusal(500, "INTERNAL", "Send outcome is unknown; do not retry") from None
        return {"ok": True, "receipt": "accepted"}

    @app.post("/upload")
    async def upload(request: Request) -> Response:
        max_bytes = max(adapter.max_bytes, 10 * 1024 * 1024 if cache is not None else 0)
        if max_bytes <= 0:
            raise Refusal(501, "NOT_SUPPORTED", "Image sending is not configured")
        length = request.headers.get("content-length")
        if length:
            try:
                too_large = int(length) > max_bytes + 65536
            except ValueError:
                raise Refusal(400, "INVALID_REQUEST", "Invalid content length") from None
            if too_large:
                raise Refusal(400, "INVALID_REQUEST", "Upload exceeds maxBytes")
        async with request.form(max_files=1, max_fields=3, max_part_size=65536) as form:
            keys = list(form.keys())
            if (
                set(keys) - {"file", "roomId", "caption", "replyTo"}
                or any(len(form.getlist(key)) != 1 for key in keys)
            ):
                raise Refusal(400, "INVALID_REQUEST", "Unknown or repeated upload field")
            if "replyTo" in form:
                raise Refusal(501, "NOT_SUPPORTED", "Replies are not supported")
            room = text_field(form, "roomId")
            caption = text_field(form, "caption", optional=True)
            file = form.get("file")
            if len(caption) > MAX_LENGTH or not isinstance(file, UploadFile):
                raise Refusal(400, "INVALID_REQUEST", "Invalid upload")
            if file.content_type not in IMAGE_TYPES:
                raise Refusal(400, "INVALID_REQUEST", "Unsupported image type")
            allowed: int = 0
            try:
                adapter.history.recipient(room)
                cached = False
                allowed = adapter.max_bytes
            except KeyError:
                feature = await cached_feature(room, "attachments.send")
                cached = True
                raw_max = feature.get("maxBytes")
                if isinstance(raw_max, int):
                    allowed = raw_max
            if allowed <= 0:
                raise Refusal(501, "NOT_SUPPORTED", "Image sending is not configured")
            if cached and caption:
                raise Refusal(400, "INVALID_REQUEST", "Cached pictures do not take captions")
            if file.size is not None and file.size > allowed:
                raise Refusal(400, "INVALID_REQUEST", "Upload exceeds maxBytes")
            data = await file.read(allowed + 1)
            if not data or len(data) > allowed:
                raise Refusal(400, "INVALID_REQUEST", "Invalid image size")
            async with adapter.write():
                if cached:
                    if cache is None:
                        raise Refusal(501, "NOT_SUPPORTED", "Local cache reader is not configured")
                    try:
                        response = await cache.upload(
                            room, file.filename or "image", file.content_type, data
                        )
                    except httpx.RequestError:
                        raise Refusal(
                            500, "INTERNAL", "Upload outcome is unknown; do not retry"
                        ) from None
                    return cached_result(response)
                recipient = adapter.check_write(room)
                try:
                    await adapter.client.send_xms_image(
                        adapter.device, recipient, file.filename or "image",
                        file.content_type, data, adapter.file_token, caption=caption,
                    )
                except Exception:
                    raise Refusal(
                        500, "INTERNAL", "Upload outcome is unknown; do not retry"
                    ) from None
        return JSONResponse(accepted(room, caption, "media"))

    @app.get("/media")
    async def media(id: str) -> Response:
        if not id:
            raise Refusal(400, "INVALID_REQUEST", "Invalid media id")
        if cache is None:
            raise Refusal(501, "NOT_SUPPORTED", "Local cache reader is not configured")
        try:
            resp = await cache.media(id)
        except (httpx.HTTPError, ValueError):
            raise Refusal(503, "BUSY", "Local cache reader unavailable") from None
        if resp.status_code == 404:
            raise Refusal(404, "NOT_FOUND", "No such media")
        if not resp.is_success:
            raise Refusal(500, "INTERNAL", "Failed to retrieve media")
        return Response(
            resp.content,
            media_type=resp.headers.get("content-type", "application/octet-stream"),
            headers={
                "Content-Security-Policy": "default-src 'none'; sandbox",
                "X-Content-Type-Options": "nosniff",
                "Cache-Control": "private, max-age=86400",
            },
        )

    async def unsupported() -> Response:
        return failure(501, "NOT_SUPPORTED", "Feature is not supported")

    for path, method in [
        ("/edit", "POST"), ("/delete", "POST"),
        ("/react", "POST"), ("/read", "POST"),
    ]:
        app.add_api_route(path, unsupported, methods=[method])
    return app
