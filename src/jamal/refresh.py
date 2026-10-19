"""Renew a short-lived session via the ChattingPlus REST token endpoint.

Response bodies are `{"encrypted_data": "<hex>"}` sealed with AES-256-GCM under a
fixed per-environment seed, with the 12-byte nonce appended AFTER the
ciphertext+tag (not prepended). Requests are plaintext JSON. The refresh grant
rotates the refresh token, so callers must persist both new tokens atomically.
No file writes or global state here; the client secret and device identity are
supplied by the caller and never hard-coded.
"""

from __future__ import annotations

import base64
import binascii
import json
from collections.abc import Mapping
from typing import Protocol, cast

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

_SEED = {
    "prd": b"prd_messaging_oasis_forever_12!@r",
    "stg": b"stg_messaging_oasis_forever_12!@r",
    "dev": b"dev_messaging_oasis_forever_12!@r",
}
_AUTH_HOST = {
    "prd": "rapi.rcsoasis.kr",
    "stg": "stg-rapi.rcsoasis.kr",
    "dev": "dev-rapi.rcsoasis.kr",
}
_TOKEN_PATH = "/oasis/oapi/v1/token"
_NONCE = 12
_TAG = 16
_DEVICE_FIELDS = ("client_id", "device_id", "device_name", "os_version", "client_version")


class RefreshError(RuntimeError):
    pass


class HttpPost(Protocol):
    def __call__(
        self, url: str, headers: Mapping[str, str], body: bytes
    ) -> tuple[int, bytes]: ...


def _key(environment: str) -> bytes:
    seed = _SEED.get(environment)
    if seed is None:
        raise RefreshError(f"unknown environment: {environment}")
    return seed[:32]


def oasis_decrypt(encrypted_hex: str, environment: str) -> bytes:
    """AES-256-GCM decrypt where the nonce is the trailing 12 bytes of the blob."""
    try:
        raw = binascii.unhexlify(encrypted_hex)
    except (binascii.Error, ValueError) as exc:
        raise RefreshError("encrypted_data is not valid hex") from exc
    if len(raw) < _NONCE + _TAG:
        raise RefreshError("encrypted_data is too short")
    nonce = raw[-_NONCE:]
    ciphertext_and_tag = raw[:-_NONCE]
    try:
        return AESGCM(_key(environment)).decrypt(nonce, ciphertext_and_tag, None)
    except InvalidTag as exc:
        raise RefreshError("oasis payload failed authentication") from exc


def oasis_encrypt(plaintext: bytes, environment: str, nonce: bytes) -> str:
    if len(nonce) != _NONCE:
        raise RefreshError("nonce must be 12 bytes")
    ciphertext_and_tag = AESGCM(_key(environment)).encrypt(nonce, plaintext, None)
    return binascii.hexlify(ciphertext_and_tag + nonce).decode("ascii")


def decode_envelope(body: bytes, environment: str) -> object:
    """Unwrap a response: decrypt `encrypted_data`, then JSON-decode once or twice.

    Plaintext error bodies (no `encrypted_data`) pass through unchanged; a
    decrypted payload is occasionally a JSON-encoded string that needs a second
    decode.
    """
    try:
        outer = cast(object, json.loads(body))
    except json.JSONDecodeError as exc:
        raise RefreshError("response body is not JSON") from exc
    if not isinstance(outer, dict) or "encrypted_data" not in outer:
        return outer
    encrypted = cast(dict[str, object], outer)["encrypted_data"]
    if not isinstance(encrypted, str):
        raise RefreshError("encrypted_data must be a string")
    plaintext = oasis_decrypt(encrypted, environment)
    try:
        inner = cast(object, json.loads(plaintext))
    except json.JSONDecodeError as exc:
        raise RefreshError("decrypted payload is not JSON") from exc
    if isinstance(inner, str):
        return cast(object, json.loads(inner))
    return inner


def basic_auth(client_id: str, client_secret: str) -> str:
    """Nested Basic credential the server expects: base64(id : base64(id : secret))."""
    inner = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode("ascii")
    return "Basic " + base64.b64encode(f"{client_id}:{inner}".encode()).decode("ascii")


def auth_base_url(environment: str) -> str:
    host = _AUTH_HOST.get(environment)
    if host is None:
        raise RefreshError(f"unknown environment: {environment}")
    return f"https://{host}"


def refresh_headers(device_info: Mapping[str, str], client_secret: str) -> dict[str, str]:
    return {
        "authorization": basic_auth(device_info["client_id"], client_secret),
        "content-type": "application/json",
        "oasis-encrypt": "true",
        "user-agent": "Dart/3.10 (dart:io)",
        "accept-encoding": "gzip",
        "x-mcs-deviceid": device_info["device_id"],
        "x-mcs-devicename": device_info["device_name"],
        "x-mcs-devicetype": device_info.get("device_type", "SD"),
        "x-mcs-osversion": device_info["os_version"],
        "x-mcs-firmwareversion": device_info.get("firmware_version", ""),
        "x-mcs-clientversion": device_info["client_version"],
        "x-mcs-clientip": device_info.get("client_ip", ""),
    }


def refresh_body(refresh_token: str, device_info: Mapping[str, str]) -> bytes:
    for field in _DEVICE_FIELDS:
        if not device_info.get(field):
            raise RefreshError(f"device_info is missing {field}")
    document = {
        "refresh_token": refresh_token,
        "grant_type": "refresh_token",
        "device_info": dict(device_info),
    }
    return json.dumps(document, ensure_ascii=False).encode("utf-8")


def renew_tokens(
    *,
    environment: str,
    refresh_token: str,
    device_info: Mapping[str, str],
    client_secret: str,
    post: HttpPost,
) -> dict[str, object]:
    """Exchange a refresh token; the result carries a new, rotated refresh_token."""
    if not refresh_token:
        raise RefreshError("no refresh token available")
    if not client_secret:
        raise RefreshError("no client secret configured")
    url = auth_base_url(environment) + _TOKEN_PATH
    headers = refresh_headers(device_info, client_secret)
    status, body = post(url, headers, refresh_body(refresh_token, device_info))
    if status != 200:
        detail = decode_envelope(body, environment) if body else None
        raise RefreshError(f"refresh rejected: HTTP {status} {detail!r}"[:300])
    payload = decode_envelope(body, environment)
    if not isinstance(payload, dict) or "access_token" not in payload:
        raise RefreshError("refresh response has no access_token")
    return cast(dict[str, object], payload)


def apply_refreshed_tokens(
    session_document: Mapping[str, object], payload: Mapping[str, object]
) -> dict[str, object]:
    access = payload.get("access_token")
    if not isinstance(access, str) or not access:
        raise RefreshError("refresh payload has no access_token")
    updated = dict(session_document)
    raw_tokens = updated.get("tokens")
    tokens = dict(raw_tokens) if isinstance(raw_tokens, dict) else {}
    tokens["access_token"] = access
    rotated = payload.get("refresh_token")
    if isinstance(rotated, str) and rotated:
        tokens["refresh_token"] = rotated
    updated["tokens"] = tokens
    return updated
