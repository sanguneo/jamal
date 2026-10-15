from __future__ import annotations

import httpx
import pytest

from chatplus_core.upload import FileRef, UploadError, upload_mms_file

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 24


def _responder(
    status: int, body: object
) -> tuple[httpx.MockTransport, dict[str, httpx.Request]]:
    captured: dict[str, httpx.Request] = {}

    def handle(request: httpx.Request) -> httpx.Response:
        captured["request"] = request
        if isinstance(body, dict):
            return httpx.Response(status, json=body)
        return httpx.Response(status, text=str(body))

    return httpx.MockTransport(handle), captured


@pytest.mark.asyncio
async def test_upload_posts_multipart_and_parses_file_ref() -> None:
    transport, captured = _responder(
        201,
        {
            "file": {
                "contentType": "image/png",
                "fileName": "pic.png",
                "href": "https://s-ft.example/oasis/oapi/v1/file?fileId=abc&s=32",
                "size": 32,
            }
        },
    )
    async with httpx.AsyncClient(transport=transport) as client:
        ref = await upload_mms_file(
            "https://s-ft.example/oasis", "file-token", "pic.png", "image/png", PNG, client=client
        )
    assert ref == FileRef(
        href="https://s-ft.example/oasis/oapi/v1/file?fileId=abc&s=32",
        size=32,
        file_name="pic.png",
        content_type="image/png",
    )
    request = captured["request"]
    assert request.method == "POST"
    assert str(request.url) == "https://s-ft.example/oasis/oapi/v1/mms-file"
    assert request.headers["authorization"] == "Bearer file-token"
    body = request.content
    assert b'name="Content-Type"' in body
    assert b'name="file"; filename="pic.png"' in body
    assert PNG in body


@pytest.mark.asyncio
async def test_upload_non_201_raises() -> None:
    transport, _ = _responder(403, {"error": "denied"})
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(UploadError):
            await upload_mms_file(
                "https://s-ft.example/oasis", "t", "p.png", "image/png", PNG, client=client
            )


@pytest.mark.asyncio
async def test_upload_missing_file_object_raises() -> None:
    transport, _ = _responder(201, {"unexpected": True})
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(UploadError):
            await upload_mms_file(
                "https://s-ft.example/oasis", "t", "p.png", "image/png", PNG, client=client
            )
