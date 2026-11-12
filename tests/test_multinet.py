"""Multi-adapter scheduler + HTTP range correctness + reliability, against a real local HTTP server (tests/netsim.py).
Adapter speeds are caps on each simulated adapter's TOTAL rate. These are loopback simulations: they verify the scheduling and
HTTP logic, NOT real Wi-Fi/Ethernet hardware (see README, 'Verifying on Windows')."""
import hashlib, os, sys, tempfile, threading, time, unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import _isolate  # noqa: F401,E402  (keeps tests out of the real ~/NexusDownloader; must stay first)
from core import downloader as D
from core import scheduler as S
from core.network_monitor import NetworkMonitor, is_virtual
from netsim import IPS, Sim, make_downloader, run

MB = 1_000_000
A, B, C, E, F, G = IPS[:6]


class Clock:
    def __init__(self): self.t = 100.0
    def __call__(self): return self.t


class SchedulerUnit(unittest.TestCase):
    """Pure scheduling logic with a fake clock."""
    def mk(self, n=40, size=1000, clock=None):
        s = S.RangeScheduler([(i, i * size, (i + 1) * size - 1) for i in range(n)], n * size, clock or Clock())
        for ip in (A, B): s.add_adapter(ip)
        return s

    def feed(self, s, clk, ip, rate, secs=2.0):
        for _ in range(int(secs * 10)):
            clk.t += 0.1; s.on_bytes(ip, int(rate * 0.1))

    def test_ranges_are_exclusive_and_all_complete(self):
        s = self.mk(10); got = []
        while True:
            a, b = s.claim(A), s.claim(B)
            for ip, i in ((A, a), (B, b)):
                if i is S.DONE or i is S.WAIT: continue
                got.append(i); s.complete(i, ip)
            if a is S.DONE and b is S.DONE: break
        self.assertEqual(sorted(got), list(range(10)), "every range handed out exactly once")

    def test_unknown_rate_explores_then_slow_adapter_is_held_back_only_at_the_tail(self):
        clk = Clock(); s = self.mk(40, MB, clk)
        self.assertIsInstance(s.claim(B), int, "no measurement yet -> may claim")
        self.feed(s, clk, A, 8 * MB); self.feed(s, clk, B, 0.5 * MB)
        s.left = 40 * MB                                        # lots left: the slow adapter still contributes
        s.inflight.clear(); self.assertIsInstance(s.claim(B), int)
        s.left = MB                                              # almost done: a 1 MB range on a ~0.25 MB/s share (4 s) is too slow
        self.assertTrue(s.inflight); self.assertIs(s.claim(B), S.WAIT)
        self.assertIsInstance(s.claim(A), int, "the fast adapter keeps claiming")

    def test_holding_back_can_never_stall_the_download(self):
        """Regression: after many failures every adapter looked slow while `left` was small -> all workers waited forever."""
        clk = Clock(); s = self.mk(40, MB, clk)
        s.adapters[A].last_rate, s.adapters[B].last_rate = 20_000.0, 10_000.0; s.left = 1000
        self.assertIsInstance(s.claim(B), int, "nothing in flight: somebody must take the range")
        self.assertIs(s.claim(B), S.WAIT, "something in flight and a faster adapter is available: B waits")
        s.adapters[A].until = clk.t + 100                         # the only faster adapter is suspended -> no reason to wait for it
        self.assertIsInstance(s.claim(B), int)

    def test_failures_suspend_then_readmit_with_exponential_cooldown(self):
        clk = Clock(); s = self.mk(5, 10, clk); i = s.claim(A)
        d, fatal = s.fail(i, A, RuntimeError("x")); self.assertFalse(fatal); self.assertGreater(d, 0)
        i = s.claim(A); s.fail(i, A, RuntimeError("x"))                          # 2nd consecutive failure -> suspended
        self.assertIs(s.claim(A), S.WAIT); self.assertTrue(s.report()[A]["suspended"])
        self.assertIsInstance(s.claim(B), int, "healthy adapters keep working")
        clk.t += 4.0; self.assertFalse(s.report()[A]["suspended"], "cooldown expired -> re-admitted")
        i = s.claim(A); self.assertIsInstance(i, int); s.complete(i, A); self.assertEqual(s.adapters[A].strikes, 0)
        # repeated suspensions lengthen the cooldown but never beyond the cap
        for _ in range(12):
            for _ in range(2):
                clk.t += 100; j = s.claim(A)
                if isinstance(j, int): s.attempts.clear(); s.fail(j, A, RuntimeError("x"))
        self.assertLessEqual(s.adapters[A].until - clk.t, S.MAX_COOLDOWN * 1.2 + 0.01)

    def test_range_attempts_are_bounded(self):
        s = self.mk(1, 10); last = None
        for _ in range(S.MAX_ATTEMPTS):
            clk = s.clock; clk.t += 100
            i = s.claim(A if s.claim(A) is not S.WAIT else B); last = s.fail(i, A, RuntimeError("boom"))
        self.assertTrue(last[1]); self.assertIs(s.claim(B), S.DONE, "after the fatal failure nobody claims more")

    def test_release_returns_range_without_penalty(self):
        s = self.mk(1, 10); i = s.claim(A); s.release(i, A)
        self.assertEqual((s.adapters[A].errors, s.adapters[A].fails), (0, 0)); self.assertEqual(s.claim(B), i)


