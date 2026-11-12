# API and Internal Service Contracts — Nexus Downloader v10

## 1. Scope

Nexus Downloader is a local desktop application. The current repository does not implement a public HTTP/REST API, hosted backend, webhook receiver, or remote authentication service. Do not add one without product approval.

This document describes the boundaries through which the GUI, engine, persistence, and external services interact.

## 2. Internal contracts

### GUI → download engine

The GUI requests operations such as adding/starting, pausing, resuming, stopping, retrying, and querying download state. Exact callable signatures should be taken from the current source rather than duplicated here as an unverified API promise.

Contract requirements:
- Validate URL, destination, and options before starting work.
- Represent lifecycle state explicitly.
- Report progress and errors through the application's existing thread-safe GUI update pattern.
- Cancellation must be idempotent and must stop/close blocking I/O where possible.
- Completion must only be reported after size/assembly verification.

### GUI → torrent engine

Magnet/torrent actions are handled by `core/torrent.py` and libtorrent when installed. Torrent networking is separate from the HTTP range scheduler.

### Engine → persistence

`database/db.py` exposes methods for creating, updating, querying, and deleting download/torrent records, plus pending-cleanup records. Keep SQL parameterized and preserve the existing allowlists of updateable fields.

### Network monitor → GUI/engine

`core/network_monitor.py` produces interface snapshots and reachability/latency/counter observations. These are interface-level observations and are not automatically proof of Nexus-specific payload contribution.

## 3. External protocol integrations

### HTTP/HTTPS

- Use `httpx` and standard HTTP semantics.
- Preserve TLS certificate verification.
- Handle redirects according to safe client defaults and validate destination behavior.
- Validate `Content-Range`, `Content-Length`, response status, and total size for ranged downloads.
- Handle `200`, `206`, and `416` explicitly.
- If range support is absent or unreliable, use a safe single-stream fallback when possible.
- Use `ETag`/`Last-Modified` with `If-Range` where implemented to avoid mixing file versions.
- Bound timeouts and retries; do not retry non-idempotent operations blindly.

### Local socket binding

The app may bind sockets to an adapter source IP. This does not guarantee a particular egress NIC on every OS. Do not promise strict interface routing unless the actual platform implementation is verified.

### BitTorrent

The `libtorrent` engine handles magnet and `.torrent` networking. Required capabilities depend on the installed libtorrent version and platform support.

## 4. Authentication and authorization

There is no app-level login or token authentication in the current local application. Operating-system user permissions govern file and network access. Do not add client-side secrets or pretend a local desktop operation is protected by server-side authorization.

## 5. Request/response and errors

For internal calls, use existing Python objects/state structures and Qt signals/callbacks established in the source. Error handling should distinguish invalid input, unavailable engine/dependency, network failure, server protocol incompatibility, file-system failure, cancellation, and unrecoverable integrity failure.

Do not introduce JSON REST response schemas unless a remote API is approved.

## 6. Rate limits, retries, and timeouts

There is no Nexus-hosted API rate limiter. Respect remote server behavior and avoid excessive concurrency. Range retries and adapter circuit-breaker behavior must remain bounded and covered by tests. Retry only safe/idempotent range reads and avoid uncontrolled retry loops.

## 7. Environment/configuration

No mandatory API keys or remote service credentials are required by the current application. Runtime settings are stored locally. Do not introduce secrets into settings JSON or source control. Coding-agent MCP configuration such as FreeLLMAPI is external to the shipped app and must not become a runtime dependency.

## 8. Privacy and failure handling

Avoid logging full signed URLs, credentials, or unnecessary local paths. Keep network and disk errors actionable. Never claim a request succeeded if it timed out or the server response was invalid. Preserve recoverable partial data only when its integrity and ownership are known.
