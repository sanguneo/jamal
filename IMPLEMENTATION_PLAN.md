# chatplus-core implementation plan

Date: 2026-10-15. Deliverable: a manually driven, direct RCS Oasis protocol library and CLI. No LLM, agent, auto-reply, cache-reader substitute, daemon, or local-login requirement.

## 1. Decisions and evidence boundary

**Choose Python 3.12, asyncio, and uv. Recover the protocol with a hybrid of capture on the already logged-in PC and targeted Dart AOT analysis. Deliver receive/subscribe and decoded notifications first. Do not enable sending before separate user re-confirmation.**

`RE_FINDINGS.md` is the authority for protocol claims below; references use its section numbers. The user's subsequent donor-PC findings [D1] and inspected upstream storage code [S1-S3] supersede its assumptions about credential storage and local DB layout. The other four documents were read as leads, not implementation contracts. Their assumed DB tables, automatic refresh, automatic read receipts, local login, and agent architecture are not adopted.

| Established evidence | Implementation consequence | Still UNVERIFIED |
|---|---|---|
| Full RPC paths, including SubscribeNotification, SendRcs, SendXms, SendBulkImdn and CapabilityService/Query [RE 1] | Use those exact method paths after recovering their transport bindings. | Payloads, response schemas not named in RE, error semantics and required metadata. |
| Message names including SubscribeNotificationRequest/Response, Notification, RcsMessage, SendRcsRequest, SendXmsRequest and CapabilityRequest/Response [RE 2] | These are the schema-recovery targets, not sufficient definitions for code generation. | Every field-to-tag/type mapping, enum value, oneof and presence rule. |
| WS receive endpoint and server-streaming subscription; distinct API gateway; config root keys [RE 1, 3, 7] | Receive uses the configured WS root. Maintain a separately verified per-method route table. | Exact WS handshake/envelope, and whether each unary method uses native gRPC/HTTP2, HTTP gRPC-Web, or WS. |
| Config path string `oapi/v1/device/config` exists [RE 3, 4] | Recover bootstrap before implementing it; derive service roots from its response. | Host, HTTP method, full path, authentication, request body and response shape. RE itself labels the path-to-config-fetch relationship inferred. |
| Bearer-token names and device/header strings [RE 4]; auth values reside in encrypted `.secure` blobs, not directly in Credential Manager/DPAPI [D1]. Upstream reads a 16-byte storage key from Credential Manager and unwraps a Hive key [S1]. | Reuse the proven donor-side decryptor; export only short-lived session values to this PC. Separate REST and RPC credentials. | Exact donor token-entry decoding, required headers, token audience/binding, JWT format for every token, portability. |
| Donor has 74 secure-store entries, 23 `edb/*.hive` boxes and `planner.db` (drift/SQLite) [D1]. | Hive and SQLite coexist; use their data only to corroborate wire identifiers. | Donor table schemas, token value encoding, and build compatibility with upstream code. |

There has been no traffic capture, binary disassembly, login, endpoint probe or real message send during planning. No send-transport conclusion can honestly be drawn from the existing strings. The transport gate below specifies how to settle it rather than selecting HTTP2 by guesswork.

### Runtime choice

- **Python wins for this project:** directly reuse the existing Python storage recipe, and keep capture processing, protobuf generation, async WS, optional native gRPC, and the public library in one language. Use `cryptography` for donor-side decryption, `protobuf`, `websockets`, `httpx` for verified bootstrap requests, and `grpcio` only if native gRPC is demonstrated. `grpc.aio` provides async native RPCs, while `websockets` exposes headers/subprotocol controls needed by a custom WS adapter [T1, T2]. Use stdlib `argparse` and dataclasses; avoid a CLI framework or event bus.
- **Bun/TypeScript is viable but not selected:** its types and deployment workflow are attractive; neither resolves the missing schema or proprietary WS envelope. A second implementation/toolchain offers no benefit at this stage. This is a project tradeoff, not a claim that Bun cannot speak the protocol.
- Use checked-in `.proto` files and generated Python files, with locked generator/runtime dependencies and `uv.lock`. Add `pytest`, `pytest-asyncio`, `ruff` and `mypy` for development. Pin versions when the first Windows install succeeds; no package installation has been tested here.
- Opus handles AOT/schema ambiguity and protocol review. Sonnet implements bounded modules against approved artifacts. Luna handles packaging, CLI wiring and fixtures only after contracts exist. No implementer may invent protobuf tags to unblock another task.