class VirtualInterfaces(unittest.TestCase):
    def test_virtual_names_are_recognised_and_start_excluded(self):
        for n in ("vEthernet (Default Switch)", "VMware Network Adapter VMnet8", "Tailscale", "docker0", "OpenVPN TAP-Windows", "WireGuard Tunnel", "Loopback Pseudo-Interface 1"):
            self.assertTrue(is_virtual(n), n)
        for n in ("Wi-Fi", "Ethernet", "Ethernet 2", "Local Area Connection", "Cellular", "USB Ethernet"):
            self.assertFalse(is_virtual(n), n)
        import collections
        st = collections.namedtuple("st", "isup speed"); ad = collections.namedtuple("ad", "family address")
        import socket
        stats = {"Ethernet": st(True, 1000), "vEthernet (WSL)": st(True, 10000), "Wi-Fi": st(True, 300)}
        addrs = {k: [ad(socket.AF_INET, ip)] for k, ip in zip(stats, ("10.0.0.2", "172.20.0.1", "192.168.1.5"))}
        with mock.patch("core.network_monitor.psutil.net_if_stats", return_value=stats), mock.patch("core.network_monitor.psutil.net_if_addrs", return_value=addrs):
            rows = {i.ifname: i for i in NetworkMonitor().snapshot()}
        self.assertEqual({k: (v.enabled, v.virtual) for k, v in rows.items()},
                         {"Ethernet": (True, False), "vEthernet (WSL)": (False, True), "Wi-Fi": (True, False)})


class Base(unittest.TestCase):
    def sim(self, size=12 * MB, **kw):
        s = Sim(size, **kw); self.addCleanup(s.close); return s

    def done(self, job, dt, ok, label=""):
        self.assertEqual(job.info.status, "COMPLETED", f"{label}: {job.info.status} {job.info.error}")
        self.assertTrue(ok, f"{label}: downloaded bytes differ from the remote file")
        self.assertFalse(os.path.exists(job.path + ".parts"), "temp parts cleaned up")


