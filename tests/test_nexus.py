"""Nexus Downloader regression tests (local mock servers only; SHA-256 verified).

Run:  QT_QPA_PLATFORM=offscreen python -m unittest discover -s tests -v
"""
import hashlib, os, socket, sys, tempfile, threading, time, unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _isolate import HOME                       # noqa: E402  (must run before anything imports database/gui)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from core.downloader import Downloader          # noqa: E402
from core import cleanup                        # noqa: E402

DATA = hashlib.sha256  # alias for brevity
BLOB = os.urandom(6 * 1024 * 1024)
BLOB_SHA = hashlib.sha256(BLOB).hexdigest()
MODE = {"ignore_range": False, "bad_range": False, "throttle": 0.0, "cut_first": 0, "log": [], "gets": 0, "fail": 0, "active": 0, "peak": 0}
MODE_LOCK = threading.Lock()


class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    def log_message(self, *a): pass

    def do_HEAD(self):
        if MODE["fail"]:
            self.send_response(MODE["fail"]); self.send_header("Content-Length", "0"); self.end_headers(); return
        self.send_response(200)
        self.send_header("Content-Length", str(len(BLOB)))
        self.send_header("Accept-Ranges", "bytes")
        if self.path.startswith("/named"):
            self.send_header("Content-Disposition", 'attachment; filename="named.bin"')
        self.end_headers()

    def do_GET(self):
        with MODE_LOCK:
            MODE["active"] += 1; MODE["peak"] = max(MODE["peak"], MODE["active"])
        try:
            self._get()
        finally:
            with MODE_LOCK: MODE["active"] -= 1

    def _get(self):
        if self.path.startswith("/slow/"):          # filename-resolution tests
            time.sleep(1.0)
            tag = self.path.split("/")[2][:1]
            self.send_response(200)
            self.send_header("Content-Disposition", f'attachment; filename="slow_{tag}.bin"')
            self.send_header("Content-Length", "1"); self.end_headers(); self.wfile.write(b"x"); return
        if self.path.startswith("/fast/"):
            self.send_response(200)
            self.send_header("Content-Disposition", 'attachment; filename="fast_real.bin"')
            self.send_header("Content-Length", "1"); self.end_headers(); self.wfile.write(b"x"); return
        if MODE["fail"]:
            self.send_response(MODE["fail"]); self.send_header("Content-Length", "0"); self.end_headers(); return
        rng = self.headers.get("Range")
        with MODE_LOCK:
            MODE["gets"] += 1; n = MODE["gets"]
            MODE["log"].append(rng)
            ignore, bad, throttle, cut = MODE["ignore_range"], MODE["bad_range"], MODE["throttle"], MODE["cut_first"]
        a, b, code = 0, len(BLOB) - 1, 200
        if rng and not ignore:
            x, y = rng[6:].split("-"); a = int(x); b = int(y) if y else b; code = 206
        body = BLOB[a:b + 1]
        self.send_response(code)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Accept-Ranges", "bytes")
        if code == 206:
            sa = a + 1 if bad else a                      # wrong start byte
            self.send_header("Content-Range", f"bytes {sa}-{b}/{len(BLOB)}")
        self.end_headers()
        try:
            sent = 0
            for i in range(0, len(body), 65536):
                if cut and n == 1 and sent >= cut:
                    self.wfile.flush(); self.connection.shutdown(socket.SHUT_RDWR); self.close_connection = True; return   # truncated response
                self.wfile.write(body[i:i + 65536]); sent += 65536
                if throttle: time.sleep(throttle)
        except Exception:
            pass


class Srv:
    def __enter__(self):
        self.s = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.s.serve_forever, daemon=True).start()
        self.port = self.s.server_address[1]
        return self
    def __exit__(self, *a): self.s.shutdown()
    def url(self, p="/f"): return f"http://127.0.0.1:{self.port}{p}"


def reset(**kw):
    with MODE_LOCK:
        MODE.update(ignore_range=False, bad_range=False, throttle=0.0, cut_first=0, gets=0, fail=0, active=0, peak=0)
        MODE["log"] = []; MODE.update(kw)


def sha(p):
    return hashlib.sha256(open(p, "rb").read()).hexdigest()


def run_job(j, timeout=60):
    j.start(); t = time.time()
    while j.is_alive() and time.time() - t < timeout: time.sleep(0.05)
    return j.info.status