## 2. Architecture and contracts

All paths below are proposed additions. Package: `src/chatplus_core`; executable: `chatplus-core`. One process, one explicitly selected session, one subscription. Async library calls return data/errors; the CLI owns printing and signals.

| Module / owner boundary | Contract |
|---|---|
| `donor_store.py`, `tools/export_session.py` | Donor-only, explicit offline export tool. Port the audited upstream key lookup/AES-GCM recipe; select and authenticate only required secure entries, then export a minimal session file. No storage mutation, whole-store plaintext dump, refresh export or network calls. See the concrete flow below. |
| `credentials.py` | On this PC, load that explicit JSON export; no local Credential Manager access or local-login fallback. Validate the file contract, keep tokens out of repr/logs, and return immutable session material. Parse a JWT expiry only when parseable, as an untrusted scheduling hint, not signature validation. Missing/expired required credentials fail before connection. |
| `config.py` | Import a captured config snapshot for offline work, or execute the **verified** bootstrap exchange. Return exact configured service roots plus provenance. No hardcoded service URL fallback, hostname rewriting, environment guessing, or silently substituting a stale snapshot after a failed fetch. |
| `transport/base.py`, `transport/ws.py` | Internal `unary(method, payload, metadata, deadline) -> bytes` and `stream(method, payload, metadata) -> AsyncIterator[bytes]`. WS implements only the captured dialect: URL construction, subprotocol, metadata placement, framing, status/trailers, close and any observed control traffic. Cancellation closes the socket. |
| `transport/h2.py` (conditional) | Add only if native gRPC is proven. Delegate TLS, HTTP2 and gRPC framing to `grpc.aio`; do not prepend a second gRPC envelope. If capture instead proves HTTP gRPC-Web, use an HTTP adapter with the verified framing, not `grpc.aio`. Implement only adapters required by observed routes. |
| `proto/`, `_generated/`, `codec.py` | Source schemas, generated code, and minimal conversions. Decode known fields with verified meanings; keep original protobuf bytes for unknown fields/events and future decoding. Separate protobuf bytes from transport envelope bytes. |
| `client.py`, `subscribe.py` | Thin client/context manager and `subscribe_notifications(request)`. Yield events with verified fields and schema revision; unknown variants remain explicitly unknown, never classified as messages by heuristics. No send, IMDN, capability query or refresh as a side effect. |
| `capability.py` | Explicit user-invoked query for exact caller-supplied identifiers, using the recovered CapabilityRequest/Response and verified route. Not an automatic prerequisite that silently chooses RCS versus XMS. |
| `send.py` | Separate `prepare_rcs` / `prepare_xms` from execution. Preparation validates recovered fields and returns a destination/body summary and exact payload digest. Execution requires a per-operation approval bound to that prepared payload. No automatic RCS-to-XMS fallback or send retry. |
| `cli.py` | Thin command routing and exit codes; JSON/NDJSON stdout, diagnostics stderr. No shell-command execution, scheduling or automation layer. Library callers receive the same mutation gate as CLI users. |

### Credential recovery: reuse the donor's proven implementation first

**First implementer task:** study `C:\Users\USER\WebstormProjects\chatplus-core` on the donor PC, especially `chatplus_core/storage.py`, `hive.py`, `values.py` and their tests. Record that checkout's revision/local differences before porting. The public code inspected for this plan is commit `2e8bf59467147b29f9832ac7c7c8d24b73ac9847` [S1-S3]; isolate the new project's environment because both repositories use the `chatplus_core` import name. Reuse the storage primitives, not the existing API/UI-send/automation layers.

The inspected Windows recipe is concrete [S1]: `storage_key()` reads the 16-byte generic credential `key_.`; `windows_profile()` selects the profile under `LocalCache/Local/SK Telecom Co., Ltd/ChattingPlus/edb`, decodes the `.sK2Nwk*.secure` filename's Base64 logical name, and finds `cipher_key_<profile>_<device>`. Its AES-GCM call uses a 12-byte nonce, **the next 16 bytes as the tag**, the remaining ciphertext, and no AAD. Decrypted cipher-key text has trailing NULs removed and is URL-safe-Base64 decoded to 32 bytes. That is a **Hive key**, not an auth token. `hive.py` supplies structural, padding and CRC checks for local frame decoding; its CRC is not cryptographic authentication [S2].

