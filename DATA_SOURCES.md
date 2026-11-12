# Data Sources — Nexus Downloader v10

## 1. Purpose

This document distinguishes measured application data from system-level observations and derived UI values. The UI must not present estimates as verified facts.

## 2. Source inventory

| Data | Source | Meaning and limitations |
|---|---|---|
| Interface names and link state | `psutil.net_if_stats()` | OS-reported interface metadata; does not prove Internet reachability |
| Interface addresses | `psutil.net_if_addrs()` | Current address list; current implementation selects IPv4 for source binding |
| Interface receive counters | `psutil.net_io_counters(pernic=True)` | Total interface traffic from all applications, not Nexus-only payload |
| Reachability | Source-bound TCP connection probe in `NetworkMonitor.test_one()` | Tests a particular source IP reaching the probe host; probe host availability affects result |
| Probe latency | `time.perf_counter()` around TCP connect | Connection-establishment latency to the probe endpoint, not general Internet latency |
| Download bytes/rates | HTTP download worker and scheduler observations | Use for download-specific metrics when correctly attributed to the active job |
| Adapter range throughput | `RangeScheduler` observations in `core/scheduler.py` | Measured bytes over a sliding time window; affected by active workers and observation duration |
| Download state/history | SQLite `downloads` table | Persisted application state; transient values may update asynchronously |
| Torrent state and progress | `core/torrent.py` / libtorrent | Torrent-specific engine values; not measured by HTTP range scheduler |
| User preferences | settings JSON path in `gui/main_window.py` | Local preferences; use safe defaults for absent/invalid keys |
| Output file integrity | Actual file size and available hash verification in tests/workflows | Size alone does not prove content correctness; use SHA-256 in tests where appropriate |

## 3. Metric definitions

- **Interface receive speed:** change in an OS interface's receive byte counter divided by elapsed monotonic time. Includes traffic from other applications.
- **Download throughput:** bytes attributable to the relevant Nexus download divided by elapsed monotonic time. Prefer this for the main download speed.
- **Adapter contribution:** bytes or throughput attributable to the current download and adapter, not simply the interface's total receive counter.
- **Contribution percentage:** per-adapter attributed payload divided by the sum of attributed payload for the same interval. If the denominator is zero, show unavailable/0 safely rather than divide by zero.
- **ETA:** remaining known bytes divided by a smoothed positive download rate. Omit when size/rate is unknown or unstable.
- **Simulated throughput:** rate limits applied by `tests/netsim.py`; useful for deterministic regression tests but not proof of real hardware behavior.

## 4. Timing and smoothing

Use monotonic clocks for durations and throughput. Scheduler currently uses a 4-second observation window and a minimum observation period before trusting an adapter rate. Changes to these values require benchmark evidence and updated tests.

Bound history arrays used by charts. Avoid treating first-sample values or stale observations as current measurements.

## 5. Data quality and stale states

- Mark adapter information stale if discovery/probes stop updating.
- Distinguish interface-up, Internet-reachable, selected, and actively contributing states.
- Clear or age out stale throughput after disconnection.
- Handle counter resets/wraparound by clamping negative deltas and documenting platform limitations.
- Do not display fabricated speed values in tests or production.

## 6. Privacy

Network names, IP addresses, full URLs, and destination paths can reveal private information. Do not include them in shared reports unless needed and authorized. Redact tokens embedded in URLs and never log secrets.
