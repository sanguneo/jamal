# Per-method route table

Local design for a future `research/method-routes.json`; no operational routes
are supplied by the scaffold. Each method is independently evidence-gated.
Objects are closed. `EvidenceRef` is defined in `session-format.md`.

```text
RouteTable {
  required: format_version, environment, app_version, routes
  format_version: integer, const 1
  environment: enum "prd" | "stg" | "dev"
  app_version: string (exact donor build)
  routes: array<Route> (method values unique)
}

Route {
  required: method, rpc_kind, status, root_config_key, transport_profile_id,
            path_construction, metadata, deadline, schemas, evidence
  method: string (exact full RPC path)
  rpc_kind: enum "unary" | "server_streaming"
  status: enum "unverified" | "verified"
  root_config_key: string | null
  transport_profile_id: string | null
  path_construction: string | null (observed rule, no guessed URL concatenation)
  metadata: array<MetadataRule> | null
  deadline: string | null (observed semantics; not an invented timeout)
  schemas: {
    required: revision, request, response, error
    revision: string | null (approved schema revision)
    request: string | null (recovered fully qualified message name)
    response: string | null
    error: string | null (verified error schema/status contract)
  }
  evidence: {
    required: static_call_sites, official_capture
    static_call_sites: array<EvidenceRef>
    official_capture: array<EvidenceRef>
  }
}

MetadataRule {
  required: name, source, placement, evidence
  name: string (observed metadata/header name)
  source: string (reference to allowlisted session material, never its value)
  placement: string (verified transport placement)
  evidence: array<EvidenceRef> (nonempty)
}
```

Null and empty evidence arrays mean unresolved and block use; they do not select
defaults. Verified routes require resolved bindings, metadata/deadline semantics,
schema contracts, and static call-site plus corresponding official-client capture
evidence. A known method-name string alone is not route verification.

RE establishes these MVP/research method paths, but not their payload schemas or
unary transport bindings:

- `/oasis.client.api.SubscribeNotificationService/SubscribeNotification`
  (server streaming)
- `/oasis.client.api.CapabilityService/Query`
- `/oasis.client.api.SendMessageService/SendRcs`
- `/oasis.client.api.SendMessageService/SendXms`

Each entry resolves its exact config root through the matching configuration
snapshot and transport profile for the same environment/build. Capability success
does not validate either sending route. There is no generic CLI RPC bypass.
Receiving must never invoke IMDN, capability, refresh or send implicitly; a
verified route is evidence, not approval to mutate.
