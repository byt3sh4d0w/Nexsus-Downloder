"""Adaptive byte-range scheduler for multi-adapter downloads (pure logic: no sockets, no threads of its own).

Why it exists: a static split ("adapter A gets N workers/ranges, adapter B gets N") makes the whole download wait for the
slowest adapter. Here every worker pulls the next pending range from one shared queue, so fast adapters naturally finish more
ranges. The only thing the scheduler decides is *whether a worker may claim now* (see `claim`)."""
import random
import threading
import time
from collections import deque

WAIT = object()          # claim(): nothing for this worker right now, ask again shortly
DONE = object()          # claim(): every range is complete, the worker can exit

WINDOW = 4.0             # s   throughput is averaged over this sliding window (smoothing)
MIN_OBS = 1.0            # s   an adapter's rate is not trusted before this much observation (until then it just explores)
RESPONSIVE = 0.75        # s   a shorter tail used so a *falling* rate reaches the scheduler quickly
                            #     (a slow adapter must not keep being judged by its old fast speed). On a rising rate the
                            #     longer window still wins, which is the safe direction (never over-assign to a newcomer).
SLACK = 1.5              # x   hysteresis: a worker is held back only if its range would end >1.5x after the whole job should
FLOOR = 0.75             # s   a range that would finish this quickly is always fine
SUSPEND_AFTER = 2        # consecutive failures before an adapter is suspended (circuit breaker)
BASE_COOLDOWN, MAX_COOLDOWN = 3.0, 20.0
MAX_ATTEMPTS = 6         # failures of one range before the whole download is failed (bounded retries)

# A worker hands its in-flight range back to the queue when another viable adapter is
# more than 2x faster (PREEMPT_FACTOR=0.5 -> "mine < 0.5 * best"). The part file already
# holds whatever was written, so the faster adapter claims it and resumes from there.
# PREEMPT_COOLDOWN then stops the slow adapter from immediately reclaiming it.
# PREEMPT_WINDOW is a *shorter* tail than the smoothing window: the pre-emption decision
# has to notice a collapsing rate within a fraction of a second, or a suddenly-slow
# adapter keeps ranges it claimed while its measurement was still stale.
PREEMPT_FACTOR = 0.5
PREEMPT_COOLDOWN = 2.0
PREEMPT_WINDOW = 0.5


class Adapter:
    def __init__(self, ip):
        self.ip = ip
        self.samples = deque()           # (monotonic time, bytes) inside WINDOW
        self.last_rate = None            # remembered while the adapter idles, so an idle slow adapter stays "slow"
        self.active = 0                  # workers currently transferring a range
        self.fails = 0                   # consecutive failures
        self.strikes = 0                 # how many times in a row it was suspended (grows the cooldown)
        self.until = 0.0                 # suspended until (monotonic)
        self.bytes = self.ranges = self.errors = 0
        self.last_error = ""
        self.preempt_until = 0.0         # briefly barred from claiming after handing a range back

    def rate(self, now):
        while self.samples and self.samples[0][0] < now - WINDOW:
            self.samples.popleft()
        if self.samples:
            span = now - self.samples[0][0]
            if span >= MIN_OBS:
                full = sum(n for _, n in self.samples) / max(span, MIN_OBS)
                # Responsive tail: when an adapter's throughput collapses (e.g. a fast
                # adapter slows to 0.5 MB/s) the 4 s window keeps reporting the old speed
                # for several seconds and the scheduler keeps handing it ranges while
                # holding the genuinely-faster adapter back. A short tail fixes that.
                # `min` makes it responsive on the way *down* and keeps the stable longer
                # window on the way *up* (the safe direction).
                cut = now - RESPONSIVE
                recent = [(t, n) for t, n in self.samples if t >= cut]
                if recent:
                    rspan = now - recent[0][0]
                    if rspan >= MIN_OBS * 0.5:
                        self.last_rate = min(full, sum(n for _, n in recent) / rspan)
                    else:
                        self.last_rate = full
                else:
                    self.last_rate = full
        return self.last_rate            # None = never observed

    def suspended(self, now):
        return now < self.until

    def barred(self, now):
        """True while the adapter is on a short hold after handing an in-flight range back to a
        faster adapter (see RangeScheduler.slower_than). Distinct from `suspended`: it is not a
        failure signal, it just stops this adapter from immediately reclaiming the same range."""
        return now < self.preempt_until


