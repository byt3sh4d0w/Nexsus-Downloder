# Architecture — Nexus Downloader v10

## 1. Architecture decision

Keep the current local desktop architecture. Do not introduce a web frontend, Electron, remote API server, microservices, or cloud database without explicit approval.

### Stack observed in the repository
- Python application entry point: `main.py`.
- PySide6/Qt GUI: `gui/`.
- HTTP/HTTPS download engine: `core/downloader.py`, using `httpx` and socket/source-address behavior.
- Adaptive range scheduling: `core/scheduler.py`.
- Interface enumeration and health checks: `core/network_monitor.py`, using `psutil` and sockets.
- Torrent/magnet engine: `core/torrent.py`, using `libtorrent` when installed.
- Local persistence: `database/db.py`, using Python `sqlite3`.
- User preferences: JSON settings handled by `gui/main_window.py`.
- Tests: `tests/`, using `unittest`, local HTTP servers and network simulation.

## 2. Component boundaries

- **GUI layer (`gui/`)**: renders state, collects user intent, dispatches actions, and receives backend updates through Qt signals/callback patterns. It must not implement network scheduling itself.
- **Download engine (`core/downloader.py`)**: owns HTTP transfer lifecycle, byte-range workers, response validation, file assembly, retry, cancellation, and download metrics.
- **Scheduler (`core/scheduler.py`)**: pure scheduling logic for range assignment, adapter rate observations, circuit breaking, and tail behavior. It should remain independently testable.
- **Network monitor (`core/network_monitor.py`)**: enumerates interfaces, identifies likely virtual interfaces, probes reachability, and collects per-interface counters. Interface counters are not necessarily Nexus-attributed payload throughput.
- **Torrent engine (`core/torrent.py`)**: owns libtorrent session/torrent operations. The HTTP scheduler does not aggregate torrent traffic.
- **Persistence (`database/db.py`)**: serializes download/torrent/history/cleanup state to SQLite and preserves schema compatibility.
- **Settings**: JSON-backed preferences; UI controls must read/write existing keys through established settings flow.
- **Security/cleanup**: `core/security.py` and `core/cleanup.py` provide relevant path and cleanup safeguards; inspect their exact behavior before changing.

## 3. Data flow

1. User submits URL or magnet/file selection in the GUI.
2. GUI validates basic input and creates/updates a persistent record.
3. Engine resolves metadata and determines single-stream versus range-capable strategy.
4. For multi-network HTTP, the engine obtains eligible adapter source addresses and creates exclusive byte ranges.
5. Workers request ranges, validate status/headers/content ranges, write per-range temporary data, and report observed bytes.
6. Scheduler uses measured throughput and failures to decide which adapter can claim the next range.
7. The engine validates part lengths, merges through a temporary output, and atomically renames when safe.
8. GUI displays actual backend state/metrics and persistence records are updated.
9. On restart, in-progress items are restored to a safe paused state and cleanup records can be retried.

## 4. Multi-network limitations

Source-IP binding does not guarantee a specific egress interface on every OS; routing tables determine actual routing, especially on Windows. The current README explicitly states that `IP_UNICAST_IF` is not implemented. Do not claim hard NIC pinning without a platform-specific implementation and real validation.

Aggregation is useful only when paths are sufficiently independent and the server/protocol supports parallel ranges. Torrent traffic uses libtorrent's networking and is outside the HTTP range scheduler. Simulated loopback benchmarks are not real hardware measurements.

## 5. Persistence and migrations

SQLite lives at `~/NexusDownloader/downloads.db` by default. The `Database` class uses a lock, `check_same_thread=False`, WAL journal mode, and `synchronous=NORMAL`. Schema upgrades currently add missing columns using `PRAGMA table_info` and `ALTER TABLE`. Preserve existing user data and make future migrations additive and tested.

Settings are stored as JSON at the application data directory path defined by `gui/main_window.py`. Inspect its helper functions before changing settings schema or defaults. Use backward-compatible defaults for new preferences.

## 6. Internal APIs and external services

There is no HTTP server or public application API in the current product. See `API.md` for internal service contracts and external dependencies. Do not add authentication or web endpoints without an approved product requirement.

## 7. Threading and lifecycle

- Network I/O and blocking file operations must not block the Qt GUI thread.
- Every worker needs explicit cancellation and bounded shutdown behavior.
- Close responses/sockets when cancellation must interrupt blocked reads.
- Protect shared scheduler/database state with appropriate locks.
- Avoid emitting UI updates from arbitrary worker threads without safe Qt signal/slot delivery.
- Stop monitors, timers, animations, and workers on shutdown.
- Never delete files while a worker may still be writing them.

## 8. Error handling and observability

Use explicit state transitions and preserve actionable failure reasons. Retries must be bounded. Distinguish recoverable adapter/range failures from fatal download failures. Do not silently swallow broad exceptions in critical loops without logging/reporting; where a background loop must survive, record the exception safely and continue only when valid.

## 9. Security boundaries

The app is local-first and runs with the user's OS permissions. No service-side authentication exists. TLS certificate verification remains enabled. Validate destination paths, filenames, archive/torrent metadata, redirects and range responses. Never alter system routes, VPNs, or firewall settings automatically. See `SECURITY_MODEL.md`.

## 10. Testing and release

Use deterministic unit tests for scheduler logic; local HTTP integration tests for range behavior; Qt offscreen tests for GUI flows; simulated adapters for controlled throughput cases; and real hardware checks for claims about OS routing. Run supported Python versions in CI and record unavailable environments honestly. See `TEST_PLAN.md`.

## 11. Proposed folder structure

Keep the current layout and add project documentation at repository root:

```text
nexus105/
├── main.py
├── core/                 # downloader, scheduler, network monitor, torrent, cleanup, security
├── database/             # SQLite persistence
├── gui/                  # PySide6 windows, dialogs, widgets, theme
├── assets/               # app icons and images
├── tests/                # unit, integration, GUI, simulator, benchmarks
├── docs/                 # optional detailed reports/evidence as they grow
├── PRD.md
├── DESIGN.md
├── ARCHITECTURE.md
├── DATABASE.md
├── API.md
├── DATA_SOURCES.md
├── SECURITY_MODEL.md
├── TEST_PLAN.md
├── AGENTS.md
└── IMPLEMENTATION_PLAN.md
```

Do not move existing modules merely to match a diagram. This structure documents the current repository and a modest documentation addition, not a request for a source rewrite.
