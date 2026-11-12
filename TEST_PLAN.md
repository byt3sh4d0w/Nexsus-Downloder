# Test Plan — Nexus Downloader v10

## 1. Goals

Verify correctness, recoverability, network scheduling, UI responsiveness, persistence compatibility, and release readiness. Prefer deterministic local tests and use real hardware only for claims that simulations cannot prove.

## 2. Test layers

### Unit tests
- Link normalization and duplicate detection.
- Scheduler range ownership, throughput estimation, tail behavior, circuit breaker, cooldown, bounded retries, and release/requeue.
- Network adapter classification and virtual-interface defaults.
- Settings parsing/defaults and theme-token selection once implemented.
- Database CRUD, status counts, history hiding, cleanup queue, and migration compatibility.
- Filename resolution and path/cleanup safety.

### Integration tests
- Local HTTP server: HEAD/GET, valid ranges, ignored ranges, malformed ranges, 416, truncated responses, redirects, changed ETag/Last-Modified, retry/resume, cancellation, server errors, and non-range fallback.
- File integrity: expected size and SHA-256; no overlapping/corrupt chunks.
- Multi-network simulation through `tests/netsim.py`: equal, asymmetric, multiple, unstable, disconnect/recovery, shared-uplink cap, and throughput changes.
- Torrent tests with local fixtures/seeder when libtorrent is installed.
- Database/settings persistence across application restart.

### Qt GUI tests
- Use the repository's existing offscreen strategy where available.
- Add download, row visibility, action menus, dialogs, torrent metadata/file selection, details updates, and settings apply/cancel.
- Reproduce and diagnose `test_dropped_file_opens_dialog_prefilled_once` if it still hangs.
- Test active-download behavior during theme changes and animation disablement once those features exist.
- Confirm UI state follows real backend state and that background work does not freeze the event loop.

### Static/release checks
- `python -m compileall -q .`
- `python -m unittest discover -s tests -v`
- `python -m unittest tests.test_multinet -v`
- `python tests/bench_multinet.py 3` (simulation only; may take time)
- `git diff --check`
- Packaging/startup smoke test on each supported OS/Python combination.
- Dependency/license and secret scan before release.

Run commands from the repository root with dependencies installed. Windows may use `py -3.12` / `py -3.13`; set `QT_QPA_PLATFORM=offscreen` for headless Qt tests where appropriate.

## 3. Multi-network acceptance tests

1. One adapter: output bytes/hash correct and mode is single-network.
2. Two similar adapters: combined run improves completion time in controlled simulation.
3. Strongly asymmetric pair (e.g., simulated 8 MB/s + 1 MB/s): combined run does not suffer the known fixed-range regression beyond the explicit test tolerance; fast adapter carries most bytes.
4. Three or more adapters: all usable adapters can contribute when useful and completion remains correct.
5. Shared-uplink cap: no false claim of additive bandwidth and no avoidable severe regression.
6. Speed change: scheduler adapts without corrupting or stalling.
7. Adapter disconnects: work retries/requeues safely and remaining viable adapters continue.
8. Repeated failures: circuit breaker/backoff remains bounded and download does not wait forever.
9. Range unsupported or invalid: safe single-stream fallback or clear failure.
10. Final assembly: expected size/hash, no overlapping writes, temporary parts cleaned only after success.

Simulation results must be labeled simulated. Real Wi-Fi/Ethernet/USB-tethering benchmarks must record OS, interface setup, server, file size, selected adapters, times, hashes, and observed interface traffic.

## 4. Known historical issues to recheck

- Full-suite hang at `test_torrent.TorrentPageTests.test_dropped_file_opens_dialog_prefilled_once`.
- Windows slash/backslash expectations in torrent-flow tests.
- Magnet metadata/adoption/seeding lifecycle.
- Existing-file verification.
- Peer counts affected by DHT/LSD or test environment.
- Missing `status` on transitional/test-double objects in GUI metrics.
- Mock server disconnect and Qt offscreen warnings.

These are historical leads, not assertions that current code still fails. Reproduce each against the current checkout.

## 5. Environment matrix

Test Python 3.12 and 3.13 if the project and dependency wheels support them. The current README states libtorrent wheels exist for Python 3.10–3.13; verify current package availability rather than assuming. Run on Windows and Linux if both are release targets. Do not mark an environment passed if dependencies could not be installed or the process was killed.

## 6. Failure triage

For every failure, capture exact command, environment, test name, traceback/log, exit code, and whether it reproduces alone. For hangs, capture elapsed time and process/thread diagnostics. Fix root causes; do not skip or weaken tests. Re-run focused tests, related modules, and full suite after final changes.

## 7. Exit criteria

- No unexplained failure or hang in the supported test matrix.
- Critical download paths pass and outputs are verified.
- Multi-network behavior passes deterministic asymmetric and failure tests.
- Security-sensitive cleanup/path/range cases are tested.
- Remaining platform limitations are documented honestly.
