# T01 offline scaffold

Requires Python 3.12 and uv. `.python-version` selects 3.12; `uv.lock` pins the
resolved dependencies. The source package is `src/jamal`.

```console
uv sync --locked
uv run jamal --help
uv run ruff check .
uv run mypy src/jamal
uv run pytest
```

uv can provision Python 3.12 when it is not already installed. The scaffold uses
protobuf, websockets and httpx as runtime dependencies, and pytest,
pytest-asyncio, ruff and mypy for development. Native grpcio is deferred until a
native gRPC route is proven. No protocol generator is selected before schema
recovery; T08 must lock its tool and document reproducible generation.

All operational CLI commands return exit code 2 with a diagnostic on stderr,
without reading input files or making requests. Help returns exit code 0.
Library operation stubs raise `NotImplementedError`. There are no local-login,
Credential Manager, network, send or protobuf-field implementations.

Format contracts:

- `session-format.md`: explicit short-lived session and bootstrap profile.
- `transport-format.md`: transport evidence and captured-config snapshot.
- `routes-format.md`: individually verified RPC route bindings.

Keep populated sessions, raw captures/dumps/logs and raw payload output outside
this checkout with restrictive Windows ACLs. Ignore rules are a backstop, not an
ACL mechanism. Only reviewed sanitized fixtures belong in `tests/fixtures`.
Reviewed source schemas and generated Python will be tracked after their gates;
Python caches and build intermediates remain ignored.

The quality tools intentionally exclude the third-party `reference/` checkout
and extracted application artifacts. Generated-code lint/type settings can be
scoped at T08 without suppressing errors in application code.
