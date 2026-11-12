"""Regression tests for the v10.1 bug-fix round (real MainWindow, real downloader, local mock server)."""
import json, os, sys, tempfile, time, unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_nexus as TN
from test_nexus import MODE, BLOB, BLOB_SHA, Srv, reset, sha, pump
from test_ui import wait_for

from PySide6.QtCore import Qt, QPoint
from PySide6.QtGui import QColor, QGuiApplication
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QCheckBox, QMessageBox

from core.links import normalize_url, unique_path, magnet_info_hash
from core.downloader import Downloader
from gui.download_table import C_NAME, C_ACT
from gui.settings_dialog import SettingsDialog, make_check
from gui import theme

M = TN.M


class FakeDialog:
    """Stands in for the Add Download window: returns what the user 'typed'."""
    spec = {}
    def __init__(self, url="", parent=None): pass
    def exec(self): return 1
    def data(self): return dict(FakeDialog.spec)


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.warnings, cls.infos, cls.choices = [], [], []
        cls._p = [mock.patch.object(QMessageBox, "warning", lambda *a, **k: cls.warnings.append(str(a[1:]))),
                  mock.patch.object(QMessageBox, "information", lambda *a, **k: cls.infos.append(str(a[1:])))]
        for p in cls._p: p.start()
        cls.win = TN.get_window(); cls.win.resize(1200, 800); pump(0.2)

    @classmethod
    def tearDownClass(cls):
        for p in cls._p: p.stop()

    def setUp(self):
        self.srv = Srv().__enter__(); reset()
        self.d = tempfile.mkdtemp()
        w = self.win
        for j in list(w.jobs.values()): j.stop(); j.wait(5)
        for r in list(w.db.all()): w.db.delete_download(r["id"])
        TN.clear_rows(w)
        w.search.setText(""); w.chips["all"].click(); w.nav_buttons["downloads"].click()
        w.settings.update(auto_start=True, connections=8, simultaneous_files=3, ask_location=True, download_dir="")
        self.choices.clear(); self.reply = "cancel"
        w.apply_settings(dict(w.settings)); self._q = mock.patch.object(M.MainWindow, "_start_queued_jobs", lambda self_: None); self._q.start()
        self._ask = mock.patch.object(M, "ask_choice", self._fake_ask); self._ask.start()
        self._dlg = mock.patch.object(M, "NewDownloadDialog", FakeDialog); self._dlg.start()

    def tearDown(self):
        self._ask.stop(); self._dlg.stop(); self._q.stop()
        for j in list(self.win.jobs.values()): j.stop(); j.wait(5)
        self.srv.__exit__()

    def _fake_ask(self, parent, title, headline, detail, actions):
        self.choices.append((headline, detail, [k for k, _ in actions], [l for _, l in actions]))
        return self.reply if self.reply in [k for k, _ in actions] + ["cancel"] else "cancel"

    # helpers
    def submit(self, url=None, name="file.bin", conns=8, directory=None):
        FakeDialog.spec = {"url": url or self.srv.url("/f"), "directory": directory or self.d, "name": name, "connections": conns}
        self.win._open_download_dialog("")

    def visible(self):
        t = self.win.table
        return [t.item(r, C_NAME).text() for r in range(t.rowCount()) if not t.isRowHidden(r)]

    def seed(self, name, status, url=None, size=0, done=0):
        path = os.path.join(self.d, name)
        rid = self.win.db.add(url or self.srv.url("/f"), name, path, size)
        self.win.db.update(rid, status=status, size=size, downloaded=done)
        self.win.add_row(rid, dict(self.win.db.get(rid)), restored=True)
        return rid, path

    def status(self, rid): return self.win.table.status_of(rid)


class DisappearingDownloads(Base):
    def test_clear_history_hides_history_but_preserves_download_and_file(self):
        w = self.win
        rid, path = self.seed("keep-after-history-clear.bin", "COMPLETED", size=4, done=4)
        with open(path, "wb") as f:
            f.write(b"data")
        w.db.update(rid, completed="2026-10-26T12:00:00")
        w.refresh_history()
        self.assertTrue(any(int(row["id"]) == rid for row in w.db.history()))
        self.assertIsNotNone(w.table.row_of(rid))

        w.clear_history()

        self.assertIsNotNone(w.db.get(rid), "clearing History must preserve the download record")
        self.assertIsNotNone(w.table.row_of(rid), "clearing History must preserve the Downloads row")
        self.assertTrue(os.path.isfile(path), "clearing History must never delete downloaded files")
        self.assertFalse(any(int(row["id"]) == rid for row in w.db.history()))

        # The hidden state persists in SQLite and does not reappear on refresh.
        w.refresh_history()
        self.assertEqual(w.history_table.rowCount(), 0)
        self.assertIsNotNone(w.db.get(rid))

    def test_new_download_visible_despite_stale_search(self):
        w = self.win
        self.seed("old.bin", "PAUSED")
        w.search.setText("https://cdn.example.com/part-of-an-old-url")      # stale text hides everything
        self.assertEqual(self.visible(), [])
        w.settings["auto_start"] = False
        self.submit(url=self.srv.url("/brand-new"), name="brand-new.bin")
        self.assertIn("brand-new.bin", self.visible(), "new download must be visible immediately")
        self.assertEqual(w.search.text(), "", "stale search is cleared so the user sees it")
        self.assertEqual(sorted(self.visible()), ["brand-new.bin", "old.bin"])
        self.assertEqual(len(w.db.all()), w.table.rowCount())

    def test_new_download_visible_under_active_filter(self):
        w = self.win; w.settings["auto_start"] = False
        w.chips["active"].click(); self.submit(name="n.bin")
        self.assertEqual(w._filter_key, "all"); self.assertIn("n.bin", self.visible())
        self.assertTrue(w.chips["all"].isChecked())

    def test_search_filter_only_hide_never_remove_and_banner_explains(self):
        w = self.win
        a, _ = self.seed("alpha.bin", "PAUSED"); b, _ = self.seed("beta.bin", "COMPLETED", size=5, done=5)
        w.search.setText("alpha"); pump(0.05)
        self.assertEqual(self.visible(), ["alpha.bin"])
        self.assertTrue(w.filter_note.isVisibleTo(w.downloads_tab), "hidden downloads must be announced, not silent")
        self.assertIn("1 of 2", w.filter_text.text())
        self.assertEqual(len(w.db.all()), 2); self.assertIsNotNone(w.table.row_of(b))      # records + rows untouched
        w.clear_filters(); pump(0.05)
        self.assertEqual(sorted(self.visible()), ["alpha.bin", "beta.bin"]); self.assertFalse(w.filter_note.isVisibleTo(w.downloads_tab))

    def test_search_matches_link_text_too(self):
        w = self.win
        self.seed("x1.bin", "PAUSED", url=self.srv.url("/weird-token-123")); self.seed("x2.bin", "PAUSED")
        w.search.setText("weird-token"); self.assertEqual(self.visible(), ["x1.bin"])

    def test_clicking_around_never_hides_or_disconnects_rows(self):
        w = self.win; t = w.table
        rids = [self.seed(f"r{i}.bin", s)[0] for i, s in enumerate(["PAUSED", "COMPLETED", "ERROR", "QUEUED", "STOPPED"])]
        hdr = t.horizontalHeader()
        for rep in range(3):
            for rid in rids:
                QTest.mouseClick(t.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, self.cell_center(rid)); pump(0.02)
            for col in (1, 2, 3, 4, 7):
                QTest.mouseClick(hdr.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(hdr.sectionViewportPosition(col) + 8, 8)); pump(0.02)
            for k in ("torrents", "networks", "history", "downloads"): w.nav_buttons[k].click(); pump(0.02)
            for chip in ("failed", "all"): w.chips[chip].click(); pump(0.02)
            pump(1.2)                                                      # a full UI tick incl. queue scheduler
        self.assertEqual(len(self.visible()), 5, "no row may vanish without a filter/search")
        self.assertEqual(t.rowCount(), len(w.db.all()))
        self.assertEqual(sorted(t.rid_at(r) for r in range(t.rowCount())), sorted(rids))

    def cell_center(self, rid):
        t = self.win.table; return t.visualRect(t.model().index(t.row_of(rid), C_NAME)).center()

    def test_downloads_restored_after_restart(self):
        w = self.win; w.settings["auto_start"] = False
        self.submit(name="persist.bin"); rid = w.db.all()[0]["id"]
        w2 = M.MainWindow(); pump(0.5)
        try:
            self.assertEqual(w2.table.rowCount(), 1); self.assertEqual(w2.table.rid_at(0), rid)
            self.assertFalse(w2.table.isRowHidden(0)); self.assertEqual(w2.table.item(0, C_NAME).text(), "persist.bin")
            self.assertEqual(w2.db.get(rid)["connections"], 8)
        finally:
            w2.close(); pump(0.2)


