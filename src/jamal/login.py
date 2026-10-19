"""Device-code (QR) login against the ChattingPlus REST auth API.

Request a QR (`user_code`), the user scans it in the phone app, then poll the
device endpoint until approval returns an `auth_code`, and exchange that for
tokens. Requests are plaintext JSON; responses are oasis-encrypted and decoded
through `refresh.decode_envelope`.
"""

from __future__ import annotations

import json
import secrets
import string
import uuid
from collections.abc import Mapping
from typing import cast

from jamal.refresh import (
    HttpPost,
    RefreshError,
    auth_base_url,
    decode_envelope,
    refresh_headers,
)

_QRCODE_PATH = "/oapi/v1/auth/method/qrcode"
_DEVICE_PATH = "/oapi/v1/auth/device"
_TOKEN_PATH = "/oasis/oapi/v1/token"

WINDOWS_DEVICE_DEFAULTS = {
    "device_kind": "computer",
    "client_vendor": "SKTelecom",
    "mno": "SKT",
    "os_type": "win",
    "os_version": "10.0.26300",
    "device_type": "SD",
    "firmware_version": "26H2",
    "service_version": "1.0",
    "client_version": "1.5.4.6",
    "dart_version": '3.10.8 (stable) (Tue Jan 27 00:02:04 2026 -0800) on "windows_x64"',
}


class LoginError(RefreshError):
    pass


class AuthorizationPending(LoginError):
    pass


def generated_device_name() -> str:
    suffix = "".join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(7))
    return f"DESKTOP-{suffix}"


def new_device_info(client_ip: str, **overrides: str) -> dict[str, str]:
    info: dict[str, str] = {
        "client_id": str(uuid.uuid4()),
        "device_id": str(uuid.uuid4()).upper(),
        "client_ip": client_ip,
        "device_name": generated_device_name(),
        **WINDOWS_DEVICE_DEFAULTS,
    }
    info.update(overrides)
    return info


def _post_json(
    path: str,
    body: dict[str, object],
    device_info: Mapping[str, str],
    client_secret: str,
    environment: str,
    post: HttpPost,
) -> tuple[int, bytes]:
    url = auth_base_url(environment) + path
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    return post(url, refresh_headers(device_info, client_secret), data)


def request_qrcode(
    *, environment: str, device_info: Mapping[str, str], client_secret: str, post: HttpPost
) -> dict[str, object]:
    status, resp = _post_json(
        _QRCODE_PATH,
        {"device_info": dict(device_info)},
        device_info,
        client_secret,
        environment,
        post,
    )
    if status != 200:
        raise LoginError(f"qrcode request failed: HTTP {status}")
    payload = decode_envelope(resp, environment)
    if not isinstance(payload, dict) or "user_code" not in payload:
        raise LoginError("qrcode response has no user_code")
    return cast(dict[str, object], payload)


def _is_pending(payload: object) -> bool:
    if not isinstance(payload, dict):
        return False
    error = payload.get("requestError")
    if not isinstance(error, dict):
        return False
    exception = error.get("serviceException")
    if not isinstance(exception, dict):
        return False
    return (
        exception.get("messageId") == "SVC2003"
        or exception.get("text") == "authorization_pending"
    )


def poll_device(
    *,
    environment: str,
    device_code: str,
    device_info: Mapping[str, str],
    client_secret: str,
    post: HttpPost,
) -> dict[str, object]:
    status, resp = _post_json(
        _DEVICE_PATH,
        {"device_code": device_code},
        device_info,
        client_secret,
        environment,
        post,
    )
    if status == 200:
        payload = decode_envelope(resp, environment)
        if not isinstance(payload, dict) or "auth_code" not in payload:
            raise LoginError("device approval has no auth_code")
        return cast(dict[str, object], payload)
    payload = decode_envelope(resp, environment) if resp else None
    if _is_pending(payload):
        raise AuthorizationPending("authorization pending")
    raise LoginError(f"device poll failed: HTTP {status} {payload!r}"[:200])


def exchange_authorization_code(
    *,
    environment: str,
    auth_code: str,
    device_info: Mapping[str, str],
    client_secret: str,
    post: HttpPost,
) -> dict[str, object]:
    status, resp = _post_json(
        _TOKEN_PATH,
        {"code": auth_code, "grant_type": "authorization_code"},
        device_info,
        client_secret,
        environment,
        post,
    )
    if status != 200:
        raise LoginError(f"token exchange failed: HTTP {status}")
    payload = decode_envelope(resp, environment)
    if not isinstance(payload, dict) or "access_token" not in payload:
        raise LoginError("token exchange has no access_token")
    return cast(dict[str, object], payload)