**Resolve the layout discrepancy before porting:** [D1] describes `IV | ciphertext | tag`; upstream implements `IV | tag | ciphertext`. T00 records this conflict; T04 validates the donor layout with a known cipher-key blob and successful GCM tag verification. Start with the exact proven upstream offsets. If they fail, inspect the donor checkout/plugin version and test the reported layout as a distinct research hypothesis. Accept only an authenticated result with the expected decoded key length; fix the observed layout in a versioned profile, not silent multi-layout guessing in production.

Donor-side export sequence:

1. Read the upstream-selected profile and matching secure namespace under `LocalCache/Roaming/SK Telecom Co., Ltd/ChattingPlus`; treat the path as a verified upstream lead and confirm it on the donor. Resolve account/device ambiguity explicitly. Enumerate logical entry names and lengths, not values, from the 74-entry inventory [D1].
2. Obtain the storage key with the proven read-only `CredReadW` recipe while the donor client has its store available. Authenticate/decrypt the cipher-key blob and validate the 32-byte Hive key as the compatibility check. Do not derive an AES key by hashing the 73-byte `cipher_key_<account>_<guid>` label or `client_uuid`: their role as KDF inputs is UNVERIFIED; the upstream code uses profile labels for selection, not a KDF.
3. Locate the actual `grpc_bearer_token`, `rest_bearer_token` and required device/config entries by decoded logical names. Reuse the verified `.secure` AES-GCM primitive for each selected entry; establish its plaintext encoding and key association with authenticated donor samples. The upstream Hive reader does **not** establish token-entry extraction, so T04 must verify it rather than use the 32-byte Hive key on all blobs. If selected entries differ, trace that specific app/plugin storage call. Never search/decrypt all values speculatively.
4. Only if evidence locates a required value inside Hive, reuse `decode_frames` and `decode_value` for that specific box and adapter; Hive decoding is not a general requirement for direct-network operation. Do not conflate local Hive field IDs with protobuf tags. Confirm `client_uuid` versus wire `deviceId` independently.
5. Write only the allowlisted short-lived credentials and required metadata to an ACL-restricted session JSON file for private transfer. No master key, Hive key, cipher-key plaintext, refresh token or whole-store dump leaves the donor. Decryption keys stay process-local; Python does not guarantee secure memory erasure.

Acceptance: synthetic known-answer decryptions and corrupted-tag/truncated-blob/wrong-profile tests; a donor run confirms authenticated selected-entry decoding and nonempty required values without printing them. Authentication failure produces no export. Verify token values against an observed official request privately. After export and capture, pause the donor app for the direct-client session trial. A file of encrypted `.secure` blobs alone is not a usable token export.

**Session-file contract (local design, not an Oasis schema):** `format_version: 1`, `environment` (`prd`, `stg`, or `dev`), donor `app_version`, `exported_at`, exact `device_id`, `tokens` containing the short-lived `grpc_bearer_token` and, for authenticated bootstrap, `rest_bearer_token`; verified header values and a bootstrap profile containing the observed URL/method/auth choice and approved service hosts. Require RPC credentials for subscribe; require REST credentials only where evidence requires them. Do not request `file_bearer_token`, `access_token`, `basic_token` or refresh tokens for the MVP.

The bootstrap profile is operator-supplied evidence, not a source of arbitrary extra requests. Bind it and the imported config to the same donor build/environment. Permit credentials only to explicit HTTPS/WSS origins approved for that environment; reject redirects and cross-environment roots. Preserve device identity instead of inventing another ID. Header names in RE are candidates: do not blindly replay a stale `x-mcs-clientIp` or assume every `x-mcs-*` field is mandatory.

**CLI surface:**

