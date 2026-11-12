"""Every page and the main dialogs must keep their buttons usable at the supported window sizes, in both themes:
no squeezed / clipped / overlapping / off-window buttons, and no horizontal scrolling on the page tables at 980 px and wider
(Networks + History at their default font)."""
import os, sys, tempfile, time, unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.dirname(HERE)); sys.path.insert(0, HERE)
from PySide6.QtCore import QPoint, QRect
from PySide6.QtWidgets import QAbstractButton, QAbstractScrollArea, QApplication, QTableWidget

from core.network_monitor import NetworkInfo
import test_nexus as TN
from gui import main_window as M

SIZES = [(980, 640), (1024, 768), (1280, 720), (1440, 900), (1920, 1080)]


def button_problems(root, bounds):
    """Every visible button under root: not squeezed below its minimum hint, inside bounds, not cut by a scroll viewport, not overlapping a sibling."""
    out = []; btns = [b for b in root.findChildren(QAbstractButton) if b.isVisible()]
    for b in btns:
        name = b.text() or b.accessibleName() or b.objectName() or b.toolTip() or type(b).__name__
        m = b.minimumSizeHint()
        if b.width() < m.width() - 1 or b.height() < m.height() - 1:
            out.append(f"squeezed '{name}' {b.width()}x{b.height()} < {m.width()}x{m.height()}")
        if not bounds.contains(QRect(b.mapTo(root, QPoint(0, 0)), b.size())):
            out.append(f"outside '{name}'")
        p = b.parentWidget()
        while p is not None and p is not root:
            if isinstance(p, QAbstractScrollArea) and p.viewport().isAncestorOf(b):
                if not p.viewport().rect().contains(QRect(b.mapTo(p.viewport(), QPoint(0, 0)), b.size())):
                    out.append(f"clipped by scroll area '{name}'")
                break
            p = p.parentWidget()
    groups = {}
    for b in btns: groups.setdefault(b.parentWidget(), []).append(b)
    for bl in groups.values():
        for i in range(len(bl)):
            for j in range(i + 1, len(bl)):
                x = bl[i].geometry().intersected(bl[j].geometry())
                if x.width() > 2 and x.height() > 2: out.append(f"overlap '{bl[i].text()}' / '{bl[j].text()}'")
    return out


