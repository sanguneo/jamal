# Session file, format version 1

The explicit, short-lived session export consumed by
`jamal.credentials.load_session`. It is a local credential file, not an
Oasis wire schema. `scripts/extract_session.py` produces it from a logged-in
install. Never commit a populated session; keep it in an ACL-restricted file
outside the checkout.

All objects below are closed — unknown fields are ignored, but a `refresh_token`
(or `OASIS_EXP_REFRESH_TOKEN`) anywhere in the document is rejected outright.
Strings must be nonempty and carry no edge whitespace.

```text
Session {
  required: format_version, environment, app_version, exported_at,
            account_hash, tokens, identity, roots, limits
  format_version: integer, const 1
  environment: enum "prd" | "stg" | "dev"
  app_version: string
  exported_at: string (ISO 8601, datetime.fromisoformat-parseable)
  account_hash: string (hexadecimal)
  tokens: {
    required: access_token, grpc_restart_token
    access_token: string (JWT; exp read as an untrusted scheduling hint only)
    grpc_restart_token: string
  }
  identity: {
    required: client_uuid, device_code
    client_uuid: string (UUID)
    device_code: string
  }
  roots: {
    required: grpc, wss, server, small_file, large_file
    grpc: string (host:port, no scheme)
    wss: string (wss:// URL)
    server: string (https:// URL)
    small_file: string (https:// URL)
    large_file: string (https:// URL)
  }
  limits: {
    required: max_group_xms_users, max_small_file_size, max_upload_file_size
    (each a nonnegative integer)
  }
}
```

The `refresh_token` and any long-lived master/storage/Hive key, storage seed, or
cipher-key plaintext are forbidden; `extract_session.py` never reads them. Each
root is validated for scheme and host shape — `grpc` must be `host:port` with no
scheme or path, `wss`/`server`/`small_file`/`large_file` must match their
scheme, and credentials in the authority or query/fragment are rejected.

`load_session` fails before any connection when a required field is missing or
when the `access_token` JWT `exp`, if parseable, is already past. Unparseable
expiry is treated as unknown, not as verified validity — the server
authenticates the token. Tokens are never logged, placed in `repr`/errors, or
printed by `credentials inspect`.
