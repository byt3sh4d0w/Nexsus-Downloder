import base64
import hashlib
import os
import re
import shutil
import time
import threading
from dataclasses import dataclass, field
from collections import deque
from urllib.parse import urlparse

import httpx

from .scheduler import DONE, WAIT, PREEMPT_FACTOR, PREEMPT_COOLDOWN, RangeScheduler
from .security import validate_download_url, refuse_https_downgrade, DowngradeRefused, MAX_REDIRECTS
from .version import APP_VERSION


class _NonRetryableError(Exception):
    """Raised for failures automatic retry can't help with (bad URL, no disk
    space, etc.) so _run() fails immediately instead of retrying them."""


class _RangeViolation(RuntimeError):
    """Server answered a Range request in a way we can't trust (200 instead of
    206, Content-Range mismatch, 416...). Never append such data."""


def _parse_content_range(value):
    """'bytes 10-99/1000' -> (10, 99, 1000); total None for '*'. None if bad."""
    m = re.fullmatch(r"\s*bytes\s+(\d+)-(\d+)/(\d+|\*)\s*", value or "", re.I)
    if not m:
        return None
    start, end = int(m.group(1)), int(m.group(2))
    total = None if m.group(3) == "*" else int(m.group(3))
    if end < start or (total is not None and end >= total):
        return None
    return start, end, total


def _check_206(r, start, end, total):
    """Validate a 206 response against the exact range we asked for.
    `end`/`total` may be None when unknown."""
    if r.status_code != 206:
        raise _RangeViolation(f"server answered HTTP {r.status_code} instead of 206 Partial Content")
    cr = _parse_content_range(r.headers.get("content-range", ""))
    if cr is None:
        raise _RangeViolation("missing or malformed Content-Range header")
    c_start, c_end, c_total = cr
    if c_start != start:
        raise _RangeViolation(f"Content-Range starts at {c_start}, requested {start}")
    if end is not None and c_end != end:
        raise _RangeViolation(f"Content-Range ends at {c_end}, requested {end}")
    if total and c_total is not None and c_total != total:
        raise _RangeViolation(f"Content-Range total {c_total} != expected {total}")
    if total and c_end != total - 1 and end is None:
        raise _RangeViolation(f"Content-Range ends at {c_end}, expected {total - 1}")
    return c_total


class _RemoteChanged(_RangeViolation):
    """The remote file is not the version the earlier bytes came from."""


class _AdapterMismatch(RuntimeError):
    """A connection did not leave from the selected adapter's address."""


class _MergeStopped(Exception):
    pass


_PREEMPT = object()     # fetch() returns this: the adapter became much slower than another viable
                        # adapter, so the worker hands the range back. The part file keeps what
                        # was already written; a faster adapter will claim it and resume there.


class _ExpiredError(Exception):
    """Link expired / no longer authorised / server won't resume. Retrying
    the same URL can't help; the user must supply a fresh link."""


EXPIRED_CODES = (401, 403, 410, 416)


def _check_redirect(start_url, final_url):
    """A redirect may never downgrade HTTPS to plain HTTP: the user asked for an encrypted transfer."""
    if urlparse(start_url).scheme.lower() == "https" and urlparse(final_url).scheme.lower() == "http":
        raise _NonRetryableError("Refused: the server redirected this HTTPS link to an unencrypted HTTP address.")


def _classify(exc):
    """-> 'invalid' | 'expired' | 'transient' (walks the exception chain)."""
    e = exc
    for _ in range(6):
        if e is None:
            break
        if isinstance(e, _ExpiredError):
            return "expired"
        if isinstance(e, (_NonRetryableError, DowngradeRefused)):
            return "invalid"
        if isinstance(e, httpx.HTTPStatusError):
            code = e.response.status_code
            if code in EXPIRED_CODES:
                return "expired"
            if 400 <= code < 500 and code not in (408, 425, 429):
                return "invalid"
            return "transient"
        if isinstance(e, (httpx.UnsupportedProtocol, httpx.InvalidURL)):
            return "invalid"
        if isinstance(e, httpx.ConnectError) and any(
                k in str(e).lower() for k in ("name or service", "nodename", "getaddrinfo", "name resolution")):
            return "invalid"
        e = e.__cause__ or e.__context__
    return "transient"


@dataclass
class DownloadInfo:
    total: int = 0
    done: int = 0
    speed: float = 0.0
    status: str = "QUEUED"
    error: str = ""
    url: str = ""
    final_url: str = ""
    range_supported: bool = False
    tls_verified: bool = False
    content_md5: str = ""
    network_mode: str = "AUTO"
    networks_used: tuple = field(default_factory=tuple)
    samples: deque = field(default_factory=lambda: deque(maxlen=12), repr=False)


