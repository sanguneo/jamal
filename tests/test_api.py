from __future__ import annotations

import asyncio
import json
import socket
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
import uvicorn

from jamal.api import Adapter, Refusal, create_app
from jamal.cache import CacheSource
from jamal.client import SubscribeClient
from jamal.codec import Message, Notification
from jamal.history import History
from jamal.icon import IconError, prepare_icon
from jamal.register import Registrar, Registration, registration_from_env
from jamal.serve import validate_cache_url
from jamal.subscribe import DeviceHeaders

TOKEN = "synthetic-adapter-token-32-bytes-long"
ROOM = "synthetic-room"
DEVICE = DeviceHeaders("id", "name", "SD", "os", "1.5.4.6", "firmware")


def note(index: int, *, outgoing: bool = False, group: bool = False) -> Notification:
    return Notification(
        push_epoch=index, control_bytes=None, nms_index=index,
        messages=(Message(
            from_="+820000000001", to=("+820000000002",) * (2 if group else 1),
            conversation_id=ROOM, date="2026-10-15T00:00:00Z",
            content_type="text/plain", direction="Out" if outgoing else "In",
            text=f"synthetic {index}", raw_attributes={"TextContent": f"synthetic {index}"},
        ),),
    )


class OfflineClient(SubscribeClient):
    def __init__(self) -> None:
        self.calls: list[tuple[object, ...]] = []
        self.queue: asyncio.Queue[Notification] = asyncio.Queue()
        self.closed = False

    async def subscribe(
        self, device: DeviceHeaders, *, epoch_micros: int | None = None
    ) -> AsyncIterator[Notification]:
        while True:
            yield await self.queue.get()

    async def send_xms_text(
        self, device: DeviceHeaders, recipient: str, text: str, *, client_ip: str | None = None
    ) -> bytes:
        self.calls.append(("text", recipient, text))
        return b""

    async def send_xms_image(
        self, device: DeviceHeaders, recipient: str, file_name: str, content_type: str,
        data: bytes, file_token: str, *, small_file_root: str | None = None,
        caption: str = "", client_ip: str | None = None,
    ) -> bytes:
        self.calls.append(("image", recipient, file_name, content_type, data, caption))
        return b""

    async def aclose(self) -> None:
        self.closed = True


@pytest_asyncio.fixture
async def fixture(
    tmp_path: Path,
) -> AsyncIterator[tuple[httpx.AsyncClient, Adapter, OfflineClient]]:
    client = OfflineClient()
    history = History(tmp_path / "history.sqlite3", "synthetic-account")
    history.ingest(note(1))
    adapter = Adapter(client, DEVICE, history, file_token="synthetic", max_bytes=100)
    app = create_app(adapter, TOKEN, receive=False)
    async with app.router.lifespan_context(app), httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test",
        headers={"Authorization": f"Bearer {TOKEN}"},
    ) as http:
        yield http, adapter, client
    assert client.closed


@pytest.mark.asyncio
async def test_auth_declaration_icon_and_unsupported(fixture):
    http, _, _ = fixture
    assert (await http.get("/health", headers={"authorization": ""})).json() == {"ok": True}
    denied = await http.get("/capabilities", headers={"authorization": "Bearer wrong"})
    assert denied.status_code == 401
    assert denied.json()["error"]["code"] == "UNAUTHORIZED"
    declaration = (await http.get("/capabilities")).json()
    assert declaration["contract"]["version"] == "0.2.7"
    assert declaration["features"]["messages.send"]["receipt"] == "accepted"
    assert "attachments.read" not in declaration["features"]
    icon = await http.get("/icon")
    assert icon.content == (Path(__file__).parents[1] / "src/jamal/icon.svg").read_bytes()
    assert b"#0E5DEE" in icon.content
    assert icon.headers["content-security-policy"] == "default-src 'none'; sandbox"
    assert (await http.post("/edit")).status_code == 501
    assert (await http.get("/missing")).json()["error"]["code"] == "NOT_FOUND"
    assert (await http.post("/rooms")).json()["error"]["code"] == "METHOD_NOT_ALLOWED"


