# Full project regression report — Networks button fix round

Scope: Nexus Downloader v10.5 working copy (`os-transformation`). Nothing committed or pushed. Original ZIPs preserved.

## 1. Project health summary
All 247 tests in 15 modules pass on Python 3.13.16 (Linux, offscreen Qt). ruff clean, compileall clean, `pip check` clean. `core/scheduler.py`, downloader, torrent and database code are untouched.

## 2. Network button root causes and fixes
- **No feedback (D1):** Refresh / Test internet started a silent background scan. Now: buttons disable, label changes ("Refreshing…"/"Testing…"), inline status line, result line on completion (counts, failed adapter names, or error). `manual_network_scan`, `_finish_manual_scan`.
- **Lost errors (D2):** an exception in `net.test_all()` killed the worker; the lock was released but nothing reached the GUI. Worker now catches and emits `(infos, purge, manual, err)` over a queued Qt signal; GUI-side render failures are also caught so buttons are restored. A 30 s watchdog restores buttons if a probe hangs.
- **Manual vs automatic race:** pending flags + lock could lose a request. Replaced by a mutex-guarded running/next state; one coalesced re-scan keeps its manual flag.
- **Clipped Action button (D3/D5):** theme QSS adds 10 px padding to table cells, insetting cell widgets. Fixed with a font-metric-based `NetworkTable` and a padded wrapper around the button.
- **History table (D4):** same `FitTable` mechanism.
- Settings dialog "Choose folder" button 48 → 52 px.

## 3. Responsive findings
Every page × 5 window sizes × dark/light swept for clipped/overlapping buttons: clean. Dialogs (Settings, New download, Choice, Conflict, Delete) pass size checks. At 980 px with the default font: Networks hides Link, Speed, Latency (Link moves to the Network tooltip); History hides Peak, Duration, Networks Used. Network, IP, Internet, State and Action always visible; no horizontal scrollbar.

## 4. Test results (Python 3.13.16, Linux, offscreen) — executed, all OK
guard 1 · layout 3 · network_buttons 10 · responsive 5 · adapters 5 · combiner 8 · nexus 19 · multinet 33 · ratelimit 8 · integrity 11 · appearance 21 · torrent 32 · torrent_flow 28 · ui 14 · regressions 49 = **247 passed, 0 failed, 0 skipped**.
Red evidence: against the old code the new tests fail (responsive 21 failures + 1 error, network_buttons 10 errors, layout red).
Provenance: `tests/test_layout.py` was already untracked in the tree and not written by me; I only made it stable (stop auto-scan timer, wait for scan, restore).

## 5. Security findings
No new network surface or dependencies. Diff scanned: no secrets or personal paths.

## 6. CPU/RAM/disk
Not profiled this round. Known: a 40 ms progress timer is always on (unchanged). Test temp dirs previously leaked disk; `tests/netsim.py` now cleans up.

## 7. Code quality
ruff clean. `network_table.py` (new, shared `FitTable`) reuses the torrent/download table pattern. `git diff --check` OK.

## 8. Files modified
`gui/main_window.py`, `gui/network_table.py` (new), `gui/settings_dialog.py`, `tests/test_network_buttons.py` (new), `tests/test_responsive.py` (new), `tests/test_layout.py` (stabilised), `tests/test_collection_guard.py`, `CHANGELOG.md`, this report.

## 9. Test status split
- Executed OK: all 247 above (Linux, loopback/simulated adapters, offscreen).
- Failed: none. Skipped: none. Not executable here: real Windows GUI, real adapters, real displays.
- Windows-only / unverified: see §11.

## 10. Known limitations
- Hidden columns at 980 px (above). "Speed" is raw interface receive traffic, including other apps.
- Burst test cannot deterministically prove the race fix; the mutex logic is verified by reasoning and the queue tests.
- Network table is fully rebuilt each scan (D6, unchanged).

## 11. Windows verification
**Not performed.** Linux offscreen results are not proof of Windows rendering, DPI scaling (the 100/125/150 % sweeps via `QT_SCALE_FACTOR` came out identical and say nothing about real DPI), fonts, or real adapters. Please check on Windows: Networks page at 980 px, 125 %/150 % scaling, Refresh/Test with real adapters.

## 12. Remaining defects
D6 full rebuild; always-on 40 ms timer; hidden low-priority columns at minimum width. None break functionality.

## 13. Preserved functionality
HTTP engine, torrent engine, database, scheduler and download workflows unchanged; existing tests all pass unmodified apart from the collection-guard floors.