- Donor only: `uv run python -m tools.export_session --profile PROFILE --out FILE`, using the audited storage profile above; no token output on stdout. On this PC, no installed/login session is needed.
- `credentials inspect --credentials FILE`: offline, redacted validity/expiry report.
- `config fetch --credentials FILE --out FILE`: verified bootstrap only; never print tokens.
- `decode --input FILE --type TYPE`: offline protobuf/envelope fixture decoding; raw diagnostics explicitly distinguished from verified semantic decoding.
- `subscribe --credentials FILE --config FILE --request FILE`: validated captured config and recovered-schema request JSON; `--fetch-config` is the explicit config alternative. Emit one NDJSON record per received notification. Optional `--raw-out FILE` is an explicit sensitive-data export.
- `capability query --request FILE`: available only after capability schema/route validation.
- `send rcs --request FILE` / `send xms --request FILE`: dry-run preparation by default, showing resolved recipients, message text/type, environment and payload digest. Execution is not shipped in the receive-only MVP. Once approved for implementation, `--execute` requires an interactive confirmation of that exact prepared operation; a low-level library caller supplies an explicit one-use approval for the same digest. No generic public `rpc call` bypass.

Do not invent a convenient high-level destination/body schema until the real required identifiers are known. Typed request JSON will use the recovered schema. Initial sending scope is one recipient, plain text; XMS means only the verified text variant. Group sending, MMS/media, rich cards, NMS history and profiles remain outside this plan.

## 3. Schema recovery: hybrid, with a concrete first experiment

### Why hybrid

Capture establishes the actual route, metadata, framing and populated field tags; it cannot by itself prove names, signedness, enums, oneofs or unused fields. Protobuf length-delimited fields can represent strings, bytes, nested messages or packed values [T3]. Dart's `BuilderInfo` exposes tag/name/type and nested-builder relationships, making targeted analysis a source of structural evidence [T4]. Neither a successful `decode_raw` nor a round trip proves a schema is correct.

Use **live capture first for transport and receive fixtures**, with **targeted AOT analysis in parallel for schema structure**. Do not attempt a whole-app decompiler before the first receive fixture.

### First experiment: donor-PC receive capture

1. Ask the donor operator for the installed app version/hash, an agreed capture window, and short-lived session export. Capture the official application on **that already logged-in PC**; tokens alone do not provide an executable protocol oracle on this machine. Keep the new client disconnected.
2. On the donor PC, use mitmproxy/mitmdump local capture restricted to the actual ChattingPlus process. Example after confirming the process name: `mitmdump --mode local:ChattingPlus.exe -w receive.mitm`. Local mode is documented on Windows [T5]. Provision temporary CA trust with the operator, restore the prior trust/proxy state afterward, and store raw captures outside the repository.
3. Record a startup/reconnect subscription and config exchange without logging out or deliberately refreshing tokens. Do not open unread conversations merely to create traffic. The official client may itself emit receipts or other mutations: classify those separately; never replay them. If no incoming notification is available, the operator must arrange/authorize one known inbound plain-text message from a controlled peer; otherwise semantic validation remains blocked.
4. Extract handshake URL/path, HTTP version/ALPN where available, content type, subprotocol, header names, request/response directions, status/trailers, close events, and binary payloads. Save raw capture IDs and private hashes; produce sanitized, structure-preserving fixtures for the repository. Record whether compression is WS-level, RPC-level, or absent.
5. Identify the application envelope before running `protoc --decode_raw` on extracted protobuf bytes. Record how metadata/request/half-close is expressed and how a completed or failed stream is signaled. Do not assume that the normal gRPC five-byte envelope is the complete WS protocol. gRPC-Web framing documentation is a comparator, not proof of Oasis's WS dialect [T6].
6. Deliver `research/receive-experiment.md`, `research/transport-profile.json`, and `tests/fixtures/receive/`: at least one config exchange (if observed), one subscribe request, one corresponding response and one independently identifiable notification. A blank socket or undecoded blob is not experiment success. Missing config capture is a specific bootstrap blocker, not permission to guess a GET URL.

Mitmproxy supports HTTP2 and WS, but exposes reassembled WS data messages rather than raw fragment boundaries; ping/pong storage is limited [T7]. For frame-level uncertainties, use a runtime send/receive hook or an additional frame-capable trace. Test arbitrary application-byte chunk boundaries synthetically.

**First-experiment failure branches:** if there is no connection in regular proxy mode, use process-scoped local capture. If local capture reaches the app but TLS trust/pinning prevents plaintext inspection, stop retrying proxy configurations and switch to targeted pre-encryption capture below. Do not disable TLS verification in the new library. If donor access is unavailable, complete static recovery and offline tests, but report live MVP validation as blocked.