class PageLayouts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.win = TN.get_window(); cls.win.show(); cls.win.net_timer.stop()
        # opening the Networks page triggers a real background scan that can land after the fixture rows and mark them DISCONNECTED
        end = time.time() + 5
        while cls.win._net_running and time.time() < end: TN.pump(0.05)
        TN.pump(0.5)                                      # deliver any finished scan's queued result before the fixtures go in
        cls._no_scan = mock.patch.object(cls.win, "_run_network_scan", lambda *a, **k: None); cls._no_scan.start()
        cls.infos = [NetworkInfo("Ethernet 2", "Ethernet 2", "192.168.1.20", 1000, "ONLINE", 14, 0, True, "ACTIVE"),
                     NetworkInfo("Wi-Fi", "Wi-Fi", "192.168.1.21", 300, "ONLINE", 22, 0, True, "ACTIVE"),
                     NetworkInfo("Ethernet 3", "Ethernet 3", "192.168.42.129", 480, "NO INTERNET", None, 0, False, "NO INTERNET")]
        cls.theme = M.theme.appearance_state() if hasattr(M.theme, "appearance_state") else {}

    @classmethod
    def tearDownClass(cls):
        cls._no_scan.stop(); cls.win._net_running = False; cls.win._net_next = None     # the stubbed scan never cleared the flag
        M.apply_theme(QApplication.instance(), {"theme": "dark"}); cls.win.net_timer.start(); cls.win.nav_buttons["downloads"].click()

    def sweep(self, theme_name):
        w = self.win; M.apply_theme(QApplication.instance(), {"theme": theme_name}); TN.pump(0.2)
        w.net_rows = {}; w._set_network_status(""); w._apply_network_update(self.infos, True)
        for idx, key in enumerate(w.nav_buttons):
            for W, H in SIZES:
                w.tabs.setCurrentIndex(idx); w.resize(W, H); TN.pump(0.15)
                with self.subTest(theme=theme_name, page=key, size=f"{W}x{H}"):
                    self.assertLessEqual(w.width(), W, "window can shrink to the requested size")
                    problems = button_problems(w.tabs.currentWidget(), w.tabs.currentWidget().rect())
                    for t in w.tabs.currentWidget().findChildren(QTableWidget):
                        if t.isVisible() and t in (w.net_table, w.history_table):
                            hb = t.horizontalScrollBar()
                            problems += [f"{key} table scrolls horizontally by {hb.maximum()} px"] if hb.isVisible() and hb.maximum() > 0 else []
                    self.assertEqual(problems, [])

    def test_all_pages_all_sizes_dark(self):
        self.sweep("dark")

    def test_all_pages_all_sizes_light(self):
        self.sweep("light")

    def test_networks_page_keeps_key_columns_and_all_action_buttons_at_980(self):
        w = self.win; w.nav_buttons["networks"].click(); w.resize(980, 640); w._set_network_status(""); w._apply_network_update(self.infos, True); TN.pump(0.3)
        t = w.net_table; self.assertEqual(t.horizontalScrollBar().maximum(), 0)
        for col in (0, 1, 3, 7, 8, 6): self.assertFalse(t.isColumnHidden(col), f"column {col} stays visible (Used is shrunk, not removed)")
        self.assertLess(t.columnWidth(6), 140, "the low-priority Used column is the one that gives way")
        for r in range(t.rowCount()):
            b = t.cellWidget(r, 8).findChild(QAbstractButton)
            self.assertGreaterEqual(b.width(), b.sizeHint().width()); self.assertTrue(t.viewport().rect().contains(QRect(b.mapTo(t.viewport(), QPoint(0, 0)), b.size())))

    def test_header_buttons_use_short_labels_only_when_the_row_would_not_fit(self):
        w = self.win; w.nav_buttons["networks"].click()
        w.resize(1920, 1080); TN.pump(0.2); self.assertEqual(w.net_buttons[2].text(), "Enable all online")
        w.resize(980, 640); TN.pump(0.2); self.assertEqual(w.net_buttons[2].text(), "Enable all"); self.assertEqual(w.net_buttons[2].toolTip(), "Enable all online")
        self.assertEqual(w.net_buttons[2].accessibleName(), "Enable all online")


class Dialogs(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.win = TN.get_window()

    def check(self, dlg, label):
        dlg.show(); TN.pump(0.2)
        for size in (dlg.sizeHint(), dlg.minimumSize()):
            dlg.resize(size.expandedTo(dlg.minimumSize())); TN.pump(0.15)
            with self.subTest(dialog=label, size=f"{dlg.width()}x{dlg.height()}"):
                self.assertGreaterEqual(dlg.width(), 400); self.assertGreaterEqual(dlg.height(), 120)
                self.assertGreater(len([b for b in dlg.findChildren(QAbstractButton) if b.isVisible()]), 0, "has visible controls")
                self.assertEqual(button_problems(dlg, dlg.rect()), [])
        dlg.close()

    def test_dialogs_have_usable_dimensions_and_visible_controls(self):
        from gui.settings_dialog import SettingsDialog
        from gui.new_download import NewDownloadDialog
        from gui.choice_dialog import ChoiceDialog
        from gui.conflict_dialog import ConflictDialog
        from gui.delete_dialog import DeleteDownloadsDialog
        for theme_name in ("dark", "light"):
            M.apply_theme(QApplication.instance(), {"theme": theme_name})
            self.check(SettingsDialog(dict(self.win.settings), self.win), f"settings/{theme_name}")
            self.check(NewDownloadDialog("https://example.com/a.bin", self.win), f"new download/{theme_name}")
            self.check(ChoiceDialog(self.win, "Title", "Headline", "Detail text " * 12, [("a", "Keep both"), ("b", "Replace the existing file")]), f"choice/{theme_name}")
            self.check(ConflictDialog(self.win, os.path.join(tempfile.gettempdir(), "exists.bin"), "File"), f"conflict/{theme_name}")
            rows = [{"id": 1, "name": "a.bin", "path": os.path.join(tempfile.gettempdir(), "a.bin"), "status": "COMPLETED", "size": 10, "downloaded": 10}]
            self.check(DeleteDownloadsDialog(rows, self.win), f"delete/{theme_name}")
        M.apply_theme(QApplication.instance(), {"theme": "dark"})


if __name__ == "__main__":
    unittest.main()
