from __future__ import annotations

from dataclasses import dataclass
from typing import Final

import httpx

MMS_FILE_PATH: Final = "/oapi/v1/mms-file"
_USER_AGENT: Final = "Dart/3.10 (dart:io)"


class UploadError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class FileRef:
    href: str
    size: int
    file_name: str
    content_type: str


def _parse_file_ref(payload: object) -> FileRef:
    if not isinstance(payload, dict):
        raise UploadError("upload response is not an object")
    file_obj = payload.get("file")
    if not isinstance(file_obj, dict):
        raise UploadError("upload response has no file object")
    try:
        return FileRef(
            href=str(file_obj["href"]),
            size=int(file_obj["size"]),
            file_name=str(file_obj["fileName"]),
            content_type=str(file_obj["contentType"]),
        )
    except (KeyError, TypeError, ValueError) as error:
        raise UploadError("upload response file object is incomplete") from error


async def upload_mms_file(
    small_file_root: str,
    file_token: str,
    file_name: str,
    content_type: str,
    data: bytes,
    *,
    client: httpx.AsyncClient | None = None,
    timeout: float = 30.0,
) -> FileRef:
    url = small_file_root.rstrip("/") + MMS_FILE_PATH
    files: dict[str, tuple[str | None, bytes | str] | tuple[str | None, bytes, str]] = {
        "Content-Type": (None, content_type),
        "file": (file_name, data, content_type),
    }
    headers = {"authorization": f"Bearer {file_token}", "user-agent": _USER_AGENT}
    owns_client = client is None
    http = client or httpx.AsyncClient(timeout=timeout)
    try:
        response = await http.post(url, files=files, headers=headers)
    finally:
        if owns_client:
            await http.aclose()
    if response.status_code != 201:
        raise UploadError(f"upload failed with status {response.status_code}")
    return _parse_file_ref(response.json())
