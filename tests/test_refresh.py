from __future__ import annotations

import base64
import json

import pytest

from jamal import refresh

DEVICE_INFO = {
    "client_id": "11111111-2222-3333-4444-555555555555",
    "device_kind": "computer",
    "device_id": "AAAAAAAA-BBBB-CCCC-DDDD-EEEEEEEEEEEE",
    "device_name": "Test MB TESTBOARD",
    "client_ip": "203.0.113.7",
    "client_vendor": "SKTelecom",
    "mno": "SKT",
    "os_type": "win",
    "os_version": "10.0.26300",
    "device_type": "SD",
    "firmware_version": "26H2",
    "service_version": "1.0",
    "client_version": "1.5.4.6",
    "dart_version": "3.10.8 (stable)",
}
SECRET = "0123456789abcdef0123456789abcdef"


@pytest.mark.parametrize("environment", ["prd", "stg", "dev"])
def test_encrypt_decrypt_round_trip(environment: str) -> None:
    payload = b'{"access_token":"a.b.c","refresh_token":"d.e.f"}'
    blob = refresh.oasis_encrypt(payload, environment, nonce=b"0" * 12)
    assert refresh.oasis_decrypt(blob, environment) == payload


def test_decrypt_rejects_wrong_environment_key() -> None:
    blob = refresh.oasis_encrypt(b"secret", "prd", nonce=b"0" * 12)
    with pytest.raises(refresh.RefreshError):
        refresh.oasis_decrypt(blob, "stg")


def test_decrypt_rejects_bad_hex() -> None:
    with pytest.raises(refresh.RefreshError):
        refresh.oasis_decrypt("not-hex", "prd")


def test_decode_envelope_unwraps_object() -> None:
    obj = {"access_token": "a.b.c", "refresh_token": "d.e.f"}
    blob = refresh.oasis_encrypt(json.dumps(obj).encode(), "prd", nonce=b"1" * 12)
    body = json.dumps({"encrypted_data": blob}).encode()
    assert refresh.decode_envelope(body, "prd") == obj


def test_decode_envelope_unwraps_double_encoded_string() -> None:
    obj = {"access_token": "a.b.c"}
    blob = refresh.oasis_encrypt(json.dumps(json.dumps(obj)).encode(), "prd", nonce=b"2" * 12)
    body = json.dumps({"encrypted_data": blob}).encode()
    assert refresh.decode_envelope(body, "prd") == obj


def test_decode_envelope_passes_plaintext_error_through() -> None:
    body = json.dumps({"requestError": {"serviceException": {"messageId": "SVC2003"}}}).encode()
    decoded = refresh.decode_envelope(body, "prd")
    assert decoded == {"requestError": {"serviceException": {"messageId": "SVC2003"}}}


def test_basic_auth_is_doubly_nested() -> None:
    header = refresh.basic_auth("CID", "SEC")
    assert header.startswith("Basic ")
    outer = base64.b64decode(header.removeprefix("Basic ")).decode()
    user, _, inner_b64 = outer.partition(":")
    assert user == "CID"
    assert base64.b64decode(inner_b64).decode() == "CID:SEC"


def test_refresh_body_contains_grant_and_device() -> None:
    body = json.loads(refresh.refresh_body("R.T.K", DEVICE_INFO))
    assert body["grant_type"] == "refresh_token"
    assert body["refresh_token"] == "R.T.K"
    assert body["device_info"]["os_type"] == "win"


def test_refresh_body_requires_device_fields() -> None:
    broken = dict(DEVICE_INFO)
    del broken["device_id"]
    with pytest.raises(refresh.RefreshError):
        refresh.refresh_body("R.T.K", broken)


def test_refresh_headers_mirror_windows_client() -> None:
    headers = refresh.refresh_headers(DEVICE_INFO, SECRET)
    assert headers["oasis-encrypt"] == "true"
    assert headers["x-mcs-deviceid"] == DEVICE_INFO["device_id"]
    assert headers["x-mcs-clientversion"] == "1.5.4.6"
    assert headers["authorization"].startswith("Basic ")


def test_renew_tokens_returns_rotated_tokens() -> None:
    new_tokens = {"access_token": "new.a.t", "refresh_token": "new.r.t", "token_type": "Bearer"}
    blob = refresh.oasis_encrypt(json.dumps(new_tokens).encode(), "prd", nonce=b"3" * 12)
    captured: dict[str, object] = {}

    def post(url: str, headers, body: bytes) -> tuple[int, bytes]:
        captured["url"] = url
        captured["body"] = json.loads(body)
        return 200, json.dumps({"encrypted_data": blob}).encode()

    result = refresh.renew_tokens(
        environment="prd",
        refresh_token="old.r.t",
        device_info=DEVICE_INFO,
        client_secret=SECRET,
        post=post,
    )
    assert result["access_token"] == "new.a.t"
    assert result["refresh_token"] == "new.r.t"
    assert captured["url"] == "https://rapi.rcsoasis.kr/oasis/oapi/v1/token"
    sent = captured["body"]
    assert isinstance(sent, dict)
    assert sent["refresh_token"] == "old.r.t"


def test_apply_refreshed_tokens_rotates_both() -> None:
    tokens = {"access_token": "old.a", "refresh_token": "old.r", "grpc_restart_token": "g"}
    document = {"tokens": tokens}
    payload = {"access_token": "new.a", "refresh_token": "new.r"}
    updated = refresh.apply_refreshed_tokens(document, payload)
    tokens = updated["tokens"]
    assert isinstance(tokens, dict)
    assert tokens["access_token"] == "new.a"
    assert tokens["refresh_token"] == "new.r"
    assert tokens["grpc_restart_token"] == "g"


def test_apply_refreshed_tokens_keeps_old_refresh_when_absent() -> None:
    document = {"tokens": {"access_token": "old.a", "refresh_token": "old.r"}}
    updated = refresh.apply_refreshed_tokens(document, {"access_token": "new.a"})
    tokens = updated["tokens"]
    assert isinstance(tokens, dict)
    assert tokens["refresh_token"] == "old.r"


def test_renew_tokens_raises_on_http_error() -> None:
    def post(url: str, headers, body: bytes) -> tuple[int, bytes]:
        return 400, json.dumps({"requestError": {"serviceException": {"text": "invalid"}}}).encode()

    with pytest.raises(refresh.RefreshError):
        refresh.renew_tokens(
            environment="prd",
            refresh_token="old.r.t",
            device_info=DEVICE_INFO,
            client_secret=SECRET,
            post=post,
        )


def test_renew_tokens_raises_when_access_token_missing() -> None:
    payload = json.dumps({"token_type": "Bearer"}).encode()
    blob = refresh.oasis_encrypt(payload, "prd", nonce=b"4" * 12)

    def post(url: str, headers, body: bytes) -> tuple[int, bytes]:
        return 200, json.dumps({"encrypted_data": blob}).encode()

    with pytest.raises(refresh.RefreshError):
        refresh.renew_tokens(
            environment="prd",
            refresh_token="old.r.t",
            device_info=DEVICE_INFO,
            client_secret=SECRET,
            post=post,
        )
