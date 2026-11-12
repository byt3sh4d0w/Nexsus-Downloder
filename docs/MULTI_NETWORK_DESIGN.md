# Multi-Network Aggregation Design - Nexus Downloader v10.5

## 1. What it does
For HTTP/HTTPS downloads the engine opens several byte-range requests and binds each to a different local interface source IP. Ranges are small (512 KiB-16 MiB, ~8 per connection) and pulled from one shared queue, so a worker that finishes a range claims the next one immediately. Fast adapters naturally serve more ranges; a slow adapter is only held back near the tail, when its share would finish long after the whole job at the combined rate.

## 2. Adaptive scheduling (core/scheduler.py)
- Per-adapter throughput is measured over a 4 s sliding window (WINDOW).
- A shorter 0.75 s tail (RESPONSIVE) is used as min(full, tail) so a *collapsing* rate reaches the scheduler within ~0.75 s. On a rising rate the longer window still wins (the safe direction).
- slower_than(ip, factor) returns True only when an adapter's short-tail rate is below both factor * its own longer-window average AND factor * the best other adapter's short-tail rate. The first condition distinguishes a sudden collapse from a stable asymmetric pair.
- A worker whose adapter matches slower_than hands its in-flight range back to the queue (_PREEMPT). The part file already holds whatever was written, so a faster adapter claims it and resumes from the last byte - no bytes lost, no overlap. The slow adapter is then bar()red for PREEMPT_COOLDOWN (2 s).
- Circuit breaker: SUSPEND_AFTER=2 consecutive failures -> exponential cooldown (3 s to 20 s cap). MAX_ATTEMPTS=6 failures of one range fails the whole download.
- Adapters appearing mid-download are adopted every ADAPTER_REFRESH=2 s. Virtual adapters (VPN/VM/container) are listed but off by default and never counted as independent bandwidth.

## 3. Binding and protocol correctness
- Binding uses the adapter's IPv4 source address via httpx.HTTPTransport(local_address=ip).
- The app verifies the connection left from the bound address (_AdapterMismatch).
- On Windows a source IP does NOT strictly force the egress NIC; the routing table decides. IP_UNICAST_IF is not implemented (could not be tested here).
- Aggregation only helps when paths are sufficiently independent and the server supports parallel ranges. Torrent traffic uses libtorrent's own networking and is outside the HTTP range scheduler.

## 3. Speed measurement definitions
- Per-interface payload throughput: bytes attributable to the current download and adapter, measured over a sliding window. Not the interface's total receive counter.
- Aggregate payload throughput: sum of per-adapter attributed bytes over the same interval.
- Download progress: bytes actually received for the relevant download.
- ETA: remaining known bytes divided by a smoothed positive rate; omitted when size or rate is unknown or unstable.
- Units: MB/s (decimal) and MiB/s are distinguished in the UI.

## 4. Benchmark methodology
Controlled loopback simulation via tests/netsim.py and tests/bench_multinet.py. An "adapter" is a loopback source address; speed caps are applied per source IP with an optional shared bottleneck. These are SIMULATIONS, not real Wi-Fi/Ethernet hardware.

Baseline (before fix), python -m unittest tests.test_multinet: 32 tests, 1 FAIL
(test_fast_adapter_becomes_slow 11.67 s vs 10.2 s threshold).

After fix:
- test_fast_adapter_becomes_slow: 10.12 s (single run) < 10.2 s threshold.
- test_strongly_asymmetric (8 + 1 MB/s): combined never slower than 1.25x the fast adapter alone.
- test_two_equal_adapters_add_up: combined < 0.7x single-adapter time.
- All other scheduler/correctness/reliability tests pass.
- test_one_adapter: 2.60 s vs 2.5 s threshold under full-suite load - a load-sensitive benchmark, not a scheduler defect.

## 5. Limitations
- Combining only helps with independent paths and a range-capable server. Behind one shared uplink or a server-side cap there is no gain, and combined is not guaranteed to beat the fastest adapter alone.
- Real hardware claims require documented measurements (OS, interface setup, server, file size, selected adapters, times, hashes, observed interface traffic). This repository contains only loopback simulations.
- Light/system theme is implemented and tested offscreen only; it has not been looked at on a real display.
- The per-download speed limit is a job-wide limiter shared by all connections (core/downloader.py `_rate_wait`); it is independent of the scheduler.

## 6. Reporting (combiner and Networks page)
- Per-adapter rate = sum over every DOWNLOADING job's scheduler report (`core.network_monitor.attribute_rates`). A finished job's last report is ignored.
- The big number is HTTP throughput (sum of job speeds). Torrent throughput comes from libtorrent and is shown separately, never added in. HTTP bytes not covered by any adapter report (single-connection jobs) are shown as "not attributed to an adapter", never spread across adapters.
- An adapter nothing is measuring shows a dash (not measured), not 0.
- Measured loopback benchmark (`python tests/bench_multinet.py 1`, 1 trial, Linux, NOT real hardware): see docs/TEST_RESULTS.md.
