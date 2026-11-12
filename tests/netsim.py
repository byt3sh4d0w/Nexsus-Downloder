"""Controlled local HTTP server that simulates several network adapters.

An "adapter" is a loopback source address (127.0.0.2 ...): the downloader binds to it and the server identifies it by the
client address. Speed limits are applied to the TOTAL rate of each adapter (all its connections share one bucket), exactly
like a real link, plus an optional shared bottleneck for the whole server (a common uplink)."""
import atexit, shutil, base64, hashlib, os, random, tempfile, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

IPS = [f"127.0.0.{i}" for i in range(2, 12)]


class Sim:
    def __init__(self, size=12_000_000, rates=None, cap=None, seed=1):
        self.blob = random.Random(seed).randbytes(size) if size else b""
        self.etag, self.mtime = '"v1"', "Mon, 01 Jan 2024 00:00:00 GMT"
        self.rates = {ip: float(r) for ip, r in (rates or {}).items()}      # bytes/s per source IP (missing = unlimited)
        self.cap = cap                                                      # shared bottleneck bytes/s
        self.lock = threading.Lock(); self.nxt = {}; self.served = {}; self.requests = 0; self.range_requests = 0
        self.dead = set()          # source IPs whose connections are dropped
        self.ignore_range = self.bad_range = self.no_etag = self.send_md5 = False
        self.truncate_every = self.flaky_pct = 0
        self.change_after = None   # after N range requests the remote file changes (new bytes, new ETag)
        self.new_blob = None
        self.stall_ip = None
        self.rng = random.Random(7)
        sim = self

        class H(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"
            def log_message(self, *a): pass
            def do_HEAD(self): self.serve(True)
            def do_GET(self): self.serve(False)

            def serve(self, head):
                ip = self.client_address[0]
                if self.path.startswith("/r"):
                    self.send_response(302); self.send_header("Location", "/f"); self.send_header("Content-Length", "0"); self.end_headers(); return
                with sim.lock:
                    sim.requests += 1
                    rng = self.headers.get("Range")
                    if rng and not head: sim.range_requests += 1
                    if sim.change_after is not None and sim.range_requests > sim.change_after and sim.new_blob is not None:
                        sim.blob, sim.new_blob, sim.etag = sim.new_blob, None, '"v2"'
                    blob, etag = sim.blob, sim.etag
                    flaky = sim.flaky_pct and sim.rng.randrange(100) < sim.flaky_pct and not head
                    trunc = bool(sim.truncate_every and rng and sim.range_requests % sim.truncate_every == 0)
                if ip in sim.dead:
                    self.connection.close(); self.close_connection = True; return
                if flaky:
                    self.send_response(503); self.send_header("Content-Length", "0"); self.end_headers(); return
                size = len(blob); a, b, code = 0, size - 1, 200
                if rng and not sim.ignore_range:
                    s, e = rng.split("=")[1].split("-"); a = int(s); b = int(e) if e else size - 1; code = 206
                    if a >= size:
                        self.send_response(416); self.send_header("Content-Range", f"bytes */{size}"); self.send_header("Content-Length", "0"); self.end_headers(); return
                self.send_response(code); self.send_header("Content-Length", str(b - a + 1)); self.send_header("Accept-Ranges", "bytes")
                if not sim.no_etag: self.send_header("ETag", etag)
                self.send_header("Last-Modified", sim.mtime)
                if sim.send_md5: self.send_header("Content-MD5", base64.b64encode(hashlib.md5(blob).digest()).decode())
                if code == 206:
                    self.send_header("Content-Range", f"bytes {a + (1 if sim.bad_range else 0)}-{b}/{size}")
                self.end_headers()
                if head: return
                limit = a + (b - a + 1) // 2 if trunc else b + 1
                try:
                    pos = a
                    while pos < limit:
                        if ip in sim.dead:
                            self.connection.close(); self.close_connection = True; return
                        n = min(32768, limit - pos); sim.pace(ip, n)
                        self.wfile.write(blob[pos:pos + n]); pos += n
                        with sim.lock: sim.served[ip] = sim.served.get(ip, 0) + n
                    if trunc: self.connection.close(); self.close_connection = True
                except OSError:
                    pass

        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H); self.srv.daemon_threads = True
        self.url = f"http://127.0.0.1:{self.srv.server_address[1]}/f"
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def pace(self, ip, n):
        buckets = [(ip, self.rates.get(ip))] + ([("*", self.cap)] if self.cap else [])
        delay = 0.0
        with self.lock:
            now = time.perf_counter()
            for k, r in buckets:
                if not r: continue
                t = max(now, self.nxt.get(k, 0.0)); self.nxt[k] = t + n / r; delay = max(delay, t - now)
        if delay > 0: time.sleep(delay)

    def close(self): self.srv.shutdown(); self.srv.server_close()
    def sha(self): return hashlib.sha256(self.blob).hexdigest()


def make_downloader(D, sim, ips, connections=8, **kw):
    """Downloader whose 'local adapters' are the simulated source IPs."""
    d = tempfile.mkdtemp(); atexit.register(shutil.rmtree, d, True)      # contributors' /tmp must not fill up
    out = os.path.join(d, "f.bin")
    j = D.Downloader(sim.url, out, connections, network_ips=ips, **kw)
    j._local_ipv4s = lambda: set(IPS) | {"127.0.0.1"}
    return j, out


def run(D, sim, ips, connections=8, timeout=120, expect_sha=None, **kw):
    """-> (job, seconds, intact)"""
    j, out = make_downloader(D, sim, ips, connections, **kw)
    t = time.perf_counter(); j.start(); j.wait(timeout); dt = time.perf_counter() - t
    ok = os.path.exists(out) and hashlib.sha256(open(out, "rb").read()).hexdigest() == (expect_sha or sim.sha())
    return j, dt, ok