class DuplicateLinks(Base):
    def test_normalize_keeps_signed_urls_distinct(self):
        n = normalize_url
        self.assertEqual(n("HTTP://Example.COM:80/a/b.zip#frag"), n("http://example.com/a/b.zip"))
        self.assertEqual(n("https://e.com:443/%7Euser/f"), n("https://e.com/~user/f"))
        self.assertEqual(n("https://e.com/a%2fb"), n("https://e.com/a%2Fb"))
        self.assertNotEqual(n("https://e.com/f?token=AAA"), n("https://e.com/f?token=BBB"))
        self.assertNotEqual(n("https://e.com/f?X-Amz-Signature=1&x=2"), n("https://e.com/f?x=2"))
        self.assertNotEqual(n("http://e.com/f"), n("https://e.com/f"))
        self.assertNotEqual(n("https://e.com:8443/f"), n("https://e.com/f"))
        self.assertNotEqual(n("https://u:p@e.com/f"), n("https://e.com/f"))
        self.assertNotEqual(n("https://e.com/F"), n("https://e.com/f"))               # path is case-sensitive

    def test_unique_path_never_returns_existing(self):
        p = os.path.join(self.d, "a.txt"); open(p, "w").write("x")
        self.assertEqual(unique_path(p), os.path.join(self.d, "a (1).txt"))
        open(os.path.join(self.d, "a (1).txt"), "w").write("y")
        self.assertEqual(unique_path(p), os.path.join(self.d, "a (2).txt"))
        self.assertEqual(unique_path(p, lambda q: q.endswith("(2).txt")), os.path.join(self.d, "a (3).txt"))

    def test_magnet_hash_parsing(self):
        h = "0123456789abcdef0123456789abcdef01234567"
        self.assertEqual(magnet_info_hash(f"magnet:?xt=urn:btih:{h.upper()}&dn=x"), h)
        import base64; b32 = base64.b32encode(bytes.fromhex(h)).decode()
        self.assertEqual(magnet_info_hash(f"magnet:?dn=x&xt=urn:btih:{b32}"), h)
        self.assertEqual(magnet_info_hash("magnet:?dn=nohash"), "")

    def test_active_link_is_detected_no_second_worker_record_or_file(self):
        w = self.win; reset(throttle=0.05)
        self.submit(name="live.bin")
        rid = w.db.all()[0]["id"]
        self.assertTrue(wait_for(lambda: w.jobs.get(rid) and w.jobs[rid].info.done > 0 and self.status(rid) == "DOWNLOADING"))
        before_files = sorted(os.listdir(self.d)); jobs_before = dict(w.jobs)
        self.reply = "cancel"; self.submit(name="live2.bin")
        self.assertEqual(len(self.choices), 1)
        head, detail, keys, _ = self.choices[0]
        self.assertEqual(head, "This download link already exists.")
        self.assertIn("already downloading", detail); self.assertIn("Downloading", detail)
        self.assertIn("show", keys); self.assertIn("again", keys)
        self.assertEqual(len(w.db.all()), 1); self.assertEqual(w.jobs, jobs_before)
        self.assertEqual(sorted(os.listdir(self.d)), before_files, "no temp/destination file for the rejected duplicate")
        self.reply = "show"; self.search_hide = w.search.setText("zzz"); self.submit(name="live3.bin")
        self.assertEqual(w.selected_ids(), [rid]); self.assertEqual(w.search.text(), "")
        self.assertEqual(len(w.db.all()), 1)

    def test_intentional_download_again_uses_new_file_name(self):
        w = self.win; reset(); self.reply = "again"
        self.submit(name="same.bin")
        first = w.db.all()[0]; self.assertTrue(wait_for(lambda: self.status(first["id"]) == "COMPLETED", 40))
        self.submit(name="same.bin")
        self.assertEqual(len(w.db.all()), 2)
        paths = sorted(r["path"] for r in w.db.all())
        self.assertEqual(paths, sorted([os.path.join(self.d, "same.bin"), os.path.join(self.d, "same (1).bin")]))
        self.assertIn("same (1).bin", self.choices[-1][3][-1], "button states the new name")
        newest = w.db.all()[0]["id"]; self.assertTrue(wait_for(lambda: self.status(newest) == "COMPLETED", 40))
        self.assertEqual(sha(os.path.join(self.d, "same.bin")), BLOB_SHA); self.assertEqual(sha(os.path.join(self.d, "same (1).bin")), BLOB_SHA)

    def test_paused_queued_failed_cancelled_completed_offer_right_actions(self):
        w = self.win; w.settings["auto_start"] = False
        expected = {"PAUSED": ("start", "paused"), "QUEUED": ("show", "queue"), "ERROR": ("retry", "failed"), "STOPPED": ("start", "cancelled"),
                    "COMPLETED": ("show", "already downloaded")}
        for i, (st, (key, word)) in enumerate(expected.items()):
            for r in list(w.db.all()): w.db.delete_download(r["id"])
            TN.clear_rows(w)
            _, path = self.seed(f"d{i}.bin", st); (open(path, "wb").write(b"x") if st == "COMPLETED" else None)
            self.choices.clear(); self.submit(name=f"d{i}.bin")
            head, detail, keys, _ = self.choices[0]
            self.assertEqual(head, "This download link already exists.")
            self.assertEqual(keys[0], key, st); self.assertIn(word, detail.lower(), st)
            self.assertIn(theme.status_label(st), detail)
            self.assertEqual(len(w.db.all()), 1, f"{st}: no duplicate record")
        self.assertIn("open", keys)                                               # completed + file exists -> Open file

    def test_paused_duplicate_resume_and_failed_retry_actually_run(self):
        w = self.win; w.settings["auto_start"] = False
        rid, path = self.seed("p.bin", "PAUSED"); self.reply = "start"; self.submit(name="p.bin")
        self.assertTrue(wait_for(lambda: self.status(rid) == "COMPLETED", 40)); self.assertEqual(sha(path), BLOB_SHA)
        self.assertEqual(len(w.db.all()), 1)
        reset(fail=404); rid2, path2 = self.seed("f.bin", "PAUSED", url=self.srv.url("/other")); w.start_job(rid2, dict(w.db.get(rid2)))
        self.assertTrue(wait_for(lambda: self.status(rid2) == "ERROR", 20)); reset()
        self.reply = "retry"; self.submit(url=self.srv.url("/other"), name="f.bin")
        self.assertTrue(wait_for(lambda: self.status(rid2) == "COMPLETED", 40)); self.assertEqual(len(w.db.all()), 2)

    def test_different_signed_urls_are_not_duplicates(self):
        w = self.win; w.settings["auto_start"] = False
        self.seed("s.bin", "PAUSED", url=self.srv.url("/f?sig=AAA"))
        self.submit(url=self.srv.url("/f?sig=BBB"), name="s2.bin")
        self.assertEqual(self.choices, []); self.assertEqual(len(w.db.all()), 2)
        self.submit(url=self.srv.url("/f?sig=BBB").replace("http://", "HTTP://"), name="s3.bin")     # same link, different spelling
        self.assertEqual(len(self.choices), 1); self.assertEqual(len(w.db.all()), 2)

    def test_duplicate_destination_uses_shared_conflict_dialog(self):
        w = self.win; w.settings["auto_start"] = False; w.settings["conflict_action"] = ""; calls = []
        def fake(parent, path, what="", **kw): calls.append((path, what, kw)); return (self.reply, False, False)
        existing = os.path.join(self.d, "keep.bin"); open(existing, "wb").write(b"PRECIOUS")
        with mock.patch.object(M, "ask_conflict", fake):
            self.reply = "abort"; self.submit(url=self.srv.url("/new-link"), name="keep.bin")
            self.assertEqual(len(w.db.all()), 0); self.assertEqual(open(existing, "rb").read(), b"PRECIOUS")
            self.assertEqual(calls[0][0], existing); self.assertTrue(calls[0][2]["allow_overwrite"])
            self.reply = "rename"; self.submit(url=self.srv.url("/new-link"), name="keep.bin")
            row = w.db.all()[0]; self.assertEqual(row["path"], os.path.join(self.d, "keep (1).bin"))
            self.assertEqual(open(existing, "rb").read(), b"PRECIOUS")
            # a second, different link to a name already used by a *record* (file not written yet) is also protected
            self.submit(url=self.srv.url("/third"), name="keep.bin")
            self.assertEqual(sorted(r["path"] for r in w.db.all()), sorted(os.path.join(self.d, n) for n in ("keep (1).bin", "keep (2).bin")))
            # ... and Overwrite is refused for a name that belongs to a queued/active record
            calls.clear(); self.submit(url=self.srv.url("/fourth"), name="keep (1).bin")
            self.assertFalse(calls[0][2]["allow_overwrite"], "record-only/active names cannot be overwritten")
            # Overwrite of a plain existing file: deleted after the dialog's own confirmation, record points at that path
            other = os.path.join(self.d, "other.bin"); open(other, "wb").write(b"OLD"); calls.clear(); self.reply = "overwrite"
            self.submit(url=self.srv.url("/fifth"), name="other.bin")
            self.assertTrue(any(r["path"] == other for r in w.db.all())); self.assertFalse(os.path.exists(other) and open(other, "rb").read() == b"OLD")
            # Remembered answer: no dialog at all; an unusable remembered Overwrite falls back to Rename
            calls.clear(); w.settings["conflict_action"] = "rename"; self.submit(url=self.srv.url("/sixth"), name="keep.bin")
            self.assertEqual(calls, []); self.assertEqual(len(w.db.all()), 5)
            w.settings["conflict_action"] = "overwrite"; self.submit(url=self.srv.url("/seventh"), name="keep (1).bin")
            self.assertEqual((calls, len(w.db.all())), ([], 6)); self.assertEqual(open(existing, "rb").read(), b"PRECIOUS")
        w.settings["conflict_action"] = ""

