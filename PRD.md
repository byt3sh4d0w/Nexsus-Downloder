# Product Requirements Document — Nexus Downloader v10

**Status:** Working source of truth; existing behavior is distinguished from proposed work.  
**Product type:** Local desktop download manager.  
**Primary platform:** Windows and other platforms supported by the installed Python/Qt dependencies.  
**Current application version:** 10 (`core/version.py`).

## 1. Problem and value proposition

Users need a desktop application to manage HTTP/HTTPS downloads and BitTorrent downloads, monitor progress, recover from interruptions, and optionally use multiple independent network adapters for HTTP range downloads. Nexus aims to combine useful bandwidth from eligible adapters without letting a slow adapter hold up most of a download.

**Value proposition:** a local-first, user-controlled download manager with transparent progress, recoverable transfers, adapter-level visibility, and tested download integrity.

## 2. Target users

- People downloading large files over one or more network connections.
- Users who need pause/resume, retry, history, and destination management.
- Users of magnets and `.torrent` files, when `libtorrent` is installed and supported.
- Users who want to see which network adapters are selected and how they contribute.

No multi-user or cloud account system is currently required.

## 3. Core user journeys

1. Launch the application and see current downloads and their states.
2. Add an HTTP/HTTPS URL, confirm the filename and destination, and start the download.
3. Pause, resume, cancel, retry, inspect details, open the downloaded file/folder, or remove a record.
4. Choose eligible network adapters and observe measured adapter metrics during an HTTP multi-range download.
5. Add a magnet or `.torrent`, inspect metadata/file selection when available, and manage the torrent lifecycle.
6. Change download, network, torrent, and advanced settings; restart and confirm persisted settings/history.
7. Review completed downloads in History and clear history without unintentionally deleting files.
8. Recover from network loss, invalid server range behavior, disk errors, malformed links, or application restart.

## 4. Functional requirements

### Downloads
- Accept valid HTTP/HTTPS URLs and resolve a useful filename from server headers and URL metadata where possible.
- Detect normalized duplicate links and offer explicit actions rather than silently duplicating work.
- Avoid overwriting an existing destination; resolve filename collisions safely.
- Show accurate state, size, progress, speed, elapsed time, ETA where meaningful, and destination.
- Support start, pause, resume, stop/cancel, retry, details, open file/folder, remove record, and separately confirmed file deletion where implemented.
- Validate ranged responses and final file size. Preserve recoverable parts on failure when safe.
- Report errors clearly and never report completion before successful integrity/size checks.

### Multi-network HTTP downloads
- Discover usable adapters and allow users to choose eligible interfaces.
- Exclude likely virtual/VPN/container adapters from automatic selection by default; respect explicit user selection.
- Use a shared byte-range queue and adaptive per-adapter measurements rather than fixed equal assignment.
- Bound retries and concurrency; recover from adapter failure and avoid overlapping writes.
- Verify range metadata, file identity validators, assembled size, and final output.
- Show adapter status, speed, contribution/failure information where measured.
- Clearly distinguish simulated benchmark results from real hardware results.

### Torrents
- Support magnets and `.torrent` files when `libtorrent` is installed.
- Prevent duplicate torrents using info-hash where available.
- Expose metadata/file selection, progress, status, pause/resume, error state, and safe removal behavior where implemented.
- Explain when the torrent engine is unavailable.

### Settings and UI
- Persist current settings in the existing settings file and keep database/user data compatible.
- Maintain the current dark desktop UI and navigation patterns.
- **Planned enhancement:** live animated network-combiner visualization driven by real backend metrics.
- **Planned enhancement:** persistent accent-color presets (orange, purple, blue, green, red, teal, pink) and optional reduced-motion control. These are requirements for future implementation, not assertions that v10.5 already supports them.

## 5. Non-functional requirements

- Correctness: no corrupt output caused by overlapping ranges or invalid response handling.
- Reliability: bounded retries, cancellation, resumability where supported, clean shutdown, and visible failures.
- Responsiveness: network/file work must not block the Qt GUI thread.
- Security: TLS verification remains enabled; validate output paths; do not expose secrets or run downloads automatically.
- Compatibility: preserve existing database/settings and user files; support only versions verified by CI and dependency wheels.
- Maintainability: keep download scheduling separate from UI rendering; test key behaviors deterministically.
- Performance: bounded threads, bounded graph history, measured rather than fabricated metrics.
- Accessibility: keyboard operation, visible focus, legible contrast, and optional reduced motion for animations.

