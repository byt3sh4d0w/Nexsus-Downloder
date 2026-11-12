"""Networks page Refresh / Test internet buttons: immediate loading state, no overlapping scans, errors reported, buttons always
restored, and a manual click is never lost while an automatic scan is running. The adapter scan is replaced by a gated fake, so no
real network is touched."""
import os, sys, threading, time, unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.dirname(HERE)); sys.path.insert(0, HERE)
from PySide6.QtWidgets import QApplication

from core.network_monitor import NetworkInfo
import test_nexus as TN


def info(name, internet="ONLINE", enabled=True):
    return NetworkInfo(name=name, ifname=name, ip="192.168.1.5", link_mbps=1000.0, internet=internet,
                       latency_ms=12.0 if internet == "ONLINE" else None, speed_bps=0.0, enabled=enabled,
                       state="ACTIVE" if internet == "ONLINE" else "NO INTERNET", virtual=False)


def wait_for(cond, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        QApplication.processEvents()
        if cond():
            return True
        time.sleep(0.005)
    return False


class Gate:
    """Fake NetworkMonitor.test_all: blocks until released, records calls, returns/raises what the test says."""
    def __init__(self, *results):
        self.results = list(results); self.calls = 0; self.go = threading.Event(); self.entered = threading.Event()

    def __call__(self):
        self.calls += 1; n = self.calls; self.entered.set()
        self.go.wait(5)
        r = self.results[min(n, len(self.results)) - 1]
        if isinstance(r, Exception):
            raise r
        return r


class NetworkButtons(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.win = TN.get_window(); cls.win.show()
        cls.win.tabs.setCurrentIndex(list(cls.win.nav_buttons).index("networks")); TN.pump(0.2)

    @classmethod
    def tearDownClass(cls):
        cls.win.nav_buttons["downloads"].click()                    # leave the shared window as other suites expect it

    def setUp(self):
        w = self.win
        w.net_timer.stop()                                            # no automatic scan unless the test starts one
        self.assertTrue(wait_for(lambda: not w._net_running), "no scan left over from another test")
        w.net_rows = {}; w._apply_network_update([], True)
        self.refresh_text, self.test_text = w.net_refresh_btn.text(), w.net_test_btn.text()
        self.addCleanup(w.net_timer.start)

    def scan(self, gate):
        p = mock.patch.object(self.win.net, "test_all", gate); p.start(); self.addCleanup(p.stop)
        return gate

    def idle(self):
        w = self.win
        return wait_for(lambda: not w._net_running and w.net_refresh_btn.isEnabled() and w.net_test_btn.isEnabled())

    def rows(self):
        t = self.win.net_table; return [t.item(r, 0).text() for r in range(t.rowCount())]

    # ------------------------------------------------------------------ Refresh
    def test_refresh_shows_loading_at_once_then_updates_and_restores(self):
        w = self.win; g = self.scan(Gate([info("Ethernet 2"), info("Wi-Fi")]))
        w.net_refresh_btn.click()
        self.assertTrue(g.entered.wait(2))
        self.assertIn("Refreshing", w.net_refresh_btn.text())
        self.assertFalse(w.net_refresh_btn.isEnabled()); self.assertFalse(w.net_test_btn.isEnabled())
        self.assertIn("Refreshing", w.network_status.text())
        g.go.set(); self.assertTrue(self.idle())
        self.assertEqual((w.net_refresh_btn.text(), w.net_test_btn.text()), (self.refresh_text, self.test_text))
        self.assertEqual(sorted(self.rows()), ["Ethernet 2", "Wi-Fi"], "adapter list updated")
        self.assertIn("Refreshed", w.network_status.text()); self.assertIn("2 adapters", w.network_status.text())

    # ------------------------------------------------------------------ Test internet
    def test_internet_test_shows_testing_state_and_names_failed_adapters(self):
        w = self.win; g = self.scan(Gate([info("Ethernet 2"), info("Wi-Fi"), info("Ethernet 3", "NO INTERNET"),
                                          info("Ethernet 4", "NO INTERNET", enabled=False)]))
        w.net_test_btn.click(); self.assertTrue(g.entered.wait(2))
        self.assertIn("Testing connections", w.network_status.text())
        self.assertFalse(w.net_refresh_btn.isEnabled()); self.assertFalse(w.net_test_btn.isEnabled())
        g.go.set(); self.assertTrue(self.idle())
        s = w.network_status.text()
        self.assertIn("2 of 3", s, "a disabled adapter is not counted as a failed test")
        self.assertIn("Ethernet 3", s); self.assertNotIn("Ethernet 4", s)
        self.assertIn("Failed", s)

    def test_internet_test_all_pass_message(self):
        w = self.win; g = self.scan(Gate([info("Ethernet 2"), info("Wi-Fi")])); g.go.set()
        w.net_test_btn.click(); self.assertTrue(self.idle())
        self.assertIn("2 of 2", w.network_status.text()); self.assertNotIn("Failed", w.network_status.text())

    def test_the_two_buttons_give_different_feedback(self):
        w = self.win; g = self.scan(Gate([info("Ethernet 2")])); g.go.set()
        w.net_refresh_btn.click(); self.assertTrue(self.idle()); a = w.network_status.text()
        w.net_test_btn.click(); self.assertTrue(self.idle()); b = w.network_status.text()
        self.assertIn("Refreshed", a); self.assertIn("Tested", b); self.assertNotEqual(a.split(" ")[0], b.split(" ")[0])

    # ------------------------------------------------------------------ errors (D2)
    def test_scan_exception_is_reported_and_buttons_recover(self):
        w = self.win; g = self.scan(Gate(RuntimeError("probe exploded"), [info("Ethernet 2")]))
        w.net_rows = {"keep": info("keep")}; w._apply_network_update(list(w.net_rows.values()), False)
        before = self.rows()
        w.net_test_btn.click(); self.assertTrue(g.entered.wait(2)); g.go.set()
        self.assertTrue(self.idle(), "buttons restored after the scan raised")
        self.assertIn("failed", w.network_status.text().lower()); self.assertIn("probe exploded", w.network_status.text())
        self.assertEqual(self.rows(), before, "a failed scan leaves the old adapter list alone")
        w.net_refresh_btn.click(); self.assertTrue(self.idle())                   # not stuck: the next scan works
        self.assertIn("Refreshed", w.network_status.text())

    def test_exception_in_ui_update_still_restores_buttons(self):
        w = self.win; g = self.scan(Gate([info("Ethernet 2")])); g.go.set()
        with mock.patch.object(type(w.net_table), "setRowCount", side_effect=RuntimeError("ui broke")):
            try:
                w.net_refresh_btn.click(); wait_for(lambda: False, 0.5)
            except Exception:
                pass
        self.assertTrue(self.idle(), "never left permanently disabled")

    # ------------------------------------------------------------------ overlap / automatic scans (D1, #6)
    def test_no_overlapping_manual_scans(self):
        w = self.win; g = self.scan(Gate([info("Ethernet 2")]))
        w.net_refresh_btn.click(); self.assertTrue(g.entered.wait(2))
        w.net_refresh_btn.click(); w.net_test_btn.click()                          # disabled: ignored
        TN.pump(0.1); self.assertEqual(g.calls, 1)
        g.go.set(); self.assertTrue(self.idle()); self.assertEqual(g.calls, 1)

    def test_manual_click_during_automatic_scan_is_not_lost(self):
        w = self.win; g = self.scan(Gate([info("Ethernet 2")], [info("Ethernet 2"), info("Wi-Fi")]))
        w.refresh_networks(False); self.assertTrue(g.entered.wait(2))              # automatic scan in flight
        w.net_refresh_btn.click()
        self.assertIn("Refreshing", w.network_status.text()); self.assertFalse(w.net_refresh_btn.isEnabled())
        g.go.set()
        self.assertTrue(self.idle())
        self.assertEqual(g.calls, 2, "the manual request ran its own scan after the automatic one")
        self.assertEqual(sorted(self.rows()), ["Ethernet 2", "Wi-Fi"], "result of the manual (second) scan is what is shown")
        self.assertIn("Refreshed", w.network_status.text())
        self.assertIn("2 adapters", w.network_status.text(), "feedback was not completed by the automatic scan's result")

    def test_automatic_scan_during_manual_scan_does_not_finish_the_manual_feedback_early(self):
        w = self.win; g = self.scan(Gate([info("Ethernet 2")]))
        w.net_test_btn.click(); self.assertTrue(g.entered.wait(2))
        w.refresh_networks(False)                                                    # timer fires meanwhile
        g.go.set(); self.assertTrue(self.idle())
        self.assertIn("Tested", w.network_status.text())

    def test_manual_requests_never_stick_under_a_burst_of_automatic_scans(self):
        w = self.win; g = self.scan(Gate([info("Ethernet 2")])); g.go.set()
        for i in range(120):
            w.refresh_networks(False)
            (w.net_refresh_btn if i % 2 else w.net_test_btn).click()
            self.assertTrue(self.idle(), f"stuck busy after iteration {i}")


if __name__ == "__main__":
    unittest.main()
