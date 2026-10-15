# chatplus-core

A low-level client library and CLI for the **RCS Oasis** backend used by SK telecom's
**채팅+ (ChattingPlus)** Windows desktop client (`com.sktelecom.torchlight`, Flutter
Desktop). It speaks the protocol directly — subscribe to inbound notifications and send
messages — with **no automation or agent layer**. You bring an exported session; the
library does the wire work.

> Reverse-engineered from the shipped client for interoperability and research. Using a
> re-implemented client against a live account carries account-restriction and
> mis-send risk; see [Safety](#safety).

## Status

Validated end-to-end against the production backend (`np-rapi.rcsoasis.kr:1520`,
`s-ft.rcsoasis.kr`):

| Capability | Transport | State |
|---|---|---|
| Receive notifications (`SubscribeNotification`) | gRPC server-stream | **live-verified** |
| Send text (`SendXms`) | gRPC unary | **live-verified** (send + echo in receive stream) |
| Send image (`SendXms` MMS) | file upload + gRPC unary | **live-verified** (send + echo in receive stream) |
| `SendRcs` | gRPC unary | not implemented — the observed client sends both text and images via `SendXms` |

188 offline tests pass (`ruff` + `mypy` clean). The send encoders are byte-for-byte
identical to captured real requests.

## Requirements

- Python 3.12 and [uv](https://docs.astral.sh/uv/)
- A ChattingPlus-for-Windows install that is **logged in** (to export a session)

```console
uv sync --locked
uv run chatplus-core --help
```

## Credentials

`chatplus-core` never logs in or refreshes tokens. It consumes an explicit, short-lived
**session export**. Produce one on the logged-in Windows machine with the bundled
exporter (read-only; the long-lived refresh token is never read):

```console
# On the PC where ChattingPlus for Windows is installed and signed in:
pip install cryptography
python scripts/extract_session.py --out session.json
```

The exporter reads the client's `flutter_secure_storage` values (AES-GCM sealed with a
Windows Credential Manager key) and emits the session format the loader validates. See
[`docs/session-format.md`](./docs/session-format.md). Keep `session.json` out of version
control and off shared channels — it contains live bearer tokens.

Device headers (`x-mcs-*`) are not part of the session export; a live subscribe/send
takes a small device profile JSON — see [`docs/receive.md`](./docs/receive.md).

## Quickstart (CLI)

```console
# Inspect a session (redacted — prints no secret values)
uv run chatplus-core credentials inspect --credentials session.json

# Decode a captured notification frame offline to NDJSON
uv run chatplus-core decode --input capture.json --type notification

# Subscribe to live notifications (read-only)
uv run chatplus-core subscribe --credentials session.json --config config.json \
    --device device.json --limit 1

# Send — real network dispatch, gated behind --confirm-send
uv run chatplus-core send xms --credentials session.json --device device.json \
    --to +8210XXXXXXXX --text "hello" --confirm-send

uv run chatplus-core send xms-image --credentials session.json --device device.json \
    --to +8210XXXXXXXX --file pic.png --file-token-file token.txt --confirm-send
```

Without `--confirm-send`, `send` prints a dry-run summary and exits non-zero without
sending.

## Quickstart (library)

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
    async for note in client.subscribe(device):          # receive
        for message in note.messages:
            print(message.direction, message.from_, message.text)
    await client.send_xms_text(device, "+8210XXXXXXXX", "hello")   # send text
```

## Architecture

```
src/chatplus_core/
  credentials.py   load/validate the explicit session export (no writes, no refresh)
  config.py        load a captured bootstrap config snapshot
  transport/h2.py  native gRPC over TLS (grpc.aio); one channel, one subscription
  codec.py         decode the notification envelope + NMS JSON into Message records
  subscribe.py     build the subscription request + metadata; yield Notifications
  upload.py        multipart file upload to the small-file server (MMS prerequisite)
  send.py          byte-exact SendXms request encoders + unary dispatch primitive
  client.py        SubscribeClient: subscribe / send_xms_text / send_xms_image
  cli.py           argparse CLI
scripts/
  extract_session.py   export a session.json from a logged-in Windows install
docs/                receive/send guides and format contracts
research/            reverse-engineering evidence (no secrets)
```

## Safety

- Short-lived session tokens only; the refresh token is never exported or stored.
- Secrets never belong in the repo — `.gitignore` excludes `*session*.json`,
  `*credentials*.json`, `.secure`, `.env`, captures and dumps.
- Sending is a real network action. The CLI gates every send behind `--confirm-send`;
  the library `send_message` primitive documents the same contract. Re-confirm before any
  real send to a live account.
- Subscribing opens a second session for the same account; running it alongside the
  desktop app risks a forced re-login. Prefer signing the app out first.

## Testing

```console
uv run ruff check .
uv run mypy src/chatplus_core
uv run pytest
```

All tests are offline and use synthetic fixtures only.

## Further reading

- [`docs/receive.md`](./docs/receive.md) — receive pipeline + device headers
- [`docs/send.md`](./docs/send.md) — send wire schema, upload, gates
- [`PROTOCOL_ANALYSIS.md`](./PROTOCOL_ANALYSIS.md) — endpoints and auth
- [`research/`](./research/) — reverse-engineering evidence