class SchedulerPerformance(Base):
    def test_one_adapter(self):
        s = self.sim(8 * MB, rates={A: 8 * MB}); j, dt, ok = run(D, s, [A]); self.done(j, dt, ok)
        self.assertEqual(j.info.network_mode, "SINGLE-NETWORK"); self.assertLess(dt, 2.5)

    def test_two_equal_adapters_add_up(self):
        s = self.sim(16 * MB, rates={A: 4 * MB, B: 4 * MB})
        j1, t1, ok1 = run(D, s, [A]); self.done(j1, t1, ok1)
        j2, t2, ok2 = run(D, s, [A, B]); self.done(j2, t2, ok2)
        self.assertEqual(j2.info.network_mode, "COMBINED"); self.assertLess(t2, t1 * 0.7, (t1, t2))

    def test_strongly_asymmetric_adapters_never_slower_than_the_fastest(self):
        """The v10.4.1 regression: 8 + 1 MB/s took 12.3 s vs 3.3 s for the fast adapter alone."""
        s = self.sim(24 * MB, rates={A: 8 * MB, B: 1 * MB})
        j1, t1, ok1 = run(D, s, [A]); self.done(j1, t1, ok1)
        s.served.clear(); j2, t2, ok2 = run(D, s, [A, B]); self.done(j2, t2, ok2)
        self.assertLess(t2, t1 * 1.25, f"combined {t2:.1f}s vs fastest alone {t1:.1f}s")
        self.assertGreater(s.served[A], 4 * s.served.get(B, 1), "the fast adapter carried most of the bytes")

    def test_three_adapters_with_different_speeds(self):
        s = self.sim(28 * MB, rates={A: 8 * MB, B: 4 * MB, C: 2 * MB})
        j1, t1, ok1 = run(D, s, [A]); self.done(j1, t1, ok1)
        j2, t2, ok2 = run(D, s, [A, B, C]); self.done(j2, t2, ok2)
        self.assertLess(t2, t1 * 0.85, (t1, t2))
        self.assertEqual(set(j2.network_usage()), {A, B, C}, "every adapter contributed")

    def test_six_adapters(self):
        rates = dict(zip((A, B, C, E, F, G), (6 * MB, 5 * MB, 4 * MB, 3 * MB, 2 * MB, 1 * MB)))
        s = self.sim(30 * MB, rates=rates)
        j1, t1, ok1 = run(D, s, [A]); self.done(j1, t1, ok1)
        j2, t2, ok2 = run(D, s, list(rates), connections=12); self.done(j2, t2, ok2)
        self.assertLess(t2, t1 * 0.8, (t1, t2)); self.assertEqual(len(j2.adapter_report()), 6)

    def test_shared_uplink_bottleneck_does_not_hurt(self):
        """Adapters that share one uplink cannot add speed; combining must not make it worse either."""
        s = self.sim(12 * MB, rates={A: 8 * MB, B: 8 * MB}, cap=4 * MB)
        j1, t1, ok1 = run(D, s, [A]); self.done(j1, t1, ok1)
        j2, t2, ok2 = run(D, s, [A, B]); self.done(j2, t2, ok2)
        self.assertLess(t2, t1 * 1.3, (t1, t2))

    def test_fast_adapter_becomes_slow(self):
        s = self.sim(24 * MB, rates={A: 8 * MB, B: 2 * MB})
        # Baseline: the formerly-slow adapter alone, measured in the *same* run so the
        # comparison is load-invariant. The old hardcoded 24/2*0.85 threshold assumed an
        # idle machine; under the full suite it fluctuated between 10.1 s and 11.9 s for
        # the same scheduler. Same pattern as test_strongly_asymmetric below.
        j1, t1, ok1 = run(D, s, [B]); self.done(j1, t1, ok1, "B alone (formerly slow)")
        s.served.clear()
        threading.Timer(1.0, lambda: s.rates.update({A: 0.5 * MB})).start()
        j, dt, ok = run(D, s, [A, B]); self.done(j, dt, ok, "A fast then slow + B")
        self.assertLess(dt, t1 * 0.85, f"combined {dt:.1f}s vs formerly-slow adapter alone {t1:.1f}s")

    def test_slow_adapter_recovers_and_contributes_again(self):
        s = self.sim(40 * MB, rates={A: 6 * MB, B: 0.3 * MB})
        threading.Timer(2.0, lambda: s.rates.update({B: 6 * MB})).start()
        j, dt, ok = run(D, s, [A, B]); self.done(j, dt, ok)
        self.assertGreater(s.served[B], 0.2 * 40 * MB, f"B served only {s.served[B] / MB:.1f} MB after recovering")

    def test_adapter_disconnects_during_download(self):
        s = self.sim(24 * MB, rates={A: 4 * MB, B: 4 * MB})
        threading.Timer(1.0, lambda: s.dead.add(B)).start()
        j, dt, ok = run(D, s, [A, B]); self.done(j, dt, ok)
        rep = j.adapter_report(); self.assertGreater(rep[B]["errors"], 0); self.assertTrue(rep[B]["error"])
        self.assertLess(dt, 30)

    def test_adapter_that_appears_during_download_is_adopted(self):
        s = self.sim(24 * MB, rates={A: 2 * MB, B: 2 * MB}); t0 = time.monotonic()
        j, out = make_downloader(D, s, [A], network_provider=lambda: [A] if time.monotonic() - t0 < 1.5 else [A, B])
        j.ADAPTER_REFRESH = 0.3; j.start(); j.wait(60)
        self.assertEqual(j.info.status, "COMPLETED", j.info.error); self.assertEqual(hashlib.sha256(open(out, "rb").read()).hexdigest(), s.sha())
        self.assertGreater(s.served.get(B, 0), 0.25 * 24 * MB); self.assertEqual(j.info.network_mode, "COMBINED")

    def test_no_silent_default_route_when_selected_adapters_vanish(self):
        s = self.sim(2 * MB, rates={})
        j, out = make_downloader(D, s, ["127.0.0.9"], max_retries=0); j._local_ipv4s = lambda: {"127.0.0.1"}
        j.start(); j.wait(20)
        self.assertEqual(j.info.status, "ERROR"); self.assertIn("selected network adapters", j.info.error); self.assertEqual(s.requests, 0)