## 6. Screens and navigation

Current navigation documented by the repository: Downloads, Torrents, Networks, and History; settings are grouped into General, Downloads, Network, Torrents, and Advanced.

Planned dashboard treatment: summary/throughput panel, animated interface paths into a combiner, current download card, per-adapter contribution rows, and a bounded throughput chart. All values must come from actual application measurements.

## 7. Roles and permissions

Single local desktop user. There is no account signup, remote authorization, or server-side role system. Network selection and file operations are controlled by the local user and operating-system permissions. The application must not change routes, firewall rules, or VPN settings without explicit consent.

## 8. Data requirements

- Download records: URL, display name, destination path, size/progress, status, timestamps, speed summaries, selected/used network metadata, connection count, history visibility.
- Torrent records: info hash, source, name, save path, total/downloaded bytes, state, timestamps and speed summaries.
- Pending cleanup records: exact temporary paths that could not be cleaned up, error text, creation time.
- User preferences: settings JSON, including existing settings and future appearance preferences.
- Temporary download parts/layout metadata required for safe resume and merge.

See `DATABASE.md` and `DATA_SOURCES.md`.

## 9. Integrations

- PySide6/Qt for the desktop GUI.
- `httpx` for HTTP/HTTPS transfer operations.
- `psutil` for network-interface enumeration and interface counters.
- Python `sqlite3` for local persistent records.
- `libtorrent` for torrent/magnet operations when installed.
- Operating-system sockets and routing behavior for source-address binding.

There is no required remote API, hosted database, authentication provider, or AI runtime in the shipped application. FreeLLMAPI is a coding-environment tool, not a runtime application dependency.

## 10. Loading, empty, and error states

- Empty Downloads/History/Torrents: explain that no items exist and provide the relevant add action.
- Filename/metadata resolution: show progress and allow safe cancellation where supported.
- Unknown size: show indeterminate progress and omit misleading percentage/ETA.
- Network adapter offline/disabled/degraded: display state and do not imply contribution.
- Server rejects ranges: fall back to a safe single-stream strategy when supported.
- Retry/circuit breaker: communicate retrying/suspended state without freezing the UI.
- Disk full/permission failure: stop safely, preserve recoverable data, and show the failing path/reason.
- Torrent engine unavailable: show a clear explanation and leave HTTP/HTTPS functionality available.
- Cancelled/paused/failed/completed: distinguish states and only mark completion after verification.

## 11. MVP scope and out of scope

### Existing core scope
HTTP/HTTPS downloads, local history/settings, multi-network HTTP range scheduling, network overview, torrent/magnet workflows when dependencies support them, safe file cleanup, and desktop GUI.

### Next planned scope
Animated live combiner visualization, configurable persistent accent colors, reduced-motion preference, expanded failure diagnostics, and stronger CI coverage.

### Out of scope unless explicitly approved
Cloud accounts, cloud synchronization, remote control APIs, proxy/VPN service, automatic OS route/firewall changes, browser extensions, paid relay infrastructure, and a rewrite to a web/Electron stack.

## 12. Success criteria

- Every critical workflow has a reproducible automated test.
- Full supported-version test suite completes without hangs or unexplained failures.
- Download outputs are size/hash verified in applicable tests.
- Fast-plus-slow adapter simulations do not suffer avoidable scheduler-induced slowdown within the test's documented tolerance.
- Real hardware claims are supported by documented measurements; simulations are labeled as simulations.
- Theme settings persist and do not interrupt active downloads once implemented.
- No known critical/high-severity security issue remains without a documented mitigation.
- License and third-party notices are accurate and ownership questions are resolved before release.

## 13. Assumptions and approval-needed decisions

- Assumption: the existing PySide6 desktop application remains the product; no framework rewrite is approved.
- Assumption: SQLite and JSON settings remain local-first.
- Approval needed: exact supported OS/Python matrix for release.
- Approval needed: final copyright holder name(s) for the MIT license.
- Approval needed: whether light/system theme options are desired in addition to accent presets.
- Approval needed: whether to distribute binaries and, if so, which platform packaging formats to support.
