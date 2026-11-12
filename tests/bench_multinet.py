"""Benchmark of the multi-adapter scheduler on simulated adapters (loopback; NOT real hardware).
Usage: python tests/bench_multinet.py [trials]      Each scenario: fastest alone / slowest alone / all combined, median of N trials,
SHA-256 verified every trial. Adapter caps apply to each adapter's TOTAL rate."""
import hashlib, os, statistics, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__))); sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core import downloader as D
from netsim import IPS, Sim, run
MB = 1_000_000
SCENARIOS = [  # name, size MB, {adapter: MB/s}, shared uplink cap MB/s
    ("equal 4+4, 24 MB", 24, (4, 4), None), ("8+1, 24 MB (v10.4.1 regression case)", 24, (8, 1), None),
    ("40+5, 256 MB (v10.4.1 regression case)", 256, (40, 5), None), ("8+4+2, 28 MB", 28, (8, 4, 2), None),
    ("6 adapters 6/5/4/3/2/1, 30 MB", 30, (6, 5, 4, 3, 2, 1), None), ("shared uplink: 2 x 8 behind a 4 MB/s cap, 12 MB", 12, (8, 8), 4)]


def med(sim, ips, n, conns):
    ts = []
    for _ in range(n):
        sim.served.clear(); j, dt, ok = run(D, sim, ips, connections=conns, timeout=300)
        if not ok or j.info.status != "COMPLETED": return None
        ts.append(dt)
    return statistics.median(ts)


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 3
    print(f"{'scenario':50s} {'fastest alone':>16s} {'slowest alone':>16s} {'combined':>16s}  ideal sum   (median of {n}, intact verified)")
    for name, size, rates, cap in SCENARIOS:
        ips = IPS[:len(rates)]; sim = Sim(size * MB, rates=dict(zip(ips, [r * MB for r in rates])), cap=cap * MB if cap else None)
        conns = max(8, 2 * len(ips)); res = [med(sim, [ips[0]], n, conns), med(sim, [ips[-1]], n, conns), med(sim, ips, n, conns)]
        f = lambda t: "FAILED" if t is None else f"{t:5.1f}s {size / t:6.1f}MB/s"
        print(f"{name:50s} {f(res[0]):>16s} {f(res[1]):>16s} {f(res[2]):>16s}  {size / min(sum(rates), cap or 1e9):5.1f}s"); sim.close()
