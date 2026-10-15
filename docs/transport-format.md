# Transport and captured configuration artifacts

These are local evidence formats, not definitions of an Oasis envelope.
No observed profile is supplied by T01. Markdown shapes are documentation, not
an implemented schema validator. Objects are closed except maps explicitly named.
`EvidenceRef` is defined in `session-format.md`.

## `transport-profile.json`

Intended evidence path: `research/transport-profile.json`. Raw capture bytes and
credential-bearing handshakes stay outside the checkout. Use sanitized IDs and
structure-preserving fixtures instead.

```text
TransportProfile {
  required: format_version, profile_id, environment, app_version, evidence,
            status, adapter, root_config_key, handshake, framing, lifecycle
  format_version: integer, const 1
  profile_id: string
  environment: enum "prd" | "stg" | "dev"
  app_version: string (exact observed donor build)
  evidence: array<EvidenceRef>
  status: enum "unverified" | "verified"
  adapter: enum "ws" | "native_grpc" | "http_grpc_web" | null
  root_config_key: string | null (exact captured config key; not a URL fallback)
  handshake: {
    required: url_construction, http_version, subprotocol, metadata, compression
    url_construction: Observation | null
    http_version: Observation | null
    subprotocol: Observation | null
    metadata: Observation | null
    compression: Observation | null
  }
  framing: {
    required: request, response, boundaries, status_trailers
    request: Observation | null
    response: Observation | null
    boundaries: Observation | null
    status_trailers: Observation | null
  }
  lifecycle: {
    required: completion, errors, close, cancellation, control_traffic
    completion: Observation | null
    errors: Observation | null
    close: Observation | null
    cancellation: Observation | null
    control_traffic: Observation | null
  }
}

Observation {
  required: description, evidence, fixture_ids
  description: string (exact recovered rule, including verified absence if absent)
  evidence: array<EvidenceRef> (nonempty)
  fixture_ids: array<string> (sanitized fixture paths/identifiers)
}
```

Null means unresolved, never a default. A `verified` profile must resolve every
required observation for its adapter and identify the corresponding fixtures.
Document exact metadata placement, envelope bytes/length/endian rules, message
boundaries, compression, status/trailers, close and control events before adapter
implementation. WS application messages are not assumed to be raw protobuf or
five-byte gRPC envelopes. No speculative reconnect, app acknowledgments or
refresh behavior belongs in a profile.

Adapters remain conditional: WS for the captured streaming dialect; native gRPC
only if proved (then `grpc.aio` supplies HTTP2/framing); otherwise a separately
verified HTTP gRPC-Web adapter if required. A route must reference its own
verified profile rather than infer its binding from another RPC.

## Imported configuration snapshot

```text
ConfigSnapshot {
  required: format_version, environment, app_version, captured_at,
            bootstrap_profile_id, evidence, service_roots
  format_version: integer, const 1
  environment: enum "prd" | "stg" | "dev"
  app_version: string
  captured_at: string (RFC 3339 timestamp with timezone)
  bootstrap_profile_id: string (same session bootstrap profile)
  evidence: array<EvidenceRef> (nonempty)
  service_roots: map<string, string> (exact observed config keys and root values)
}
```

This is the local normalized artifact, not an invented server response schema.
Preserve the captured response separately as a sanitized fixture with provenance.
Only observed roots belong in `service_roots`; validate each root against the
session's environment and explicit approved origins/hosts. Config candidates in
RE do not establish which roots are present, required or interchangeable.
Failure to fetch must never silently substitute a snapshot. Offline import and
verified fetch are explicit alternatives.
