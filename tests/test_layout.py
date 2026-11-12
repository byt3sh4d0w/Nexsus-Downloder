"""Networks page: the Action button must never be cut off, at any window width or font scale.
Regression: the Action column (110 px) was exactly as wide as the button's minimum width and the other columns were fixed,
so a narrow window or a larger font clipped the pill (and pushed it off the right edge)."""
import os, sys, time, unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.dirname(HERE)); sys.path.insert(0, HERE)
from PySide6.QtWidgets import QApplication, QPushButton

from core.network_monitor import NetworkInfo
import test_nexus as TN

ACTION = 8


def info(name, enabled):
    return NetworkInfo(name=name, ifname=name, ip="192.168.1.5", link_mbps=1000.0, internet="ONLINE", latency_ms=12.0,
                       speed_bps=0.0, enabled=enabled, state="ACTIVE", virtual=False)


class NetworkActionButtons(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.win = TN.get_window(); cls.win.show()
        cls.win.net_timer.stop()                          # a real scan landing mid-test would replace the fixture rows
        end = time.time() + 5
        while cls.win._net_running and time.time() < end: TN.pump(0.05)
        TN.pump(0.5)                                      # deliver the finished scan's queued result BEFORE the fixtures go in
        cls._no_scan = mock.patch.object(cls.win, "_run_network_scan", lambda *a, **k: None); cls._no_scan.start()   # opening the Networks tab also scans

    @classmethod
    def tearDownClass(cls):
        cls._no_scan.stop(); cls.win._net_running = False; cls.win._net_next = None; cls.win.net_timer.start(); cls.win.nav_buttons["downloads"].click()      # leave the shared window as other suites expect it

    def setUp(self):
        self.addCleanup(self.restore, QApplication.instance().font(), self.win.size())

    def restore(self, font, size):
        QApplication.instance().setFont(font); self.win.resize(size); TN.pump(0.1)

    def check(self, width, point_size=None):
        app = QApplication.instance()
        if point_size:
            f = app.font(); f.setPointSize(point_size); app.setFont(f)
        self.win.resize(width, 700); self.win.tabs.setCurrentIndex(3 if self.win.tabs.count() > 3 else 0)
        for i in range(self.win.tabs.count()):
            if self.win.tabs.widget(i).findChild(type(self.win.net_table), "") is self.win.net_table or self.win.net_table.parent() is self.win.tabs.widget(i) or self.win.tabs.widget(i).isAncestorOf(self.win.net_table):
                self.win.tabs.setCurrentIndex(i)
        self.win._apply_network_update([info("Ethernet 2", True), info("Wi-Fi", False)], True)
        TN.pump(0.3)
        t = self.win.net_table
        self.assertFalse(t.isColumnHidden(ACTION), "the Action column itself is never dropped")
        right = t.columnViewportPosition(ACTION) + t.columnWidth(ACTION)
        self.assertLessEqual(right, t.viewport().width(), f"Action column inside the visible area at width {width}")
        for r in range(t.rowCount()):
            cell = t.cellWidget(r, ACTION); b = cell.findChild(QPushButton)
            self.assertGreaterEqual(b.width(), b.sizeHint().width(), "button is not squeezed below its natural width")
            self.assertGreaterEqual(b.x(), 4); self.assertLessEqual(b.x() + b.width(), cell.width() - 4, "padding on both sides")
            self.assertGreaterEqual(b.height(), b.sizeHint().height()); self.assertLessEqual(b.height(), t.rowHeight(r))
        return t

    def test_action_button_fits_at_minimum_and_common_widths(self):
        for w in (980, 1100, 1366, 1920):
            self.check(w)

    def test_action_button_fits_with_larger_fonts_and_narrow_window(self):
        for pt in (11, 14):
            self.check(980, pt)

    def test_low_priority_columns_drop_before_action_is_cut(self):
        t = self.check(980, 14)
        self.assertTrue(any(t.isColumnHidden(c) for c in (2, 4, 5, 6)), "narrow + big font hides low-priority columns")
        t = self.check(1920)
        self.assertFalse(any(t.isColumnHidden(c) for c in range(9)), "a wide window shows every column")


if __name__ == "__main__":
    unittest.main()
