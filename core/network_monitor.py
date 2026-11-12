
import socket
import time
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Callable, Optional
import psutil

TEST_HOST = "https://www.gstatic.com/generate_204"
TEST_TIMEOUT = 4.0

def is_combinable(name: str) -> bool:
    """True for adapters eligible for multi-network download aggregation.
    Bluetooth tethering is excluded: too slow/high-latency to usefully add
    to a combined download, and stalls the whole batch when it lags."""
    return "bluetooth" not in (name or "").lower()


def attribute_rates(reports, http_total):
    """Sum per-adapter rates over every running multi-adapter job.

    -> ({ip: bytes/s}, unattributed bytes/s). Unattributed is HTTP traffic no adapter report
    accounts for (single-connection jobs); it is never spread across adapters."""
    per_ip = {}
    for rep in reports:
        for ip, r in (rep or {}).items():
            per_ip[ip] = per_ip.get(ip, 0.0) + float((r or {}).get("rate") or 0.0)
    return per_ip, max(0.0, float(http_total or 0.0) - sum(per_ip.values()))


VIRTUAL_HINTS = ("vethernet", "vmware", "virtualbox", "vbox", "hyper-v", "docker", "wsl", "tap-", "tap ", "tun", "wireguard",
                 "tailscale", "zerotier", "vpn", "openvpn", "nordlynx", "loopback", "virtual", "veth", "br-")


def is_virtual(name: str) -> bool:
    """Name-based guess: VM/container/VPN adapters are online but usually share the physical uplink, so they are not
    auto-selected as extra bandwidth. The user can still enable them by hand."""
    n = (name or "").lower()
    return any(h in n for h in VIRTUAL_HINTS)


# MAC prefixes assigned to hypervisors (VirtualBox incl. its Host-Only adapter 0A:00:27, VMware, Hyper-V/WSL, Parallels, QEMU).
# An adapter carrying one is virtual whatever Windows named it ("Ethernet 7"). Adapters without MAC metadata are not matched.
VM_MAC_PREFIXES = ("08:00:27", "0a:00:27", "00:50:56", "00:0c:29", "00:05:69", "00:1c:14", "00:15:5d", "00:1c:42", "52:54:00")


def has_vm_mac(addrs):
    """True when one of the interface's psutil addresses is a hypervisor-assigned MAC."""
    for a in addrs or []:
        if getattr(a, "family", None) == psutil.AF_LINK and isinstance(a.address, str):
            if a.address.lower().replace("-", ":").startswith(VM_MAC_PREFIXES):
                return True
    return False


@dataclass
class NetworkInfo:
    name: str
    ifname: str
    ip: str = ""
    link_mbps: float = 0.0
    internet: str = "UNKNOWN"
    latency_ms: Optional[float] = None
    speed_bps: float = 0.0
    enabled: bool = True
    state: str = "ACTIVE"
    virtual: bool = False

class NetworkMonitor:
    def __init__(self, callback: Optional[Callable] = None):
        self.callback = callback
        self.running = False
        self.thread = None
        self.enabled = {}
        self._enabled_lock = threading.Lock()
        self.interval = 5
        self._wake = threading.Event()
        self._traffic_prev = {}
        self._traffic_time = None

    def _classify(self, name):
        n = name.lower()
        if "bluetooth" in n or "pan" in n:
            return "Bluetooth PAN"
        if "wi-fi" in n or "wifi" in n or "wlan" in n:
            return "Wi-Fi"
        if "ethernet" in n or "usb" in n or "rndis" in n:
            return name
        return name

    def snapshot(self):
        stats = psutil.net_if_stats()
        addrs = psutil.net_if_addrs()
        out = []
        for ifname, st in stats.items():
            # Loopback is a software-only local interface (typically 127.0.0.1),
            # not a usable Internet connection for Nexus downloads.
            lname = ifname.lower()
            if ("loopback" in lname or lname in {"lo", "lo0"}):
                continue
            if not st.isup or has_vm_mac(addrs.get(ifname)):      # hypervisor adapters are not Internet connections: never listed or probed
                continue
            ip = ""
            for a in addrs.get(ifname, []):
                if getattr(a, "family", None) == socket.AF_INET:
                    ip = a.address
                    break
            if not ip:
                continue
            label = self._classify(ifname)
            with self._enabled_lock:
                if ifname not in self.enabled:
                    self.enabled[ifname] = not is_virtual(ifname)      # virtual/VPN adapters start excluded (not independent bandwidth)
                is_enabled = self.enabled[ifname]
            out.append(NetworkInfo(
                name=label, ifname=ifname, ip=ip,
                link_mbps=float(st.speed or 0),
                enabled=is_enabled, virtual=is_virtual(ifname)
            ))
        return out

    def test_one(self, info: NetworkInfo):
        if not info.enabled:
            info.internet = "DISABLED"
            info.state = "DISABLED"
            return info
        try:
            # A connected adapter can still have no Internet. Bind the socket
            # to the adapter's IPv4 address to test that particular route.
            start = time.perf_counter()
            s = socket.create_connection(("www.gstatic.com", 443), timeout=TEST_TIMEOUT,
                                         source_address=(info.ip, 0))
            info.latency_ms = (time.perf_counter() - start) * 1000
            s.close()
            info.internet = "ONLINE"
            info.state = "ACTIVE"
        except Exception:
            info.internet = "NO INTERNET"
            info.latency_ms = None
            info.speed_bps = 0.0
            info.state = "NO INTERNET"
        return info

    def test_all(self):
        now = time.perf_counter()
        counters = psutil.net_io_counters(pernic=True) or {}
        previous = self._traffic_prev
        previous_time = self._traffic_time
        dt = (now - previous_time) if previous_time is not None else 0.0

        infos = self.snapshot()
        for i in infos:
            # bytes_recv is the receive/download traffic seen by this interface.
            # This is interface traffic, so it also reflects downloads performed
            # by Nexus when that interface carries the connection.
            current = counters.get(i.ifname)
            if current is not None and previous_time is not None and dt > 0:
                old = previous.get(i.ifname)
                if old is not None:
                    delta = max(0, int(current.bytes_recv) - int(old.bytes_recv))
                    i.speed_bps = delta / dt
        # Probe adapters in parallel: one dead adapter costs its 4 s timeout
        # once, not once per adapter in sequence.
        if infos:
            with ThreadPoolExecutor(max_workers=len(infos)) as pool:
                result = list(pool.map(self.test_one, infos))
        else:
            result = []

        self._traffic_prev = {k: v for k, v in counters.items()}
        self._traffic_time = now
        if self.callback:
            self.callback(result)
        return result

    def start(self, interval=5):
        if self.running:
            return
        self.interval = interval
        self.running = True
        def loop():
            while self.running:
                try:
                    self.test_all()
                except Exception:
                    pass
                self._wake.wait(self.interval)
                self._wake.clear()
        self.thread = threading.Thread(target=loop, daemon=True)
        self.thread.start()

    def stop(self):
        self.running = False
        self._wake.set()

    def refresh_now(self):
        self._wake.set()

    def set_enabled(self, ifname, value):
        with self._enabled_lock:
            self.enabled[ifname] = bool(value)

    def is_enabled(self, ifname):
        with self._enabled_lock:
            return self.enabled.get(ifname, True)
