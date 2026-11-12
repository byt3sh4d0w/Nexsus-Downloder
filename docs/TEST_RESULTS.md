# Test Results - Nexus Downloader

> Older run log. Results for the current source are in [SECURITY_TEST_RESULTS.md](../SECURITY_TEST_RESULTS.md) (251 tests, Python 3.13.16, Linux headless).

## Latest run: open-source transformation (2026-11-03)

Every number was produced by running these commands in this session. Linux only.

- OS: Linux sandbox (kernel 6.18), headless Qt. **Not Windows.** Windows 3.12.10 / 3.13.14 were not available and were not run.
- Python 3.13.16 and 3.12.3 (venv); PySide6 6.12.0, httpx 0.28.1, psutil 7.2.2, libtorrent 2.1.1.0.
- Commands (one module at a time, run serially): `QT_QPA_PLATFORM=offscreen python -W ignore -m unittest -v tests.<module>`; `python -W ignore -m compileall -q .`; `python -m pip check`; `ruff check .`.
- compileall exit 0 and pip check clean on both. ruff (config in pyproject.toml) passes on 3.13.
- Failed: 0, skipped: 0, in every module on both interpreters.

| Module | Tests | 3.13.16 | 3.12.3 |
|---|---:|---|---|
| test_collection_guard | 1 | OK | OK |
| test_nexus | 19 | OK (24 s) | OK (24 s) |
| test_multinet (scheduler; 32 original + 1 simultaneous-jobs) | 33 | OK (119 s) | OK (127 s) |
| test_ratelimit | 8 | OK (32 s) | OK (32 s) |
| test_integrity | 11 | OK (3 s) | OK (4 s) |
| test_appearance | 21 | OK (5 s) | OK (5 s) |
| test_combiner (new) | 8 | OK | OK |
| test_torrent | 32 | OK (48 s) | OK (46 s) |
| test_torrent_flow | 28 | OK (24 s) | OK (25 s) |
| test_ui | 14 | OK (16 s) | OK (15 s) |
| test_regressions | 49 | OK (93 s) | OK (97 s) |
| **Total** | **224** | | |

Limits: loopback simulation proves scheduler logic, not real adapter aggregation. GUI tests run offscreen; nothing was looked at on a real display.

### Multi-network benchmark (loopback simulation, NOT real hardware)
`python tests/bench_multinet.py 1` (1 trial per cell, SHA-256 verified, Python 3.13.16, Linux). Single trials are noisy; differences of a few percent are not meaningful.

```
scenario                                              fastest alone    slowest alone         combined  ideal sum   (median of 1, intact verified)
equal 4+4, 24 MB                                     6.4s    3.7MB/s   6.3s    3.8MB/s   3.4s    7.1MB/s    3.0s
8+1, 24 MB (v10.4.1 regression case)                 3.3s    7.3MB/s  24.3s    1.0MB/s   3.1s    7.8MB/s    2.7s
40+5, 256 MB (v10.4.1 regression case)               6.9s   37.4MB/s  51.7s    4.9MB/s   7.1s   36.1MB/s    5.7s
8+4+2, 28 MB                                         3.8s    7.4MB/s  14.3s    2.0MB/s   2.6s   10.7MB/s    2.0s
6 adapters 6/5/4/3/2/1, 30 MB                        5.3s    5.6MB/s  30.5s    1.0MB/s   2.1s   14.1MB/s    1.4s
shared uplink: 2 x 8 behind a 4 MB/s cap, 12 MB      3.3s    3.7MB/s   3.4s    3.5MB/s   3.3s    3.7MB/s    3.0s

real	3m11.582s
user	0m26.532s
sys	0m6.291s
```

Reading: with unequal adapters the combined time stays at or near the fastest adapter alone (8+1, 40+5), and with equal adapters it is about 1.9x faster (4+4: 3.4 s vs 6.4 s). The ideal-sum column is a theoretical bound, the scheduler does not reach it in the 6-adapter case (2.1 s vs 1.4 s).

---

## Previous run: audit repair (2026-10-31)


All numbers below were produced by running the commands in this run; none are copied from earlier reports.

Environment (Linux sandbox, NOT Windows):
- Python 3.13.16 and Python 3.12.3 (venv), PySide6 6.12.0, httpx 0.28.1, psutil 7.2.2, libtorrent 2.1.1.0.
- `QT_QPA_PLATFORM=offscreen python -W ignore -m unittest -v tests.<module>`; `compileall` exit 0 and `pip check` clean on both.
- **Windows 3.12.10 (`C:\Python-3.12\python.exe`) and 3.13.14 (`C:\Python\python.exe`) were not available here and were not run. Re-run the commands below on them before release.**

| Module | Tests | Python 3.13.16 | Python 3.12.3 |
|---|---:|---|---|
| tests.test_collection_guard (new) | 1 | OK | OK |
| tests.test_nexus | 19 | OK (24 s) | OK (25 s) |
| tests.test_multinet (scheduler regression) | 32 | OK (113 s) | OK (117 s) |
| tests.test_ratelimit (new) | 8 | OK (32 s) | OK (32 s) |
| tests.test_integrity (new) | 11 | OK (4 s) | OK (4 s) |
| tests.test_appearance (new) | 18 | OK (5 s) | OK (7 s) |
| tests.test_torrent (restored) | 32 | OK (48 s) | OK (48 s) |
| tests.test_torrent_flow | 28 | OK (24 s) | OK (24 s) |
| tests.test_ui | 14 | OK (16 s) | OK (16 s) |
| tests.test_regressions | 49 | OK (113 s, re-run) | OK (108 s, re-run) |
| **Total** | **212** | | |

Notes on tests.test_regressions:
- First full run on 3.13 had 2 failures (DuplicateLinks): a test left `max_speed_kbps=50` in the shared window and, now that the limit is really enforced, later downloads were throttled. Fixed in the test (restore the value). Re-run: 49/49 on both interpreters.
- First full run on 3.12 had 1 failure (ActionSystem start -> COMPLETED within 40 s). It coincided with an extra test process the author started in parallel on the same machine; two clean serial re-runs (above) passed. Treated as load interference, not proven; watch for it.
- The suite takes about 110 s instead of about 690 s before the DialogFader fix.

## Benchmarks (loopback, local server, this sandbox)
Speed limit, steady state after a 1.5 s warm-up, 4.0 s window, 8 MB payload (job stopped after the window), limit in KB = 1024 bytes:

| Limit | Connections | Before fix | After fix (3.13 / 3.12) |
|---|---:|---:|---|
| 100 KB/s | 1 | 256 KB/s | 96 / 96 KB/s |
| 100 KB/s | 8 | 1281 KB/s | 96 / 96 KB/s |
| 500 KB/s | 1 | 496 KB/s | 496 / 512 KB/s |
| 500 KB/s | 8 | 1153 KB/s | 496 / 496 KB/s |

apply_theme() x 40 (8 open dialogs, accent cycling): before 40 DialogFader filters, 80 stylesheet builds, 11.1 s total (0.07 s to 0.47 s per call, growing); after 1 filter, 40 builds, 1.07 s total (0.03 s per call, flat).

## Not verified
- Windows interpreters (above), real Wi-Fi + USB-tethering aggregation (loopback tests prove the scheduling logic only), a real display (light theme rendered offscreen only), the FreeLLMAPI MCP (not available in this run).
- bench_multinet was not run.

## Known environment noise (not defects)
Qt offscreen warnings ("This plugin does not support propagateSizeHints / setting window opacity"); server-side ConnectionResetError text when a test stops a download mid-read (the new rate-limit tests silence their own server).
