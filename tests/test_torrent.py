"""Torrent tests: real libtorrent engine against a local throttled seeder (no internet needed)."""
import gc, hashlib, os, sys, tempfile, time, unittest, warnings
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_nexus as TN
from test_nexus import pump
from test_ui import wait_for

from PySide6.QtCore import QMimeData, QPoint, QPointF, QUrl, Qt
from PySide6.QtGui import QDragEnterEvent, QDropEvent
from PySide6.QtWidgets import QFileDialog, QMessageBox
from core import torrent as T
from gui.torrent_dialog import TorrentDialog, validate_torrent_source
from gui.torrent_page import TorrentPage, TorrentTable, C_ACT, C_ETA, C_KEY, C_NAME, C_PATH, C_PROG, C_STATUS

lt = T.lt
M = TN.M
import itertools
SIZE = 4_000_000
_ports = itertools.count(7900 + (os.getpid() % 500) * 2)      # unique per seeder: sessions are released lazily


def make_torrent(root):
    """-> (torrent_path, seed_dir, payload_sha256). 4 MB random file, 64 KiB pieces."""
    seed = Path(root) / "seed"; (seed / "data").mkdir(parents=True)
    blob = os.urandom(SIZE); (seed / "data" / "a.bin").write_bytes(blob)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        fs = lt.file_storage(); lt.add_files(fs, str(seed / "data"))
        ct = lt.create_torrent(fs, piece_size=65536); lt.set_piece_hashes(ct, str(seed))
    tf = Path(root) / "t.torrent"; tf.write_bytes(lt.bencode(ct.generate()))
    return str(tf), str(seed), hashlib.sha256(blob).hexdigest()


MULTI = [("sub/a.bin", 600_000), ("b.bin", 500_000), ("c.bin", 400_000)]


def make_multi(root, v1=True):
    """Nested 3-file torrent 'Pack/...' -> (torrent_path, seed_dir, {relative path: sha256}). v1=False -> hybrid (has .pad files)."""
    seed = Path(root) / "seed"; sha = {}
    for rel, n in MULTI:
        f = seed / "Pack" / rel; f.parent.mkdir(parents=True, exist_ok=True); blob = os.urandom(n); f.write_bytes(blob)
        sha[f"Pack/{rel}"] = hashlib.sha256(blob).hexdigest()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        fs = lt.file_storage(); lt.add_files(fs, str(seed / "Pack"))
        ct = lt.create_torrent(fs, piece_size=32768, flags=lt.create_torrent.v1_only if v1 else 0); lt.set_piece_hashes(ct, str(seed))
    tf = Path(root) / "pack.torrent"; tf.write_bytes(lt.bencode(ct.generate()))
    return str(tf), str(seed), sha