### Targeted AOT work and fallback

1. Fingerprint `extracted_msix/data/app.so` and `flutter_windows.dll` with SHA-256; identify the actual file container, machine architecture, snapshot version/features and engine/Dart revision. Do not infer ELF solely from `.so`, nor infer the donor build matches this binary.
2. Load the confirmed container/architecture into Ghidra (IDA is an acceptable available alternative). Anchor on `SubscribeNotificationRequest`, `SubscribeNotificationResponse`, `Notification` and `RcsMessage`; follow object-pool references and type initializer code to the relevant BuilderInfo/FieldInfo construction, not just neighboring strings.
3. Use the matching Dart SDK runtime source to interpret snapshot clusters, object headers, tagged integers, compressed pointers and object pools. Trace field registration arguments and referenced sub-builders/enums; emit a ledger: message, tag, source name, scalar/message type, cardinality, packed flag, oneof/presence, default, referenced type, and evidence location. Locations must be file offsets/RVAs plus binary hash, or capture ID/direction/frame offset.
4. Generic tooling compatibility is a first gate: Blutter documents Android ARM64 input, not this Windows x64 target [T8]. Do not budget a successful one-command Blutter dump. A version-matched, narrowly scoped object-pool/snapshot reader or Ghidra annotation script is acceptable research output; a general decompiler port is not an MVP prerequisite.
5. If static mapping cannot resolve live field registration or the MITM path fails, use Frida or WinDbg on the donor app to observe the identified serialization boundary or BuilderInfo initialization. Recover addresses and ABI from this exact build first; Frida supplies interception/memory inspection, not a universal Dart-message hook [T9]. Capture before encryption/after decryption, or inspect initialized metadata; do not patch outgoing message contents or authentication checks.
6. If the required object layout cannot be recovered and no plaintext capture is available, explicitly block codec/live integration. Deliver the evidence ledger and completed independent foundation work. Never replace missing schemas with a guessed tag dictionary.

**Schema gate:** checked-in `.proto` plus a field-level evidence ledger and sanitized fixtures. Every field needed to serialize the subscribe request must have verified tag/type and semantics; verify the notification envelope and nested fields needed to expose sender, text and conversation/message identifiers when present. Decode at least two distinguishable notifications to avoid equating one matching value with semantic proof. Preserve unverified receive variants as original bytes; do not fabricate field names or mark absent data as empty text. Proto syntax/presence must follow evidence.

Compare generated decoding with independently observed app/UI or donor DB values; compare encoded request structure with a captured official request. Byte equality is useful only for fixtures where ordering/unknown-field representation is fixed; otherwise compare normalized field values and wire records. Add unknown-field, repeated/oneof, Unicode, 64-bit, truncated-frame and malformed-length cases relevant to the recovered schema.

### Unary-route and send-schema gate

Trace the SendRcs/SendXms client construction and its use of `oasis_grpc_root` or `oasis_wss_root`; record the call chain and config binding. Do the same separately for CapabilityService/Query. A working capability route does not prove a send route.

Before sender execution is implemented, document each method's target root, path construction, framing and metadata in the route table, backed by static call-site evidence and a corresponding official-client capture. If no historical send capture exists, obtaining one requires the user's sending re-confirmation for a controlled official-client test. Do not send a speculative request just to discover transport. Recover send request **and response/error** schemas, including destination, conversation, message identity, encoding and status semantics; response type names remain UNVERIFIED until recovered.

## 4. Receive-first MVP and risk gates

MVP acceptance is an observed end-to-end run on Windows: import donor credentials, load a provenance-checked configured endpoint set or fetch verified config, subscribe, decode a known inbound notification, print verified content/identifiers, and terminate cleanly. Independently match at least two notifications to donor observations across capture/live validation. Offline replay alone is not the live MVP.