@pytest.mark.asyncio
async def test_history_paging_and_restart(fixture, tmp_path):
    http, adapter, _ = fixture
    first = (await http.get("/messages", params={"roomId": ROOM})).json()["messages"][0]
    assert adapter.history.ingest(note(2))
    assert not adapter.history.ingest(note(2))
    rooms = (await http.get("/rooms")).json()
    assert rooms["complete"] is False
    assert len(rooms["rooms"]) == 1
    newest = (await http.get("/messages", params={"roomId": ROOM, "limit": 1})).json()
    assert newest["hasEarlier"] is True
    earlier = (await http.get("/messages", params={
        "roomId": ROOM, "before": newest["messages"][0]["id"],
    })).json()
    assert earlier["messages"] == [first]
    reopened = History(tmp_path / "history.sqlite3", "synthetic-account")
    assert len(reopened.messages(ROOM, 50, None)["messages"]) == 2
    reopened.close()
    with pytest.raises(ValueError, match="different account"):
        History(tmp_path / "history.sqlite3", "another-account")


@pytest.mark.asyncio
@pytest.mark.parametrize("params,status", [
    ({}, 400), ({"roomId": ROOM, "limit": 201}, 400),
    ({"roomId": "missing"}, 404), ({"roomId": ROOM, "before": "missing"}, 404),
])
async def test_read_refusals(fixture, params, status):
    http, _, _ = fixture
    assert (await http.get("/messages", params=params)).status_code == status


@pytest.mark.asyncio
async def test_send_only_accepts_and_does_not_invent_stored_history(fixture):
    http, adapter, client = fixture
    result = await http.post("/send", json={"roomId": ROOM, "text": "synthetic send"})
    assert result.status_code == 200
    assert result.json()["message"]["id"].startswith("accepted-")
    assert client.calls == [("text", "+820000000001", "synthetic send")]
    assert len(adapter.history.messages(ROOM, 50, None)["messages"]) == 1
    assert (await http.post("/send", json={
        "roomId": ROOM, "text": "reply", "replyTo": "message",
    })).status_code == 501
    assert len(client.calls) == 1
    adapter.history.ingest(note(2, group=True))
    assert (await http.post("/send", json={"roomId": ROOM, "text": "group"})).status_code == 501
    assert len(client.calls) == 1


@pytest.mark.asyncio
async def test_compose_to_new_recipient_without_observed_room(fixture):
    http, adapter, client = fixture
    result = await http.post("/x-chatplus/compose", json={
        "to": "01012345678", "text": "synthetic first message",
    })
    assert result.status_code == 200
    assert result.json() == {"ok": True, "receipt": "accepted"}
    assert client.calls == [("text", "+821012345678", "synthetic first message")]
    assert len(adapter.history.rooms()) == 1
    for recipient in ("", "+8210123456789", "01000000000", "synthetic"):
        refused = await http.post("/x-chatplus/compose", json={"to": recipient, "text": "x"})
        assert refused.status_code == 400
    assert len(client.calls) == 1


@pytest.mark.asyncio
async def test_compose_failure_does_not_claim_a_send_or_retry(fixture, monkeypatch):
    http, _, client = fixture

    async def failed_send(device, recipient, text):
        client.calls.append(("text", recipient, text))
        raise RuntimeError("synthetic transport failure")

    monkeypatch.setattr(client, "send_xms_text", failed_send)
    answer = await http.post("/x-chatplus/compose", json={
        "to": "+821012345678", "text": "synthetic send",
    })
    assert answer.status_code == 500
    assert answer.json()["error"]["code"] == "INTERNAL"
    assert client.calls == [("text", "+821012345678", "synthetic send")]


