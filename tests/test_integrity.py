"""Download integrity / safety regressions from the v10.5 audit (C-07 and related hardening)."""
import os, sqlite3, sys, tempfile, time, unittest
from collections import namedtuple
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import _isolate  # noqa: F401,E402  (keeps tests out of the real ~/NexusDownloader; must stay first)
from core import downloader as D
from core.security import safe_filename
from database.db import Database
from netsim import IPS, Sim, make_downloader

Usage = namedtuple("Usage", "total used free")


def quiet(sim):
    sim.srv.handle_error = lambda *a: None
    return sim


def run(sim, preexisting=None, conns=4, ips=None):
    j, out = make_downloader(D, sim, ips or [IPS[0]], conns)
    if preexisting is not None:
        Path(out).write_bytes(preexisting)
    j.start(); j.wait(60)
    return j, out


class ExistingDestination(unittest.TestCase):
    """C-07: a destination file of the right SIZE was declared COMPLETED without a download."""

    def test_same_size_foreign_file_is_not_accepted(self):
        sim = quiet(Sim(size=2_000_000))
        try:
            j, out = run(sim, preexisting=b"x" * 2_000_000)
            self.assertEqual(j.info.status, "COMPLETED")
            self.assertGreater(sim.range_requests, 0, "the content was really fetched")
            self.assertEqual(Path(out).read_bytes(), sim.blob, "the destination now holds the downloaded bytes")
        finally:
            sim.close()

    def test_matching_content_md5_skips_the_download(self):
        sim = quiet(Sim(size=2_000_000)); sim.send_md5 = True
        try:
            j, out = run(sim, preexisting=Sim(size=2_000_000).blob)      # identical bytes (same seed)
            self.assertEqual(j.info.status, "COMPLETED")
            self.assertEqual(sim.range_requests, 0, "verified by Content-MD5: nothing to fetch")
        finally:
            sim.close()

    def test_same_size_wrong_content_with_md5_is_replaced(self):
        sim = quiet(Sim(size=2_000_000)); sim.send_md5 = True
        try:
            j, out = run(sim, preexisting=b"y" * 2_000_000)
            self.assertEqual(j.info.status, "COMPLETED")
            self.assertGreater(sim.range_requests, 0)
            self.assertEqual(Path(out).read_bytes(), sim.blob)
        finally:
            sim.close()


class DiskSpace(unittest.TestCase):
    """Multi-connection mode keeps every part AND writes the merged file: it needs about 2x the size."""

    def test_multi_connection_needs_room_for_the_merge(self):
        sim = quiet(Sim(size=4_000_000))
        try:
            with mock.patch.object(D.shutil, "disk_usage", return_value=Usage(10**9, 0, 6_000_000)):
                j, out = run(sim, conns=4)
            self.assertEqual(j.info.status, "ERROR")
            self.assertIn("disk space", j.info.error.lower())
            self.assertEqual(sim.range_requests, 0, "refused before downloading anything")
        finally:
            sim.close()

    def test_single_stream_only_needs_the_file_size(self):
        sim = quiet(Sim(size=4_000_000))
        try:
            with mock.patch.object(D.shutil, "disk_usage", return_value=Usage(10**9, 0, 6_000_000)):
                j, out = run(sim, conns=1)
            self.assertEqual(j.info.status, "COMPLETED")
        finally:
            sim.close()


class PartDirectory(unittest.TestCase):
    def test_fallback_never_deletes_foreign_files_in_the_parts_folder(self):
        sim = quiet(Sim(size=3_000_000)); sim.bad_range = True            # forces the multi -> single fallback
        try:
            j, out = make_downloader(D, sim, [IPS[0]], 4)
            parts = Path(out + ".parts"); parts.mkdir()
            (parts / "keep-me.txt").write_text("not ours")
            j.start(); j.wait(60)
            self.assertEqual(j.info.status, "COMPLETED")
            self.assertTrue((parts / "keep-me.txt").exists(), "only files this app wrote may be removed")
        finally:
            sim.close()


class RedirectPolicy(unittest.TestCase):
    def test_https_to_http_is_refused_everything_else_allowed(self):
        for a, b, ok in (("https://a/x", "http://b/x", False), ("https://a/x", "https://b/x", True),
                         ("http://a/x", "https://b/x", True), ("http://a/x", "http://b/x", True)):
            with self.subTest(start=a, final=b):
                if ok:
                    D._check_redirect(a, b)
                else:
                    with self.assertRaises(D._NonRetryableError):
                        D._check_redirect(a, b)


class UnsafeNames(unittest.TestCase):
    def test_names_stay_valid_on_windows(self):
        cases = {"../../etc/passwd": ".._.._etc_passwd", "con .txt": "_con .txt", "CONIN$": "_CONIN$",
                 "report.": "report", "x" * 239 + ". y": None}
        for raw, expect in cases.items():
            with self.subTest(raw=raw[:20]):
                got = safe_filename(raw)
                self.assertLessEqual(len(got), 240)
                self.assertFalse(got.endswith((".", " ")), repr(got[-5:]))
                self.assertNotIn("/", got); self.assertNotIn("\\", got)
                if expect: self.assertEqual(got, expect)

    def test_bidi_controls_cannot_disguise_the_extension(self):
        self.assertEqual(safe_filename("invoice‮gpj.exe"), "invoicegpj.exe")


class CorruptDatabase(unittest.TestCase):
    def test_garbage_file_is_set_aside_and_a_fresh_db_starts(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "downloads.db"
            path.write_bytes(b"this is not a sqlite database" * 50)
            db = Database(path)
            rid = db.add("http://x/y", "y", str(Path(tmp) / "y"), network_mode="COMBINED", network_ips="", connections=2)
            self.assertEqual(db.get(rid)["name"], "y")
            kept = [p for p in Path(tmp).iterdir() if ".corrupt-" in p.name]
            self.assertEqual(len(kept), 1, "the damaged file is preserved, not deleted")
            self.assertTrue(kept[0].read_bytes().startswith(b"this is not"))

    def test_healthy_db_is_untouched(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "downloads.db"
            Database(path).add("http://x/y", "y", "p", network_mode="COMBINED", network_ips="", connections=1)
            again = Database(path)
            self.assertEqual(len(list(Path(tmp).glob("*.corrupt-*"))), 0)


if __name__ == "__main__":
    unittest.main()