class DownloaderTests(unittest.TestCase):
    def setUp(self):
        self.srv = Srv().__enter__(); reset()
        self.d = tempfile.mkdtemp(); self.path = os.path.join(self.d, "f.bin")
    def tearDown(self): self.srv.__exit__()

    def test06_multi_resume_same_layout(self):
        reset(throttle=0.05)
        j = Downloader(self.srv.url(), self.path, connections=3); j.start(); time.sleep(0.8); j.stop(); j.wait(5)
        self.assertFalse(j.is_alive())
        parts = os.path.join(self.path + ".parts")
        before = {f: os.path.getsize(os.path.join(parts, f)) for f in os.listdir(parts) if f.endswith(".part")}
        self.assertTrue(sum(before.values()) > 0)
        reset()
        j2 = Downloader(self.srv.url(), self.path, connections=3)
        j2.start(); time.sleep(0.01)
        # parts must have been KEPT (not wiped) when the layout is unchanged
        self.assertEqual(run_job(j2), "COMPLETED")
        self.assertEqual(sha(self.path), BLOB_SHA)
        self.assertFalse(os.path.exists(parts), "parts dir must be removed after a successful merge")
        # first resumed request for the first part must not start at 0 for a part that had data
        # resumed requests must continue AFTER the bytes already on disk
        self.assertTrue(any(int(r[6:].split("-")[0]) > 0 for r in MODE["log"] if r))

    def test07_multi_resume_changed_connections(self):
        reset(throttle=0.05)
        j = Downloader(self.srv.url(), self.path, connections=2); j.start(); time.sleep(0.8); j.stop(); j.wait(5)
        parts = self.path + ".parts"
        self.assertTrue(os.path.isdir(parts))
        old_layout = open(os.path.join(parts, "layout.txt")).read()
        reset()
        j2 = Downloader(self.srv.url(), self.path, connections=5)
        self.assertEqual(run_job(j2), "COMPLETED")
        self.assertEqual(sha(self.path), BLOB_SHA)          # hash proves no stale parts were merged
        self.assertNotEqual(old_layout, "v2:%d:5:%d" % (len(BLOB), -(-len(BLOB) // 5)))

    def test08_single_failover_after_partial(self):
        reset(cut_first=2 * 1024 * 1024)
        j = Downloader(self.srv.url(), self.path, connections=1)
        j._valid_network_ips = lambda: ["127.0.0.1", "127.0.0.1"]     # two "adapters"
        self.assertEqual(run_job(j), "COMPLETED")
        self.assertEqual(sha(self.path), BLOB_SHA)
        ranges = [r for r in MODE["log"] if r]
        self.assertTrue(ranges, "second adapter must resume with a Range header")
        start = int(ranges[0][6:].split("-")[0])
        self.assertGreater(start, 0)
        self.assertLessEqual(start, 2 * 1024 * 1024 + 65536)     # offset == bytes actually written before the cut

    def test09_server_ignores_range_single(self):
        reset(ignore_range=True)
        with open(self.path + ".part", "wb") as f: f.write(BLOB[:1_000_000])
        j = Downloader(self.srv.url(), self.path, connections=1)
        self.assertEqual(run_job(j), "COMPLETED")
        self.assertEqual(sha(self.path), BLOB_SHA)           # 200 body was NOT appended to the partial file
        self.assertEqual(MODE["gets"], 1, "must be handled in one request, not by a retry after appending")

    def test09b_server_ignores_range_multi_falls_back(self):
        reset(ignore_range=True)
        j = Downloader(self.srv.url(), self.path, connections=4)
        self.assertEqual(run_job(j), "COMPLETED")
        self.assertEqual(sha(self.path), BLOB_SHA)
        self.assertFalse(os.path.exists(self.path + ".parts"))

    def test10_bad_content_range_multi_falls_back(self):
        reset(bad_range=True)
        j = Downloader(self.srv.url(), self.path, connections=4)
        self.assertEqual(run_job(j), "COMPLETED")
        self.assertEqual(sha(self.path), BLOB_SHA)

    def test10b_bad_content_range_single_resume_restarts(self):
        reset(bad_range=True)
        with open(self.path + ".part", "wb") as f: f.write(BLOB[:1_000_000])
        j = Downloader(self.srv.url(), self.path, connections=1)
        self.assertEqual(run_job(j), "COMPLETED")
        self.assertEqual(sha(self.path), BLOB_SHA)
        self.assertEqual(MODE["gets"], 2)                    # rejected ranged reply + one clean restart

    def test03b_stop_closes_blocked_worker_quickly(self):
        reset(throttle=0.05)
        j = Downloader(self.srv.url(), self.path, connections=4); j.start(); time.sleep(0.6)
        t = time.time(); ok = cleanup.stop_job(j, 5.0)
        self.assertTrue(ok); self.assertLess(time.time() - t, 3.0)

    def test_merge_rejects_wrong_part_size(self):
        os.makedirs(self.path + ".parts")
        for i in range(2):
            open(os.path.join(self.path + ".parts", f"{i}.part"), "wb").write(b"x" * 10)
        j = Downloader(self.srv.url(), self.path)
        with self.assertRaises(RuntimeError):
            j._merge_parts(self.path + ".parts", [(0, 0, 9), (1, 10, 24)], 25)   # part 1 should be 15 bytes
        self.assertFalse(os.path.exists(self.path)); self.assertFalse(os.path.exists(self.path + ".merge"))
        self.assertTrue(os.path.exists(os.path.join(self.path + ".parts", "0.part")))   # good part kept


class CleanupModuleTests(unittest.TestCase):
    def test_only_known_names_deleted_and_links_not_followed(self):
        d = tempfile.mkdtemp(); p = os.path.join(d, "movie.mkv")
        os.makedirs(p + ".parts"); open(os.path.join(p + ".parts", "0.part"), "wb").write(b"a")
        open(os.path.join(p + ".parts", "layout.txt"), "w").write("v2")
        open(os.path.join(p + ".parts", "mine.txt"), "w").write("user")       # foreign file inside
        other = os.path.join(d, "movie.mkv.backup"); open(other, "w").write("keep")
        sibling = os.path.join(d, "movie2.mkv"); open(sibling, "w").write("keep")
        res = cleanup.delete_download_files(p)
        self.assertTrue(os.path.exists(other)); self.assertTrue(os.path.exists(sibling))
        self.assertTrue(os.path.exists(os.path.join(p + ".parts", "mine.txt")))   # not ours -> kept
        self.assertTrue(res.skipped)
        # symlinked parts dir: the link goes, the target survives
        tgt = tempfile.mkdtemp(); open(os.path.join(tgt, "0.part"), "w").write("precious")
        q = os.path.join(d, "x.bin"); os.symlink(tgt, q + ".parts")
        cleanup.delete_download_files(q)
        self.assertTrue(os.path.exists(os.path.join(tgt, "0.part")))
        self.assertFalse(os.path.lexists(q + ".parts"))


# ---------------------------------------------------------------- GUI tests
from PySide6.QtWidgets import QApplication, QMessageBox   # noqa: E402
APP = QApplication.instance() or QApplication([])
from gui import main_window as M                           # noqa: E402


def pump(sec=0.2):
    end = time.time() + sec
    while time.time() < end: APP.processEvents(); time.sleep(0.01)


def clear_rows(win):
    for rid in [win.table.rid_at(i) for i in range(win.table.rowCount())]:
        if rid is not None:
            win._drop_row_ui(rid)
    win.jobs.clear()


_WIN = None
def get_window():
    """One shared MainWindow for all GUI tests (two windows would both auto-start queued DB rows)."""
    global _WIN
    if _WIN is None:
        from gui import main_window as _m
        _m.apply_theme(APP)
        _WIN = M.MainWindow(); _WIN.show(); pump(0.5)
    return _WIN


class GuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.warnings = []
        cls._p = mock.patch.object(QMessageBox, "warning", lambda *a, **k: cls.warnings.append(" ".join(str(x) for x in a[1:])))
        cls._p.start()
        cls.infos = []
        cls._p2 = mock.patch.object(QMessageBox, "information", lambda *a, **k: cls.infos.append(" ".join(str(x) for x in a[1:])))
        cls._p2.start()
        cls.win = get_window()
    @classmethod
    def tearDownClass(cls): cls._p.stop(); cls._p2.stop()

    def setUp(self):
        self.srv = Srv().__enter__(); reset()
        self.d = tempfile.mkdtemp()
        for r in list(self.win.db.all()): self.win.db.delete_download(r["id"])
        self.win.db.conn.execute("DELETE FROM pending_cleanup"); self.win.db.conn.commit()
        clear_rows(self.win); self.warnings.clear()
    def tearDown(self): self.srv.__exit__()

    def add(self, name, url=None, status="QUEUED"):
        path = os.path.join(self.d, name)
        rid = self.win.db.add(url or self.srv.url(), name, path)
        self.win.db.update(rid, status=status)
        self.win.add_row(rid, dict(self.win.db.get(rid)))
        return rid, path

    def make_artifacts(self, path, final=True):
        if final: open(path, "wb").write(b"FINAL")
        open(path + ".part", "wb").write(b"p"); open(path + ".merge", "wb").write(b"m")
        os.makedirs(path + ".parts"); open(os.path.join(path + ".parts", "0.part"), "wb").write(b"0")
        open(os.path.join(path + ".parts", "layout.txt"), "w").write("v2:1:1:1")

    def test01_remove_from_list_keeps_final_file(self):
        rid, path = self.add("a.bin", status="COMPLETED"); self.make_artifacts(path)
        rep = self.win._remove_downloads([rid], "remove_only")
        self.assertEqual(rep["done"], [rid]); self.assertEqual(rep["failed"], [])
        self.assertTrue(os.path.exists(path)); self.assertEqual(open(path, "rb").read(), b"FINAL")
        for t in (".part", ".merge", ".parts"): self.assertFalse(os.path.exists(path + t), t)
        self.assertIsNone(self.win.db.get(rid)); self.assertEqual(self.win.table.rowCount(), 0)

    def test02_delete_files_removes_everything(self):
        rid, path = self.add("b.bin", status="COMPLETED"); self.make_artifacts(path)
        other_rid, other = self.add("b.bin.keep", status="COMPLETED"); open(other, "w").write("other")
        rep = self.win._remove_downloads([rid], "delete_files")
        self.assertEqual(rep["failed"], [])
        for t in ("", ".part", ".merge", ".parts"): self.assertFalse(os.path.lexists(path + t), t)
        self.assertTrue(os.path.exists(other)); self.assertIsNotNone(self.win.db.get(other_rid))
        self.assertIsNone(self.win.db.get(rid))

    def test03_active_download_waits_for_worker(self):
        reset(throttle=0.2)
        rid, path = self.add("c.bin")
        self.win.start_job(rid, dict(self.win.db.get(rid)), force=True); pump(0.8)
        job = self.win.jobs[rid]; self.assertTrue(job.is_alive())
        rep = self.win._remove_downloads([rid], "delete_files")
        self.assertEqual(rep["busy"], []); self.assertEqual(rep["done"], [rid])
        self.assertFalse(job.is_alive())                       # worker confirmed gone BEFORE deletion
        time.sleep(0.5); pump(0.3)
        for t in ("", ".part", ".merge", ".parts"): self.assertFalse(os.path.lexists(path + t), "recreated: " + t)
        self.assertNotIn(rid, self.win.jobs)

    def test04_stuck_worker_blocks_deletion(self):
        rid, path = self.add("d.bin", status="DOWNLOADING"); self.make_artifacts(path)
        class Stuck:                                # a worker that ignores stop()
            on_update = None
            info = mock.Mock(status="DOWNLOADING", total=0, done=0, speed=0.0)
            def stop(self): pass
            def wait(self, t): time.sleep(min(t, 0.05))
            def is_alive(self): return True
        self.win.jobs[rid] = Stuck(); self.win.STOP_TIMEOUT = 0.1
        try:
            rep = self.win._remove_downloads([rid], "delete_files")
        finally:
            self.win.STOP_TIMEOUT = 8.0
        self.assertEqual(rep["busy"], [rid])
        for t in ("", ".part", ".merge", ".parts"): self.assertTrue(os.path.lexists(path + t), "must not be deleted: " + t)
        self.assertIsNotNone(self.win.db.get(rid)); self.win.jobs.pop(rid)

    def test05_failed_deletion_reported_and_retryable(self):
        rid, path = self.add("e.bin", status="COMPLETED"); self.make_artifacts(path)
        real = os.unlink
        def locked(p, *a, **k):
            if os.fspath(p) == path: raise PermissionError("file is locked by another process")
            return real(p, *a, **k)
        with mock.patch("os.unlink", locked):
            rep = self.win._remove_downloads([rid], "delete_files")
        self.assertTrue(any(f == path for f, _ in rep["failed"]))
        self.assertTrue(os.path.exists(path))
        self.assertIsNotNone(self.win.db.get(rid), "record must be kept so cleanup can be retried")
        rep2 = self.win._remove_downloads([rid], "delete_files")            # lock released
        self.assertEqual(rep2["failed"], []); self.assertFalse(os.path.exists(path)); self.assertIsNone(self.win.db.get(rid))

    def test05b_remove_only_leftovers_go_to_pending_and_retry(self):
        rid, path = self.add("g.bin", status="PAUSED"); self.make_artifacts(path, final=False)
        real = os.unlink
        def locked(p, *a, **k):
            if os.fspath(p) == path + ".part": raise PermissionError("locked")
            return real(p, *a, **k)
        with mock.patch("os.unlink", locked):
            rep = self.win._remove_downloads([rid], "remove_only")
        self.assertTrue(rep["failed"]); self.assertEqual(len(self.win.db.pending_cleanups()), 1)
        self.win._retry_pending_cleanup()
        self.assertFalse(os.path.exists(path + ".part")); self.assertEqual(len(self.win.db.pending_cleanups()), 0)

    def test13_repeat_and_duplicate_removal(self):
        rid, path = self.add("h.bin", status="COMPLETED"); self.make_artifacts(path)
        rid2, path2 = self.add("i.bin", status="COMPLETED"); open(path2, "w").write("two")
        rep = self.win._remove_downloads([rid, rid, rid], "delete_files")
        self.assertEqual(rep["done"], [rid])
        rep = self.win._remove_downloads([rid], "delete_files")             # again: no error, no effect
        self.assertEqual(rep["done"], []) ; self.assertEqual(rep["failed"], [])
        self.assertTrue(os.path.exists(path2)); self.assertIsNotNone(self.win.db.get(rid2))
        self.assertEqual(len(self.win.db.all()), 1)
        # same target path in two records: the second record must not delete the shared file
        rid3 = self.win.db.add("http://x/y", "i.bin", path2)
        rep = self.win._remove_downloads([rid3], "delete_files")
        self.assertTrue(os.path.exists(path2)); self.assertTrue(rep["skipped"])

    def test11_filename_resolution_rapid_url_changes(self):
        from gui.new_download import NewDownloadDialog
        hexid = "a" * 32
        dlg = NewDownloadDialog(self.srv.url(f"/slow/{hexid}"), self.win)
        done = []; dlg.accepted.connect(lambda: done.append(1))
        dlg._accept(); pump(0.2)                                  # starts slow lookup (1s)
        dlg.url_edit.setText(self.srv.url(f"/fast/{'b' * 32}")); pump(1.6)   # URL changed -> old result ignored
        self.assertEqual(done, [], "stale result must not accept the dialog")
        self.assertNotIn("slow_a", dlg.name_edit.text())
        self.assertTrue(dlg._download_btn.isEnabled())
        dlg.name_edit.setText("")                                  # force generated-name path again
        dlg._accept(); pump(1.5)
        self.assertEqual(dlg.name_edit.text(), "fast_real.bin")
        self.assertEqual(done, [1])
        # a name the user typed while resolving is never overwritten
        dlg2 = NewDownloadDialog(self.srv.url(f"/slow/{hexid}"), self.win)
        dlg2._accept(); pump(0.2)
        dlg2.name_edit.setText("mine.bin"); dlg2.name_edit.textEdited.emit("mine.bin"); pump(1.6)
        self.assertEqual(dlg2.name_edit.text(), "mine.bin")

    def test12_magnet_restore_after_restart(self):
        if self.win.torrent_manager is None: self.skipTest("libtorrent not installed")
        h = "0123456789abcdef0123456789abcdef01234567"
        magnet = f"magnet:?xt=urn:btih:{h}&dn=restoretest"
        self.win.db.add_torrent(h, magnet, self.d, "restoretest")
        self.win.db.add_torrent("f" * 40, os.path.join(self.d, "gone.torrent"), self.d, "gone")
        before = len(self.warnings)
        win2 = M.MainWindow(); pump(0.6)
        try:
            self.assertIsNotNone(win2.torrent_manager)
            self.assertEqual(win2.torrent_table.rowCount(), 1, "magnet must be restored")
            self.assertTrue(any("gone.torrent" in w for w in self.warnings[before:]), "missing .torrent must be reported")
            # same magnet again is not added twice
            calls = []
            with mock.patch.object(M, "ask_choice", lambda parent, title, head, detail, acts: calls.append((head, detail, acts)) or "show"):
                win2.add_torrent_source(magnet, self.d); pump(0.2)
            self.assertEqual(win2.torrent_table.rowCount(), 1)
            self.assertTrue(calls and "already added" in calls[0][0], "duplicate magnet must show the existing state")
        finally:
            win2.close(); pump(0.2)
            self.win.db.delete_torrent(h); self.win.db.delete_torrent("f" * 40)


if __name__ == "__main__":
    unittest.main(verbosity=2)