class ConnectionSettings(Base):
    def test_per_download_value_stored_shown_and_used(self):
        w = self.win; reset(throttle=0.02)
        self.submit(name="c16.bin", conns=16)
        row = w.db.all()[0]; rid = row["id"]
        self.assertEqual(row["connections"], 16)
        self.assertEqual(w.conn.value(), 8, "the footer shows the global default for NEW downloads")
        self.assertTrue(wait_for(lambda: rid in w.jobs))
        self.assertEqual(w.jobs[rid].connections, 16)
        self.assertEqual(w._connections_text(rid), "16 connections")
        w.table.selectRow(w.table.row_of(rid)); pump(0.05)
        self.assertIn("16 connections", w.sel_info.text())
        conns = {l: v for l, v, _ in w._details_data(rid)["tabs"]["Connections"]}; self.assertEqual(conns["Connections"], "16 connections")

    def test_changing_defaults_does_not_touch_running_or_stored_downloads(self):
        w = self.win; reset(throttle=0.25)
        self.submit(name="run.bin", conns=6); rid = w.db.all()[0]["id"]
        self.assertTrue(wait_for(lambda: w.jobs.get(rid) and w.jobs[rid].info.done > 0))
        s = dict(w.settings); s.update(connections=2, simultaneous_files=1, max_speed_kbps=50)
        w.apply_settings(s); pump(0.3)
        self.assertEqual(w.jobs[rid].connections, 6); self.assertEqual(self.status(rid), "DOWNLOADING")
        self.assertEqual(w.db.get(rid)["connections"], 6)
        self.assertEqual((w.conn.value(), w.simul.value()), (2, 1))
        w.settings.update(connections=8, simultaneous_files=3, max_speed_kbps=0)   # 50 KB/s must not leak into later tests (the limit is now really enforced)
        w.pause_one(rid); self.assertTrue(wait_for(lambda: self.status(rid) == "PAUSED"))
        w.simul.setValue(3); w.start_one(rid); pump(0.2); self.assertEqual(w.jobs[rid].connections, 6, "resume keeps the per-download value")

    def test_restored_download_uses_its_own_connections_not_global(self):
        w = self.win; w.settings["auto_start"] = False
        self.submit(name="r.bin", conns=11); rid = w.db.all()[0]["id"]
        w2 = M.MainWindow(); pump(0.4)
        try:
            w2.settings["connections"] = 3
            w2.start_job(rid, dict(w2.db.get(rid))); self.assertEqual(w2.jobs[rid].connections, 11)
            w2.jobs[rid].stop(); w2.jobs[rid].wait(5)
        finally:
            w2.close(); pump(0.2)

    def test_old_records_without_value_take_default_once_and_freeze_it(self):
        w = self.win; w.settings.update(auto_start=False, connections=7)
        rid, _ = self.seed("old.bin", "PAUSED"); self.assertEqual(w.db.get(rid)["connections"], 0)
        w.start_job(rid, dict(w.db.get(rid))); self.assertEqual(w.jobs[rid].connections, 7)
        self.assertEqual(w.db.get(rid)["connections"], 7)
        w.jobs[rid].stop(); w.jobs[rid].wait(5)

    def test_worker_cap_is_reported_not_hidden(self):
        w = self.win; w.simul.setValue(16); self.assertEqual(w.simul.value(), 16)
        rid, _ = self.seed("cap.bin", "PAUSED"); w.db.update(rid, connections=32)
        self.assertIn("requested 32", w._connections_text(rid)); self.assertIn("4 connections", w._connections_text(rid))
        w.simul.setValue(3)

    def test_engine_really_opens_the_configured_number_of_connections(self):
        for n in (2, 5):
            reset(throttle=0.02)
            path = os.path.join(self.d, f"n{n}.bin")
            j = Downloader(self.srv.url(), path, connections=n); j.start()
            self.assertTrue(wait_for(lambda: not j.is_alive(), 60)); self.assertEqual(j.info.status, "COMPLETED")
            self.assertEqual(sha(path), BLOB_SHA)
            # v10.5: ranges are small and pulled from a shared queue, so there are >= n requests; the CONNECTION count is what is configured.
            self.assertGreaterEqual(MODE["gets"], n, f"server received {MODE['gets']} range requests for connections={n}")
            self.assertGreaterEqual(MODE["peak"], n - 1, "the connections really run in parallel")
            self.assertLessEqual(MODE["peak"], n, "never more simultaneous connections than configured")

    def test_validation_limits(self):
        j = Downloader(self.srv.url(), os.path.join(self.d, "x"), connections=999); self.assertEqual(j.connections, 32)
        j = Downloader(self.srv.url(), os.path.join(self.d, "x"), connections=0); self.assertEqual(j.connections, 1)
        from gui.new_download import NewDownloadDialog
        FakeDialogReal = NewDownloadDialog("", self.win); self.assertEqual((FakeDialogReal.connections.minimum(), FakeDialogReal.connections.maximum()), (1, 32)); FakeDialogReal.close()