@pytest.mark.asyncio
async def test_cache_rooms_and_messages_are_visible_beside_native_notifications(
    tmp_path: Path,
):
    requests: list[str] = []

    def cache_answer(request: httpx.Request) -> httpx.Response:
        requests.append(request.url.path)
        assert request.headers["authorization"] == "Bearer synthetic-cache-token"
        if request.url.path == "/capabilities":
            return httpx.Response(200, json={"ok": True, "features": {
                "messages.send": {"maxLength": 3072, "receipt": "stored"},
                "attachments.send": {"maxBytes": 10_485_760, "caption": False,
                                     "types": ["image/jpeg", "image/png", "image/gif"],
                                     "receipt": "stored"},
            }})
        if request.url.path == "/rooms":
            return httpx.Response(200, json={"ok": True, "complete": True, "rooms": [
                {"id": "cached-room", "title": "합성 이름", "kind": "direct",
                 "unread": 0, "lastActivityAt": "2026-10-15T00:00:00Z",
                 "lastMessage": {"text": "earlier", "fromMe": False},
                 "features": {"messages.send": False, "attachments.send": False}},
            ]})
        if request.url.path == "/messages":
            return httpx.Response(200, json={"ok": True, "hasEarlier": False, "messages": [
                {"id": "cached-message", "roomId": "cached-room",
                 "sender": "합성 이름", "senderId": "tel:+821012345678",
                 "text": "earlier", "kind": "text",
                 "sentAt": "2026-10-14T00:00:00Z", "fromMe": False},
            ]})
        return httpx.Response(404)

    client = OfflineClient()
    history = History(tmp_path / "combined.sqlite3", "synthetic-account")
    history.ingest(note(1))
    adapter = Adapter(client, DEVICE, history)
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(cache_answer), base_url="http://127.0.0.1:8768"
    ) as upstream:
        cache = CacheSource(upstream, "synthetic-cache-token")
        app = create_app(adapter, TOKEN, receive=False, cache=cache)
        async with app.router.lifespan_context(app), httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test",
            headers={"authorization": f"Bearer {TOKEN}"},
        ) as http:
            listed = (await http.get("/rooms")).json()
            assert {room["title"] for room in listed["rooms"]} == {
                "합성 이름", "+820000000001",
            }
            assert listed["complete"] is False
            cached_room = next(room for room in listed["rooms"] if room["id"] == "cached-room")
            assert cached_room["features"]["messages.send"] is False
            page = (await http.get("/messages", params={"roomId": "cached-room"})).json()
            assert [message["id"] for message in page["messages"]] == ["cached-message"]
            assert (await http.post("/send", json={
                "roomId": "cached-room", "text": "not verified",
            })).status_code == 501
    assert requests == ["/rooms", "/capabilities", "/messages", "/capabilities", "/rooms"]


@pytest.mark.asyncio
async def test_cached_room_sends_through_verified_desktop_adapter(tmp_path: Path):
    requests: list[tuple[str, str]] = []

    def answer(request: httpx.Request) -> httpx.Response:
        requests.append((request.url.path, request.method))
        if request.url.path == "/rooms":
            return httpx.Response(200, json={"ok": True, "complete": True, "rooms": [
                {"id": "cached-send", "title": "합성 수신자", "kind": "direct",
                 "unread": 0, "lastActivityAt": "2026-10-15T00:00:00Z"},
                {"id": "cached-blocked", "title": "검증 전", "kind": "direct",
                 "unread": 0, "features": {"messages.send": False}},
            ]})
        if request.url.path == "/capabilities":
            return httpx.Response(200, json={"ok": True, "features": {
                "messages.send": {"maxLength": 3072, "receipt": "stored"},
                "attachments.send": {"maxBytes": 10_485_760, "caption": False,
                                     "types": ["image/jpeg", "image/png", "image/gif"],
                                     "receipt": "stored"},
            }})
        if request.url.path == "/send":
            assert request.read() == b'{"roomId":"cached-send","text":"synthetic text"}'
            return httpx.Response(200, json={"ok": True, "message": {
                "id": "stored-cache", "roomId": "cached-send", "sender": "",
                "text": "synthetic text", "kind": "text", "fromMe": True,
                "sentAt": "2026-10-15T00:00:00Z",
            }})
        if request.url.path == "/upload":
            assert b"synthetic-image" in request.read()
            return httpx.Response(200, json={"ok": True, "message": {
                "id": "stored-image", "roomId": "cached-send", "sender": "",
                "text": "", "kind": "media", "fromMe": True,
                "sentAt": "2026-10-15T00:00:00Z",
            }})
        if request.url.path == "/media":
            assert request.url.params["id"] == "cached-mid/pic.png"
            return httpx.Response(200, content=b"synthetic-png-bytes",
                                 headers={"content-type": "image/png"})
        return httpx.Response(404)

    upstream = httpx.AsyncClient(
        transport=httpx.MockTransport(answer), base_url="http://127.0.0.1:8768"
    )
    client = OfflineClient()
    adapter = Adapter(client, DEVICE, History(tmp_path / "cached-send.sqlite3", "account"))
    app = create_app(adapter, TOKEN, receive=False, cache=CacheSource(upstream, "synthetic"))
    async with app.router.lifespan_context(app), httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test",
        headers={"authorization": f"Bearer {TOKEN}"},
    ) as http:
        assert (await http.get("/capabilities")).json()["features"]["attachments.send"][
            "types"
        ] == ["image/jpeg", "image/png", "image/gif"]
        rooms = (await http.get("/rooms")).json()["rooms"]
        assert next(room for room in rooms if room["id"] == "cached-send").get(
            "features", {}
        ).get("messages.send") is not False
        assert next(room for room in rooms if room["id"] == "cached-blocked")[
            "features"
        ]["messages.send"] is False
        result = await http.post("/send", json={
            "roomId": "cached-send", "text": "synthetic text",
        })
        assert result.json()["message"]["id"] == "stored-cache"
        image = await http.post(
            "/upload", data={"roomId": "cached-send"},
            files={"file": ("synthetic.png", b"synthetic-image", "image/png")},
        )
        assert image.json()["message"]["id"] == "stored-image"
        media_resp = await http.get("/media?id=cached-mid/pic.png")
        assert media_resp.status_code == 200
        assert media_resp.content == b"synthetic-png-bytes"
        assert media_resp.headers["content-type"] == "image/png"
        assert media_resp.headers["content-security-policy"] == "default-src 'none'; sandbox"
        refused = await http.post("/send", json={
            "roomId": "cached-blocked", "text": "synthetic text",
        })
        assert refused.status_code == 501
    assert requests.count(("/send", "POST")) == 1
    assert requests.count(("/upload", "POST")) == 1
    assert client.calls == []


