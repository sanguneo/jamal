# tsuba adapter

The native Oasis adapter answers tsuba 0.2.7 over HTTP and SSE. It lists only rooms
observed through its subscription and retains their messages in an account-bound
SQLite database across restarts. It does not import the desktop client's history.
`/rooms` therefore answers `complete: false`; unread counts are currently zero.
Protect the state directory: it contains message text and phone numbers.

To show older conversations and their saved contact/room names, run the existing
`chatplus-core` local-cache adapter on the **same Windows account** without its
Saya registration, then configure this adapter to read its loopback HTTP endpoint:

```text
CHATPLUS_CACHE_URL=http://127.0.0.1:8768
# Optional when both adapters use the same CHATPLUS_TOKEN:
CHATPLUS_CACHE_TOKEN=<the chatplus-core adapter's bearer token>
```

The cache reader must be installed and running separately. `CHATPLUS_CACHE_URL`
is required; `CHATPLUS_CACHE_TOKEN` defaults to `CHATPLUS_TOKEN` when omitted.
On different machines, expose the cache reader only through Tailscale Serve
and use a `https://<device>.<tailnet>.ts.net:<port>` URL instead of loopback.
This adapter reads only its
`/rooms` and `/messages` routes and does not give it an Oasis session. Cached
rooms keep their cache IDs and names and can display their cached messages;
rooms first observed by Oasis retain their native IDs. A room shown only from
the cache has no independently verified native recipient, so its send controls
are disabled. Cached and newly observed views of the same conversation may
appear separately until a shared room identity can be verified. If the cache
reader is configured but unreachable, reads return `503 BUSY` rather than
silently claiming a complete list.

```console
uv sync --locked
uv run jamal-adapter serve --session /var/lib/chatplus/session.json \
  --device /var/lib/chatplus/device.json --config research/config-snapshot.prd.json \
  --state /var/lib/chatplus/history.sqlite3 --port 8767
```

Required environment: `CHATPLUS_TOKEN` containing at least 32 UTF-8 bytes.
The host defaults to `127.0.0.1`. `--host` and `--port` override it.
The CLI also reads `CHATPLUS_SESSION`, `CHATPLUS_DEVICE`, `CHATPLUS_CONFIG`,
`CHATPLUS_STATE`, `CHATPLUS_HOST`, and `CHATPLUS_PORT`.

The session is the current `scripts/extract_session.py` export, consumed by
`credentials.load_session`: format_version, environment, app_version, exported_at,
account_hash, tokens (access_token and grpc_restart_token), identity (client_uuid
and device_code), roots, and limits. The older `docs/session-format.md` describes
an earlier scaffold, not this loader. The optional config is a
`config.load_config_snapshot` snapshot; session roots suffice without it.

The device JSON requires `device_id`, `device_name`, `device_type`, `os_version`,
`client_version`, and `firmware_version`; optional `client_ip` and `user_agent`
default to `127.0.0.1` and `Dart/3.10 (dart:io)`. Copy the actual donor's captured
`x-mcs-*` values. `device_code` is not proven to be `x-mcs-deviceid`; do not substitute
it or the OCI hostname. Session tokens are short-lived and are not refreshed.
When the subscription ends, restart with a fresh session; sends are never retried.
One account should have one running adapter.

Supported features: rooms.list, messages.read (before-id, maxLimit 200),
messages.stream (cursor), messages.send (maxLength 3072, receipt accepted).
Only observed single-recipient phone-number rooms can send. Group rooms narrow
their features to disable sends. Replies, edits, deletion, reactions, marking read,
and downloading inbound attachments are unsupported and return 501.

To compose the first text to a new Korean mobile number, use the adapter-specific
`POST /x-chatplus/compose` endpoint with a bearer token and JSON
`{"to":"01012345678","text":"..."}`. It accepts a national `010` number or the
equivalent `+8210` number, limits text to 3072 code points, and sends through
the native Oasis XMS transport. The answer `{"ok":true,"receipt":"accepted"}`
means only that the unary dispatch succeeded: the room and message appear
after their notification arrives, not before. Any error after dispatch has an
unknown outcome and must not be retried automatically. This route is a
provider extension, not part of tsuba 0.2.7, so Saya does not draw a
new-recipient compose button from this declaration.

Optional `--file-token-file` / `CHATPLUS_FILE_TOKEN_FILE` enables attachments.send
when the session's max_small_file_size is positive. It needs the actual small-file
server bearer token, not an assumed access-token alias. Only JPEG, PNG, and GIF
are supported; the smaller of the session limit and 10 MiB applies. Captions share
the 3072-code-point limit. Both write routes share a serialized four-slot line.
Accepted receipts carry adapter-local `accepted-...` IDs and do not claim the
backend stored the message; the notification echo is the persisted message.

Saya registration is opt-in. On an OCI host running saya locally:

```text
SAYA_REGISTER_URL=http://127.0.0.1:8798
SAYA_REGISTER_TOKEN=<saya register token>
SAYA_ADVERTISE_URL=http://127.0.0.1:8767
```

Registration uses id `chatplus`, heartbeats at saya's returned interval, and
withdraws on graceful shutdown. Disable the old chatplus adapter first to avoid
two processes registering the same id. Registration outages do not stop serving.
Every endpoint except `/health` requires `Authorization: Bearer <CHATPLUS_TOKEN>`.
The bundled blue ChattingPlus icon is the original old-core SVG, validated at
startup and served with inert-image security headers.