class SettingsTests(Base):
    def setUp(self):
        super().setUp(); self._saved = dict(self.win.settings)

    def tearDown(self):
        self.win.apply_settings(dict(self._saved)); super().tearDown()

    def open(self):
        return SettingsDialog(self.win.settings, self.win)

    def test_open_and_close_changes_nothing_and_keeps_unlisted_keys(self):
        w = self.win; w.settings["delete_action"] = "remove_only"; before = dict(w.settings)
        d = self.open(); d.reject(); self.assertEqual(w.settings, before)
        d = self.open(); d.accept_settings(); self.assertEqual(w.settings["delete_action"], "remove_only")
        after = dict(w.settings); self.assertEqual({k: after[k] for k in before}, before, "Save without edits must not change any value")
        w.settings["delete_action"] = ""

    def test_every_control_saves_loads_and_applies(self):
        w = self.win; d = self.open()
        folder = os.path.join(self.d, "dl"); tdir = os.path.join(self.d, "tor")
        d.dl_dir.edit.setText(folder); d.ask_location.setChecked(False); d.auto_start.setChecked(False)
        d.open_completed.setChecked(True); d.confirm_delete.setChecked(False); d.accept_drop.setChecked(False)
        d.connections.setValue(5); d.simultaneous.setValue(2); d.max_speed.setValue(300); d.read_timeout.setValue(33); d.max_retries.setValue(1)
        d.multi_network.setChecked(False); d.net_interval.setValue(7)
        d.torrent_dir.edit.setText(tdir); d.torrent_port.setValue(7001); d.torrent_down.setValue(400); d.torrent_up.setValue(80); d.torrent_auto.setChecked(True)
        d.defender.setChecked(True)
        d.accept_settings()
        saved = json.loads(Path(M.SETTINGS_PATH).read_text())
        expect = dict(download_dir=folder, ask_location=False, auto_start=False, open_completed=True, confirm_delete=False, accept_url_drop=False,
                      connections=5, simultaneous_files=2, max_speed_kbps=300, read_timeout=33, max_retries=1, multi_network=False,
                      network_interval=7, torrent_dir=tdir, torrent_port=7001, torrent_down_kbps=400, torrent_up_kbps=80,
                      torrent_auto_resume=True, defender_scan=True)
        for k, v in expect.items(): self.assertEqual(saved[k], v, k)
        self.assertEqual(M.load_settings()["torrent_port"], 7001)
        d2 = self.open()                                                        # reopen: shows what was saved
        self.assertEqual((d2.dl_dir.edit.text(), d2.ask_location.isChecked(), d2.connections.value(), d2.read_timeout.value(), d2.torrent_up.value()), (folder, False, 5, 33, 80))
        # applied: footer, timer, engine
        self.assertEqual((w.conn.value(), w.simul.value()), (5, 2)); self.assertEqual(w.net_timer.interval(), 7000)
        from gui.new_download import NewDownloadDialog
        nd = NewDownloadDialog("", w); self.assertEqual(nd.dir_edit.text(), folder); self.assertTrue(nd.dir_box.isHidden(), "ask_location off hides the folder picker")
        self.assertEqual(nd.connections.value(), 5); nd.close()
        j_args = {}
        real = M.Downloader
        class Spy(real):
            def __init__(s, *a, **k): j_args.update(k); super().__init__(*a, **k)
        rid, _ = self.seed("spy.bin", "PAUSED")
        with mock.patch.object(M, "Downloader", Spy): w.start_job(rid, dict(w.db.get(rid)))
        self.assertEqual((j_args["read_timeout"], j_args["max_retries"]), (33, 1)); self.assertEqual(w.jobs[rid].speed_limit_bps, 300 * 1024)
        self.assertEqual(w.jobs[rid].MAX_RETRIES, 1)
        w.jobs[rid].stop(); w.jobs[rid].wait(5)
        if w.torrent_manager is not None:
            st = w.torrent_manager.session.get_settings()
            self.assertEqual((st["download_rate_limit"], st["upload_rate_limit"]), (400 * 1024, 80 * 1024))
            self.assertIn(":7001", st["listen_interfaces"])

    def test_invalid_folder_is_reported_and_nothing_saved(self):
        w = self.win; before = dict(w.settings)
        blocker = os.path.join(self.d, "file.txt"); open(blocker, "w").write("x")
        d = self.open(); d.dl_dir.edit.setText(os.path.join(blocker, "sub")); d.connections.setValue(2)
        d.accept_settings()
        self.assertIn("Default download folder", d.error.text()); self.assertEqual(w.settings, before)

    def test_worker_ceiling_is_explained(self):
        d = self.open(); d.simultaneous.setValue(16); d.connections.setValue(32); d.accept_settings()
        self.assertTrue(any("Connections adjusted" in i or "lowered" in i for i in self.infos)); self.assertEqual(self.win.settings["connections"], 4)
        self.win.apply_settings(dict(self.win.settings, connections=8, simultaneous_files=3))

    def test_forget_remembered_delete_answer(self):
        self.win.settings["delete_action"] = "delete_files"
        d = self.open(); d.settings["delete_action"] = ""; d.accept_settings(); self.assertEqual(self.win.settings["delete_action"], "")

    def test_all_pages_exist_and_no_decorative_controls(self):
        d = self.open(); self.assertEqual([d.nav.item(i).text() for i in range(d.nav.count())], ["General", "Appearance", "Downloads", "Network", "Torrents", "Advanced"])
        self.assertFalse(hasattr(d, "debug"), "the old do-nothing 'detailed errors' checkbox is gone")



