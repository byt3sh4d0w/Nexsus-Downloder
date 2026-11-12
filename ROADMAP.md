# Roadmap

Nothing under "Planned" or "Experimental" is implemented unless it says so.

## Implemented
See the feature list in [README.md](README.md). Verified on Linux (Python 3.12/3.13, headless) only.

## Work in progress
- Windows verification of the full test suite (Python 3.12 and 3.13).
- Reading real-adapter statistics for the Networks page instead of only scheduler-measured rates.

## Planned
- Windows + Linux CI matrix; packaged builds (PyInstaller evaluated only after the app is verified on each OS).
- User-supplied checksum field (SHA-256) per download.
- Per-adapter throughput that also covers single-connection jobs.
- Optional file-type icon palette per theme; keyboard-navigation pass over the download table.

## Experimental
- Multi-network aggregation. Correct and measured on a loopback simulator only; **not verified on Wi-Fi + USB tethering or any real hardware**.
- Light theme: all widgets themed and contrast-checked in code, not reviewed on a real display.

## Known limitations
- On Windows a source-IP bind does not strictly force the egress NIC (`IP_UNICAST_IF` is not implemented).
- Torrents are not part of the HTTP scheduler.
- `slower_than()` per-chunk CPU cost is not measured on an idle machine.
- The speed limiter's sleep wakes on `stop()` but not on a sibling worker's abort.
- No installer, no auto-update.