@pytest.mark.parametrize("url", [
    "http://127.0.0.1:8768@public.invalid",
    "http://127.0.0.1:8768/proxy",
    "https://127.0.0.1:8768",
    "http://127.0.0.1",
    "http://localhost:8768",
    "http://win.example.ts.net:8768",
    "https://public.invalid:8768",
])
def test_cache_source_only_accepts_loopback_origin(url):
    with pytest.raises(ValueError, match="loopback HTTP or tailnet HTTPS"):
        validate_cache_url(url)
    assert validate_cache_url("http://127.0.0.1:8768") == "http://127.0.0.1:8768"
    assert validate_cache_url("https://win.example.ts.net:8768") == (
        "https://win.example.ts.net:8768"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [None, [], {"roomId": ROOM, "text": ""},
                                   {"roomId": ROOM, "text": "a" * 3073}])
async def test_invalid_sends_do_not_dispatch(fixture, payload):
    http, _, client = fixture
    result = await http.post("/send", json=payload)
    assert result.status_code == 400
    assert not client.calls


@pytest.mark.asyncio
async def test_upload_validation_and_send(fixture):
    http, _, client = fixture
    result = await http.post("/upload", data={"roomId": ROOM, "caption": "caption"},
                             files={"file": ("synthetic.png", b"image", "image/png")})
    assert result.status_code == 200
    assert client.calls == [
        ("image", "+820000000001", "synthetic.png", "image/png", b"image", "caption")
    ]
    for files in [
        [("file", ("a.png", b"a", "image/png")), ("file", ("b.png", b"b", "image/png"))],
        {"file": ("a.txt", b"a", "text/plain")},
        {"file": ("a.png", b"a" * 101, "image/png")},
    ]:
        assert (await http.post("/upload", data={"roomId": ROOM}, files=files)).status_code == 400
    assert len(client.calls) == 1


@pytest.mark.asyncio
async def test_receive_and_stream_signals(fixture):
    _, adapter, client = fixture
    initial = adapter.history.cursors()
    baseline = adapter.history.latest()
    stream = adapter.events({}, baseline, False, initial)
    assert "event: ready" in await anext(stream)
    signal = adapter.changed
    receiver = asyncio.create_task(adapter.receive())
    await client.queue.put(note(2))
    await asyncio.wait_for(signal.wait(), 2)
    assert "synthetic 2" in await anext(stream)
    assert "event: cursor" in await anext(stream)
    await stream.aclose()
    receiver.cancel()
    with pytest.raises(asyncio.CancelledError):
        await receiver
    positions = {ROOM: adapter.history.position(ROOM, initial[ROOM])}
    replay = adapter.events(positions, baseline, True, initial)
    assert json.loads((await anext(replay)).split("data: ")[1])["resumed"] is True
    assert "synthetic 2" in await anext(replay)
    await replay.aclose()
    assert adapter.streams == 0