- **Session gate:** confirm which environment the donor account actually uses; no production default and no staging guess. Finish official-client capture, then close/pause the donor desktop app without logout before the clone subscribes. This does not prove that the server allows session portability or exclusivity [RE 8]. Start one short receive trial; stop on session-conflict/revocation errors, and return control to the donor app.
- **Refresh gate:** reject a credential file containing refresh-token fields in the MVP and request a short-lived-only export. Do not call QR/token paths, rotate credentials, import storage seeds, or implement automatic refresh. On expiry/401/UNAUTHENTICATED, stop and request another export. Refresh protocol, rotation and concurrent-session effects require a separate future decision [RE 4, 8].
- **Mutation gate:** receiving must not call SendBulkImdn, send delivery/read receipts, mark conversations read, or send application acknowledgments unless they are proven required and separately approved. WS ping/pong is distinct from a message receipt. If receive requires a state-changing app-level acknowledgment, disclose that concrete requirement and stop the read-only gate rather than silently adding it.
- **Sending gate:** after receive acceptance and initial static schema/route review, get explicit user re-confirmation naming environment, sender account, controlled recipient, RCS or XMS, exact content and one-message limit. If an official-app send is needed for capture, that consumes its own approved operation; later direct-client execution needs a separate approved operation after schema/route verification and local tests. General project authorization is not sending approval. Account restriction and misdelivery are the specific risks flagged in RE 8.
- **Ambiguous send result:** timeout/disconnect after dispatch is `outcome_unknown`, not failure-to-send. Do not retry automatically, even with a client-generated ID; idempotency is UNVERIFIED. A successful RPC is not evidence of final recipient delivery without a verified status/receipt.
- **Storage:** ACL-restrict credential/capture files outside the checkout; keep raw exports, packet captures, dumps, logs and payloads out of Git. Redact exception text and HTTP metadata. Test using canary tokens and synthetic identifiers; raw receive output remains a user-selected sensitive-data export. Production TLS verification remains enabled.
- **Connection policy:** no automatic reconnect/resume in the first release; callers explicitly restart after inspecting failure. No durable cursor, history backfill, exactly-once claim or silent deduplication without evidence. Bound frame/message memory; a slow consumer must get a defined backpressure/overflow error rather than silently dropped notifications.

## 5. Sequenced implementation tasks

Each row is one implementer assignment with a single owned deliverable. Adjacent parallel rows must not edit each other's owned paths. Model tiers are recommendations, not extra product components. Coordinator owns shared contract changes.