class HttpCorrectness(Base):
    def check(self, s, ips=(A, B), label="", **kw):
        j, dt, ok = run(D, s, list(ips), **kw); self.done(j, dt, ok, label); return j

    def test_sizes_including_boundaries(self):
        M = D.Downloader.MIN_CHUNK
        for n in (0, 1, 100, M - 1, M, M + 1, 3 * M, 3 * M + 7, 8 * M + 3):
            s = self.sim(n); j, dt, ok = run(D, s, [A, B], timeout=30); self.done(j, dt, ok, f"size {n}")
            self.assertEqual(os.path.getsize(j.path), n)

    def test_server_ignores_range_falls_back_to_single_stream(self):
        s = self.sim(6 * MB); s.ignore_range = True; self.check(s, label="ignore-range")

    def test_invalid_content_range_is_never_trusted(self):
        s = self.sim(6 * MB); s.bad_range = True; self.check(s, label="bad Content-Range")

    def test_truncated_responses_are_retried_without_corruption(self):
        s = self.sim(8 * MB, rates={A: 8 * MB, B: 8 * MB}); s.truncate_every = 3; self.check(s, label="truncated")

    def test_transient_503s_are_retried(self):
        s = self.sim(8 * MB); s.flaky_pct = 30; self.check(s, label="flaky", ips=(A, B, C))

    def test_redirect_is_followed(self):
        s = self.sim(4 * MB); s.url = s.url.rsplit("/", 1)[0] + "/r"; self.check(s, label="redirect")

    def test_remote_file_changing_mid_download_never_mixes_versions(self):
        s = self.sim(8 * MB, rates={A: 4 * MB, B: 4 * MB}); old = s.sha()
        import random; s.new_blob = random.Random(99).randbytes(8 * MB); s.change_after = 4
        new_sha = hashlib.sha256(s.new_blob).hexdigest()
        j, dt, ok_new = run(D, s, [A, B], expect_sha=new_sha)
        self.assertEqual(j.info.status, "COMPLETED", j.info.error)
        self.assertTrue(ok_new, "result must be the complete NEW file, never a mix of old and new bytes"); self.assertNotEqual(new_sha, old)

    def test_validator_in_layout_wipes_stale_parts(self):
        s = self.sim(6 * MB); j, out = make_downloader(D, s, [A, B])
        pd = out + ".parts"; os.makedirs(pd); open(os.path.join(pd, "layout.txt"), "w").write("v3:1:1:1:\"old-etag\"")
        open(os.path.join(pd, "0.part"), "wb").write(b"stale-from-another-version")
        j.start(); j.wait(60); self.assertEqual(j.info.status, "COMPLETED"); self.assertEqual(hashlib.sha256(open(out, "rb").read()).hexdigest(), s.sha())

    def test_connection_is_bound_to_the_selected_adapter(self):
        s = self.sim(4 * MB)
        j, dt, ok = run(D, s, [B]); self.done(j, dt, ok)
        self.assertEqual(set(s.served), {B}, "server saw bytes only from the selected source address")