def sha_of(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def on_disk(root):
    """Real files below root, ignoring libtorrent's hidden '.<hash>.parts' scratch file (pieces shared with skipped files)."""
    return sorted(str(p.relative_to(root)).replace(os.sep, "/") for p in Path(root).rglob("*") if p.is_file() and not p.name.startswith("."))


class Seeder:
    """Local seed session, upload-throttled so tests can observe a half-finished download."""
    def __init__(self, tfile, seed_dir, limit=300_000):
        self.port = next(_ports)
        self.s = lt.session({"listen_interfaces": f"127.0.0.1:{self.port}", "enable_dht": False, "enable_lsd": False})
        p = lt.add_torrent_params(); p.ti = lt.torrent_info(tfile); p.save_path = seed_dir
        self.h = self.s.add_torrent(p); self.h.set_upload_limit(limit)
        wait_for(lambda: self.h.status().is_seeding, 10)

    def unlimit(self): self.h.set_upload_limit(0)
    def peer(self, handle): handle.connect_peer(("127.0.0.1", self.port))


@unittest.skipIf(lt is None, "libtorrent not installed")
class EngineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.tf, self.seed_dir, self.sha = make_torrent(self.tmp)
        self.seeder = Seeder(self.tf, self.seed_dir)
        self.m = T.TorrentManager(0, cache_dir=os.path.join(self.tmp, "cache"))
        self.dl = os.path.join(self.tmp, "dl")
        self.key = T.TorrentManager.info_hash_of(self.tf)
        self.addCleanup(self._release)

    def _release(self):
        """Drop every libtorrent session now; TestCase instances would otherwise keep them (and threads) alive."""
        for m in (self.m, getattr(self, "m2", None)):
            if m is not None: m.session.pause(); m.session = m.handles = None
        self.seeder.s.pause(); self.seeder.s = self.seeder.h = None; self.m = self.m2 = self.seeder = None; gc.collect()

    def wait_state(self, key, pred, timeout=20):
        return wait_for(lambda: pred(self.m.status(key)), timeout)

    def test_invalid_sources_rejected_cleanly(self):
        for bad in ["magnet:?xt=urn:btih:zzz", "magnet:?dn=nohash", str(Path(self.tmp) / "missing.torrent"),
                    "http://x/y.zip", str(Path(self.tmp) / "seed" / "data" / "a.bin")]:
            with self.assertRaises(ValueError, msg=bad):
                self.m.add(bad, self.dl)
        self.assertEqual(self.m.handles, {})
        (Path(self.tmp) / "junk.torrent").write_text("not bencode")
        with self.assertRaises(ValueError): self.m.add(str(Path(self.tmp) / "junk.torrent"), self.dl)

    def test_no_duplicate_session_file_or_magnet(self):
        key, _ = self.m.add(self.tf, self.dl)
        self.assertEqual(key, self.key)
        with self.assertRaises(ValueError): self.m.add(self.tf, self.dl)
        with self.assertRaises(ValueError): self.m.add(f"magnet:?xt=urn:btih:{key}", self.dl)     # same torrent, other form
        self.assertEqual(len(self.m.session.get_torrents()), 1)

    def test_download_pause_resume_complete_with_real_data(self):
        key, h = self.m.add(self.tf, self.dl); self.seeder.peer(h)
        self.assertTrue(self.wait_state(key, lambda s: s.progress > 3 and s.download_rate > 0 and s.state == "DOWNLOADING"))
        mid = self.m.status(key)
        self.assertLess(mid.progress, 100); self.assertGreater(mid.download_rate, 0); self.assertGreaterEqual(mid.peers, 1)
        self.m.pause(key); self.assertTrue(self.wait_state(key, lambda s: s.state == "PAUSED"))
        p1 = self.m.status(key); time.sleep(1.5); p2 = self.m.status(key)
        self.assertEqual((p2.download_rate, p2.upload_rate), (0.0, 0.0))
        self.assertEqual(p1.done, p2.done, "paused torrent must not advance")
        self.assertTrue(self.m.is_paused(key))
        self.m.resume(key); self.seeder.unlimit()
        self.assertTrue(self.wait_state(key, lambda s: s.state == "SEEDING", 30))
        done = self.m.status(key)
        self.assertEqual((done.progress, done.done, done.total), (100.0, SIZE, SIZE))
        self.assertEqual(hashlib.sha256((Path(self.dl) / "data" / "a.bin").read_bytes()).hexdigest(), self.sha)
        self.assertFalse(self.m.is_paused(key))

    def test_magnet_metadata_cached_and_offline_restore(self):
        key, h = self.m.add(f"magnet:?xt=urn:btih:{self.key}&dn=data", self.dl)
        self.assertEqual(self.m.status(key).state, "METADATA")
        self.assertIsNone(self.m.cache_metadata(key), "no metadata yet -> nothing to cache")
        self.seeder.unlimit(); self.seeder.peer(h)
        self.assertTrue(self.wait_state(key, lambda s: s.state == "SEEDING", 30))
        cached = self.m.cache_metadata(key)
        self.assertTrue(cached and os.path.isfile(cached))
        self.assertEqual(T.TorrentManager.info_hash_of(cached), key)
        # "restart": new engine, no peers, no DHT reachable -> restores from the cached copy, paused, from disk
        m2 = self.m2 = T.TorrentManager(0, cache_dir=os.path.join(self.tmp, "cache"))
        k2, _ = m2.add(cached, self.dl, paused=True)
        self.assertEqual(k2, key)
        self.assertTrue(wait_for(lambda: m2.status(k2).state == "PAUSED" and m2.status(k2).name == "data", 10))
        m2.resume(k2)
        self.assertTrue(wait_for(lambda: m2.status(k2).state == "SEEDING" and m2.status(k2).progress == 100.0, 20))

    def test_remove_keeps_files_or_deletes_them(self):
        key, h = self.m.add(self.tf, self.dl); self.seeder.unlimit(); self.seeder.peer(h)
        self.assertTrue(self.wait_state(key, lambda s: s.state == "SEEDING", 30))
        self.m.cache_metadata(key); cache = Path(self.tmp) / "cache" / f"{key}.torrent"; self.assertTrue(cache.exists())
        self.assertTrue(self.m.remove(key, False))
        self.assertTrue((Path(self.dl) / "data" / "a.bin").exists(), "plain remove must keep data")
        self.assertFalse(cache.exists()); self.assertEqual(self.m.handles, {})
        key, h = self.m.add(self.tf, self.dl)                                        # re-add (data already there)
        self.assertTrue(self.wait_state(key, lambda s: s.state == "SEEDING", 20))
        self.assertTrue(self.m.remove(key, True))
        self.assertTrue(wait_for(lambda: not (Path(self.dl) / "data" / "a.bin").exists(), 10), "delete+files removes data")

    def test_state_is_not_auto_managed(self):
        _, h = self.m.add(self.tf, self.dl, paused=True)
        self.assertFalse(h.flags() & lt.torrent_flags.auto_managed)
        self.assertEqual(self.m.status(self.key).state, "PAUSED")


@unittest.skipIf(lt is None, "libtorrent not installed")
class TorrentUiTests(unittest.TestCase):
    """Real MainWindow: Add Torrent -> table row -> Pause / Resume / Remove / Delete buttons -> DB -> restore."""
    @classmethod
    def setUpClass(cls):
        cls.msgs = []
        cls._p = [mock.patch.object(QMessageBox, n, lambda *a, **k: cls.msgs.append(str(a[1:]))) for n in ("warning", "information", "critical")]
        for p in cls._p: p.start()
        cls.win = TN.get_window()

    @classmethod
    def tearDownClass(cls):
        for p in cls._p: p.stop()

    def setUp(self):
        w = self.win; self.tmp = tempfile.mkdtemp()
        self.tf, self.seed_dir, self.sha = make_torrent(self.tmp)
        self.seeder = Seeder(self.tf, self.seed_dir)
        self.key = T.TorrentManager.info_hash_of(self.tf); self.dl = os.path.join(self.tmp, "dl")
        w.torrent_manager.cache_dir = Path(self.tmp) / "cache"
        w.settings["torrent_auto_resume"] = False
        self.msgs.clear()

    def tearDown(self):
        w = self.win; self.seeder.s.pause(); self.seeder.s = self.seeder.h = self.seeder = None; gc.collect()
        for k in list(w.torrent_manager.handles): w.torrent_manager.remove(k, False)
        for r in w.db.all_torrents(): w.db.delete_torrent(r["info_hash"])
        w.torrent_table.setRowCount(0); w.nav_buttons["downloads"].click()     # do not leak the Torrents tab into later tests

    def row(self):
        t = self.win.torrent_table
        return next((r for r in range(t.rowCount()) if t.item(r, 9) and t.item(r, 9).text() == self.key), None)

    def status_text(self):
        r = self.row(); return self.win.torrent_table.cellWidget(r, 2).status if r is not None else None

    def select(self):
        self.win.torrent_table.selectRow(self.row())

    def test_full_lifecycle_through_ui(self):
        w = self.win
        w.add_torrent_source(self.tf, self.dl)
        self.assertIsNotNone(self.row()); self.assertIsNotNone(w.db.get_torrent(self.key))
        self.seeder.peer(w.torrent_manager.handles[self.key])
        self.assertTrue(wait_for(lambda: self.status_text() == "DOWNLOADING" and w.db.get_torrent(self.key)["downloaded"] > 0, 20))
        self.assertEqual(w.torrent_table.item(self.row(), 5).text(), "1", "peers column shows the real peer")
        # duplicate: same file, and magnet form -> no second row/session, chooser shown
        with mock.patch.object(M, "ask_choice", return_value="show") as ask:
            w.add_torrent_source(self.tf, self.dl); w.add_torrent_source(f"magnet:?xt=urn:btih:{self.key}", self.dl)
            self.assertEqual(ask.call_count, 2)
        self.assertEqual((w.torrent_table.rowCount(), len(w.torrent_manager.session.get_torrents())), (1, 1))
        w.nav_buttons["torrents"].click()
        self.select(); w.pause_torrent()
        self.assertTrue(wait_for(lambda: self.status_text() == "PAUSED" and w.db.get_torrent(self.key)["status"] == "PAUSED", 8))
        self.assertEqual(w.torrent_table.item(self.row(), 3).text(), w.fmt_bytes(0) + "/s")
        self.select(); w.resume_torrent(); self.seeder.unlimit()
        self.assertTrue(wait_for(lambda: self.status_text() == "SEEDING", 30))
        row = w.db.get_torrent(self.key)
        self.assertTrue(row["completed"]); self.assertEqual((row["downloaded"], row["total"]), (SIZE, SIZE))
        self.assertTrue(wait_for(lambda: w.db.get_torrent(self.key)["source"].endswith(f"{self.key}.torrent"), 5), "metadata persisted")
        self.assertEqual(hashlib.sha256((Path(self.dl) / "data" / "a.bin").read_bytes()).hexdigest(), self.sha)

        # restart: second window restores from DB, paused (auto-resume off), no duplicate, no warning
        with mock.patch.object(M.MainWindow, "_start_queued_jobs", lambda s: None):
            old = w.torrent_manager; old.session.remove_torrent(old.handles[self.key])      # engine goes away, files/cache stay
            w.torrent_manager = T.TorrentManager(0, cache_dir=old.cache_dir)               # fresh engine = app restart
            w.torrent_table.setRowCount(0)
            w._restore_torrents(); pump(0.3)
            self.assertEqual(w.torrent_table.rowCount(), 1)
            self.assertTrue(wait_for(lambda: self.status_text() == "PAUSED", 8))
            self.assertEqual(self.msgs, [])
        self.select(); w.resume_torrent()
        self.assertTrue(wait_for(lambda: self.status_text() == "SEEDING", 20), "restored torrent re-verifies data and seeds")

        # remove from list keeps data; row/DB/session gone
        self.select(); w.remove_torrent()
        self.assertIsNone(self.row()); self.assertIsNone(w.db.get_torrent(self.key)); self.assertEqual(w.torrent_manager.handles, {})
        self.assertTrue((Path(self.dl) / "data" / "a.bin").exists())

    def test_delete_torrent_and_files(self):
        w = self.win; w.add_torrent_source(self.tf, self.dl); self.seeder.unlimit(); self.seeder.peer(w.torrent_manager.handles[self.key])
        self.assertTrue(wait_for(lambda: self.status_text() == "SEEDING", 30))
        self.select()
        with mock.patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.No):
            w.delete_torrent_files()
        self.assertIsNotNone(self.row())
        self.select()
        with mock.patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes):
            w.delete_torrent_files()
        self.assertIsNone(self.row()); self.assertIsNone(w.db.get_torrent(self.key))
        self.assertTrue(wait_for(lambda: not (Path(self.dl) / "data" / "a.bin").exists(), 10))

    def test_invalid_magnet_reports_error_and_adds_nothing(self):
        w = self.win; w.add_torrent_source("magnet:?xt=urn:btih:nothex", self.dl)
        self.assertEqual(w.torrent_table.rowCount(), 0); self.assertEqual(w.db.all_torrents(), [])
        self.assertTrue(any("Invalid magnet" in m for m in self.msgs), self.msgs)

    def test_magnet_without_peers_waits_for_metadata_safely(self):
        w = self.win; w.add_torrent_source(f"magnet:?xt=urn:btih:{self.key}&dn=data", self.dl)
        self.assertEqual(self.status_text(), "METADATA")
        self.assertEqual(w.db.get_torrent(self.key)["source"].startswith("magnet:"), True)
        pump(1.2); self.assertEqual(self.status_text(), "METADATA")        # no fake progress while nothing is known
        self.assertEqual(w.torrent_table.item(self.row(), 1).text(), "—")


