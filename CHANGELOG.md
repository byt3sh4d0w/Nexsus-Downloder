# Changelog

Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). Version numbers are the app's `APP_VERSION`. Older entries are summarised from `docs/HISTORY.md`.

## [Unreleased] — open-source preparation on top of v10.5

### Added
- Combiner shows HTTP throughput, torrent throughput and unattributed traffic separately; adapters nobody measures show "—" instead of 0.
- Light and System themes, persisted appearance settings, live OS-theme following.
- `Content-MD5` verification of an existing same-size file; HTTPS→HTTP redirect refusal; corrupt-database recovery.
- Tests: `test_ratelimit`, `test_integrity`, `test_appearance`, `test_combiner`, `test_collection_guard`, a simultaneous-downloads multinet test.
- README, CONTRIBUTING, SECURITY, CODE_OF_CONDUCT, ROADMAP, TROUBLESHOOTING, GitHub issue/PR templates, CI workflows, `pyproject.toml` (lint config), `requirements-dev.txt`.

### Fixed
- Tests no longer write into the real `~/NexusDownloader`: under `unittest discover` the database/settings path was fixed before the temp HOME was set, so test rows (`PAUSED.bin`, `DOWNLOADING.bin`, ...) landed in the user's own list. `tests/_isolate.py` is now imported first; a guard test fails if any test would touch the real profile.
- Row "⋯" menu opens after the click event finishes and reports an error instead of failing silently (could not be reproduced on Linux; Windows unverified).
- Security: an HTTPS link redirected to `http://` no longer sends the plain-HTTP request before being refused (request hook; also covers the file-name probe).
- Security: the optional Defender scan passes the file path through an environment variable instead of the PowerShell `-Command` text, so a crafted file name cannot run commands.
- Version string is `10`. Tests: `test_security_fixes` (4).
- Networks page: Refresh and Test internet show an immediate loading state, an inline result line (success, per-adapter failures, errors), cannot overlap, and always restore their buttons (30 s watchdog).
- Networks/History tables fit at the 980 px minimum without horizontal scrolling: low-priority columns shrink or hide first, Action buttons stay fully visible.
- Manual scans requested during an automatic scan are queued (one coalesced re-scan), never dropped; scan exceptions reach the GUI thread.
- Settings dialog folder button was 2 px under its minimum width.
- Tests: `test_network_buttons` (10), `test_responsive` (5), `test_layout` (3).
- Hypervisor adapters (VirtualBox Host-Only, VMware, Hyper-V/WSL, Parallels, QEMU) that Windows names like "Ethernet 7" were listed, enabled and probed as "No Internet". They are now recognised by MAC prefix and hidden from the Networks page (`tests/test_adapters.py`). VPN/tunnel adapters with no VM MAC stay listed but off by default.
- Speed limit was enforced per connection; it is now one limit per download (100 KB/s with 8 connections measured 96 KB/s, was 1281).
- Theme re-apply stacked a new dialog fader each time (40 applies: 11.1 s → 1.07 s); `test_regressions` ~687 s → ~110 s.
- Accent swatch click raised `AttributeError`; status colours did not follow accent/theme; appearance settings were not saved across restarts.
- Combiner reported only the first job's rate per adapter and ran its 25 FPS timer while hidden or idle.
- Primary-button hover, selected-row and action-button tints were fixed cyan; file-type icon colours were unreadable in Light.
- Disk-space check ignored the merge copy; `.parts` cleanup could remove files the app did not write; filename edge cases (bidi controls, `CONIN$`, trailing dots).
- Restored the original `tests/test_torrent.py` (32 tests) after a truncated copy was found.

### Changed
- Primary-button hover gradient is now derived from the accent colour (slightly different from the old fixed blue/cyan on Dark).
- A same-size destination file without a server `Content-MD5` is re-downloaded instead of trusted.
- Per-release notes moved from README to `docs/HISTORY.md`.

## [10.5]
Adaptive shared-queue multi-network scheduler, per-adapter statistics, Networks page.
## [10.4] / [10.4.1]
Torrent dialog with file tree and priorities, conflict dialog, details panel, resume data; multi-network regression fix.
## [10.3] / [10.2]
Add-Torrent dialog and page, torrent engine fixes (libtorrent declared as a dependency).
## [10.1]
Bug-fix round (disappearing rows, duplicates, per-download connections, cleanup, range integrity) and UI redesign.
