# Session file, format version 1

This is a local export contract from implementation-plan section 2, not an Oasis
wire schema or a working loader. All JSON objects below are closed
(`additionalProperties: false`); required fields are listed explicitly. Strings
must be nonempty unless stated otherwise. Never put a populated session in Git.

```text
Session {
  required: format_version, environment, app_version, exported_at, device_id,
            tokens, headers, bootstrap_profile
  format_version: integer, const 1
  environment: enum "prd" | "stg" | "dev" (no default)
  app_version: string (exact donor build version)
  exported_at: string (RFC 3339 timestamp with timezone)
  device_id: string (exact observed device identity, not a new generated ID)
  tokens: {
    required: grpc_bearer_token
    grpc_bearer_token: string
    rest_bearer_token?: string
  }
  headers: map<string, string> (only verified required header values)
  bootstrap_profile: BootstrapProfile
}

BootstrapProfile {
  required: profile_id, environment, app_version, evidence, url, method, auth,
            approved_origins, approved_service_hosts
  profile_id: string (local evidence-profile identifier)
  environment: enum "prd" | "stg" | "dev"
  app_version: string
  evidence: array<EvidenceRef> (nonempty)
  url: string (exact observed HTTPS bootstrap URL)
  method: string (exact observed HTTP method; no guessed GET default)
  auth: enum "none" | "rest_bearer_token" | "grpc_bearer_token"
  approved_origins: array<string> (explicit HTTPS/WSS origins for this environment)
  approved_service_hosts: array<string> (exact approved hostnames)
}

EvidenceRef {
  required: source_id, locator
  source_id: string (sanitized capture ID or binary SHA-256)
  locator: string (direction/frame offset, or file offset/RVA and call site)
}
```

The bootstrap profile records an observed exchange, not permission to make
arbitrary requests. Profile environment/build must equal the session and imported
configuration environment/build. Only approved HTTPS/WSS origins may receive
credentials; reject redirects, cross-environment roots, and unapproved hosts.
No hardcoded URL, host rewriting, or stale-config fallback is permitted.
If the exchange requires a body or further parameters, its verified contract must
be added before bootstrap execution can be implemented.

Subscribe requires RPC credentials. REST credentials are optional in an offline
session and required only when the verified bootstrap auth choice requires them.
Do not duplicate bearer values into `headers`; derive auth placement only from
the verified profile. Header names in RE are candidates, not a mandatory replay
list; in particular, do not copy a stale client IP blindly.

Reject refresh tokens, master/storage keys, Hive keys, storage seeds, cipher-key
plaintext, `file_bearer_token`, `access_token`, `basic_token`, and arbitrary
additional token fields. RE_FINDINGS sections 9-10 describe donor storage names;
mapping a persisted `access_token` to an exported RPC/REST credential is a T04
evidence task, not an alias assumed by this scaffold. Restart tokens do not belong
in this receive-first export contract without a separately reviewed change.

Missing/expired required credentials must fail before connection. JWT expiry,
when parseable, is only an untrusted scheduling hint. Never log tokens, include
them in repr/errors, or print them through `credentials inspect`. Export only
short-lived allowlisted values to an ACL-restricted file outside the checkout.
