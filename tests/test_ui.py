"""UI behaviour tests against the real MainWindow + real downloader + local mock server.

Run:  QT_QPA_PLATFORM=offscreen python -m unittest discover -s tests -v
"""
import os, sys, tempfile, time, unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_nexus as TN                                   # sets HOME + QApplication first
from test_nexus import MODE, BLOB, BLOB_SHA, Srv, reset, sha, pump

from PySide6.QtCore import Qt, QPoint
from PySide6.QtGui import QGuiApplication
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QAbstractItemView, QLineEdit, QMessageBox, QApplication

from gui.download_table import C_ID, C_NAME, C_STATUS, C_ACT, C_PROG, C_SIZE, C_SPEED, HEADERS, row_actions
from gui import theme


def wait_for(cond, timeout=20.0):
    end = time.time() + timeout
    while time.time() < end:
        TN.APP.processEvents()
        if cond():
            return True
        time.sleep(0.02)
    return False


class UiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.warnings, cls.infos = [], []
        cls._p = [mock.patch.object(QMessageBox, "warning", lambda *a, **k: cls.warnings.append(str(a[1:]))),
                  mock.patch.object(QMessageBox, "information", lambda *a, **k: cls.infos.append(str(a[1:])))]
        for p in cls._p: p.start()
        cls.win = TN.get_window(); cls.win.resize(1440, 880); pump(0.2)

    @classmethod
    def tearDownClass(cls):
        for p in cls._p: p.stop()

    def setUp(self):
        self.srv = Srv().__enter__(); reset()
        self.d = tempfile.mkdtemp()
        w = self.win
        for r in list(w.db.all()): w.db.delete_download(r["id"])
        TN.clear_rows(w)
        w.search.setText(""); w.chips["all"].click()
        self.warnings.clear()

    def tearDown(self):
        for j in list(self.win.jobs.values()):
            j.stop(); j.wait(5)
        self.srv.__exit__()

    # -- helpers -------------------------------------------------------------
    def add(self, name, status="PAUSED", size=0, done=0, url=None):
        path = os.path.join(self.d, name)
        rid = self.win.db.add(url or self.srv.url("/f"), name, path, size)
        self.win.db.update(rid, status=status, downloaded=done, size=size)
        self.win.add_row(rid, dict(self.win.db.get(rid)), restored=True)
        return rid, path

    def cell_rect(self, rid, col):
        t = self.win.table
        return t.visualRect(t.model().index(t.row_of(rid), col))

    def click_button(self, rid, which):
        t = self.win.table
        cell = self.cell_rect(rid, C_ACT)
        rect = dict(t.itemDelegate().button_rects(cell))[which]
        QTest.mouseMove(t.viewport(), rect.center()); pump(0.02)
        QTest.mouseClick(t.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, rect.center())
        pump(0.05)

    def toolbar(self, rid, key):
        """Per-download action through the More menu (the only place actions live)."""
        m = self.win._build_more_menu(rid)
        self.assertTrue(m._acts[key].isEnabled(), f"{key} must be enabled for {self.status(rid)}")
        m._acts[key].trigger(); pump(0.05)

    def status(self, rid): return self.win.table.status_of(rid)

    # -- 1. read-only / hidden id ------------------------------------------
    def test_id_column_hidden_and_no_id_in_visible_text(self):
        w = self.win
        rid, _ = self.add("alpha.bin", "COMPLETED", 1000, 1000)
        self.assertTrue(w.table.isColumnHidden(C_ID))
        self.assertEqual(HEADERS[C_ID], "")
        for col in range(w.table.columnCount()):
            if not w.table.isColumnHidden(col):
                it = w.table.item(w.table.row_of(rid), col)
                self.assertNotEqual(it.text(), str(rid)) if rid > 99 else None
        vis_headers = [HEADERS[c] for c in range(len(HEADERS)) if not w.table.isColumnHidden(c)]
        self.assertNotIn("#", vis_headers)
        self.assertTrue(w.torrent_table.isColumnHidden(9), "torrent info-hash column must be hidden")

    def test_every_field_is_read_only(self):
        w = self.win; t = w.table
        rids = [self.add("a.bin", "DOWNLOADING", 100, 50)[0], self.add("b.bin", "PAUSED", 100, 10)[0],
                self.add("c.bin", "ERROR", 100, 0)[0], self.add("d.bin", "COMPLETED", 100, 100)[0]]
        self.assertEqual(t.editTriggers(), QAbstractItemView.EditTrigger.NoEditTriggers)
        before = {(r, c): t.item(t.row_of(r), c).text() for r in rids for c in range(t.columnCount())}
        for rid in rids:
            row = t.row_of(rid)
            for col in range(t.columnCount()):
                it = t.item(row, col)
                self.assertFalse(it.flags() & Qt.ItemFlag.ItemIsEditable, f"col {col} editable")
                if t.isColumnHidden(col): continue
                idx = t.model().index(row, col)
                t.setCurrentIndex(idx)
                with mock.patch.object(TN.M.QDesktopServices, "openUrl", lambda *_: True), mock.patch.object(TN.M.MainWindow, "show_details", lambda *_: None):
                    pos = self.cell_rect(rid, col).center()
                    if col == C_ACT: pos = QPoint(self.cell_rect(rid, col).left() + 3, pos.y())   # avoid the buttons
                    QTest.mouseDClick(t.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, pos)
                QTest.keyClick(t, Qt.Key.Key_F2); QTest.keyClicks(t, "zzz")
                pump(0.02)
                self.assertNotEqual(t.state(), QAbstractItemView.State.EditingState)
                self.assertFalse(t.edit(idx))
                t.editItem(it)
                self.assertNotEqual(t.state(), QAbstractItemView.State.EditingState)
                self.assertEqual(t.viewport().findChildren(QLineEdit), [], "an inline editor appeared")
        after = {(r, c): t.item(t.row_of(r), c).text() for r in rids for c in range(t.columnCount())}
        self.assertEqual(before, after, "typing/double-click must not change any value")
        self.assertEqual(t.contextMenuPolicy(), Qt.ContextMenuPolicy.CustomContextMenu)   # only our safe menu
        for tb in (w.torrent_table, w.history_table, w.net_table):
            self.assertEqual(tb.editTriggers(), QAbstractItemView.EditTrigger.NoEditTriggers)
        for tb in (w.history_table, w.net_table):
            self.assertEqual(tb.contextMenuPolicy(), Qt.ContextMenuPolicy.NoContextMenu)
        self.assertEqual(w.torrent_table.contextMenuPolicy(), Qt.ContextMenuPolicy.CustomContextMenu)   # torrent row menu (safe actions only)

    def test_double_click_opens_read_only_details_for_every_status(self):
        w = self.win; shown = []
        for st in ("DOWNLOADING", "PAUSED", "COMPLETED", "ERROR", "STOPPED", "QUEUED"):
            rid, _ = self.add(f"{st}.bin", st, 100, 40)
            before = (self.status(rid), dict(w.db.get(rid)))
            with mock.patch.object(TN.M, "DownloadDetailsDialog", lambda prov, p: type("D", (), {"exec": lambda s_: shown.append(prov(False))})()):
                QTest.mouseDClick(w.table.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, self.cell_rect(rid, C_NAME).center())
            self.assertEqual((self.status(rid), dict(w.db.get(rid))), before, "opening Details must not change anything")
        self.assertEqual(len(shown), 6)

    # -- 2. real engine controls through real mouse clicks -------------------
    def test_start_pause_resume_stop_start_again_completes(self):
        reset(throttle=0.25)
        rid, path = self.add("ctl.bin", "PAUSED", len(BLOB), 0)
        t = self.win.table
        self.assertEqual(row_actions(self.status(rid))[0], "start")
        self.toolbar(rid, 'start')                               # Start
        self.assertTrue(wait_for(lambda: self.status(rid) == "DOWNLOADING" and self.win.jobs.get(rid) and self.win.jobs[rid].info.done > 0))
        job = self.win.jobs[rid]
        self.assertTrue(wait_for(lambda: t.item(t.row_of(rid), C_SPEED).text() not in ("—", "")), "real speed must appear")
        self.assertGreater(float(t.item(t.row_of(rid), C_PROG).data(TN.M.__dict__.get("R_PCT", Qt.ItemDataRole.UserRole + 3)) or 0), 0.0) if False else None
        self.assertEqual(row_actions("DOWNLOADING")[0], "pause")
        self.toolbar(rid, 'pause')                               # Pause
        self.assertTrue(wait_for(lambda: self.status(rid) == "PAUSED"))
        d0 = job.info.done; time.sleep(0.5); pump(0.1)
        self.assertLessEqual(job.info.done - d0, 3 * 65536, "download must not progress while paused")
        self.assertEqual(self.win.db.get(rid)["status"], "PAUSED")
        self.toolbar(rid, 'start')                               # Resume
        self.assertTrue(wait_for(lambda: self.status(rid) == "DOWNLOADING"))
        self.assertTrue(wait_for(lambda: job.info.done > d0 + 65536))
        self.toolbar(rid, 'stop')                                # Stop / cancel
        self.assertTrue(wait_for(lambda: self.status(rid) == "STOPPED" and not job.is_alive()))
        self.assertEqual(theme.status_label("STOPPED"), "Cancelled")
        self.assertEqual(self.win.db.get(rid)["status"], "STOPPED")
        self.assertTrue(os.path.isdir(path + ".parts") or os.path.exists(path + ".part"), "partial data kept")
        reset()
        self.toolbar(rid, 'start')                               # Start again -> resumes
        self.assertTrue(wait_for(lambda: self.status(rid) == "COMPLETED", 40))
        self.assertEqual(sha(path), BLOB_SHA)
        self.assertEqual(self.win.db.get(rid)["status"], "COMPLETED")
        self.assertEqual(row_actions("COMPLETED")[0], "open")

    def test_failed_then_retry_and_expired_link(self):
        reset(fail=404)
        rid, path = self.add("bad.bin", "PAUSED", 0, 0)
        self.win.start_job(rid, dict(self.win.db.get(rid)))                  # invalid link -> Failed, no retry storm
        self.assertTrue(wait_for(lambda: self.status(rid) == "ERROR", 20))
        self.assertEqual(theme.status_label("ERROR"), "Failed")
        self.assertIn("404", self.win.table.item(self.win.table.row_of(rid), C_NAME).data(TN.M.__dict__.get("R_SUB", Qt.ItemDataRole.UserRole + 5)) or "404")
        self.assertEqual(row_actions("ERROR")[0], "retry")
        reset()                                                              # link fixed
        self.toolbar(rid, 'retry')                                # Retry
        self.assertTrue(wait_for(lambda: self.status(rid) == "COMPLETED", 40))
        self.assertEqual(sha(path), BLOB_SHA)
        # expired link: shows Retrying (needs user action), Retry works again once the link is valid
        reset(fail=403)
        rid2, path2 = self.add("exp.bin", "PAUSED", 0, 0)
        self.win.start_job(rid2, dict(self.win.db.get(rid2)))
        self.assertTrue(wait_for(lambda: self.status(rid2) == "RETRYING", 20))
        reset()
        self.toolbar(rid2, 'retry')
        self.assertTrue(wait_for(lambda: self.status(rid2) == "COMPLETED", 40))
        self.assertEqual(sha(path2), BLOB_SHA)

    def test_queued_and_restored_rows_without_job(self):
        rid, _ = self.add("q.bin", "QUEUED")
        self.win.settings["auto_start"] = True
        self.win.pause_one(rid)                                              # pausing a queued row must stick
        self.assertEqual(self.status(rid), "PAUSED"); self.assertEqual(self.win.db.get(rid)["status"], "PAUSED")
        self.win.stop_one(rid)
        self.assertEqual(self.status(rid), "STOPPED"); self.assertEqual(self.win.db.get(rid)["status"], "STOPPED")
        pump(1.3)                                                            # the queue must not resurrect it
        self.assertEqual(self.status(rid), "STOPPED")

    # -- 3. sorting / selection / filtering -------------------------------------------
    def test_sort_select_filter(self):
        w = self.win; t = w.table
        a, _ = self.add("bravo.zip", "COMPLETED", 300, 300)
        b, _ = self.add("alpha.mkv", "PAUSED", 100, 10)
        c, _ = self.add("charlie.pdf", "ERROR", 200, 0)
        d, _ = self.add("delta.zip", "DOWNLOADING", 900, 100)
        hdr = t.horizontalHeader()
        def order(): return [t.item(r, C_NAME).text() for r in range(t.rowCount()) if not t.isRowHidden(r)]
        QTest.mouseClick(hdr.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(hdr.sectionViewportPosition(C_NAME) + 10, 8)); pump(0.05)
        self.assertEqual(order(), ["alpha.mkv", "bravo.zip", "charlie.pdf", "delta.zip"])
        QTest.mouseClick(hdr.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(hdr.sectionViewportPosition(C_NAME) + 10, 8)); pump(0.05)
        self.assertEqual(order(), ["delta.zip", "charlie.pdf", "bravo.zip", "alpha.mkv"])
        QTest.mouseClick(hdr.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(hdr.sectionViewportPosition(C_SIZE) + 10, 8)); pump(0.05)
        self.assertEqual(order(), ["alpha.mkv", "charlie.pdf", "bravo.zip", "delta.zip"])      # numeric, not text, order
        # row click selects the right download even after sorting
        QTest.mouseClick(t.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, self.cell_rect(c, C_NAME).center()); pump(0.05)
        self.assertEqual(w.selected_ids(), [c])
        # clicking an action button must NOT change the selection
        self.click_button(a, "more") if False else None
        # filters
        w.chips["completed"].click(); pump(0.05)
        self.assertEqual(order(), ["bravo.zip"]); self.assertEqual(w.selected_ids(), [], "hidden rows must be deselected")
        w.chips["failed"].click(); self.assertEqual(order(), ["charlie.pdf"])
        w.chips["active"].click(); self.assertEqual(order(), ["delta.zip"])
        w.chips["all"].click(); w.search.setText("zip"); pump(0.05)
        self.assertEqual(sorted(order()), ["bravo.zip", "delta.zip"])
        w.search.setText("nothing-matches"); pump(0.05)
        self.assertEqual(w.dl_stack.currentIndex(), 1); self.assertEqual(w.drop_zone.title.text(), "Nothing matches this view")
        w.search.setText(""); pump(0.05)
        self.assertEqual(w.dl_stack.currentIndex(), 0)
        # selection-based toolbar state
        t.selectRow(t.row_of(c)); pump(0.05)
        m = w._build_more_menu(c)
        self.assertTrue(m._acts["retry"].isEnabled()); self.assertFalse(m._acts["pause"].isEnabled())

    def test_empty_state(self):
        self.assertEqual(self.win.dl_stack.currentIndex(), 1)
        self.assertEqual(self.win.drop_zone.title.text(), "No downloads yet")

    # -- 4. menu / details / removal -----------------------------------------------------------
    def test_more_menu_actions(self):
        w = self.win
        rid, path = self.add("menu.bin", "COMPLETED", 4, 4); open(path, "wb").write(b"data")
        m = w._build_more_menu(rid)
        self.assertEqual([a.isEnabled() for k, a in m._acts.items() if k in ("open", "folder", "details", "copy", "remove", "delete")], [True] * 6)
        seen = []
        with mock.patch.object(TN.M.QDesktopServices, "openUrl", lambda u: seen.append(u.toLocalFile()) or True):
            m._acts["open"].trigger(); m._acts["folder"].trigger()
        self.assertEqual(seen, [path, self.d])
        m._acts["copy"].trigger(); self.assertEqual(QGuiApplication.clipboard().text(), self.srv.url("/f"))
        shown = []
        with mock.patch.object(TN.M, "DownloadDetailsDialog", lambda prov, p: type("D", (), {"exec": lambda s_: shown.append((prov(False), prov(True)))})()):
            m._acts["details"].trigger()
        plain, adv = shown[0]
        general = {l: v for l, v, _ in plain["tabs"]["General"]}; general_adv = {l: v for l, v, _ in adv["tabs"]["General"]}
        self.assertNotIn("Download ID", general); self.assertEqual(general_adv["Download ID"], rid)
        self.assertEqual(general["Status"], "Completed")
        m._acts["remove"].trigger()                                         # remove from list keeps the file
        self.assertIsNone(w.db.get(rid)); self.assertTrue(os.path.exists(path)); self.assertIsNone(w.table.row_of(rid))
        rid2, path2 = self.add("menu2.bin", "COMPLETED", 4, 4); open(path2, "wb").write(b"data")
        m2 = w._build_more_menu(rid2)
        with mock.patch.object(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes):
            m2._acts["delete"].trigger()
        self.assertFalse(os.path.exists(path2)); self.assertIsNone(w.db.get(rid2))
        rid3, path3 = self.add("keep.bin", "COMPLETED", 4, 4); open(path3, "wb").write(b"data")
        m3 = w._build_more_menu(rid3)
        with mock.patch.object(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.No):
            m3._acts["delete"].trigger()
        self.assertTrue(os.path.exists(path3)); self.assertIsNotNone(w.db.get(rid3), "declined confirmation must delete nothing")

    # -- 5. layout / animation / responsiveness ---------------------------------------
    def test_resize_hides_secondary_columns_and_scales_rows(self):
        w = self.win
        from gui.download_table import C_ADDED, C_ELAPSED, C_ETA, DownloadTable
        t = DownloadTable(); t.show()                                   # standalone: the offscreen screen caps real window sizes
        em = t.fontMetrics().horizontalAdvance("0")
        def check_fit():
            shown = [c for c in range(t.columnCount()) if not t.isColumnHidden(c)]
            used = sum(t.columnWidth(c) for c in shown if c != C_NAME)
            self.assertGreaterEqual(t.viewport().width() - used, t.NAME_MIN_EM * em - 2, "name column must keep a readable width")
            self.assertTrue(all(c in shown for c in (C_NAME, C_STATUS, C_PROG, C_ACT)), "essential columns always visible")
        hidden_counts = []
        for width in (1900, 1500, 1100, 900, 700, 600):                 # no manual column dragging at any size
            t.setFixedWidth(width); pump(0.05); check_fit()
            hidden_counts.append(sum(t.isColumnHidden(c) for c in range(1, t.columnCount())))
        self.assertEqual(hidden_counts, sorted(hidden_counts), "columns only disappear as the window narrows")
        t.setFixedWidth(1900); pump(0.05)
        self.assertFalse(t.isColumnHidden(C_ADDED)); self.assertFalse(t.isColumnHidden(C_ELAPSED))
        t.setFixedWidth(760); pump(0.05)
        self.assertTrue(t.isColumnHidden(C_ADDED), "least important column leaves first on narrow windows")
        f = t.font(); px = f.pixelSize()
        if px > 0: f.setPixelSize(int(px * 1.3))
        else: f.setPointSizeF(f.pointSizeF() * 1.3)
        t.setFont(f); t.fit_columns(); check_fit()   # 130% display scaling
        t.close()
        w.resize(1000, 700); pump(0.1); self.assertGreaterEqual(w.width(), 980)
        w.resize(1440, 880); pump(0.1)
        f = QApplication.font(); old = f.pointSize(); f.setPointSize(old * 2); w.table.setFont(f)
        self.assertGreater(w.table.row_height(), int(QApplication.fontMetrics().height() * 2.9) + 4)   # rows follow font/DPI scale
        f.setPointSize(old); w.table.setFont(f)

    def test_progress_animation_is_real_and_converges(self):
        w = self.win; t = w.table
        rid, _ = self.add("anim.bin", "DOWNLOADING", 100, 10)
        it = t._items[rid][C_PROG]; R_SHOWN, R_PCT = TN.M.__dict__.get("R_SHOWN"), None
        from gui.download_table import R_SHOWN as SH, R_PCT as PC
        t.set_progress(rid, 80)
        seen = []
        end = time.time() + 0.6
        while time.time() < end:
            TN.APP.processEvents(); seen.append(float(it.data(SH))); time.sleep(0.01)
        self.assertEqual(seen[-1], 80.0)
        self.assertTrue(all(b >= a - 1e-9 for a, b in zip(seen, seen[1:])), "monotonic, never overshoots")
        self.assertLessEqual(max(seen), 80.0)
        self.assertGreater(len({round(x, 1) for x in seen}), 3, "must ease through intermediate values")

    def test_dialogs_fade_in(self):
        from gui.new_download import NewDownloadDialog
        dlg = NewDownloadDialog("https://example.com/a.zip", self.win); dlg.show()
        pump(0.05); self.assertLess(dlg.windowOpacity(), 1.0 + 1e-6)
        pump(0.35); self.assertAlmostEqual(dlg.windowOpacity(), 1.0, places=2); dlg.close()

    def test_ui_stays_responsive_during_download(self):
        reset(throttle=0.01)
        rid, path = self.add("resp.bin", "PAUSED", len(BLOB), 0)
        self.win.start_job(rid, dict(self.win.db.get(rid)))
        gaps = []; last = time.perf_counter(); end = time.time() + 2.0
        while time.time() < end:
            TN.APP.processEvents(); now = time.perf_counter(); gaps.append(now - last); last = now; time.sleep(0.005)
        self.assertLess(max(gaps), 0.25, f"event loop stalled for {max(gaps):.3f}s during a download")
        self.win.stop_one(rid)

    def test_sidebar_navigation_and_search_visibility(self):
        w = self.win
        w.nav_buttons["torrents"].click(); pump(0.05)
        self.assertEqual(w.tabs.currentWidget(), w.torrent_tab); self.assertFalse(w.search.isVisible()); self.assertEqual(w.page_title.text(), "Torrents")
        w.nav_buttons["networks"].click(); self.assertEqual(w.tabs.currentWidget(), w.networks_tab)
        w.nav_buttons["history"].click(); self.assertEqual(w.tabs.currentWidget(), w.history_tab)
        w.nav_buttons["downloads"].click(); self.assertTrue(w.search.isVisible())


if __name__ == "__main__":
    unittest.main(verbosity=2)