@pytest.mark.asyncio
async def test_stream_refuses_unknown_cursors_and_errors(fixture):
    http, adapter, _ = fixture
    assert (await http.get("/stream?since=unknown:message")).status_code == 400
    adapter.streams = 8
    assert (await http.get("/stream")).status_code == 503
    adapter.streams = 0
    adapter.receive_error = (503, "REAUTH_REQUIRED", "Restart")
    result = await http.get("/stream")
    assert "event: ready" in result.text
    assert "event: error" in result.text
    assert '"ok": false' in result.text


@pytest.mark.asyncio
async def test_write_admission(fixture):
    _, adapter, _ = fixture
    entered = asyncio.Event()
    release = asyncio.Event()

    async def writer():
        async with adapter.write():
            entered.set()
            await release.wait()

    first = asyncio.create_task(writer())
    await asyncio.wait_for(entered.wait(), 2)
    adapter.writes = 4
    with pytest.raises(Refusal) as refused:
        async with adapter.write():
            pytest.fail("Fifth write was admitted")
    assert refused.value.status == 429
    adapter.writes = 1
    release.set()
    await first
    assert adapter.writes == 0


def test_registration_configuration_and_heartbeat(monkeypatch):
    assert registration_from_env({}, port=8767, token=TOKEN) is None
    with pytest.raises(ValueError):
        registration_from_env({"SAYA_REGISTER_TOKEN": "synthetic"}, port=8767, token=TOKEN)
    registration = registration_from_env({
        "SAYA_REGISTER_URL": "http://127.0.0.1:8798",
        "SAYA_REGISTER_TOKEN": "synthetic-register",
    }, port=8767, token=TOKEN)
    assert registration == Registration(
        "http://127.0.0.1:8798/register", "synthetic-register", "http://127.0.0.1:8767", TOKEN,
    )
    calls = []

    def request(method, url, **kwargs):
        calls.append((method, url, kwargs["json"]))
        return httpx.Response(200, json={"interval": 17})

    monkeypatch.setattr(httpx, "request", request)
    registrar = Registrar(registration)
    assert registrar.request("POST") == 17
    registrar.request("DELETE")
    assert calls[0][2] == {
        "kind": "adapter", "id": "chatplus", "url": "http://127.0.0.1:8767", "token": TOKEN,
    }
    assert calls[1][2] == {"kind": "adapter", "id": "chatplus"}


@pytest.mark.parametrize("svg", [
    b'<svg onload="run()"/>', b'<svg><script/></svg>',
    b'<svg><image href="https://example.test/image"/></svg>',
])
def test_icon_refuses_active_content(svg):
    with pytest.raises(IconError):
        prepare_icon(svg, "image/svg+xml")


@pytest.mark.asyncio
async def test_real_socket_startup_rooms_and_compose(tmp_path):
    ready = asyncio.Event()
    client = OfflineClient()
    adapter = Adapter(client, DEVICE, History(tmp_path / "socket.sqlite3", "account"))
    adapter.history.ingest(note(1))
    app = create_app(adapter, TOKEN)

    class SignallingServer(uvicorn.Server):
        async def startup(self, sockets=None):
            await super().startup(sockets=sockets)
            ready.set()

    server = SignallingServer(uvicorn.Config(app, log_level="error", access_log=False))
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        task = asyncio.create_task(server.serve(sockets=[sock]))
        try:
            await asyncio.wait_for(ready.wait(), 5)
            async with httpx.AsyncClient() as http:
                result = await http.get(f"http://127.0.0.1:{sock.getsockname()[1]}/health")
                assert result.json() == {"ok": True}
                headers = {"authorization": f"Bearer {TOKEN}"}
                base = f"http://127.0.0.1:{sock.getsockname()[1]}"
                rooms = await http.get(base + "/rooms", headers=headers)
                assert [room["id"] for room in rooms.json()["rooms"]] == [ROOM]
                composed = await http.post(
                    base + "/x-chatplus/compose", headers=headers,
                    json={"to": "01012345678", "text": "synthetic socket message"},
                )
                assert composed.json() == {"ok": True, "receipt": "accepted"}
                assert client.calls == [
                    ("text", "+821012345678", "synthetic socket message")
                ]
        finally:
            server.should_exit = True
            await asyncio.wait_for(task, 5)
    assert client.closed