class RangeScheduler:
    """ranges: [(idx, start, end)] still to download; left: bytes still missing in them."""

    def __init__(self, ranges, left, clock=time.monotonic):
        self.clock = clock
        # RLock: rate()/slower_than() are called while the lock is already held (claim ->
        # _total_rate -> rate, and slower_than -> rate), so a plain Lock would deadlock.
        self.lock = threading.RLock()
        self.size = {i: b - a + 1 for i, a, b in ranges}
        self.queue = deque(i for i, _, _ in ranges)
        self.inflight = set()
        self.attempts = {}
        self.left = int(left)
        self.adapters = {}
        self.fatal = None

    # ---- adapters --------------------------------------------------------------------------------
    def add_adapter(self, ip):
        with self.lock:
            return self.adapters.setdefault(ip, Adapter(ip))

    def _total_rate(self, now):
        return sum((a.rate(now) or 0.0) for a in self.adapters.values() if not a.suspended(now))

    def slower_than(self, ip, factor):
        """True if `ip` has *collapsed*: its short-tail rate is far below its own
        longer-window average AND another viable adapter's short-tail rate.

        The "below its own longer average" half is the part that distinguishes a sudden
        collapse from a stable asymmetric pair. With only "below another adapter" a stable
        slow adapter (1 MB/s vs an 8 MB/s one) would hand every range back and the download
        would serialize on the fast adapter; with only "below its own average" a fast adapter
        that merely fluctuates would be preempted for no gain. Both conditions are needed.

        `own_long` is the FULL smoothing window, not `a.rate()` (which is itself
        `min(full, responsive)`). Using `rate()` here would make the two sides equal as
        soon as the rate collapsed, so `mine < factor * own_long` could never be True and
        no pre-emption would ever happen.

        Uses a *short* tail (PREEMPT_WINDOW) rather than the smoothing window: a pre-emption
        decision has to notice a collapsing rate within a fraction of a second, or a suddenly
        slow adapter keeps ranges it claimed while its measurement was still stale."""
        with self.lock:
            now = self.clock()
            a = self.adapters.get(ip)
            if a is None or a.suspended(now):
                return False
            mine = self._tail_rate(a, now, PREEMPT_WINDOW)
            if mine <= 0:
                return False
            own_long = self._full_rate(a, now)
            if own_long <= 0 or mine >= factor * own_long:
                return False
            best = 0.0
            for o in self.adapters.values():
                if o is a or o.suspended(now):
                    continue
                r = self._tail_rate(o, now, PREEMPT_WINDOW)
                if r > best:
                    best = r
            return best > 0.0 and mine < factor * best

    @staticmethod
    def _tail_rate(a, now, window):
        """Mean throughput over the last `window` seconds of `a`'s samples (0.0 if too little
        data). Shorter than the smoothing window, so a collapsing rate is visible quickly."""
        cut = now - window
        recent = [(t, n) for t, n in a.samples if t >= cut]
        if not recent:
            return 0.0
        span = now - recent[0][0]
        if span <= 0:
            return 0.0
        return sum(n for _, n in recent) / span

    @staticmethod
    def _full_rate(a, now):
        """Mean throughput over the whole smoothing window (WINDOW), independent of the
        responsive short tail used for scheduling decisions. Used by slower_than() to
        detect a *change* relative to the adapter's own recent history."""
        while a.samples and a.samples[0][0] < now - WINDOW:
            a.samples.popleft()
        if not a.samples:
            return 0.0
        span = now - a.samples[0][0]
        if span < MIN_OBS:
            return 0.0
        return sum(n for _, n in a.samples) / max(span, MIN_OBS)

    def bar(self, ip, seconds):
        """Put `ip` on a short hold after it handed a range back (see PREEMPT_COOLDOWN)."""
        with self.lock:
            a = self.adapters.get(ip)
            if a is not None:
                a.preempt_until = self.clock() + seconds

    # ---- claiming ----------------------------------------------------------------------------------
    def claim(self, ip):
        """-> a range index, WAIT or DONE. Ranges are exclusive: a claimed range is owned by exactly one worker until it is
        completed or failed, so two workers can never write the same bytes."""
        with self.lock:
            now = self.clock(); a = self.adapters[ip]
            if self.fatal or (not self.queue and not self.inflight):
                return DONE
            if not self.queue or a.suspended(now) or a.barred(now):
                return WAIT
            mine = a.rate(now)
            # Liveness: with nothing in flight someone must take the range, and a worker is only held back for a FASTER adapter that
            # is actually available; otherwise holding back can only stall the download.
            faster = any(o is not a and not o.suspended(now) and (o.rate(now) or 0.0) > (mine or 0.0) for o in self.adapters.values())
            if mine and self.inflight and faster:
                # End-game guard. If this worker's share of the adapter would still be busy long after the whole job should be
                # over at the combined rate, a faster adapter should take the range instead (this is what removes the
                # "wait for the slow adapter" tail). Far from the end the budget is large, so slow adapters keep contributing.
                budget = max(FLOOR, SLACK * self.left / max(self._total_rate(now), mine))
                if self.size[self.queue[0]] / (mine / (a.active + 1)) > budget:
                    return WAIT
                # A *collapsed* adapter (suddenly much slower than another viable one) must not be handed NEW ranges
                # either, while something is in flight. Its in-flight ranges are handed back by the worker (fetch() ->
                # _PREEMPT), and new ones would just churn: claim -> 64 KiB -> preempt -> barred briefly -> claim again,
                # which keeps the collapsed adapter winning the race for its own released ranges. With nothing in flight
                # there is nothing faster to wait for, so the liveness rule above still lets it claim.
                if len(self.adapters) > 1 and self.slower_than(ip, PREEMPT_FACTOR):
                    return WAIT
            idx = self.queue.popleft(); self.inflight.add(idx); a.active += 1
            return idx

    def on_bytes(self, ip, n):
        with self.lock:
            a = self.adapters[ip]; a.samples.append((self.clock(), n)); a.bytes += n; self.left = max(0, self.left - n)

    def complete(self, idx, ip):
        with self.lock:
            self.inflight.discard(idx); a = self.adapters[ip]
            a.active = max(0, a.active - 1); a.ranges += 1; a.fails = 0; a.strikes = max(0, a.strikes - 1)   # re-admission

    def release(self, idx, ip):
        """Worker stopped/paused-cancelled without finishing: give the range back untouched (no strike)."""
        with self.lock:
            if idx in self.inflight:
                self.inflight.discard(idx); self.queue.appendleft(idx)
            self.adapters[ip].active = max(0, self.adapters[ip].active - 1)

    def fail(self, idx, ip, exc):
        """A range attempt failed. The range goes back to the queue; the adapter may be suspended.
        -> (seconds the worker should back off, True if the download must be failed)."""
        with self.lock:
            now = self.clock(); a = self.adapters[ip]
            self.inflight.discard(idx); self.queue.appendleft(idx)
            a.active = max(0, a.active - 1); a.errors += 1; a.fails += 1; a.last_error = str(exc)[:160]
            n = self.attempts[idx] = self.attempts.get(idx, 0) + 1
            if a.fails >= SUSPEND_AFTER:
                a.strikes += 1; a.fails = 0
                a.until = now + min(MAX_COOLDOWN, BASE_COOLDOWN * 2 ** (a.strikes - 1)) * random.uniform(0.8, 1.2)
            if n >= MAX_ATTEMPTS:
                self.fatal = exc
            return min(5.0, 0.2 * 2 ** (n - 1)) * random.uniform(0.5, 1.5), n >= MAX_ATTEMPTS

    # ---- reporting (GUI / tests) ---------------------------------------------------------------------
    def report(self):
        with self.lock:
            now = self.clock()
            return {ip: {"rate": a.rate(now) or 0.0, "active": a.active, "bytes": a.bytes, "ranges": a.ranges, "errors": a.errors,
                         "suspended": a.suspended(now), "error": a.last_error} for ip, a in self.adapters.items()}