class Downloader:
    """Resumable HTTP/HTTPS downloader with application-level multi-network aggregation.

    v9 does not change the Windows routing table and does not create a bonded OS adapter.
    Instead, it opens multiple HTTP byte-range requests and binds different worker
    connections to different local interface IP addresses. The ranges are merged into
    one final file after all workers complete.
    """

    def __init__(self, url, path, connections=8, network_monitor=None,
                 speed_limit_bps=0, bind_ip="", network_ips=None,
                 read_timeout=60.0, max_retries=3, network_provider=None):
        self.url = str(url).strip()
        self.path = str(path)
        self.connections = max(1, min(int(connections), 32))
        self.monitor = network_monitor
        self.read_timeout = max(5.0, float(read_timeout))
        self.MAX_RETRIES = max(0, int(max_retries))      # per-instance: set from Settings
        self.speed_limit_bps = max(0, int(speed_limit_bps or 0))
        # v8 bind_ip remains accepted for compatibility. v9 prefers network_ips.
        legacy = str(bind_ip or "").strip()
        values = list(network_ips or [])
        if legacy and not values:
            values = [legacy]
        self.network_ips = []
        for ip in values:
            ip = str(ip or "").strip()
            if ip and ip not in self.network_ips:
                self.network_ips.append(ip)

        self.network_provider = network_provider      # optional callable -> current eligible adapter IPs (re-read each attempt)
        self._etag = self._last_modified = ""
        self.info = DownloadInfo(url=self.url)
        self._stop = threading.Event()
        self._pause = threading.Event()
        self._pause.set()
        self._thread = None
        self._live = set()           # open responses; closed by stop() to unblock reads
        self._live_lock = threading.Lock()
        self._lock = threading.RLock()
        self._sample_lock = threading.Lock()
        self._sample_bytes = 0
        self._sample_time = time.perf_counter()
        self._samples = deque(maxlen=10)
        self._last_emit = 0.0
        self._rate_lock = threading.Lock()
        self._rate_next = 0.0         # perf_counter time at which the bytes charged so far would be fully sent
        self._network_used = set()
        self._network_use_counts = {}
        self._network_plan = {}
        self.on_update = None
        self.security = {"transport": "", "tls_verified": False, "url_valid": False}

    def start(self):
        if self._thread and self._thread.is_alive():
            self.resume()
            return
        self._stop.clear()
        self._pause.set()
        self.info.error = ""
        self.info.status = "DOWNLOADING"
        self._reset_speed_meter(self.info.done)
        self._thread = threading.Thread(target=self._run, name="NexusDownload", daemon=True)
        self._thread.start()
        self._emit(force=True)

    def pause(self):
        if self.info.status in ("COMPLETED", "ERROR", "STOPPED"):
            return
        self._pause.clear()
        self.info.status = "PAUSED"
        self.info.speed = 0.0
        self._emit(force=True)

    def resume(self):
        if self._stop.is_set():
            self._stop.clear()
        self._pause.set()
        if not self._thread or not self._thread.is_alive():
            self.start()
            return
        if self.info.status in ("PAUSED", "QUEUED", "STOPPED"):
            self.info.status = "DOWNLOADING"
            self._reset_speed_meter(self.info.done)
            self._emit(force=True)

    def stop(self):
        self._stop.set()
        self._pause.set()
        self.info.status = "STOPPED"
        self.info.speed = 0.0
        # Close open responses so a worker blocked in a socket read wakes up
        # now instead of after the read timeout.
        with self._live_lock:
            live = list(self._live)
        for r in live:
            try:
                r.close()
            except Exception:
                pass
        self._emit(force=True)

    def _track(self, r):
        with self._live_lock:
            self._live.add(r)
        if self._stop.is_set():       # stop() raced with this response opening
            try:
                r.close()
            except Exception:
                pass

    def _untrack(self, r):
        with self._live_lock:
            self._live.discard(r)

    def is_alive(self):
        return bool(self._thread and self._thread.is_alive())

    def wait(self, timeout=2.0):
        thread = self._thread
        if thread and thread is not threading.current_thread():
            thread.join(max(0.0, float(timeout)))

    def network_usage(self):
        with self._lock:
            return dict(self._network_use_counts)

    def network_plan(self):
        with self._lock:
            return dict(self._network_plan)

    def _emit(self, force=False):
        now = time.perf_counter()
        if not force and now - self._last_emit < 0.2:
            return
        self._last_emit = now
        with self._lock:
            self.info.networks_used = tuple(sorted(self._network_used))
        if self.on_update:
            try:
                self.on_update(self.info)
            except Exception:
                pass

    def _record_network_use(self, bind_ip):
        if not bind_ip:
            return
        with self._lock:
            self._network_used.add(bind_ip)
            self._network_use_counts[bind_ip] = self._network_use_counts.get(bind_ip, 0) + 1
            self.info.networks_used = tuple(sorted(self._network_used))

    def _wait_if_paused(self):
        if not self._pause.is_set():
            self._pause.wait()
        return not self._stop.is_set()

    def _reset_speed_meter(self, done=None):
        with self._sample_lock:
            self._sample_bytes = int(self.info.done if done is None else done)
            self._sample_time = time.perf_counter()
            self._samples.clear()
        self.info.speed = 0.0

    def _update_speed(self, done):
        now = time.perf_counter()
        with self._sample_lock:
            dt = now - self._sample_time
            if dt >= 0.20:
                delta = max(0, int(done) - self._sample_bytes)
                inst = delta / dt
                self._samples.append((now, inst))
                self._sample_bytes = int(done)
                self._sample_time = now
                cutoff = now - 1.25
                recent = [v for t, v in self._samples if t >= cutoff]
                if recent:
                    self.info.speed = sum(recent) / len(recent)
        self._emit()

    RATE_BURST = 0.1       # seconds of unused credit the limiter may bank (e.g. across a pause)

    def _rate_wait(self, nbytes):
        """Job-wide speed limit shared by every worker. Each call reserves nbytes/limit seconds on a single
        monotonic timeline and sleeps until its slot, so the total honours the limit however many connections run.
        Call it BEFORE the bytes are written/counted so progress never runs ahead of the budget. The sleep is sliced
        on the stop event: stop() is never delayed by a throttle."""
        limit = self.speed_limit_bps
        if limit <= 0 or nbytes <= 0:
            return
        with self._rate_lock:
            slot = max(self._rate_next, time.perf_counter() - self.RATE_BURST) + nbytes / limit
            self._rate_next = slot
        while not self._stop.is_set():
            left = slot - time.perf_counter()
            if left <= 0:
                return
            self._stop.wait(min(left, 0.1))

    def _local_ipv4s(self):
        if self.monitor is not None and hasattr(self.monitor, "local_ipv4s"):
            try:
                return set(self.monitor.local_ipv4s())
            except Exception:
                pass
        try:
            import socket, psutil
            return {
                str(getattr(addr, "address", ""))
                for values in psutil.net_if_addrs().values()
                for addr in values
                if getattr(addr, "family", None) == socket.AF_INET and getattr(addr, "address", "")
            }
        except Exception:
            return set()

    def _valid_network_ips(self):
        if self.network_provider is not None:
            try:
                self.network_ips = [ip for ip in dict.fromkeys(str(x).strip() for x in self.network_provider()) if ip]
            except Exception:
                pass                                   # keep the last known list
        if not self.network_ips:
            return []
        local = self._local_ipv4s()
        return [ip for ip in self.network_ips if ip in local]

    def _client(self, bind_ip=None):
        timeout = httpx.Timeout(connect=6.0, read=self.read_timeout, write=60.0, pool=15.0)
        if bind_ip:
            transport = httpx.HTTPTransport(verify=True, local_address=bind_ip, retries=0)
            return httpx.Client(
                follow_redirects=True,
                max_redirects=MAX_REDIRECTS,
                verify=True,
                timeout=timeout,
                headers={"User-Agent": f"NexusDownloader/{APP_VERSION}", "Accept-Encoding": "identity"},
                event_hooks={"request": [refuse_https_downgrade(self.url)]},
                transport=transport,
                # A proxy would collapse traffic onto the proxy route and defeat
                # per-adapter source binding, so explicit multi-network requests
                # intentionally bypass environment proxy settings.
                trust_env=False,
            )
        return httpx.Client(
            follow_redirects=True,
            max_redirects=MAX_REDIRECTS,
            verify=True,
            timeout=timeout,
            headers={"User-Agent": f"NexusDownloader/{APP_VERSION}", "Accept-Encoding": "identity"},
            event_hooks={"request": [refuse_https_downgrade(self.url)]},
        )

    def _probe_once(self, bind_ip=None):
        with self._client(bind_ip) as client:
            try:
                r = client.head(self.url)
                r.raise_for_status()
                final_url = str(r.url)
                if urlparse(final_url).scheme.lower() not in ("http", "https"):
                    raise ValueError("Redirected to an unsupported URL scheme.")
                _check_redirect(self.url, final_url)
                total = int(r.headers.get("content-length", "0") or 0)
                ranges = r.headers.get("accept-ranges", "").lower() == "bytes"
                ctype = r.headers.get("content-type", "")
                md5 = r.headers.get("content-md5", "")
                self.security["tls_verified"] = final_url.lower().startswith("https://")
                self._etag, self._last_modified = r.headers.get("etag", ""), r.headers.get("last-modified", "")
                return total, ranges, final_url, ctype, md5
            except Exception:
                pass

            with client.stream("GET", self.url, headers={"Range": "bytes=0-0"}) as r:
                r.raise_for_status()
                final_url = str(r.url)
                if urlparse(final_url).scheme.lower() not in ("http", "https"):
                    raise ValueError("Redirected to an unsupported URL scheme.")
                _check_redirect(self.url, final_url)
                self.security["tls_verified"] = final_url.lower().startswith("https://")
                ctype = r.headers.get("content-type", "")
                content_range = r.headers.get("content-range", "")
                total = 0
                if "/" in content_range:
                    try:
                        total = int(content_range.rsplit("/", 1)[1])
                    except ValueError:
                        total = 0
                if not total:
                    total = int(r.headers.get("content-length", "0") or 0)
                ranges = r.status_code == 206 and bool(content_range)
                md5 = r.headers.get("content-md5", "")
                self._etag, self._last_modified = r.headers.get("etag", ""), r.headers.get("last-modified", "")
                for _ in r.iter_bytes(1):
                    break
                return total, ranges, final_url, ctype, md5

    def _probe(self):
        ok, reason = validate_download_url(self.url)
        if not ok:
            raise _NonRetryableError(reason)
        self.security["url_valid"] = True
        parsed = urlparse(self.url)
        self.security["transport"] = "HTTPS / TLS" if parsed.scheme.lower() == "https" else "HTTP (unencrypted)"

        candidates = self._valid_network_ips()
        attempts = list(candidates)
        if not attempts:
            if self.network_ips:
                raise RuntimeError("None of the selected network adapters is online.")
            attempts = [None]
        last_exc = None
        for bind_ip in attempts:
            try:
                result = self._probe_once(bind_ip)
                return result
            except Exception as exc:
                last_exc = exc
        # No silent fallback to the default route: the user chose these adapters.
        if last_exc:
            raise last_exc
        raise RuntimeError("Unable to probe download URL.")

    MAX_RETRIES = 3
    RETRY_BACKOFF = (2.0, 5.0, 15.0)

    def _run(self):
        attempt = 0
        while True:
            try:
                self._attempt_download()
                return
            except Exception as exc:
                if self._stop.is_set():
                    self.info.status = "STOPPED"
                    self.info.speed = 0.0
                    self._emit(force=True)
                    return
                kind = _classify(exc)
                if kind == "expired":
                    # Expired link / resume unavailable: terminal "Retrying"
                    # state — user presses Resume (ideally with a fresh link).
                    self.info.error = f"Link expired or resume not available ({exc}). Press Resume to retry."
                    self.info.status = "RETRYING"
                    self.info.speed = 0.0
                    self._emit(force=True)
                    return
                if kind == "invalid" or attempt >= self.MAX_RETRIES:
                    self.info.error = f"Invalid or unreachable link: {exc}" if kind == "invalid" else str(exc)
                    self.info.status = "ERROR"
                    self.info.speed = 0.0
                    self._emit(force=True)
                    return
                wait_s = self.RETRY_BACKOFF[min(attempt, len(self.RETRY_BACKOFF) - 1)]
                attempt += 1
                self.info.error = f"{exc} — retrying in {int(wait_s)}s ({attempt}/{self.MAX_RETRIES})"
                self.info.status = "RETRYING"
                self.info.speed = 0.0
                self._emit(force=True)
                # Event.wait() with a timeout: sleeps, but wakes immediately
                # if stop() fires during the backoff, instead of an
                # uninterruptible time.sleep().
                if self._stop.wait(wait_s):
                    self.info.status = "STOPPED"
                    self._emit(force=True)
                    return

    def _attempt_download(self):
        total, ranges, final_url, _ctype, content_md5 = self._probe()
        if self.info.status == "RETRYING" and self._pause.is_set():
            self.info.status = "DOWNLOADING"   # URL works: stop showing Retrying
            self.info.error = ""
        self.info.total = max(0, int(total))
        self.info.final_url = final_url
        self.info.range_supported = bool(ranges)
        self.info.tls_verified = final_url.lower().startswith("https://")
        self.info.content_md5 = content_md5
        self.security["tls_verified"] = self.info.tls_verified

        candidates = self._valid_network_ips()
        if len(candidates) > 1 and ranges and self.info.total > 0 and self.connections > 1:
            self.info.network_mode = "COMBINED"
        elif len(candidates) == 1 and ranges and self.info.total > 0:
            self.info.network_mode = "SINGLE-NETWORK"
        else:
            self.info.network_mode = "AUTO"
        self._emit(force=True)

        os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
        if self._existing_file_is_verified():
            self.info.done = self.info.total
            self.info.status = "COMPLETED"
            self.info.speed = 0.0
            self._emit(force=True)
            return

        multi = bool(ranges) and self.info.total > self.MIN_CHUNK and self.connections > 1
        self._check_disk_space(self.info.total, multi)

        if not multi:
            self._single(final_url)
        else:
            try:
                self._multi(final_url, self.info.total)
            except _RangeViolation:
                # Server can't be trusted for byte ranges: drop the multi-part
                # data and fall back to one safe, sequential stream.
                if self._stop.is_set():
                    return
                self._clear_partdir(self.path + ".parts")
                self.info.network_mode = "AUTO"
                self.info.range_supported = False
                self.info.done = 0
                self._reset_speed_meter(0)
                self._emit(force=True)
                self._single(final_url)

    def _existing_file_is_verified(self):
        """A file already at the destination counts as this download only when the server's Content-MD5
        proves it. A matching size alone never does: the file may be anything. Otherwise the normal
        download runs and replaces it atomically once the new content is complete and verified."""
        if not (self.info.total > 0 and self.info.content_md5 and os.path.isfile(self.path)
                and os.path.getsize(self.path) == self.info.total):
            return False
        try:
            self._check_integrity(self.path)
            return True
        except Exception:
            return False

    @staticmethod
    def _clear_partdir(partdir):
        """Remove what this app wrote into a .parts folder (numbered parts, layout, temp files) and the folder
        itself only if that leaves it empty: anything else in there is not ours to delete."""
        try:
            for fn in os.listdir(partdir):
                if re.fullmatch(r"\d+\.part|layout\.txt|.*\.tmp", fn):
                    Downloader._remove_quiet(os.path.join(partdir, fn))
            os.rmdir(partdir)
        except OSError:
            pass

    def _check_disk_space(self, total, multi=False):
        """Priority 5.17 — fail clearly, before writing anything, instead of
        letting the disk fill mid-transfer. Multi-connection mode keeps every part until the merged file is
        complete, so it needs the remaining download plus a full-size merge copy."""
        if total <= 0:
            return
        if multi:
            partdir = self.path + ".parts"
            have = sum(os.path.getsize(os.path.join(partdir, n)) for n in os.listdir(partdir)) if os.path.isdir(partdir) else 0
            total = total + max(0, total - have)
        try:
            target_dir = os.path.dirname(os.path.abspath(self.path)) or "."
            free = shutil.disk_usage(target_dir).free
        except OSError:
            return  # can't determine free space; proceed as before
        if free < total:
            raise _NonRetryableError(
                f"Not enough free disk space: need {total:,} bytes, {free:,} available."
            )

    def _check_integrity(self, path):
        """Priority 4.12 — verify Content-MD5 when the server provided one.
        Servers that don't provide it are unaffected (compatibility preserved);
        this never claims a file is verified beyond the byte-count check unless
        an actual hash was available to check against."""
        if not self.info.content_md5:
            return
        h = hashlib.md5()
        with open(path, "rb") as f:
            while True:
                chunk = f.read(1024 * 1024)
                if not chunk:
                    break
                h.update(chunk)
        digest = base64.b64encode(h.digest()).decode()
        if digest != self.info.content_md5:
            raise RuntimeError("Downloaded content failed integrity verification (Content-MD5 mismatch).")

    def _single(self, url):
        part = self.path + ".part"
        existing = os.path.getsize(part) if os.path.exists(part) else 0
        if self.info.total and existing >= self.info.total:
            try:
                if existing != self.info.total:
                    raise RuntimeError("Partial file is larger than the remote file; restarting.")
                self._check_integrity(part)
            except Exception:
                try:
                    os.remove(part)
                except OSError:
                    pass
                raise
            os.replace(part, self.path)
            self.info.done = self.info.total
            self.info.status = "COMPLETED"
            self._emit(force=True)
            return

        attempts = self._valid_network_ips() or [None]
        last_exc = None
        finished = False
        for bind_ip in attempts:
            # Two rounds: if the server's range behaviour is untrustworthy we
            # discard the partial file and retry once from byte 0.
            for _round in (0, 1):
                try:
                    # Re-read the real offset for EVERY attempt/adapter: a previous
                    # attempt may already have appended bytes before failing.
                    existing = os.path.getsize(part) if os.path.exists(part) else 0
                    if self.info.total and existing > self.info.total:
                        os.remove(part)
                        existing = 0
                    if self.info.total and existing == self.info.total:
                        finished = True
                        break
                    if not self._single_request(url, part, existing, bind_ip):
                        return                      # stopped
                    finished = True
                    break
                except _RangeViolation as exc:
                    last_exc = exc
                    if self._stop.is_set():
                        return
                    try:
                        os.remove(part)
                    except OSError:
                        pass
                    continue
                except Exception as exc:
                    last_exc = exc
                    if self._stop.is_set():
                        return
                    break
            if finished:
                break
        if not finished:
            if self._stop.is_set():
                return
            raise last_exc or RuntimeError("Download failed.")

        size = os.path.getsize(part)
        if self.info.total and size != self.info.total:
            raise RuntimeError(f"Incomplete download: {size} of {self.info.total} bytes.")
        self._check_integrity(part)
        os.replace(part, self.path)
        self.info.done = size
        self.info.total = size
        self.info.speed = 0.0
        self.info.status = "COMPLETED"
        self._emit(force=True)

    def _single_request(self, url, part, existing, bind_ip):
        """One streamed GET appended to `part`. Returns False if stopped."""
        headers = {"User-Agent": f"NexusDownloader/{APP_VERSION}", "Accept-Encoding": "identity"}
        if existing:
            headers["Range"] = f"bytes={existing}-"
        with self._client(bind_ip) as client:
            with client.stream("GET", url, headers=headers) as r:
                self._track(r)
                try:
                    if existing and r.status_code == 416:
                        raise _RangeViolation("server rejected the resume offset (HTTP 416)")
                    r.raise_for_status()
                    if existing and r.status_code == 206:
                        _check_206(r, existing, None, self.info.total)
                        append = True
                    else:
                        # 200 = whole file from byte 0. NEVER append it to a partial file.
                        append = False
                        clen = r.headers.get("content-length")
                        if self.info.total and clen and clen.isdigit() and int(clen) != self.info.total:
                            raise RuntimeError("Remote file size changed during download; aborting to avoid a corrupted file.")
                    base = existing if append else 0
                    self.info.done = base
                    self._reset_speed_meter(base)
                    written = 0
                    self._record_network_use(bind_ip)
                    with open(part, "ab" if append else "wb") as f:
                        for chunk in r.iter_bytes(64 * 1024):
                            if not self._wait_if_paused():
                                return False
                            self._rate_wait(len(chunk))
                            f.write(chunk)
                            written += len(chunk)
                            self.info.done = base + written
                            self._update_speed(self.info.done)
                    return not self._stop.is_set()
                finally:
                    self._untrack(r)

    MIN_CHUNK, MAX_CHUNK, CHUNKS_PER_CONNECTION = 512 * 1024, 16 * 1024 * 1024, 8
    ADAPTER_REFRESH = 2.0          # s between looks at the adapter list (newly connected / vanished adapters)
    MAX_WORKERS = 32

    @classmethod
    def plan_ranges(cls, total, connections):
        """Range layout: many small ranges (>= 512 KiB, <= 16 MiB, ~8 per connection) so a worker that finishes can always
        claim more work and the tail owned by a slow adapter stays short. Independent of the adapter count, so resume keeps
        working when adapters come and go."""
        chunk = min(cls.MAX_CHUNK, max(cls.MIN_CHUNK, -(-total // (max(1, connections) * cls.CHUNKS_PER_CONNECTION))))
        return chunk, [(i, o, min(total - 1, o + chunk - 1)) for i, o in enumerate(range(0, total, chunk))]

    def _validator(self):
        return self._etag if self._etag and not self._etag.startswith("W/") else self._last_modified

    def _multi(self, url, total):
        partdir = self.path + ".parts"
        os.makedirs(partdir, exist_ok=True)
        chunk_size, ranges = self.plan_ranges(total, self.connections)

        # Part files are only valid for the exact range layout AND remote version that wrote them.
        layout_file = os.path.join(partdir, "layout.txt")
        layout = f"v3:{total}:{len(ranges)}:{chunk_size}:{self._validator()}"
        try:
            with open(layout_file, "r") as lf:
                old_layout = lf.read().strip()
        except OSError:
            old_layout = ""
        if old_layout != layout:
            for fn in os.listdir(partdir):
                if fn.endswith(".part") or fn.endswith(".tmp"):
                    self._remove_quiet(os.path.join(partdir, fn))
            with open(layout_file, "w") as f:
                f.write(layout)

        part_sizes, pending = {}, []
        for idx, a, b in ranges:
            max_len = b - a + 1
            p = os.path.join(partdir, f"{idx}.part")
            size = os.path.getsize(p) if os.path.exists(p) else 0
            if size > max_len:
                size = 0
                self._remove_quiet(p)
            part_sizes[idx] = size
            if size < max_len:
                pending.append((idx, a, b))
        self.info.done = min(total, sum(part_sizes.values()))
        self._reset_speed_meter(self.info.done)
        if not pending:
            self.info.done = total
            self._merge_parts(partdir, ranges, total)
            return

        sched = self._sched = RangeScheduler(pending, total - self.info.done)
        state_lock = threading.Lock()
        errors, abort = [], threading.Event()
        threads, started = [], {}

        def fatal(exc):
            with state_lock:
                errors.append(exc)
            abort.set()

        def fetch(client, ip, idx):
            """One range over this worker's own (keep-alive) client. Returns False when stopped. Only the claiming worker ever
            writes this range's part file."""
            _, a, b = ranges[idx]
            p = os.path.join(partdir, f"{idx}.part")
            max_len = b - a + 1
            local = min(os.path.getsize(p) if os.path.exists(p) else 0, max_len)
            if local >= max_len:
                return True
            headers = {"Range": f"bytes={a + local}-{b}"}
            if self._validator():
                headers["If-Range"] = self._validator()       # a changed file answers 200 -> _RangeViolation, never mixed bytes
            with client.stream("GET", url, headers=headers) as r:
                self._track(r)
                try:
                    if r.status_code != 416:
                        r.raise_for_status()
                    _check_206(r, a + local, b, total)
                    etag = r.headers.get("etag")
                    if self._etag and etag and etag != self._etag:
                        raise _RemoteChanged("the remote file changed during the download (ETag differs)")
                    if ip:
                        got = (r.extensions.get("network_stream").get_extra_info("client_addr") or ("",))[0]
                        if got != ip:
                            raise _AdapterMismatch(f"connection left from {got}, not from the selected adapter {ip}")
                    written = local
                    try:
                        with open(p, "ab") as f:
                            for data in r.iter_bytes(64 * 1024):
                                if abort.is_set() or not self._wait_if_paused():
                                    return False
                                if written + len(data) > max_len:
                                    raise _RangeViolation("server sent more bytes than the requested range")
                                self._rate_wait(len(data))
                                f.write(data)
                                written += len(data)
                                with state_lock:
                                    self.info.done = min(total, self.info.done + len(data))
                                sched.on_bytes(ip, len(data))
                                # A *suddenly* slow adapter (e.g. 8 MB/s collapsing to 0.5 MB/s)
                                # must not keep ranges it claimed while its measurement was
                                # stale. Hand the rest back: the part file already holds what
                                # we wrote, so a faster adapter claims it and resumes there.
                                if written < max_len and sched.slower_than(ip, PREEMPT_FACTOR):
                                    return _PREEMPT
                                self._update_speed(self.info.done)
                    except OSError as exc:
                        raise _NonRetryableError(f"Could not write the download to disk: {exc}") from exc
                finally:
                    self._untrack(r)
            if written != max_len:
                raise RuntimeError(f"Incomplete byte range {idx}: received {written} of {max_len} bytes.")
            return True

        def worker(ip):
            client = None
            try:
                while not self._stop.is_set() and not abort.is_set():
                    idx = sched.claim(ip)
                    if idx is DONE:
                        return
                    if idx is WAIT:
                        self._stop.wait(0.05)
                        continue
                    try:
                        client = client or self._client(ip)
                        ok = fetch(client, ip, idx)
                    except (_RangeViolation, _NonRetryableError) as exc:      # not an adapter problem: stop everyone
                        sched.release(idx, ip); fatal(exc); return
                    except Exception as exc:
                        if self._stop.is_set() or abort.is_set():
                            sched.release(idx, ip); return
                        try: client.close()
                        except Exception: pass
                        client = None                                          # connection state unknown: start fresh
                        delay, give_up = sched.fail(idx, ip, exc)
                        if give_up:
                            fatal(exc); return
                        self._stop.wait(delay)
                        continue
                    if ok is _PREEMPT:
                        # The adapter became much slower than another viable one: hand the
                        # range back (partial progress is kept on disk) and bar this adapter
                        # briefly so it does not immediately reclaim the same range.
                        sched.release(idx, ip); sched.bar(ip, PREEMPT_COOLDOWN)
                        self._stop.wait(0.2)
                        continue
                    if not ok:
                        sched.release(idx, ip); return
                    sched.complete(idx, ip); self._record_network_use(ip)
            finally:
                if client is not None:
                    try: client.close()
                    except Exception: pass

        def spawn(ip, n):
            sched.add_adapter(ip)
            for _ in range(n):
                if len(threads) >= self.MAX_WORKERS and threads:
                    return
                t = threading.Thread(target=worker, args=(ip,), name=f"NexusRange-{len(threads)}", daemon=True)
                threads.append(t); started[ip] = started.get(ip, 0) + 1; t.start()

        def balance():
            """Start workers for adapters we have none for yet (also adapters that appeared after the download began)."""
            ips = self._valid_network_ips() or ([None] if not self.network_ips else [])
            fresh = [ip for ip in ips if ip not in started]
            if not fresh:
                return
            per = max(1, -(-self.connections // (len(started) + len(fresh))))      # >= 1 worker per adapter
            for ip in fresh:
                spawn(ip, per)
            n_ips = [ip for ip in started if ip]
            self.info.network_mode = "COMBINED" if len(n_ips) > 1 else ("SINGLE-NETWORK" if n_ips else "AUTO")
            with self._lock:
                self._network_plan = {(ip or "AUTO"): n for ip, n in started.items()}

        balance()
        if not threads:
            raise RuntimeError("None of the selected network adapters is online.")
        next_look = time.monotonic() + self.ADAPTER_REFRESH
        while any(t.is_alive() for t in threads):
            time.sleep(0.1)
            if time.monotonic() >= next_look and not abort.is_set():
                next_look = time.monotonic() + self.ADAPTER_REFRESH
                try: balance()
                except Exception: pass
        for t in threads:
            t.join()

        if self._stop.is_set():
            return
        if errors:
            for e in errors:
                if isinstance(e, _RangeViolation):
                    raise e
            raise errors[0] if isinstance(errors[0], _NonRetryableError) else RuntimeError(str(errors[0]))
        if self.info.done < total:
            raise RuntimeError("Download did not finish all byte ranges.")
        self._merge_parts(partdir, ranges, total)

    def adapter_report(self):
        """{ip: {rate, active, bytes, ranges, errors, suspended, error}} of the running/last multi-adapter transfer."""
        s = getattr(self, "_sched", None)
        return s.report() if s else {}

    def _discard_merge_failure(self, tmp, partdir, ranges):
        """Corrupt content (integrity mismatch): discard merge output and ALL
        parts so the next attempt starts clean."""
        try:
            os.remove(tmp)
        except OSError:
            pass
        for idx, _, _ in ranges:
            try:
                os.remove(os.path.join(partdir, f"{idx}.part"))
            except OSError:
                pass

    def _merge_parts(self, partdir, ranges, total):
        # 1. Every part must be exactly the size its range requires.
        bad = []
        for idx, a, b in ranges:
            p = os.path.join(partdir, f"{idx}.part")
            if not os.path.exists(p) or os.path.getsize(p) != b - a + 1:
                bad.append(idx)
        if bad:
            for idx in bad:      # only the invalid pieces are dropped; the rest stay resumable
                try:
                    os.remove(os.path.join(partdir, f"{idx}.part"))
                except OSError:
                    pass
            raise RuntimeError(f"Range part(s) {bad} have the wrong size; they will be downloaded again.")

        # 2. Merge to a temp file. Parts are kept until the merge fully succeeded.
        tmp = self.path + ".merge"
        try:
            with open(tmp, "wb") as out:
                for idx, _, _ in ranges:
                    with open(os.path.join(partdir, f"{idx}.part"), "rb") as src:
                        while True:
                            if self._stop.is_set():
                                raise _MergeStopped()
                            data = src.read(1024 * 1024)
                            if not data:
                                break
                            out.write(data)
        except _MergeStopped:
            self._remove_quiet(tmp)
            return False
        except Exception:
            self._remove_quiet(tmp)   # recoverable: parts are untouched
            raise
        if os.path.getsize(tmp) != total:
            self._remove_quiet(tmp)   # sizes were validated, so this is an I/O problem: keep parts
            raise RuntimeError("Merged file size does not match the expected download size.")
        try:
            self._check_integrity(tmp)
        except Exception:
            self._discard_merge_failure(tmp, partdir, ranges)
            raise
        os.replace(tmp, self.path)
        # 3. Only now remove temp parts and layout metadata.
        for idx, _, _ in ranges:
            self._remove_quiet(os.path.join(partdir, f"{idx}.part"))
        self._remove_quiet(os.path.join(partdir, "layout.txt"))
        self._clear_partdir(partdir)
        self.info.done = total
        self.info.speed = 0.0
        self.info.status = "COMPLETED"
        self._emit(force=True)
        return True

    @staticmethod
    def _remove_quiet(p):
        try:
            os.remove(p)
        except OSError:
            pass
