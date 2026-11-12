"""Appearance regressions from the v10.5 audit (C-02 DialogFader leak, C-03 light/system theme,
C-05 accent swatch, C-06 STATUS_COLORS)."""
import os, sys, unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _isolate  # noqa: F401,E402  (keeps tests out of the real ~/NexusDownloader; must stay first)
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QApplication

from gui import main_window as mw, theme


def app():
    return QApplication.instance() or QApplication([])


class ApplyThemeIsIdempotent(unittest.TestCase):
    """C-02: apply_theme() ran on every Settings save and stacked one more DialogFader
    event filter and two stylesheet rebuilds each time."""

    def test_repeated_apply_theme_keeps_a_single_fader(self):
        a = app()
        mw.apply_theme(a, {"accent": "blue"})
        first = a._nx_fader
        for i in range(10):
            mw.apply_theme(a, {"accent": ("green", "red", "orange")[i % 3]})
        self.assertIs(a._nx_fader, first, "the same fader instance is reused")
        self.assertEqual(len(a.findChildren(theme.DialogFader)), 1)

    def test_apply_theme_builds_the_stylesheet_once(self):
        a = app()
        with mock.patch.object(theme, "build_qss", wraps=theme.build_qss) as spy:
            mw.apply_theme(a, {"accent": "teal"})
            self.assertEqual(spy.call_count, 1)
            spy.reset_mock()
            mw.apply_theme(a, {"accent": "pink"})
            self.assertEqual(spy.call_count, 1)
        theme.apply_accent("orange")

    def test_dialog_still_fades_after_repeated_apply(self):
        from PySide6.QtWidgets import QDialog
        a = app()
        for _ in range(3):
            mw.apply_theme(a, {})
        d = QDialog(); d.show(); QApplication.processEvents()
        self.assertTrue(getattr(d, "_nx_faded", False), "fade-in still applied to dialogs")
        d.close()


def lum(hex_color):
    from PySide6.QtGui import QColor
    c = QColor(hex_color)
    f = lambda v: v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    return 0.2126 * f(c.redF()) + 0.7152 * f(c.greenF()) + 0.0722 * f(c.blueF())


