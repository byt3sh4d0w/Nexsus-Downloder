"""Speed-limit accuracy (audit finding C-04). Controlled local server (tests/netsim.py), unthrottled, so any
slowdown is the limiter's. Loopback only: proves the limiter, not real network behaviour."""
import os, sys, time, unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import _isolate  # noqa: F401,E402  (keeps tests out of the real ~/NexusDownloader; must stay first)
from core import downloader as D
from netsim import IPS, Sim, make_downloader

KB = 1024
WARMUP, WINDOW = 1.5, 4.0          # seconds


def quiet_sim(size):
    sim = Sim(size=size)
    sim.srv.handle_error = lambda *a: None        # stop() resets live connections; that is expected here
    return sim


def measure(limit, connections, size=8_000_000):
    """-> (bytes/s over the steady-state window, payload bytes, seconds). Stops the job afterwards."""
    sim = quiet_sim(size=size)
    try:
        j, out = make_downloader(D, sim, [IPS[0]], connections, speed_limit_bps=limit)
        j.start()
        time.sleep(WARMUP); d0, t0 = j.info.done, time.perf_counter()
        time.sleep(WINDOW); d1, t1 = j.info.done, time.perf_counter()
        j.stop(); j.wait(15)
        return (d1 - d0) / (t1 - t0), size, t1 - t0
    finally:
        sim.close()


class LimitIsHonoured(unittest.TestCase):
    ROWS = []

    def check(self, limit_kb, conns):
        limit = limit_kb * KB
        rate, size, secs = measure(limit, conns)
        self.ROWS.append((limit_kb, conns, size, secs, rate / KB))
        print(f"  limit {limit_kb} KB/s x{conns} conn: {rate / KB:.0f} KB/s over {secs:.1f}s (payload {size / 1e6:.0f} MB)")
        self.assertGreaterEqual(rate, limit * 0.8, "limiter is too slow")
        self.assertLessEqual(rate, limit * 1.2, "limit exceeded")

    def test_100kbs_1_connection(self): self.check(100, 1)
    def test_100kbs_8_connections(self): self.check(100, 8)
    def test_500kbs_1_connection(self): self.check(500, 1)
    def test_500kbs_8_connections(self): self.check(500, 8)

    def test_unlimited_is_not_throttled(self):
        sim = quiet_sim(size=8_000_000)
        try:
            j, out = make_downloader(D, sim, [IPS[0]], 4, speed_limit_bps=0)
            t = time.perf_counter(); j.start(); j.wait(30)
            self.assertEqual(j.info.status, "COMPLETED")
            self.assertLess(time.perf_counter() - t, 5.0, "8 MB over loopback with no limit set")
        finally:
            sim.close()

    def test_stop_is_prompt_even_with_a_tiny_limit(self):
        sim = quiet_sim(size=4_000_000)
        try:
            j, _ = make_downloader(D, sim, [IPS[0]], 4, speed_limit_bps=2 * KB)
            j.start(); time.sleep(1.0)
            t = time.perf_counter(); j.stop(); j.wait(10)
            self.assertLess(time.perf_counter() - t, 3.0, "stop() must not wait out the throttle sleep")
            self.assertFalse(j.is_alive())
        finally:
            sim.close()

    def test_resume_after_pause_does_not_burst(self):
        limit = 200 * KB
        sim = quiet_sim(size=8_000_000)
        try:
            j, _ = make_downloader(D, sim, [IPS[0]], 4, speed_limit_bps=limit)
            j.start(); time.sleep(1.5)
            j.pause(); time.sleep(2.0)
            j.resume(); d0, t0 = j.info.done, time.perf_counter(); time.sleep(2.0)
            rate = (j.info.done - d0) / (time.perf_counter() - t0)
            j.stop(); j.wait(15)
            self.assertLessEqual(rate, limit * 1.3, f"{rate / KB:.0f} KB/s right after resume")
        finally:
            sim.close()

    def test_limited_download_completes_intact(self):
        sim = quiet_sim(size=600_000)
        try:
            j, out = make_downloader(D, sim, [IPS[0]], 4, speed_limit_bps=400 * KB)
            t = time.perf_counter(); j.start(); j.wait(30)
            self.assertEqual(j.info.status, "COMPLETED")
            self.assertEqual(open(out, "rb").read(), sim.blob)
            self.assertGreaterEqual(time.perf_counter() - t, 600_000 / (400 * KB) * 0.6)
        finally:
            sim.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