class SimultaneousJobs(Base):
    def test_two_jobs_share_two_adapters_and_rates_are_summed_across_jobs(self):
        import time
        from core.network_monitor import attribute_rates
        s = self.sim(16 * MB, rates={A: 4 * MB, B: 4 * MB})
        j1, _ = make_downloader(D, s, [A, B]); j2, _ = make_downloader(D, s, [A, B])
        j1.start(); j2.start()
        seen = {}
        t0 = time.time()
        while time.time() - t0 < 60 and (j1.info.status == "DOWNLOADING" or j2.info.status == "DOWNLOADING" or j1.info.status == "QUEUED"):
            if j1.info.status == j2.info.status == "DOWNLOADING":
                per_ip, _ = attribute_rates([j1.adapter_report(), j2.adapter_report()], j1.info.speed + j2.info.speed)
                for ip, r in per_ip.items():
                    seen[ip] = max(seen.get(ip, 0.0), r)
            time.sleep(0.1)
        j1.wait(60); j2.wait(60)
        self.assertEqual((j1.info.status, j2.info.status), ("COMPLETED", "COMPLETED"))
        for ip in (A, B):
            self.assertGreater(seen.get(ip, 0), 0, f"adapter {ip} measured while both jobs ran")


class Reliability(Base):
    def test_pause_resume(self):
        s = self.sim(16 * MB, rates={A: 3 * MB, B: 3 * MB}); j, out = make_downloader(D, s, [A, B]); j.start(); time.sleep(1.0)
        j.pause(); time.sleep(0.6); d1 = j.info.done; time.sleep(1.0)
        self.assertLessEqual(j.info.done - d1, 256 * 1024, "no progress while paused (only in-flight buffers)")
        self.assertEqual(j.info.status, "PAUSED"); j.resume(); j.wait(60)
        self.assertEqual(j.info.status, "COMPLETED"); self.assertEqual(hashlib.sha256(open(out, "rb").read()).hexdigest(), s.sha())

    def test_cancel_then_restart_resumes_from_existing_parts(self):
        s = self.sim(24 * MB, rates={A: 4 * MB, B: 4 * MB}); j, out = make_downloader(D, s, [A, B]); j.start(); time.sleep(2.0)
        j.stop(); j.wait(10); partial = j.info.done; self.assertGreater(partial, 2 * MB); served_before = sum(s.served.values())
        j2 = D.Downloader(s.url, out, 8, network_ips=[A, B]); j2._local_ipv4s = lambda: set(IPS) | {"127.0.0.1"}   # "application restart"
        j2.start(); j2.wait(60)
        self.assertEqual(j2.info.status, "COMPLETED"); self.assertEqual(hashlib.sha256(open(out, "rb").read()).hexdigest(), s.sha())
        self.assertLess(sum(s.served.values()) - served_before, 24 * MB - 0.5 * partial, "finished bytes were not downloaded again")

    def test_disk_write_failure_is_reported_not_retried_forever(self):
        s = self.sim(6 * MB); j, out = make_downloader(D, s, [A, B], max_retries=3)
        real = open
        def bad(path, mode="r", *a, **k):
            if str(path).endswith(".part") and "a" in mode: raise OSError(28, "No space left on device")
            return real(path, mode, *a, **k)
        with mock.patch.object(D, "open", bad, create=True):
            t = time.perf_counter(); j.start(); j.wait(30)
        self.assertEqual(j.info.status, "ERROR"); self.assertIn("disk", j.info.error.lower()); self.assertLess(time.perf_counter() - t, 10)
        self.assertFalse(os.path.exists(out), "no false completion")

    def test_concurrent_worker_failures_do_not_corrupt(self):
        s = self.sim(12 * MB, rates={A: 4 * MB, B: 4 * MB, C: 4 * MB}); s.flaky_pct = 25; s.truncate_every = 5
        j, dt, ok = run(D, s, [A, B, C], connections=12); self.done(j, dt, ok)

    def test_progress_and_sizes_are_exact(self):
        s = self.sim(10 * MB, rates={A: 8 * MB, B: 8 * MB}); ticks = []
        j, out = make_downloader(D, s, [A, B]); j.on_update = lambda i: ticks.append((i.done, i.total)); j.start(); j.wait(30)
        self.assertEqual((j.info.done, j.info.total, os.path.getsize(out)), (10 * MB, 10 * MB, 10 * MB))
        self.assertTrue(all(d <= t for d, t in ticks if t)); self.assertEqual(sum(j.network_usage().values()) > 0, True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
