"""Guards against a test module silently collapsing (audit finding C-01: test_torrent.py was
overwritten by a fragment, so 60 torrent tests vanished without any suite noticing).
Floors are the verified counts for v10.5; raise them when tests are added, never lower them."""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _isolate  # noqa: F401,E402

FLOORS = {"tests.test_nexus": 19, "tests.test_layout": 3, "tests.test_network_buttons": 10, "tests.test_responsive": 5, "tests.test_adapters": 5, "tests.test_combiner": 8, "tests.test_multinet": 33, "tests.test_torrent": 32,
          "tests.test_torrent_flow": 28, "tests.test_regressions": 51, "tests.test_ui": 14,
          "tests.test_appearance": 21, "tests.test_ratelimit": 8, "tests.test_integrity": 11, "tests.test_security_fixes": 4}


def _count(suite):
    return sum(_count(t) if isinstance(t, unittest.TestSuite) else 1 for t in suite)


class CollectionGuard(unittest.TestCase):
    def test_every_module_imports_and_keeps_its_test_count(self):
        for name, floor in FLOORS.items():
            with self.subTest(module=name):
                suite = unittest.defaultTestLoader.loadTestsFromName(name)
                n = _count(suite)
                bad = [t for t in suite if "_FailedTest" in type(t).__name__]
                self.assertFalse(bad, f"{name} failed to import")
                self.assertGreaterEqual(n, floor, f"{name}: {n} tests collected, expected >= {floor}")

    def test_tests_never_touch_the_real_profile(self):
        """Regression: under `unittest discover` the tests used to write into the user's real ~/NexusDownloader."""
        import database.db as db
        from gui import main_window
        tmp = os.path.realpath(tempfile.gettempdir())
        for p in (db.DB_PATH, main_window.APP_DIR, main_window.SETTINGS_PATH):
            self.assertTrue(os.path.realpath(str(p)).startswith(tmp), f"{p} is outside the temp folder")


if __name__ == "__main__":
    unittest.main()
