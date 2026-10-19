from __future__ import annotations

import json

import pytest

from jamal import login, refresh

DEVICE_INFO = {
    "client_id": "cid",
    "device_id": "did",
    "device_name": "DESKTOP-ABC1234",
    "os_version": "10.0.26300",
    "client_version": "1.5.4.6",
    "client_ip": "9.9.9.9",
}
SECRET = "secret"


def _sealed(obj: object, nonce: bytes = b"9" * 12) -> bytes:
    blob = refresh.oasis_encrypt(json.dumps(obj).encode(), "prd", nonce)
    return json.dumps({"encrypted_data": blob}).encode()


def test_generated_device_name_is_windows_shaped_and_unique() -> None:
    first = login.generated_device_name()
    second = login.generated_device_name()
    assert first.startswith("DESKTOP-")
    assert len(first) == len("DESKTOP-") + 7
    assert first != second


def test_new_device_info_has_fresh_identity_and_generated_name() -> None:
    info = login.new_device_info("9.9.9.9")
    assert info["client_ip"] == "9.9.9.9"
    assert info["os_type"] == "win"
    assert info["device_name"].startswith("DESKTOP-")
    assert info["client_id"] != login.new_device_info("9.9.9.9")["client_id"]


def test_request_qrcode_returns_user_code() -> None:
    def post(url: str, headers: object, body: bytes) -> tuple[int, bytes]:
        return 200, _sealed({"device_code": "dc", "user_code": "msgapp://x", "interval": 3})

    result = login.request_qrcode(
        environment="prd", device_info=DEVICE_INFO, client_secret=SECRET, post=post
    )
    assert result["user_code"] == "msgapp://x"


def test_poll_device_raises_pending_then_returns_auth_code() -> None:
    pending_body = {"requestError": {"serviceException": {"messageId": "SVC2003"}}}

    def pending(url: str, headers: object, body: bytes) -> tuple[int, bytes]:
        return 400, _sealed(pending_body)

    with pytest.raises(login.AuthorizationPending):
        login.poll_device(
            environment="prd",
            device_code="dc",
            device_info=DEVICE_INFO,
            client_secret=SECRET,
            post=pending,
        )

    def approved(url: str, headers: object, body: bytes) -> tuple[int, bytes]:
        return 200, _sealed({"auth_code": "ac", "mdn": "+8210", "oasis_server_root": "https://x"})

    result = login.poll_device(
        environment="prd",
        device_code="dc",
        device_info=DEVICE_INFO,
        client_secret=SECRET,
        post=approved,
    )
    assert result["auth_code"] == "ac"


def test_exchange_authorization_code_returns_tokens() -> None:
    def post(url: str, headers: object, body: bytes) -> tuple[int, bytes]:
        assert json.loads(body)["grant_type"] == "authorization_code"
        return 200, _sealed({"access_token": "a.t", "refresh_token": "r.t", "token_type": "bearer"})

    result = login.exchange_authorization_code(
        environment="prd", auth_code="ac", device_info=DEVICE_INFO, client_secret=SECRET, post=post
    )
    assert result["access_token"] == "a.t"