def contrast(a, b):
    la, lb = sorted((lum(a), lum(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


class Restore(unittest.TestCase):
    def setUp(self):
        app()
        theme.apply_appearance_from_settings({"theme": "dark", "accent": "orange"})

    def tearDown(self):
        theme.apply_appearance_from_settings({"theme": "dark", "accent": "orange"})


class ThemeModes(Restore):
    """C-03: 'light' and 'system' were stored but never changed a single colour."""

    def test_light_switches_palette_and_stylesheet(self):
        dark_bg = theme.BG
        theme.apply_appearance_from_settings({"theme": "light"})
        self.assertNotEqual(theme.BG, dark_bg)
        self.assertGreater(lum(theme.BG), 0.5, "light background is light")
        qss = theme.build_qss()
        self.assertIn(theme.BG, qss)
        self.assertNotIn(dark_bg, qss, "no dark background literal leaks into the light stylesheet")
        self.assertEqual(theme.palette_name(), "light")

    def test_switching_back_restores_dark_exactly(self):
        before = (theme.BG, theme.TEXT, theme.SURFACE, theme.build_qss())
        theme.apply_appearance_from_settings({"theme": "light"})
        theme.apply_appearance_from_settings({"theme": "dark"})
        self.assertEqual((theme.BG, theme.TEXT, theme.SURFACE, theme.build_qss()), before)

    def test_system_follows_the_os_scheme(self):
        for scheme, expect in (("light", "light"), ("dark", "dark"), (None, "dark")):   # unknown -> dark default
            with self.subTest(os=scheme), mock.patch.object(theme, "_os_scheme", return_value=scheme):
                theme.apply_appearance_from_settings({"theme": "system"})
                self.assertEqual(theme.palette_name(), expect)
                self.assertEqual(theme.appearance_state()["theme"], "system", "the choice stays 'system'")

    def test_garbage_theme_value_falls_back_to_dark(self):
        theme.apply_appearance_from_settings({"theme": "light"})
        theme.apply_appearance_from_settings({"theme": "neon"})
        self.assertEqual(theme.palette_name(), "dark")
        self.assertEqual(theme.appearance_state()["theme"], "dark")

    def test_palette_contrast_meets_wcag_in_both_modes(self):
        for mode in ("dark", "light"):
            for accent in theme.ACCENT_PRESETS:
                theme.apply_appearance_from_settings({"theme": mode, "accent": accent})
                with self.subTest(mode=mode, accent=accent):
                    self.assertGreaterEqual(contrast(theme.TEXT, theme.BG), 7.0)
                    self.assertGreaterEqual(contrast(theme.MUTED, theme.SURFACE), 4.5)
                    self.assertGreaterEqual(contrast(theme.FAINT, theme.SURFACE), 3.0)
                    self.assertGreaterEqual(contrast(theme.ACCENT, theme.SURFACE), 4.5)
                    self.assertGreaterEqual(contrast(theme.ON_ACCENT, theme.ACCENT), 4.5, "primary button label")
                    for tone in (theme.SUCCESS, theme.WARN, theme.DANGER):
                        self.assertGreaterEqual(contrast(tone, theme.SURFACE), 4.5)

    def test_every_status_stays_readable_in_both_modes(self):
        for mode in ("dark", "light"):
            theme.apply_appearance_from_settings({"theme": mode})
            for status, color in theme.STATUS_COLORS.items():
                with self.subTest(mode=mode, status=status):
                    self.assertGreaterEqual(contrast(color, theme.SURFACE), 3.0)


class StatusColors(Restore):
    """C-06: STATUS_COLORS was computed once at import and stayed on the old accent."""

    def test_follows_accent_and_keeps_identity(self):
        from gui.theme import STATUS_COLORS as imported       # how main_window.py holds it
        for name in ("blue", "green", "purple"):
            theme.apply_accent(name)
            self.assertIs(theme.STATUS_COLORS, imported, "mutated in place so existing imports stay valid")
            for k in ("DOWNLOADING", "ACTIVE", "SEEDING"):
                self.assertEqual(imported[k], theme.ACCENT)

    def test_follows_theme_mode(self):
        theme.apply_appearance_from_settings({"theme": "light"})
        self.assertEqual(theme.STATUS_COLORS["COMPLETED"], theme.SUCCESS)
        self.assertEqual(theme.STATUS_COLORS["ERROR"], theme.DANGER)


class Swatches(Restore):
    """C-05: clicking a swatch called QPushButton._apply_style(), which does not exist."""

    def dialog(self):
        from gui.settings_dialog import SettingsDialog
        return SettingsDialog(mw.load_settings(), None)

    def test_selecting_every_accent_updates_the_ring(self):
        d = self.dialog()
        for name in theme.ACCENT_PRESETS:
            with self.subTest(accent=name):
                d._select_accent(name)                                  # used to raise AttributeError
                self.assertEqual(d._selected_accent(), name)
                for n, b in d.accent_buttons.items():
                    ring = theme.TEXT if n == name else theme.BORDER
                    self.assertIn(f"border: 2px solid {ring};", b.styleSheet())
                    self.assertEqual(b.isChecked(), n == name)

    def test_real_click_reaches_the_handler_without_error(self):
        from PySide6.QtTest import QTest
        from PySide6.QtCore import Qt
        d = self.dialog(); d.show()
        errors = []
        old = sys.excepthook; sys.excepthook = lambda *a: errors.append(a)
        try:
            QTest.mouseClick(d.accent_buttons["green"], Qt.MouseButton.LeftButton)
            QApplication.processEvents()
        finally:
            sys.excepthook = old
        self.assertEqual(errors, [])
        self.assertEqual(d._selected_accent(), "green")
        d.close()


class Persistence(Restore):
    """Appearance keys were not in load_settings()'s defaults, so they were dropped on every start."""

    def test_appearance_survives_save_and_load(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.object(mw, "APP_DIR", Path(tmp)), mock.patch.object(mw, "SETTINGS_PATH", Path(tmp) / "settings.json"):
            s = mw.load_settings()
            self.assertEqual((s["accent"], s["theme"]), (theme.DEFAULT_ACCENT, "dark"), "safe defaults")
            s.update(accent="blue", theme="light", reduced_motion=True, animation_enabled=False)
            mw.save_settings(s)
            again = mw.load_settings()
            self.assertEqual((again["accent"], again["theme"], again["reduced_motion"], again["animation_enabled"]),
                             ("blue", "light", True, False))
            theme.apply_appearance_from_settings(again)
            self.assertEqual(theme.palette_name(), "light")
            self.assertEqual(theme.appearance_state()["accent"], "blue")
            self.assertTrue(theme.is_reduced_motion())
        theme.apply_appearance_from_settings({"reduced_motion": False, "animation_enabled": True})


class LiveWindow(Restore):
    """The real MainWindow follows a theme change without a restart and without touching its jobs."""

    @classmethod
    def setUpClass(cls):
        import test_nexus as TN
        cls.TN = TN
        cls.win = TN.get_window(); cls.win.resize(1100, 700); TN.pump(0.2)

    def paint_lum(self):
        self.TN.pump(0.1)
        img = self.win.grab().toImage()
        c = QColor(img.pixel(img.width() * 3 // 4, img.height() - 8))      # content area, near the bottom edge
        return 0.3 * c.red() + 0.59 * c.green() + 0.11 * c.blue()

    def test_window_repaints_light_and_back_to_dark(self):
        mw.apply_theme(QApplication.instance(), {"theme": "dark"}); dark = self.paint_lum()
        mw.apply_theme(QApplication.instance(), {"theme": "light"}); light = self.paint_lum()
        mw.apply_theme(QApplication.instance(), {"theme": "dark"}); again = self.paint_lum()
        self.assertLess(dark, 60); self.assertGreater(light, 180); self.assertLess(again, 60)

    def test_icons_are_recoloured_by_the_theme_change(self):
        app_ = QApplication.instance()
        mw.apply_theme(app_, {"theme": "dark"}); dark_key = self.win.add_torrent_btn.icon().cacheKey()
        mw.apply_theme(app_, {"theme": "light"}); light_key = self.win.add_torrent_btn.icon().cacheKey()
        self.assertNotEqual(dark_key, light_key)
        mw.apply_theme(app_, {"theme": "dark"}); self.assertEqual(self.win.add_torrent_btn.icon().cacheKey(), dark_key)

    def test_nav_icon_keeps_selected_accent_after_theme_change(self):
        app_ = QApplication.instance()
        mw.apply_theme(app_, {"theme": "light", "accent": "blue"})
        self.win._page_changed(0)
        on = self.win.nav_buttons[self.win.NAV[0][0]]
        self.assertTrue(on.property("nxIcon").endswith(":ACCENT:0"))
        mw.apply_theme(app_, {"theme": "dark"})
        self.assertTrue(on.property("nxIcon").endswith(":ACCENT:0"), "selected nav entry stays in the accent colour")

    def test_jobs_are_untouched(self):
        jobs = dict(self.win.jobs)
        mw.apply_theme(QApplication.instance(), {"theme": "light"}); mw.apply_theme(QApplication.instance(), {"theme": "dark"})
        self.assertEqual(self.win.jobs, jobs)


class AccentFollowers(unittest.TestCase):
    """Phase F: formerly fixed-cyan states must follow the accent and theme."""

    def setUp(self):
        self.a = app()
        self.addCleanup(mw.apply_theme, self.a, {"theme": "dark", "accent": "blue"})

    def test_primary_hover_gradient_is_derived_from_accent(self):
        seen = set()
        for acc in ("blue", "green", "red"):
            mw.apply_theme(self.a, {"accent": acc})
            qss = theme.build_qss()
            self.assertNotIn("#5bb0ff", qss); self.assertNotIn("#5be3f5", qss)
            seen.add(qss.split("QPushButton#primaryButton:hover")[1].split("}")[0])
        self.assertEqual(len(seen), 3, "hover gradient differs per accent")

    def test_selected_row_and_action_tint_use_the_accent(self):
        for acc in ("green", "red"):
            mw.apply_theme(self.a, {"accent": acc})
            c = theme.accent_alpha(30); base = QColor(theme.ACCENT)
            self.assertEqual((c.red(), c.green(), c.blue(), c.alpha()), (base.red(), base.green(), base.blue(), 30))
        import inspect
        from gui import download_table
        self.assertNotIn("34, 211, 238", inspect.getsource(download_table))

    def test_file_type_colours_are_legible_in_light(self):
        from gui import icons
        mw.apply_theme(self.a, {"theme": "light"})
        for name in ("a.zip", "a.mp4", "a.mp3", "a.png", "a.pdf", "a.exe", "a.bin"):
            _, col, _ = icons.file_kind(name)
            self.assertGreaterEqual(theme._contrast(col, theme.SURFACE), 3.0, name)
        mw.apply_theme(self.a, {"theme": "dark"})
        self.assertEqual(icons.file_kind("a.zip")[1], "#f5b955", "dark colours unchanged")


if __name__ == "__main__":
    unittest.main()