def mime_for(*paths_or_text):
    md = QMimeData()
    urls = [QUrl.fromLocalFile(p) for p in paths_or_text if os.path.exists(p)]
    if urls: md.setUrls(urls)
    else: md.setText(paths_or_text[0])
    return md


def drop(widget, md, enter_only=False):
    pos = QPointF(10, 10)
    ev = QDragEnterEvent(pos.toPoint(), Qt.DropAction.CopyAction, md, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    widget.dragEnterEvent(ev)
    if enter_only: return ev
    d = QDropEvent(pos, Qt.DropAction.CopyAction, md, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    widget.dropEvent(d); return ev


@unittest.skipIf(lt is None, "libtorrent not installed")
class AddDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.win = TN.get_window()

    def setUp(self):
        self.tmp = tempfile.mkdtemp(); self.tf, _, _ = make_torrent(self.tmp)
        self.junk = os.path.join(self.tmp, "junk.torrent"); Path(self.junk).write_text("nope")
        self.d = TorrentDialog(self.win); self.d.show()

    def tearDown(self): self.d.close()

    def test_browse_opens_file_picker_with_filters_and_fills_path(self):
        with mock.patch.object(QFileDialog, "getOpenFileName", return_value=(self.tf, "")) as g:
            self.d.browse_file.click()
        self.assertTrue(g.called); self.assertEqual(g.call_args.args[0], self.d)                  # parented to the dialog
        self.assertIn("*.torrent", g.call_args.args[3]); self.assertIn("All files (*.*)", g.call_args.args[3])
        self.assertEqual(self.d.source.text(), os.path.normpath(self.tf)); self.assertTrue(self.d.ok.isEnabled())
        self.assertEqual(self.d.data()["source"], os.path.normpath(self.tf))

    def test_cancel_keeps_previous_selection(self):
        self.d.source.setText(self.tf)
        with mock.patch.object(QFileDialog, "getOpenFileName", return_value=("", "")):
            self.d.browse_file.click()
        self.assertEqual(self.d.source.text(), self.tf)

    def test_folder_button_picks_save_folder_not_torrent(self):
        with mock.patch.object(QFileDialog, "getExistingDirectory", return_value=self.tmp) as g, \
             mock.patch.object(QFileDialog, "getOpenFileName") as f:
            self.d.browse_dir.click()
        self.assertTrue(g.called); self.assertFalse(f.called); self.assertEqual(self.d.save.text(), os.path.normpath(self.tmp))

    def test_picker_failure_is_reported_not_swallowed(self):
        with mock.patch.object(QFileDialog, "getOpenFileName", side_effect=OSError("boom")), \
             mock.patch.object(QMessageBox, "warning") as w:
            self.d.browse_file.click()
        self.assertIn("boom", w.call_args.args[2])

    def test_validation_blocks_bad_input_with_clear_messages(self):
        for text, frag in [("", "Paste"), ("hello", "magnet"), ("magnet:?dn=x", "info-hash"), (self.junk, "Not a valid .torrent"),
                           (os.path.join(self.tmp, "gone.torrent"), "does not exist")]:
            self.d.source.setText(text)
            self.assertFalse(self.d.ok.isEnabled(), text); self.assertIn(frag, self.d.kind.text(), text)
            self.d.accept(); self.assertTrue(self.d.isVisible(), "invalid input must not close the dialog")
        self.d.source.setText(self.tf); self.assertTrue(self.d.ok.isEnabled()); self.assertEqual(validate_torrent_source(self.tf)[0], True)
        self.d.source.setText("magnet:?xt=urn:btih:" + "a" * 40)                       # valid magnet: metadata still loading -> no fake list, can't start yet
        self.assertFalse(self.d.ok.isEnabled()); self.assertIn("Fetching metadata", self.d.placeholder.text()); self.assertIsNone(self.d.meta)

    def test_drop_on_dialog_selects_file_and_rejects_others(self):
        drop(self.d, mime_for(self.tf)); self.assertEqual(self.d.source.text(), os.path.normpath(self.tf))
        self.d.source.setText("")
        other = os.path.join(self.tmp, "x.zip"); Path(other).write_text("z")
        ev = drop(self.d, mime_for(other)); self.assertFalse(ev.isAccepted()); self.assertEqual(self.d.source.text(), "")
        drop(self.d, mime_for("magnet:?xt=urn:btih:" + "b" * 40)); self.assertTrue(self.d.source.text().startswith("magnet:"))

    def test_prefilled_and_fits_on_screen(self):
        d = TorrentDialog(self.win, self.tf); d.show(); pump(0.1)
        self.assertEqual(d.source.text(), self.tf); self.assertTrue(d.ok.isEnabled())
        self.assertTrue(d.rect().contains(d.ok.geometry()), "Download button must be inside the dialog")
        d.close()


@unittest.skipIf(lt is None, "libtorrent not installed")
class TorrentPageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._p = [mock.patch.object(QMessageBox, n, lambda *a, **k: None) for n in ("warning", "information", "critical")]
        for p in cls._p: p.start()
        cls.win = TN.get_window()

    @classmethod
    def tearDownClass(cls):
        for p in cls._p: p.stop()

    def setUp(self):
        self.tmp = tempfile.mkdtemp(); self.tf, _, _ = make_torrent(self.tmp); os.makedirs(os.path.join(self.tmp, "b")); self.tf2, _, _ = make_torrent(os.path.join(self.tmp, "b"))
        self.win.torrent_manager.cache_dir = Path(self.tmp) / "cache"; self.win.nav_buttons["torrents"].click(); pump(0.1)

    def tearDown(self):
        w = self.win
        for k in list(w.torrent_manager.handles): w.torrent_manager.remove(k, False)
        for r in w.db.all_torrents(): w.db.delete_torrent(r["info_hash"])
        w.torrent_table.setRowCount(0); w.nav_buttons["downloads"].click()

    def test_single_add_button_and_empty_state(self):
        w = self.win
        self.assertTrue(w.add_torrent_btn.isVisibleTo(w)); self.assertFalse(w.add_btn.isVisibleTo(w), "no second Add button on the torrent page")
        self.assertIs(w.torrent_page.stack.currentWidget(), w.torrent_page.empty)
        w.add_torrent_source(self.tf, os.path.join(self.tmp, "dl"))
        self.assertIs(w.torrent_page.stack.currentWidget(), w.torrent_table)
        w._torrent_tick(); self.assertIn("1 torrent", w.torrent_page.summary.text())
        w.remove_torrent(w.torrent_table.key_at(0))
        self.assertIs(w.torrent_page.stack.currentWidget(), w.torrent_page.empty)
        w.nav_buttons["downloads"].click(); self.assertTrue(w.add_btn.isVisibleTo(w)); self.assertFalse(w.add_torrent_btn.isVisibleTo(w))

    def test_row_menu_acts_on_its_own_row_not_the_selected_one(self):
        w = self.win; a, b = os.path.join(self.tmp, "dlA"), os.path.join(self.tmp, "dlB")
        w.add_torrent_source(self.tf, a); w.add_torrent_source(self.tf2, b)
        ka, kb = T.TorrentManager.info_hash_of(self.tf), T.TorrentManager.info_hash_of(self.tf2)
        self.assertEqual(w.torrent_table.rowCount(), 2)
        w.torrent_table.selectRow(w.torrent_table.row_of(ka))                      # A selected ...
        m = w._build_torrent_menu(kb)                                              # ... menu opened for B
        self.assertNotIn("resume", m._acts); self.assertTrue(m._acts["pause"].isEnabled())      # running: only Pause is offered
        m._acts["pause"].trigger()
        self.assertTrue(wait_for(lambda: w.torrent_table.cellWidget(w.torrent_table.row_of(kb), C_STATUS).status == "PAUSED", 8))
        self.assertFalse(w.torrent_manager.is_paused(ka), "the other torrent must be untouched")
        m = w._build_torrent_menu(kb); self.assertTrue(m._acts["resume"].isEnabled()); self.assertNotIn("pause", m._acts)
        m._acts["resume"].trigger(); self.assertFalse(w.torrent_manager.is_paused(kb))
        # per-row button exists in the action column and opens that row's menu
        btn = w.torrent_table.cellWidget(w.torrent_table.row_of(kb), C_ACT); self.assertIsNotNone(btn)
        with mock.patch.object(M.MainWindow, "_build_torrent_menu") as bm:
            bm.return_value.exec = lambda *a: None; btn.click()
        bm.assert_called_once_with(kb)
        # remove from list (data kept) / delete + files via the row menu
        w._build_torrent_menu(ka)._acts["remove"].trigger()
        self.assertIsNone(w.torrent_table.row_of(ka)); self.assertIsNone(w.db.get_torrent(ka)); self.assertIsNotNone(w.torrent_table.row_of(kb))
        with mock.patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes):
            w._build_torrent_menu(kb)._acts["delete"].trigger()
        self.assertEqual(w.torrent_table.rowCount(), 0); self.assertEqual(w.torrent_manager.handles, {})

    def test_magnet_copy_and_context_menu_on_row(self):
        w = self.win; w.add_torrent_source(self.tf, os.path.join(self.tmp, "dl")); k = w.torrent_table.key_at(0)
        self.assertEqual(w.torrent_magnet(k), f"magnet:?xt=urn:btih:{k}" + ("&dn=" + w.torrent_manager.status(k).name if w.torrent_manager.status(k).name != "Torrent" else ""))
        t = w.torrent_table; pos = QPoint(20, t.rowViewportPosition(0) + 5)
        with mock.patch.object(M.MainWindow, "_show_torrent_menu") as sm:
            t.customContextMenuRequested.emit(pos)
        self.assertEqual(sm.call_args.args[0], k)
        with mock.patch.object(M.MainWindow, "_show_torrent_menu") as sm:
            t.customContextMenuRequested.emit(QPoint(20, 5000))                      # empty area -> no menu
        self.assertFalse(sm.called)

    def test_columns_are_responsive_and_key_stays_hidden(self):
        t = TorrentTable(); t.setRowCount(1); t.show()
        for width, hidden_path in ((1500, False), (560, True)):
            t.resize(width, 300); pump(0.1)
            self.assertEqual(t.isColumnHidden(C_PATH), hidden_path, width)
            name_w = t.columnWidth(C_NAME)
            self.assertGreaterEqual(name_w, t.fontMetrics().horizontalAdvance("0") * 15, f"name squeezed at {width}")
            self.assertFalse(t.isColumnHidden(C_STATUS)); self.assertFalse(t.isColumnHidden(C_PROG)); self.assertFalse(t.isColumnHidden(C_ACT))
            self.assertTrue(t.isColumnHidden(C_KEY))
            total = sum(t.columnWidth(c) for c in range(t.columnCount()) if not t.isColumnHidden(c))
            self.assertLessEqual(total, t.viewport().width() + 2, f"columns overflow at {width}")
        t.close()

    def test_drop_on_page_indicator_validation_and_no_double_add(self):
        w = self.win; page = w.torrent_page; got, bad = [], []
        p = mock.patch.object(M.MainWindow, "add_torrent"); p.start(); self.addCleanup(p.stop)     # window's own handler would open a modal
        page.dropped.connect(got.append); page.rejected.connect(lambda: bad.append(1))
        page.show(); ev = drop(page, mime_for(self.tf), enter_only=True)
        self.assertTrue(ev.isAccepted()); self.assertTrue(page.overlay.isVisible(), "drag-over indicator")
        page.dragLeaveEvent(None); self.assertFalse(page.overlay.isVisible())
        drop(page, mime_for(self.tf, self.tf)); self.assertEqual(got, [self.tf]); self.assertFalse(page.overlay.isVisible())
        other = os.path.join(self.tmp, "x.zip"); Path(other).write_text("z")
        ev = drop(page, mime_for(other)); self.assertTrue(ev.isAccepted(), "accepted so it never reaches the HTTP link handler"); self.assertFalse(page.overlay.isVisible())
        pump(0.2)                                                  # let the window's queued handlers run while add_torrent is stubbed
        self.assertEqual((got, bad), ([self.tf], [1]))

    def test_dropped_file_opens_dialog_prefilled_once_and_double_drop_adds_once(self):
        w = self.win; seen = []
        class Fake:
            def __init__(s, parent, source="", manager=None):
                s.source = source; seen.append(source)
            def exec(s):
                w.add_torrent(s.source)                       # re-entrant drop while the dialog is open: ignored
                return 1
            def data(s): return {"source": s.source, "save_path": os.path.join(self_.tmp, "dl"), "paused": False, "priorities": None, "preview": ""}
        self_ = self
        with mock.patch.object(M, "TorrentDialog", Fake), mock.patch.object(M, "ask_choice", return_value="show") as ask:
            w.torrent_page.dropped.emit(self.tf); pump(0.3)
            self.assertEqual(seen, [self.tf]); self.assertEqual(w.torrent_table.rowCount(), 1)
            w.torrent_page.dropped.emit(self.tf); pump(0.3)                                  # same file again
        self.assertEqual((w.torrent_table.rowCount(), len(w.torrent_manager.session.get_torrents()), ask.call_count), (1, 1, 1))

    def test_eta_and_summary_show_real_data_while_downloading(self):
        w = self.win; seeder = Seeder(self.tf, os.path.join(self.tmp, "seed"), limit=300_000)
        w.add_torrent_source(self.tf, os.path.join(self.tmp, "dl")); seeder.peer(w.torrent_manager.handles[T.TorrentManager.info_hash_of(self.tf)])
        self.assertTrue(wait_for(lambda: w.torrent_table.item(0, C_ETA).text() not in ("—", "") and "↓" in w.torrent_page.summary.text(), 25))
        seeder.s.pause(); seeder.s = seeder.h = None


@unittest.skipIf(lt is None, "libtorrent not installed")
class EngineFeatureTests(unittest.TestCase):
    """Preview / selection / rename / resume / recheck / details against a local nested multi-file torrent."""
    def setUp(self):
        self.tmp = tempfile.mkdtemp(); self.tf, self.seed_dir, self.sha = make_multi(self.tmp)
        self.seeder = Seeder(self.tf, self.seed_dir + "", limit=0)
        self.cache = os.path.join(self.tmp, "cache"); self.m = T.TorrentManager(0, cache_dir=self.cache)
        self.key = T.TorrentManager.info_hash_of(self.tf); self.dl = os.path.join(self.tmp, "dl")
        self.names = [f.path for f in self.m.inspect(self.tf).files]
        self.addCleanup(self._release)

    def _release(self):
        for m in (self.m, getattr(self, "m2", None)):
            if m is not None and m.session is not None: m.session.pause()
        self.seeder.s.pause(); self.m = self.m2 = self.seeder.s = self.seeder.h = None; gc.collect()

    def prio(self, **by_suffix):
        meta = self.m.inspect(self.tf); out = [4] * meta.num_files_raw
        for f in meta.files:
            for suf, p in by_suffix.items():
                if f.path.endswith(suf.replace("_", ".")): out[f.index] = p
        return out

    def test_inspect_lists_nested_files_without_padding(self):
        meta = self.m.inspect(self.tf)
        self.assertEqual(sorted((f.path, f.size) for f in meta.files), sorted((f"Pack/{r}", n) for r, n in MULTI))
        self.assertEqual(meta.total, sum(n for _, n in MULTI)); self.assertEqual(meta.name, "Pack")
        self.assertGreater(meta.num_pieces, 0); self.assertEqual(meta.piece_length, 32768)
        hy_dir = os.path.join(self.tmp, "hy"); os.makedirs(hy_dir); hy, _, _ = make_multi(hy_dir, v1=False)
        hm = self.m.inspect(hy); self.assertEqual(len(hm.files), 3); self.assertGreater(hm.num_files_raw, 3, "pad files exist but are hidden")
        with self.assertRaises(ValueError): self.m.inspect(os.path.join(self.tmp, "nope.torrent"))

    def test_file_selection_priorities_and_rename_apply_to_real_paths(self):
        meta = self.m.inspect(self.tf); idx = {f.path: f.index for f in meta.files}
        prios = [0] * meta.num_files_raw; prios[idx["Pack/sub/a.bin"]] = 4; prios[idx["Pack/b.bin"]] = 7
        key, h = self.m.add(self.tf, self.dl, priorities=prios, renames={idx["Pack/b.bin"]: "Pack/b (1).bin"})
        self.seeder.peer(h)
        self.assertTrue(wait_for(lambda: self.m.status(key).state == "SEEDING", 30))
        got = on_disk(self.dl)
        self.assertEqual(got, ["Pack/b (1).bin", "Pack/sub/a.bin"], "c.bin was skipped, b.bin written under its NEW name")
        self.assertEqual(sha_of(Path(self.dl) / "Pack/b (1).bin"), self.sha["Pack/b.bin"])
        self.assertEqual(sha_of(Path(self.dl) / "Pack/sub/a.bin"), self.sha["Pack/sub/a.bin"])
        fl = {f["path"]: f for f in self.m.files(key)}
        self.assertEqual(fl["Pack/c.bin"]["priority"], 0); self.assertEqual(fl["Pack/b (1).bin"]["priority"], 7, "files() reports the renamed path")
        self.assertTrue(1_100_000 <= self.m.status(key).total <= 1_100_000 + 2 * 32768, "wanted bytes = selected files (+ shared edge pieces)")

    def test_magnet_preview_shows_real_metadata_then_adopts_selection(self):
        mag = f"magnet:?xt=urn:btih:{self.key}&dn=Pack"
        pk = self.m.start_preview(mag, self.dl); self.assertEqual(pk, self.key)
        meta, peers = self.m.preview_state(pk); self.assertIsNone(meta, "no file list before real metadata")
        self.assertEqual(self.m.handles, {}, "preview is never listed")
        self.seeder.peer(self.m.previews[pk])
        self.assertTrue(wait_for(lambda: self.m.preview_state(pk)[0] is not None, 20))
        meta, _ = self.m.preview_state(pk); self.assertEqual(sorted(f.path for f in meta.files), sorted(self.names))
        time.sleep(0.5); self.assertEqual([p for p in Path(self.dl).rglob("*") if p.is_file()], [], "preview writes nothing")
        meta_idx = {f.path: f.index for f in meta.files}; prios = [0] * meta.num_files_raw; prios[meta_idx["Pack/c.bin"]] = 4
        dest = os.path.join(self.tmp, "chosen")
        key, h = self.m.add(mag, dest, priorities=prios, preview=pk)
        self.assertEqual((key, self.m.previews), (self.key, {}))
        self.seeder.peer(h)                                                       # (no tracker/DHT in the test swarm)
        self.assertTrue(wait_for(lambda: (Path(dest) / "Pack/c.bin").exists() and self.m.status(key).progress == 100 and self.m.status(key).state == "SEEDING", 30))
        self.assertEqual(on_disk(dest), ["Pack/c.bin"])
        self.assertEqual(sha_of(Path(dest) / "Pack/c.bin"), self.sha["Pack/c.bin"])

    def test_preview_cancel_and_no_peers_stay_clean(self):
        pk = self.m.start_preview(f"magnet:?xt=urn:btih:{self.key}", self.dl)
        pump(1.0); self.assertEqual(self.m.preview_state(pk)[0], None)
        self.m.cancel_preview(pk); self.assertEqual(self.m.previews, {})
        self.assertTrue(wait_for(lambda: len(self.m.session.get_torrents()) == 0, 5))
        with self.assertRaises(ValueError): self.m.start_preview("magnet:?xt=urn:btih:zzz", self.dl)
        key, _ = self.m.add(self.tf, self.dl)
        with self.assertRaises(ValueError): self.m.start_preview(f"magnet:?xt=urn:btih:{key}", self.dl)

    def test_resume_data_survives_restart_with_priorities_and_renames(self):
        meta = self.m.inspect(self.tf); idx = {f.path: f.index for f in meta.files}
        prios = [4] * meta.num_files_raw; prios[idx["Pack/c.bin"]] = 0
        self.seeder.h.set_upload_limit(250_000)
        key, h = self.m.add(self.tf, self.dl, priorities=prios, renames={idx["Pack/b.bin"]: "Pack/renamed.bin"}); self.seeder.peer(h)
        self.assertTrue(wait_for(lambda: 3 < self.m.status(key).progress < 90, 20))
        self.m.pause(key); self.assertTrue(wait_for(lambda: (self.m.poll() or True) and Path(self.cache, f"{key}.resume").exists(), 10))
        before = self.m.status(key).done
        self.m.shutdown(); self.m.session.pause(); self.m.session = None                      # app closes
        m2 = self.m2 = T.TorrentManager(0, cache_dir=self.cache)
        k2, _ = m2.add(self.tf, self.dl, paused=True, restore=True)
        self.assertEqual(k2, key); self.assertTrue(wait_for(lambda: m2.status(k2).state == "PAUSED", 20), m2.status(k2))
        st = m2.status(k2); self.assertGreaterEqual(st.done, before - 4 * 32768, "verified pieces kept, not re-downloaded (only in-flight partial pieces may be lost)")
        fl = {f["path"]: f for f in m2.files(k2)}
        self.assertEqual(fl["Pack/c.bin"]["priority"], 0, "selection restored"); self.assertTrue(1_100_000 <= st.total <= 1_100_000 + 2 * 32768)
        m2.resume(k2); self.seeder.unlimit(); self.seeder.peer(m2.handles[k2])
        self.assertTrue(wait_for(lambda: m2.status(k2).state == "SEEDING", 30))
        self.assertEqual(sha_of(Path(self.dl) / "Pack/renamed.bin"), self.sha["Pack/b.bin"], "rename survived the restart")
        self.assertFalse((Path(self.dl) / "Pack/c.bin").exists())
        m2.session.pause(); m2.session = None

    def test_recheck_detects_corruption_and_missing_data(self):
        key, h = self.m.add(self.tf, self.dl); self.seeder.peer(h)
        self.assertTrue(wait_for(lambda: self.m.status(key).state == "SEEDING", 30))
        self.m.pause(key); pump(0.5)
        f = Path(self.dl) / "Pack/b.bin"; data = bytearray(f.read_bytes()); data[100:200] = b"\0" * 100; f.write_bytes(bytes(data))
        self.m.recheck(key)
        self.assertTrue(wait_for(lambda: self.m.status(key).state != "CHECKING" and self.m.status(key).progress < 100, 20), "hash check finds the bad piece")
        self.assertLess(self.m.status(key).done, 1_500_000)
        self.m.resume(key); self.seeder.peer(self.m.handles[key])
        self.assertTrue(wait_for(lambda: self.m.status(key).state == "SEEDING" and self.m.status(key).progress == 100, 30))
        self.assertEqual(sha_of(f), self.sha["Pack/b.bin"])
        self.m.pause(key); (Path(self.dl) / "Pack/sub/a.bin").unlink(); self.m.recheck(key)       # missing file
        self.assertTrue(wait_for(lambda: self.m.status(key).state != "CHECKING" and self.m.status(key).progress < 100, 20))

    def test_details_report_real_engine_values(self):
        key, h = self.m.add(self.tf, self.dl, paused=True)
        h.add_tracker({"url": "http://127.0.0.1:9/announce"}); self.seeder.h.set_upload_limit(200_000)
        d = self.m.details(key)
        self.assertEqual((d["meta"].total, len(d["meta"].files), d["selected"]), (1_500_000, 3, 3))
        self.assertEqual(d["meta"].piece_length, 32768); self.assertEqual(d["info"].state, "PAUSED"); self.assertEqual(d["info"].eta, -1)
        self.m.resume(key); self.seeder.peer(h)
        self.assertTrue(wait_for(lambda: self.m.details(key)["peer_list"] and self.m.details(key)["info"].download_rate > 0, 20))
        d = self.m.details(key)
        self.assertIn("127.0.0.1", d["peer_list"][0]["addr"]); self.assertGreater(d["info"].eta, 0)
        self.assertGreater(d["pieces_have"], 0); self.assertGreaterEqual(d["connections"], 1)
        self.assertTrue(wait_for(lambda: self.m.details(key)["trackers"] and self.m.details(key)["trackers"][0]["status"] in ("Error", "Working", "Not contacted"), 10))
        self.assertEqual(self.m.details(key)["trackers"][0]["url"], "http://127.0.0.1:9/announce")
        self.assertTrue(wait_for(lambda: self.m.details(key)["trackers"][0]["status"] == "Error", 15), self.m.details(key)["trackers"])
        self.m.set_file_priority(key, [f["index"] for f in self.m.files(key) if f["path"].endswith("c.bin")], 0)
        self.assertEqual({f["path"]: f["priority"] for f in self.m.files(key)}["Pack/c.bin"], 0)
        self.assertIsNone(self.m.details("0" * 40))

    def test_no_peers_means_metadata_state_not_fake_progress(self):
        key, _ = self.m.add(f"magnet:?xt=urn:btih:{self.key}", self.dl); pump(1.0)
        st = self.m.status(key); d = self.m.details(key)
        self.assertEqual((st.state, st.progress, st.eta, st.download_rate), ("METADATA", 0.0, -1, 0.0))
        self.assertFalse(d["has_metadata"]); self.assertIsNone(d["meta"]); self.assertEqual(self.m.files(key), [])


if __name__ == "__main__":
    unittest.main()
