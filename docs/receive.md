# Receive path (T04–T10)

The receive MVP reads RCS Oasis notifications at a low level: no automation, no
send, no implicit mutation. It composes an explicit session export, a captured
config snapshot, a native gRPC transport, and an offline notification codec.

All examples assume Python 3.12 and uv, run from the checkout root:

```console
uv sync --locked
```

Keep populated sessions, device profiles, raw captures and decoded output outside
this checkout. Synthetic data only is committed; real tokens and message content
stay out-of-repo.

## Modules

- `credentials.load_session(path) -> Session`: validate an explicit short-lived
  session export (see `session-format.md`). Never writes files or refreshes
  tokens. `repr(Session)` is redacted.
- `config.load_config_snapshot(path) -> Config`: validate a captured bootstrap
  snapshot. Live `fetch_config_live` stays deferred (encrypted bootstrap).
- `transport/h2.H2Transport`: native gRPC over TLS. grpc.aio owns HTTP/2, TLS,
  length-prefixed framing, deadlines and status trailers. One transport owns one
  channel and at most one subscription; it yields raw protobuf messages without
  transport framing.
- `codec`: decode one notification message. `iter_grpc_frames(bytes)` splits a
  raw gRPC stream into message bodies; `decode_notification(msg) -> Notification`
  parses the protobuf envelope (push epoch, opaque control bytes, NMS JSON) into
  `Message` records (`from_`, `to`, `conversation_id`, `date`, `content_type`,
  `direction`, `text`, `raw_attributes`). Malformed NMS JSON raises `CodecError`.
- `subscribe` / `client.SubscribeClient`: build the subscription request and
  metadata, open the stream, and yield decoded `Notification` values.

## Device headers

The subscribe metadata needs device identity (`x-mcs-deviceid`, `-devicename`,
`-devicetype`, `-osversion`, `-clientversion`, `-firmwareversion`, `-clientip`,
`user-agent`). These are not part of the session export, so live subscribe takes a
separate device profile JSON:

```json
{
  "device_id": "...",
  "device_name": "...",
  "device_type": "SD",
  "os_version": "...",
  "client_version": "1.5.4.6",
  "firmware_version": "...",
  "client_ip": "127.0.0.1",
  "user_agent": "Dart/3.10 (dart:io)"
}
```

The real `x-mcs-*` values must come from the donor capture for a live run.

## CLI

```console
uv run chatplus-core credentials inspect --credentials session.json
uv run chatplus-core decode --input capture.json --type notification
uv run chatplus-core subscribe --credentials session.json --config config.json \
    --device device.json [--epoch-micros N] [--limit N]
```

- `credentials inspect`: redacted session summary as one JSON line (environment,
  versions, expiry, token presence booleans, root keys). No secret values.
- `decode`: offline decode of a captured frame. `--input` is either a JSON object
  with a `grpc_frame_b64` string or a plain base64 file. Emits one NDJSON line per
  notification.
- `subscribe`: live read-only subscription. Emits NDJSON per notification;
  `--limit` stops after N. `config fetch` is deferred and returns exit code 2.

Operational errors return exit code 1 with a redacted `error:` line on stderr;
usage errors return exit code 2 via argparse. `capability` and `send` are not in
the receive MVP and return exit code 2.

## Library

```python
from chatplus_core.client import SubscribeClient
from chatplus_core.config import load_config_snapshot
from chatplus_core.credentials import load_session
from chatplus_core.subscribe import DeviceHeaders

session = load_session("session.json")
config = load_config_snapshot("config.json")
device = DeviceHeaders(
    device_id="...", device_name="...", device_type="SD", os_version="...",
    client_version="1.5.4.6", firmware_version="...",
)

async with SubscribeClient(session, config=config) as client:
    async for note in client.subscribe(device):
        for message in note.messages:
            print(message.from_, message.text)
```

## Gates

- Live validation against `:1520` opens a second session for the same account.
  Running it while the Chatting+ app is signed in on the same account risks a
  forced re-login or conflict. Confirm with the account owner, and sign the app
  out first.
- The send path (`SendRcs` / `SendXms`) is out of the receive MVP and stays behind
  a separate send confirmation.
