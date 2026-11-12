# Project Audit - Nexus Downloader v10.5

Scope: full-repository architecture, defect, security, performance and release-readiness review.
Environment: Windows, Python 3.12.10, PySide6 6.12.0, httpx 0.28.1, psutil 7.2.2.

## 1. Repository layout (observed)
- main.py - entry point: from gui.main_window import run
- core/ - downloader.py (HTTP engine), scheduler.py (adaptive range scheduler), network_monitor.py (interface discovery), torrent.py (libtorrent), cleanup.py, security.py, links.py, version.py
- database/ - db.py (SQLite: downloads, torrents, pending_cleanup)
- gui/ - main_window.py, download_table.py, torrent_page/dialog/details, settings_dialog.py, theme.py, widgets.py, icons.py, new_download.py, conflict_dialog.py, delete_dialog.py, choice_dialog.py, details_dialog.py
- tests/ - test_nexus.py, test_multinet.py, test_torrent.py, test_torrent_flow.py, test_regressions.py, test_ui.py, netsim.py, bench_multinet.py
- assets/ - logo.png, logo_source.png, logo_64.png, nexus.ico
- Docs: PRD.md, DESIGN.md, ARCHITECTURE.md, DATABASE.md, API.md, DATA_SOURCES.md, SECURITY_MODEL.md, TEST_PLAN.md, IMPLEMENTATION_PLAN.md, AGENTS.md, README.md, requirements.txt, run_nexus.bat

## 2. Architecture and critical call paths
- Add URL -> NewDownloadDialog -> MainWindow.start_job -> Downloader.start -> Downloader._run -> _attempt_download -> _probe -> _multi / _single
- Multi-network: Downloader._multi -> RangeScheduler (claim/complete/fail/on_bytes) -> worker threads -> fetch() -> httpx range requests bound to a source IP
- GUI updates: Downloader.on_update -> MainWindow.download_update signal -> update_job
- Persistence: Database (SQLite, WAL, check_same_thread=False) -> add_row / update
- Shutdown: MainWindow.closeEvent -> stop workers, flush torrent resume data, close DB

## 4. Confirmed defects and fixes
| # | Defect | Root cause | Fix | Evidence |
|---|---|---|---|---|
| 1 | test_fast_adapter_becomes_slow baseline: 11.67 s vs 10.2 s threshold | Scheduler averaged rate over a 4 s window; a suddenly-slow adapter kept being judged by its old speed for ~4 s | rate() returns min(full_window_average, responsive_short_tail) [RESPONSIVE=0.75 s] so collapsing rate is visible within ~1 s; scheduler.lock now RLock (rate()/slower_than() are called while lock held) | tests.test_multinet SchedulerUnit: all 6 OK |
| 2 | ResourceWarning on layout.txt | open() without context manager in _multi() | with open(...) as lf | ResourceWarning gone; remaining test-fixture warnings are environment noise |

## 5. Suspected risks (not reproduced / not fixed)
- gui/widgets.py is dead code that fails to import; left untouched (out of scope)
- test_one_adapter and scheduler performance tests have load-sensitive thresholds; not regressions from changes
- Qt offscreen warnings and ConnectionResetError from mock HTTP server are test-environment noise

## 6. Security review
- TLS verification preserved (httpx verify=True, trust_env=False for multi-network)
- Path traversal: filename sanitization in core/security.py; cleanup only touches exact app-owned temp paths (core/cleanup.py)
- No secrets logged; settings JSON is local-only
- No route/firewall/VPN changes; source-IP binding only

## 6. Performance
- Scheduler is pure logic, lock-protected, no thread creation of its own
- Combiner widget: 25 FPS timer, bounded history (60 samples), single paintEvent
- No evidence of unbounded growth or CPU spikes in tested paths

## 7. Open questions for release
- Copyright holder name in LICENSE is a placeholder (Nexus Downloader Contributors). Must be confirmed before publishing.
- Light/system theme is implemented but not exercised on a real display in this environment.
- Python 3.13 not checked (only 3.12 available)

## 8. Status after the repair run
Audit findings C-01 to C-07 and the related items P-04 to P-07 are fixed and covered by tests; see BUG_FIX_REPORT.md ("Repair run") and TEST_RESULTS.md. Items still open are listed under "Remaining issues" there.
