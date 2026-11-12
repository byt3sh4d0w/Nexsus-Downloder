"""Combiner reporting (Phase B/F): per-adapter rates must be summed across ALL jobs, HTTP / torrent /
unattributed traffic are separate values, and the animation timer only runs when it can be seen."""
import os, sys, unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _isolate  # noqa: F401,E402  (keeps tests out of the real ~/NexusDownloader; must stay first)
from PySide6.QtWidgets import QApplication

from core.network_monitor import attribute_rates
from gui import theme


def app():
    return QApplication.instance() or QApplication([])


class AttributeRates(unittest.TestCase):
    def test_two_jobs_on_same_adapter_are_summed(self):
        a = {"10.0.0.2": {"rate": 300.0}, "10.0.1.2": {"rate": 100.0}}
        b = {"10.0.0.2": {"rate": 200.0}}
        per_ip, unattributed = attribute_rates([a, b], 600.0)
        self.assertEqual(per_ip, {"10.0.0.2": 500.0, "10.0.1.2": 100.0})
        self.assertEqual(unattributed, 0.0)

    def test_single_connection_job_is_unattributed_not_invented(self):
        per_ip, unattributed = attribute_rates([{"10.0.0.2": {"rate": 100.0}}], 400.0)
        self.assertEqual(per_ip, {"10.0.0.2": 100.0})
        self.assertEqual(unattributed, 300.0)

    def test_no_reports_and_bad_values(self):
        self.assertEqual(attribute_rates([], 0.0), ({}, 0.0))
        self.assertEqual(attribute_rates([None, {"x": {"rate": None}}], 5.0), ({"x": 0.0}, 5.0))

    def test_never_negative_when_adapter_sum_exceeds_total(self):
        _, unattributed = attribute_rates([{"a": {"rate": 900.0}}], 100.0)
        self.assertEqual(unattributed, 0.0)


class CombinerWidget(unittest.TestCase):
    def setUp(self):
        app()
        theme.apply_appearance_from_settings({"reduced_motion": False, "animation_enabled": True})
        self.w = theme.NetworkCombiner()

    def test_timer_idle_when_hidden_or_no_traffic(self):
        self.assertFalse(self.w.timer.isActive(), "idle at construction")
        self.w.update_metrics([{"name": "Wi-Fi", "ip": "a", "rate": 5e6, "state": "ACTIVE"}], 5e6)
        self.assertFalse(self.w.timer.isActive(), "hidden widget never animates")
        self.w.show()
        self.assertTrue(self.w.timer.isActive(), "visible + traffic -> animates")
        self.w.update_metrics([], 0.0)
        self.assertFalse(self.w.timer.isActive(), "traffic stopped -> timer stopped")
        self.w.update_metrics([], 5e6)
        self.assertTrue(self.w.timer.isActive())
        self.w.hide()
        self.assertFalse(self.w.timer.isActive())

    def test_reduced_motion_never_starts_timer(self):
        theme.apply_appearance_from_settings({"reduced_motion": True})
        w = theme.NetworkCombiner(); w.show()
        w.update_metrics([], 5e6)
        self.assertFalse(w.timer.isActive())

    def test_accessible_text_states_http_vs_torrent_and_unmeasured(self):
        self.w.update_metrics([{"name": "Wi-Fi", "ip": "a", "rate": 2e6, "state": "ACTIVE"}], 3e6,
                              torrent_rate=1e6, unattributed=1e6)
        d = self.w.accessibleDescription()
        self.assertIn("HTTP", d); self.assertIn("Wi-Fi", d)
        self.assertIn("Torrent", d); self.assertIn("not attributed", d.lower())
        self.assertTrue(self.w.accessibleName())
        self.w.update_metrics([{"name": "USB", "ip": "b", "rate": None, "state": "ACTIVE"}], 0.0)
        self.assertIn("not measured", self.w.accessibleDescription().lower())

    def test_fmt_rate_none_is_dash(self):
        self.assertEqual(theme.NetworkCombiner._fmt_rate(None), "—")


if __name__ == "__main__":
    unittest.main()
