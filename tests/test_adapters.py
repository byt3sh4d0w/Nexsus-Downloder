"""Virtual / host-only adapters are identified by their MAC address (interface metadata), not only by name.
Regression: a VirtualBox Host-Only adapter that Windows names "Ethernet 7" was listed, enabled and probed ("No Internet")."""
import collections, os, socket, sys, unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import psutil
from core.network_monitor import NetworkMonitor, has_vm_mac

St = collections.namedtuple("St", "isup speed")
Ad = collections.namedtuple("Ad", "family address")


def snapshot(adapters):
    """adapters: {name: (ip, mac or None, mbps)} -> {ifname: NetworkInfo} from NetworkMonitor.snapshot()."""
    stats = {n: St(True, mbps) for n, (_, _, mbps) in adapters.items()}
    addrs = {n: [Ad(socket.AF_INET, ip)] + ([Ad(psutil.AF_LINK, mac)] if mac else []) for n, (ip, mac, _) in adapters.items()}
    with mock.patch("core.network_monitor.psutil.net_if_stats", return_value=stats), \
         mock.patch("core.network_monitor.psutil.net_if_addrs", return_value=addrs):
        mon = NetworkMonitor()
        return {i.ifname: i for i in mon.snapshot()}, mon


PHYSICAL = {
    "Ethernet 2": ("192.168.1.20", "3C-52-82-AA-BB-01", 1000),
    "Wi-Fi": ("192.168.1.21", "A4:C3:F0:11:22:33", 300),
    "Ethernet 3": ("192.168.42.129", "A2-9E-3B-11-22-33", 480),       # Android USB tethering (RNDIS, random locally-administered MAC)
}


class VirtualAdapterExclusion(unittest.TestCase):
    def test_virtualbox_host_only_named_ethernet_7_is_hidden(self):
        rows, mon = snapshot({**PHYSICAL, "Ethernet 7": ("192.168.56.1", "0A-00-27-00-00-07", 1000)})
        self.assertNotIn("Ethernet 7", rows)
        self.assertNotIn("Ethernet 7", mon.enabled, "a hidden adapter is never registered or probed")

    def test_other_hypervisor_macs_are_hidden_in_either_separator_style(self):
        for mac in ("00:50:56:C0:00:08", "00-0C-29-12-34-56", "00-15-5D-AB-CD-EF", "08:00:27:5e:aa:01", "00:1C:42:00:00:08", "52:54:00:12:34:56"):
            rows, _ = snapshot({"Ethernet 9": ("10.1.1.1", mac, 1000)})
            self.assertEqual(rows, {}, mac)

    def test_physical_ethernet_wifi_and_usb_tethering_are_preserved_and_enabled(self):
        rows, _ = snapshot({**PHYSICAL, "Ethernet 7": ("192.168.56.1", "0A-00-27-00-00-07", 1000)})
        self.assertEqual(sorted(rows), ["Ethernet 2", "Ethernet 3", "Wi-Fi"])
        self.assertTrue(all(r.enabled and not r.virtual for r in rows.values()))
        self.assertEqual(rows["Ethernet 3"].ip, "192.168.42.129")

    def test_unclassifiable_adapters_are_not_blindly_hidden(self):
        rows, _ = snapshot({"Ethernet 5": ("10.9.9.9", None, 1000),                    # no MAC metadata at all
                            "Tailscale": ("100.64.0.2", None, 100),                    # VPN tunnel: name hint only
                            "VirtualBox Host-Only Network": ("192.168.56.1", "3C-52-82-00-00-09", 1000)})   # name says virtual, MAC does not
        self.assertEqual(sorted(rows), ["Ethernet 5", "Tailscale", "VirtualBox Host-Only Network"])
        self.assertEqual((rows["Ethernet 5"].enabled, rows["Ethernet 5"].virtual), (True, False))
        self.assertEqual((rows["Tailscale"].enabled, rows["Tailscale"].virtual), (False, True))   # unchanged: listed, off by default

    def test_has_vm_mac_handles_missing_and_bad_data(self):
        self.assertFalse(has_vm_mac(None)); self.assertFalse(has_vm_mac([])); self.assertFalse(has_vm_mac([Ad(socket.AF_INET, "0A-00-27-00-00-07")]))
        self.assertFalse(has_vm_mac([Ad(psutil.AF_LINK, "")])); self.assertFalse(has_vm_mac([Ad(psutil.AF_LINK, None)]))


if __name__ == "__main__":
    unittest.main()