| ID / tier / dependency | Inputs | Output / owned paths | Acceptance check |
|---|---|---|---|
| T00 / Opus / first | Donor checkout, pinned upstream [S1-S3], [D1] inventory | `research/secure-store.md`: audited reusable functions, checkout/build versions, naming/layout conflict and token-entry verification procedure | Read storage/Hive/value code and tests before designing extraction; distinguish 16-byte storage key, 32-byte Hive key and token values. Record unresolved donor compatibility, never invent a KDF. |
| T01 / Sonnet / after T00 | This plan, audited export contract | Foundation contracts, `pyproject.toml`, `uv.lock`, ignore rules, package skeleton and test configuration | Windows locked install; CLI help; no network on import; session/config/route artifact formats documented. No fake operational methods. |
| T02 / Opus / after T00; parallel with T01 and T03 | Donor access, app build, capture window | Receive experiment, transport/config evidence and sanitized receive fixtures under `research/capture/`, `tests/fixtures/receive/` | First experiment above passes, or exact capture/bootstrap blockers documented. No unapproved sends or refresh export. |
| T03 / Opus / after T00; parallel with T01 and T02 | Local AOT binary and engine, matching Dart source | `research/aot/` evidence ledger for subscribe/notification fields and send/capability call-site leads | Verified hash/architecture; mappings have offsets and type evidence; unresolved fields explicitly unresolved. No guessed proto. |
| T04 / Sonnet / after T00, T01; coordinate donor access with T02 | Audited storage recipe, donor samples, session contract | `donor_store.py`, `tools/export_session.py`, `credentials.py`, related synthetic tests | Proven GCM layout and selected token extraction; donor exports only allowlisted values; consumer loads file without local store access; wrong key/tag/profile rejected; canary secrets never logged; refresh fields rejected. |
| T05 / Sonnet / after T01, T02 config evidence | Captured bootstrap and config contract | `config.py`, related fixtures/tests | Local server reproduces exact observed method/path/auth; roots and environment validated; redirects/root substitution rejected; no production network in tests. |
| T06 / Sonnet / after T01, T02 transport evidence | Verified WS profile, byte fixtures | `transport/base.py`, `transport/ws.py`, local WS fixture server/tests | Handshake and envelope match profile; split/coalesced input, status, close, cancellation and bounded buffering tested with no protobuf assumptions. |
| T07 / Opus / after T02, T03 | Capture + AOT ledgers | `proto/receive.proto`, field evidence ledger and schema revision | Schema gate passes for request, response envelope and MVP notification subtree; independent fixture values match. Unverified branches stay opaque. |
| T08 / Sonnet / after T07 | Approved receive schema | `_generated/`, `codec.py`, codec tests and generation command | Reproducible generation, independent fixture decoding, unknown bytes preserved, verified field values and malformed-input behavior correct. |
| T09 / Sonnet / after T04, T05, T06, T08 | Verified session/config/transport/codec contracts | `client.py`, `subscribe.py`, integration tests | Local endpoint receives the exact subscription; emitted events match fixtures; failure/cancel closes resources; no mutation/token endpoint called. |
| T10 / Luna / after T09 | Stable client API | Receive-only `cli.py`, usage documentation, subprocess tests | Credential inspection/decode work offline; subscribe NDJSON/stderr/exit behavior correct; Ctrl-C cleanup; no generic mutation bypass. |
| T11 / Opus / after T10 | All receive artifacts, coordinated donor window | `research/mvp-acceptance.md` | Clean offline quality checks plus real configured subscribe and independently matched notifications; redacted evidence records auth failure/exit behavior. If live inputs unavailable, label MVP unvalidated. |
| T12 / Opus / after T11 | Receive evidence and static leads; user re-confirmation before any send capture | `proto/capability.proto`, `proto/send.proto`, unary route/schema evidence | Capability and each send method individually have request/response/error and route evidence. Can complete capability first; sender output stays blocked without approved capture. |
| T13 / Sonnet / after T12 capability subset | Approved capability schema/route | Capability generated files, `capability.py`, required unary adapter, explicit CLI query/tests | Local fixture request/response match; no implicit queries or fallback sends. Live query only for operator-selected identifier/window. |
| T14 / Sonnet / after T12 send subset, T13 shared adapter, sending re-confirmation | Verified RCS/XMS schemas and prepared-operation approval contract | Send generated files, `send.py`, dry-run/execute CLI and tests | Dry-run makes zero network calls; approval binds exact payload/recipient/environment; one dispatch only; unknown outcome never retried. |
| T15 / Opus / after T14 and still-valid operation approval | Reviewed implementation, exact controlled operation | `research/send-acceptance.md` | Execute only the authorized protocol/recipient/content once; compare actual receipt on peer. RCS and XMS require separate operations/approvals; untested route is labeled unvalidated. |

Parallel schedule: T00 (study/reuse the existing secure-store code) is first. Then T01/T02/T03 start together. After their prerequisites, T04/T05/T06/T07 have disjoint ownership and can run alongside one another; T02/T04 share one coordinated donor window, not competing live sessions. T08 follows T07; T09-T11 are sequential integration. T12 is a later evidence phase, not a shortcut around MVP acceptance. T13 and T14 serialize shared unary-adapter ownership. Schema recovery is a hard dependency for codecs, capability calls and send serialization; transport-only byte work can proceed earlier.

For each implementation assignment: run its focused tests once reliably, then integrate with `uv run ruff check .`, `uv run mypy src/chatplus_core`, and `uv run pytest`. Generated protobuf code may have a scoped generated-code configuration, not suppressed application errors. Recheck generated artifacts for drift. Async tests subscribe to local server events before triggering actions, with bounded deadlines; no sleeps or timing-based polling. The complete test suite is offline by default; live acceptance is a separate operator-run procedure, never CI.

## 6. Questions for the user / donor operator

These do not block offline foundation work, but their answers gate the specified live steps.