def _close(a, b, tol=60):
    """True if two colors are within `tol` per channel - a painted accent pixel is rarely
    exactly the token value, so a tolerance is needed to count it."""
    return (abs(a.red() - b.red()) <= tol and abs(a.green() - b.green()) <= tol
            and abs(a.blue() - b.blue()) <= tol)

class CheckboxTests(Base):
    def test_style_states_and_assets(self):
        qss = theme.build_qss()
        for token in ("QCheckBox::indicator:checked", "QCheckBox::indicator:hover", "QCheckBox::indicator:disabled", "QCheckBox::indicator:focus", "QRadioButton::indicator:checked"):
            self.assertIn(token, qss)
        self.assertNotIn("QCheckBox::indicator:checked {{ background: " + theme.ACCENT + ";", qss)
        for n in ("check_on", "check_off"): self.assertTrue((Path.home() / "NexusDownloader" / "cache" / f"{n}.png").exists())

    def render(self, checked, enabled=True, focus=False):
        from PySide6.QtWidgets import QDialog, QVBoxLayout
        dlg = QDialog(); lay = QVBoxLayout(dlg); c = make_check("Sample", checked); c.setEnabled(enabled); c.setFocusPolicy(Qt.FocusPolicy.StrongFocus if focus else Qt.FocusPolicy.NoFocus); lay.addWidget(c); dlg.show(); pump(0.05)
        if focus: c.setFocus(Qt.FocusReason.TabFocusReason)
        pump(0.05); img = c.grab().toImage(); dlg.close(); return img

    def lum(self, px): c = QColor(px); return 0.3 * c.red() + 0.59 * c.green() + 0.11 * c.blue()

    def test_checked_is_dark_with_accent_not_bright_fill(self):
        """The checked indicator uses the *active* accent (not a hardcoded cyan), and the
        accent only appears when checked. Works for every preset, not just cyan."""
        from gui.theme import ACCENT_PRESETS
        for name, (main, _companion) in ACCENT_PRESETS.items():
            with self.subTest(accent=name):
                theme.apply_accent(name)
                off, on = self.render(False), self.render(True)
                cy = on.height() // 2; x = 9                                   # inside the 18px indicator
                self.assertLess(self.lum(off.pixel(x, cy)), 40, f"{name}: unchecked indicator is a dark box")
                self.assertLess(self.lum(on.pixel(4, cy)), 120, f"{name}: checked background stays dark (no bright fill)")
                # The accent color must actually appear in the checked widget (border + checkmark).
                c = QColor(main)
                acc_pixels = [QColor(on.pixel(i, j)) for i in range(on.width() // 3) for j in range(on.height())
                              if _close(QColor(on.pixel(i, j)), c)]
                self.assertGreater(len(acc_pixels), 10, f"{name}: checkmark/border use the active accent {main}")
                off_acc = [QColor(off.pixel(i, j)) for i in range(off.width() // 3) for j in range(off.height())
                           if _close(QColor(off.pixel(i, j)), c)]
                self.assertEqual(len(off_acc), 0, f"{name}: accent appears only when checked")
                dis = self.render(True, enabled=False)
                self.assertLess(sum(1 for i in range(dis.width() // 3) for j in range(dis.height())
                                    if _close(QColor(dis.pixel(i, j)), c)), 3, f"{name}: disabled is muted")
        theme.apply_accent("orange")

    def test_keyboard_and_label_click_and_empty_space(self):
        from PySide6.QtWidgets import QDialog, QVBoxLayout
        dlg = QDialog(); dlg.resize(500, 200); lay = QVBoxLayout(dlg); c = make_check("Option", False); lay.addWidget(c); lay.addStretch(); dlg.show(); pump(0.1)
        c.setFocus(); QTest.keyClick(c, Qt.Key.Key_Space); self.assertTrue(c.isChecked()); QTest.keyClick(c, Qt.Key.Key_Space); self.assertFalse(c.isChecked())
        QTest.mouseClick(c, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(c.width() - 4, c.height() // 2)); self.assertTrue(c.isChecked(), "clicking the label toggles")
        self.assertLess(c.width(), 200, "widget hugs its label")
        far = c.mapTo(dlg, QPoint(0, 0)) + QPoint(400, c.height() // 2)
        self.assertIsNot(dlg.childAt(far), c, "empty space beside the label is not part of the checkbox")
        QTest.mouseClick(dlg, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, far); self.assertTrue(c.isChecked(), "clicking empty space must not toggle")
        dlg.close()

    def test_settings_checkboxes_all_use_compact_policy(self):
        d = SettingsDialog(self.win.settings, self.win)
        boxes = d.findChildren(QCheckBox); self.assertGreaterEqual(len(boxes), 8)
        for b in boxes: self.assertEqual(b.sizePolicy().horizontalPolicy().name, "Fixed", b.text())


class ActionSystem(Base):
    def test_row_has_only_more_control_and_no_selected_toolbar(self):
        w = self.win; rid, _ = self.seed("a.bin", "PAUSED")
        cell = w.table.visualRect(w.table.model().index(w.table.row_of(rid), C_ACT))
        self.assertEqual([n for n, _ in w.table.itemDelegate().button_rects(cell)], ["more"])
        for name in ("btn_start", "btn_pause", "btn_stop", "btn_retry", "btn_remove"): self.assertFalse(hasattr(w, name), name)
        from PySide6.QtWidgets import QPushButton, QToolButton
        texts = {b.text() for cls in (QPushButton, QToolButton) for b in w.downloads_tab.findChildren(cls)}
        self.assertFalse(texts & {"Start", "Pause", "Stop", "Retry", "Remove"}, "no duplicate action buttons on the page")
        self.assertNotIn("Selected:", [l.text() for l in w.downloads_tab.findChildren(type(w.sel_info))])

    def test_more_menu_is_the_single_action_place_and_matches_state(self):
        w = self.win
        for st, enabled in {"PAUSED": {"start", "stop"}, "DOWNLOADING": {"pause", "stop"}, "ERROR": {"retry"}, "COMPLETED": set(), "STOPPED": {"start"}, "QUEUED": {"start", "pause", "stop"}}.items():
            rid, _ = self.seed(f"{st}.bin", st); m = w._build_more_menu(rid)
            self.assertEqual({k for k in ("start", "pause", "stop", "retry") if m._acts[k].isEnabled()}, enabled, st)
            for k in ("start", "pause", "stop", "retry"): self.assertFalse(m._acts[k].icon().isNull())
            self.assertFalse(m._acts["open"].icon().isNull())

    def test_more_menu_drives_the_real_engine_and_leaves_selection_alone(self):
        w = self.win; reset(throttle=0.02)
        a, pa = self.seed("a.bin", "PAUSED", size=len(BLOB)); b, _ = self.seed("b.bin", "PAUSED", url=self.srv.url("/b"), size=len(BLOB))
        w.table.selectRow(w.table.row_of(b)); pump(0.02)
        w._build_more_menu(a)._acts["start"].trigger()                       # menu on row A while row B is selected
        self.assertTrue(wait_for(lambda: self.status(a) == "DOWNLOADING" and w.jobs[a].info.done > 0))
        self.assertEqual(self.status(b), "PAUSED"); self.assertEqual(w.selected_ids(), [b], "row menu must not act on / change the selection")
        w._build_more_menu(a)._acts["pause"].trigger(); self.assertTrue(wait_for(lambda: self.status(a) == "PAUSED"))
        w._build_more_menu(a)._acts["start"].trigger(); self.assertTrue(wait_for(lambda: self.status(a) == "DOWNLOADING"))
        w._build_more_menu(a)._acts["stop"].trigger(); self.assertTrue(wait_for(lambda: self.status(a) == "STOPPED"))
        reset(); w._build_more_menu(a)._acts["start"].trigger(); self.assertTrue(wait_for(lambda: self.status(a) == "COMPLETED", 40)); self.assertEqual(sha(pa), BLOB_SHA)

    def test_clicking_more_button_opens_menu_without_selecting_or_acting(self):
        w = self.win; t = w.table; a, _ = self.seed("a.bin", "PAUSED"); b, _ = self.seed("b.bin", "COMPLETED", size=1, done=1)
        t.selectRow(t.row_of(b)); pump(0.02); shown = []
        with mock.patch.object(M.MainWindow, "_show_more_menu", lambda self_, rid, pos=None: shown.append(rid)):
            cell = t.visualRect(t.model().index(t.row_of(a), C_ACT)); rect = dict(t.itemDelegate().button_rects(cell))["more"]
            QTest.mouseClick(t.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, rect.center()); pump(0.05)
        self.assertEqual(shown, [a]); self.assertEqual(w.selected_ids(), [b]); self.assertEqual(self.status(a), "PAUSED")

    def test_clicking_more_button_really_opens_the_popup_menu(self):
        """The existing test mocks _show_more_menu; this one uses the real click path and looks for the popup."""
        from PySide6.QtCore import QTimer
        w = self.win; t = w.table; a, _ = self.seed("pop.bin", "PAUSED"); seen = []
        def grab():
            pw = TN.APP.activePopupWidget()
            if pw is not None:
                seen.append([x.text() for x in pw.actions() if x.text()]); pw.close()
        QTimer.singleShot(300, grab)
        cell = t.visualRect(t.model().index(t.row_of(a), C_ACT)); rect = dict(t.itemDelegate().button_rects(cell))["more"]
        QTest.mouseClick(t.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, rect.center()); pump(0.6)
        self.assertTrue(seen and "Remove from list" in seen[0], f"no popup menu appeared after clicking the dots: {seen}")

    def test_more_menu_failure_is_reported_not_swallowed(self):
        w = self.win; a, _ = self.seed("boom.bin", "PAUSED"); msgs = []
        with mock.patch.object(M.MainWindow, "_build_more_menu", side_effect=RuntimeError("boom")), \
                mock.patch.object(QMessageBox, "warning", lambda *x, **k: msgs.append(x[2])):
            w._show_more_menu(a)
        self.assertTrue(msgs and "boom" in msgs[0])

    def test_remove_vs_delete_still_distinct(self):
        w = self.win; rid, path = self.seed("rm.bin", "COMPLETED", size=3, done=3); open(path, "wb").write(b"abc")
        w._build_more_menu(rid)._acts["remove"].trigger(); self.assertTrue(os.path.exists(path)); self.assertIsNone(w.db.get(rid))
        rid, path = self.seed("rm2.bin", "COMPLETED", size=3, done=3); open(path, "wb").write(b"abc")
        with mock.patch.object(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes):
            w._build_more_menu(rid)._acts["delete"].trigger()
        self.assertFalse(os.path.exists(path))


class DetailsDialogTests(Base):
    def open(self, rid, adv=False):
        from gui.details_dialog import DownloadDetailsDialog
        d = DownloadDetailsDialog(lambda a: self.win._details_data(rid, a), self.win); d.show(); pump(0.1); return d

    def values(self, d, tab):
        from PySide6.QtWidgets import QPlainTextEdit
        _keys, widgets, _page = d._pages[tab]
        return {k: (w.toPlainText() if isinstance(w, QPlainTextEdit) else w.text()) for k, w in widgets.items()}

    def test_three_tabs_with_real_values_for_every_status(self):
        w = self.win
        for st in ("DOWNLOADING", "PAUSED", "COMPLETED", "ERROR", "STOPPED", "QUEUED"):
            rid, path = self.seed(f"{st}.bin", st, size=1000, done=400 if st != "COMPLETED" else 1000)
            row_before = dict(w.db.get(rid)); status_before = self.status(rid)
            d = self.open(rid)
            self.assertEqual([d.tabs.tabText(i) for i in range(d.tabs.count())], ["General", "Progress", "Connections"])
            g, p, c = self.values(d, "General"), self.values(d, "Progress"), self.values(d, "Connections")
            self.assertEqual(g["File name"], f"{st}.bin"); self.assertEqual(g["Save to"], path)
            self.assertEqual(g["Download URL"], self.srv.url("/f")); self.assertEqual(g["Status"], theme.status_label(st))
            self.assertNotIn("Priority", g); self.assertNotIn("Source webpage", g); self.assertNotIn("Download ID", g)
            self.assertEqual(p["Progress"], "100.0%" if st == "COMPLETED" else "40.0%")
            self.assertEqual(p["Remaining"], w.fmt_bytes(0) if st == "COMPLETED" else w.fmt_bytes(600)); self.assertEqual(p["Speed"], "—")
            self.assertEqual(c["Per-connection detail"], "Not reported by the download engine")
            self.assertIn({"QUEUED": "Waiting", "PAUSED": "Paused", "STOPPED": "Cancelled", "COMPLETED": "Finished", "ERROR": "Failed"}.get(st, "zzz"), c["Connection status"]) if st != "DOWNLOADING" else None
            pump(1.2)                                               # timer refresh must also be read-only
            d.close(); pump(0.05)
            self.assertEqual((self.status(rid), dict(w.db.get(rid))), (status_before, row_before), f"{st}: opening/closing Details changed something")
            self.assertEqual(len(w.db.all()), w.table.rowCount())

    def test_live_download_shows_real_progress_speed_connections_and_error(self):
        w = self.win; reset(throttle=0.3)
        self.submit(name="live.bin", conns=4); rid = w.db.all()[0]["id"]
        self.assertTrue(wait_for(lambda: w.jobs.get(rid) and w.jobs[rid].info.done > 100000 and w.jobs[rid].info.speed > 0))
        d = self.open(rid); g, p, c = self.values(d, "General"), self.values(d, "Progress"), self.values(d, "Connections")
        self.assertEqual(g["Status"], "Downloading"); self.assertEqual(c["Connections"], "4 connections")
        self.assertIn("Active", c["Connection status"]); self.assertIn("Supported", c["Range requests"])
        self.assertNotEqual(p["Speed"], "—"); self.assertTrue(p["Progress"].endswith("%") and p["Progress"] != "0.0%")
        self.assertEqual(g["Total size"], w.fmt_bytes(len(BLOB)))
        w.jobs[rid].info.error = "boom"; w.table.set_fields(rid, status="ERROR")
        d.refresh(); self.assertEqual(self.values(d, "General")["Error"], "boom")        # the Error row appears only when there is one
        w.jobs[rid].info.error = ""; w.table.set_fields(rid, status="DOWNLOADING"); d.close()

    def test_interfaces_listed_from_real_usage(self):
        w = self.win; rid, _ = self.seed("n.bin", "DOWNLOADING", size=10, done=5)
        class J:
            connections = 2; info = type("I", (), {"status": "DOWNLOADING", "total": 10, "done": 5, "speed": 1.0, "error": "", "final_url": "", "range_supported": True, "tls_verified": False})()
            def network_usage(s): return {"10.0.0.7": 2}
            def is_alive(s): return True
        w.jobs[rid] = J()
        with mock.patch.object(M.MainWindow, "_network_name_for_ip", lambda s, ip: "Wi-Fi"):
            d = self.open(rid); c = self.values(d, "Connections"); d.close()
        self.assertIn("Wi-Fi  (10.0.0.7) — 2 connections", c["Network interfaces used"]); w.jobs.pop(rid)

    def test_long_url_and_path_wrap_selectable_copyable(self):
        from PySide6.QtWidgets import QPlainTextEdit, QPushButton
        w = self.win; url = self.srv.url("/" + "segment/" * 40 + "file.bin?token=" + "A" * 300)
        rid, path = self.seed("long.bin", "PAUSED", url=url)
        d = self.open(rid); d.resize(640, 560); pump(0.1)
        edit = d._pages["General"][1]["Download URL"]
        self.assertIsInstance(edit, QPlainTextEdit); self.assertTrue(edit.isReadOnly()); self.assertEqual(edit.toPlainText(), url)
        self.assertLessEqual(edit.width(), d.width()); self.assertEqual(d.tabs.currentWidget().width() <= d.width(), True)
        self.assertEqual(edit.verticalScrollBar().isVisible() or edit.document().size().height() > 0, True)
        edit.selectAll(); self.assertEqual(edit.textCursor().selectedText(), url)
        for b in d._pages["General"][2].findChildren(QPushButton):
            if b.accessibleName() == "Copy Download URL": b.click()
        self.assertEqual(QGuiApplication.clipboard().text(), url)
        d.close()

    def test_advanced_shows_id_and_removed_download_does_not_crash(self):
        w = self.win; rid, _ = self.seed("adv.bin", "PAUSED")
        d = self.open(rid); d.adv_box.setChecked(True); pump(0.1)
        self.assertEqual(self.values(d, "General")["Download ID"], str(rid))
        w.db.delete_download(rid); pump(1.2); d.close()                  # provider returns None -> timer stops quietly
        self.assertFalse(d.timer.isActive())

    def test_double_click_and_menu_open_it_without_side_effects(self):
        w = self.win; rid, _ = self.seed("dc.bin", "PAUSED"); opened = []
        with mock.patch.object(M, "DownloadDetailsDialog", lambda prov, p: type("D", (), {"exec": lambda s_: opened.append(prov(False)["name"])})()):
            QTest.mouseDClick(w.table.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                              w.table.visualRect(w.table.model().index(w.table.row_of(rid), C_NAME)).center())
            w._build_more_menu(rid)._acts["details"].trigger()
        self.assertEqual(opened, ["dc.bin", "dc.bin"]); self.assertEqual(self.status(rid), "PAUSED"); self.assertEqual(w.db.get(rid)["status"], "PAUSED")


class TableLayoutTests(unittest.TestCase):
    LONG = "The_Elusive_Samurai-S02E01-1080p-HEVC-[HIN-JAP]-PIKAHD.CO-" + "very-long-segment-" * 8 + ".mkv"

    def table(self, width, px=None):
        from gui.download_table import DownloadTable
        t = DownloadTable(); 
        if px:
            f = t.font(); f.setPixelSize(px); t.setFont(f)
        for i, name in enumerate(("a.bin", self.LONG, "mid-length-file-name.zip")):
            t.add_download(i + 1, animate=False)
            t.set_fields(i + 1, name=name, sub="cdn.example.com" if i != 1 else "_ssl.c:993: The handshake operation timed out - retrying in 2s (1/3)",
                         size_text="888.8 MB / 888.8 MB", status="RETRYING" if i == 1 else "DOWNLOADING", pct=6, snap=True, speed_text="888.8 MB/s",
                         eta_text="88d 88h", added_text="2026-10-24 09:17:47", elapsed_text="88m 88s")
        t.setFixedWidth(width); t.show(); pump(0.05); return t

    def check(self, t, label):
        from gui.download_table import C_SIZE, C_STATUS, C_PROG, C_SPEED, C_ETA, C_ADDED, C_ELAPSED
        shown = [c for c in range(1, t.columnCount()) if not t.isColumnHidden(c)]
        total = sum(t.columnWidth(c) for c in shown)
        self.assertLessEqual(total, t.viewport().width() + 1, f"{label}: columns overflow the view")
        self.assertFalse(t.horizontalScrollBar().isVisible(), f"{label}: horizontal scrolling appeared")
        self.assertGreaterEqual(t.columnWidth(C_NAME), t.NAME_MIN_EM * t.fontMetrics().horizontalAdvance("0") - 1, label)
        self.assertTrue(all(c in shown for c in (C_NAME, C_STATUS, C_PROG, C_ACT)), f"{label}: essential column hidden")
        fm = t.fontMetrics()
        for c in shown:                                                   # every visible value fits its column (no ellipsis/overlap)
            if c in (C_NAME, C_ACT, C_PROG, C_STATUS): continue
            for r in range(t.rowCount()):
                need = fm.horizontalAdvance(t.item(r, c).text()) + 20
                self.assertLessEqual(need, t.columnWidth(c), f"{label}: column {c} text '{t.item(r, c).text()}' needs {need}px, has {t.columnWidth(c)}")
        from gui.download_table import R_SUB
        from PySide6.QtGui import QFont, QFontMetrics
        pill = QFont(t.font()); pill.setWeight(QFont.Weight.DemiBold); pill.setPixelSize(max(10, int(fm.height() * 0.86)))
        self.assertLessEqual(QFontMetrics(pill).horizontalAdvance("Downloading") + 32 + 20, t.columnWidth(C_STATUS), f"{label}: status pill clipped")

    def test_wide_window_shows_every_column_fully_even_with_very_long_names(self):
        from gui.download_table import C_ADDED, C_ELAPSED, C_ETA, C_SIZE, C_SPEED
        for px in (None, 16, 20, 26):                                     # 100% .. ~200% font/display scaling
            t = self.table(1500 if (px or 0) < 20 else 1900, px)
            for c in (C_SIZE, C_SPEED, C_ETA, C_ADDED, C_ELAPSED):
                self.assertFalse(t.isColumnHidden(c), f"px={px}: column {c} hidden although the window is wide")
            self.check(t, f"px={px}"); t.close()

    def test_narrow_windows_drop_least_important_columns_without_overlap(self):
        from gui.download_table import C_ADDED
        seen = []
        for width in (1400, 1100, 900, 760, 640):
            for px in (None, 18):
                t = self.table(width, px); self.check(t, f"w={width} px={px}")
                seen.append((px, width, sum(t.isColumnHidden(c) for c in range(1, t.columnCount())))); t.close()
        for px in (None, 18):
            counts = [n for p, w, n in seen if p == px]; self.assertEqual(counts, sorted(counts), "columns only disappear as it narrows")
        self.assertGreater(max(n for *_, n in seen), 0)

    def test_only_name_is_user_resizable_and_others_follow(self):
        from gui.download_table import C_SIZE, C_STATUS, C_ACT
        from PySide6.QtWidgets import QHeaderView
        t = self.table(1500); h = t.horizontalHeader()
        self.assertEqual(h.sectionResizeMode(C_NAME), QHeaderView.ResizeMode.Interactive)
        for c in range(2, t.columnCount()): self.assertEqual(h.sectionResizeMode(c), QHeaderView.ResizeMode.Fixed, c)
        default_name = t.columnWidth(C_NAME); other = {c: t.columnWidth(c) for c in range(2, t.columnCount())}
        self.assertEqual(default_name + sum(other[c] for c in other if not t.isColumnHidden(c)), t.viewport().width(), "Name takes exactly the remaining space")
        h.resizeSection(C_NAME, 300); pump(0.05)                          # user drags the Name boundary
        self.assertEqual(t.columnWidth(C_NAME), 300)
        self.assertGreaterEqual(t.columnWidth(C_SIZE), other[C_SIZE]); self.assertLessEqual(t.columnWidth(C_SIZE), int(other[C_SIZE] * 1.6))
        h.resizeSection(C_NAME, 5000); pump(0.05)                         # too wide: clamped, nothing pushed off-screen
        self.assertLessEqual(t.columnWidth(C_NAME), default_name + 1); self.check(t, "after drag")
        t.setFixedWidth(1700); pump(0.05); self.check(t, "resized after drag"); t.close()

    def test_long_name_is_elided_in_the_row_and_complete_in_tooltip(self):
        from PySide6.QtCore import QEvent, QPoint
        from PySide6.QtGui import QHelpEvent
        t = self.table(1100); name = self.LONG
        shown = {}
        with mock.patch("gui.download_table.QToolTip.showText", lambda pos, text, *a: shown.setdefault("tip", text)):
            idx = t.model().index(1, C_NAME); pos = t.visualRect(idx).center()
            t.viewportEvent(QHelpEvent(QEvent.Type.ToolTip, pos, t.viewport().mapToGlobal(pos)))
        self.assertTrue(shown["tip"].startswith(name), "tooltip carries the complete file name")
        self.assertIn("handshake operation timed out", shown["tip"], "status message is readable in the tooltip")
        t.grab(); self.assertEqual(t.item(1, C_NAME).text(), name, "the stored name is never truncated")
        t.close()

    def test_details_show_complete_long_name(self):
        w = TN.get_window(); w.search.setText("")
        rid = w.db.add("http://x/f", self.LONG, os.path.join(tempfile.mkdtemp(), self.LONG)); 
        try:
            data = w._details_data(rid); name = [v for l, v, _ in data["tabs"]["General"] if l == "File name"][0]
            self.assertEqual(name, self.LONG)
            from gui.details_dialog import DownloadDetailsDialog
            d = DownloadDetailsDialog(lambda a: w._details_data(rid, a), w); d.show(); pump(0.1)
            box = d._pages["General"][1]["File name"]; self.assertEqual(box.toPlainText(), self.LONG); self.assertTrue(box.isReadOnly()); d.close()
        finally:
            w.db.delete_download(rid)


if __name__ == "__main__":
    unittest.main(verbosity=2)