1. **Environment/account:** Is the donor session production, staging or development, and may it be used from this PC? No staging account or server compatibility is assumed.
2. **Session handoff:** Can the donor desktop client be stopped without logout for one receive-only trial, and restarted afterward? Is another desktop session already active?
3. **Build/capture access:** What installed version and `app.so`/engine hashes does the donor PC have? Can the operator run process-scoped capture with temporary CA trust, or runtime instrumentation if trust fails? A short-lived token file alone cannot answer transport/schema questions.
4. **Secure-store/bootstrap export:** What revision/local changes are in the donor's existing `chatplus-core` checkout, and does its known cipher-key blob authenticate with the upstream tag-before-ciphertext layout? Can T04 run there to export selected decrypted tokens plus the observed config request/response, required headers and exact device identity through a private file channel? Do not transfer raw keys or send tokens in chat.
5. **Receive oracle:** Are two existing, independently identifiable notification captures available, or may the operator arrange controlled inbound messages? Which app/UI fields can be compared without generating read receipts?
6. **Local data:** Can the donor provide the actual secure/edb paths, names-only inventory of the reported 74 entries and 23 boxes, and schema-only export of `planner.db`? Hive/SQLite coexistence is established by [D1]; exact donor paths/tables still need recording. Local message/cache schemas are optional corroboration, not a switch to cache-reading.
7. **Later sending:** After receive acceptance, which exact controlled recipient, text and RCS/XMS route does the user approve? Does the user want XMS validation now or a separately scheduled operation? Until answered, only offline send preparation is permitted after schema recovery.

## 7. Evidence and tool references

Target protocol claims derive from `RE_FINDINGS.md`; storage corrections derive from [D1] and inspected code [S1-S3]. Tool references support capabilities and generic protobuf behavior, not Oasis-specific conclusions. Accessed 2026-10-15.

- [D1] User's donor-PC update in this task: encrypted auth entries, candidate key labels, 74 secure entries, 23 Hive boxes and `planner.db`. The reported trailing-tag order conflicts with [S1] and requires donor verification.
- [S1] Existing repository at commit `2e8bf59467147b29f9832ac7c7c8d24b73ac9847`, `chatplus_core/storage.py:81-105,134-161`: key lookup, profile selection and exact GCM layout.
  `https://github.com/sanguneo/chatplus-core/blob/2e8bf59467147b29f9832ac7c7c8d24b73ac9847/chatplus_core/storage.py`
- [S2] Same revision, `chatplus_core/hive.py`: CBC/PKCS7 frame decoding and keyed CRC checks.
  `https://github.com/sanguneo/chatplus-core/blob/2e8bf59467147b29f9832ac7c7c8d24b73ac9847/chatplus_core/hive.py`
- [S3] Same revision, `chatplus_core/reader.py` and `tests/test_storage.py`: cache-reader boundary and existing storage test style. `values.py` is assigned to T00 for detailed inspection; not inspected during planning.
  `https://github.com/sanguneo/chatplus-core/blob/2e8bf59467147b29f9832ac7c7c8d24b73ac9847/chatplus_core/reader.py`
  `https://github.com/sanguneo/chatplus-core/blob/2e8bf59467147b29f9832ac7c7c8d24b73ac9847/tests/test_storage.py`

- [T1] gRPC Python AsyncIO API: async channels and RPC calls.
  `https://grpc.github.io/grpc/python/grpc_asyncio.html`
- [T2] websockets asyncio client: headers, subprotocols and connection controls.
  `https://websockets.readthedocs.io/en/stable/reference/asyncio/client.html`
- [T3] Protocol Buffers encoding: wire types and length-delimited ambiguity.
  `https://protobuf.dev/programming-guides/encoding/`
- [T4] Dart protobuf BuilderInfo API: tag/type/name, oneof and sub-builder metadata. Match the app's actual protobuf version before interpreting layouts.
  `https://pub.dev/documentation/protobuf/latest/protobuf/BuilderInfo-class.html`
- [T5] mitmproxy proxy modes: process-scoped local capture on Windows.
  `https://docs.mitmproxy.org/stable/concepts/modes/`
- [T6] gRPC-Web protocol specification.
  `https://github.com/grpc/grpc/blob/master/doc/PROTOCOL-WEB.md`
- [T7] mitmproxy protocol support and WebSocket message representation.
  `https://docs.mitmproxy.org/stable/concepts/protocols/`
  `https://docs.mitmproxy.org/stable/api/mitmproxy/websocket.html`
- [T8] Blutter supported input: Android ARM64, not a ready-made Windows x64 solution.
  `https://github.com/worawit/blutter`
- [T9] Frida JavaScript API: Interceptor and memory APIs.
  `https://frida.re/docs/javascript-api/`
