"""Nexus design tokens + application stylesheet (dark charcoal, configurable accent).

The palette is centralized here: every widget reads its color from the module-level
tokens below, so an accent change is applied by calling ``apply_accent`` and rebuilding
the stylesheet. Semantic colors (success/warning/error) are independent of the accent.
"""
import json
import math
import os
from collections import deque

from PySide6.QtCore import QEasingCurve, QEvent, QObject, QPropertyAnimation, QTimer, Qt, QRect, QSize, QPointF
from PySide6.QtGui import QColor, QFontMetrics, QLinearGradient, QPaintEvent, QBrush, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QDialog, QFileDialog, QLabel, QWidget

# ---- palettes ------------------------------------------------------------
# Every neutral/semantic colour the stylesheet and the custom-painted widgets use lives in one of
# these two dicts; apply_palette() copies the active one onto module-level names (BG, TEXT, ...)
# which is what the rest of the GUI reads (``theme.BG``). ACCENT/ACCENT2 are separate: they come
# from the accent preset and are contrast-fitted for the light palette (see _sync_tokens).
_DARK = dict(
    BG="#0f1115", SIDEBAR="#0b0d11", SURFACE="#151920", SURFACE2="#1b2029", BORDER="#252c37",
    TEXT="#e8edf3", MUTED="#8b96a5", FAINT="#5c6675",
    SUCCESS="#34d399", WARN="#f5b955", DANGER="#f7768e",
    ON_ACCENT="#04141f",                                   # label colour on an accent-filled button
    TINT="255,255,255",                                    # r,g,b of faint hover overlays
    HOVER_BG="#222936", HOVER_BORDER="#354052", SUNKEN="#11151b", INPUT_HOVER_BORDER="#323b49",
    ROW_LINE="#1d232c", SCROLL="#2a3340", SCROLL_HOVER="#384456",
    IND_BORDER="#3a4454", IND_HOVER_BORDER="#5b6a80", IND_HOVER_BG="#161b23",
    IND_DIS_BG="#12161c", IND_DIS_BORDER="#232a34", IND_CHK_DIS_BORDER="#2a3440",
    DASH="#2c3542", LONG_TEXT="#cbd5e1", IND_CHECK_OFF="#4a5666", TRACK="#232a35",
    S_STOPPED="#9aa4b2", S_QUEUED="#a8b0bb", S_META="#b18cff", S_RETRY="#ffa94d",
)
_LIGHT = dict(
    BG="#f4f6f9", SIDEBAR="#e9edf2", SURFACE="#ffffff", SURFACE2="#eef1f5", BORDER="#d5dbe3",
    TEXT="#16202c", MUTED="#4b5868", FAINT="#6b7686",
    SUCCESS="#0b7a55", WARN="#8a5a00", DANGER="#c0243c",
    ON_ACCENT="#ffffff",
    TINT="0,0,0",
    HOVER_BG="#e3e8ef", HOVER_BORDER="#b9c3d0", SUNKEN="#e6eaf0", INPUT_HOVER_BORDER="#b4beca",
    ROW_LINE="#e3e7ed", SCROLL="#c3ccd8", SCROLL_HOVER="#a9b4c3",
    IND_BORDER="#98a4b5", IND_HOVER_BORDER="#6f7f95", IND_HOVER_BG="#f0f3f7",
    IND_DIS_BG="#eceff3", IND_DIS_BORDER="#d5dbe3", IND_CHK_DIS_BORDER="#cbd3de",
    DASH="#b9c3d0", LONG_TEXT="#2f3b4b", IND_CHECK_OFF="#8a97a8", TRACK="#dde3ea",
    S_STOPPED="#5b6676", S_QUEUED="#556071", S_META="#6d44c9", S_RETRY="#a35a00",
)
_PALETTES = {"dark": _DARK, "light": _LIGHT}
globals().update(_DARK)                 # defaults, so the names exist for linters/imports; apply_palette() takes over below
ACCENT = "#22d3ee"      # active accent, set by apply_accent()
ACCENT2 = "#3b9eff"     # companion highlight for the active accent
FONT = "'Segoe UI Variable Text', 'Segoe UI', 'Inter', 'SF Pro Text', 'Helvetica Neue', sans-serif"

# ---- accent presets ------------------------------------------------------
# Each preset supplies the main accent and a companion highlight. The dark theme is
# always the base; the accent only recolors active paths, progress, the combiner and
# selected states. Semantic success/warning/error above are never recolored.
ACCENT_PRESETS = {
    "orange":  ("#ff8c2e", "#ffb15c"),
    "purple":  ("#b18cff", "#d8baff"),
    "blue":    ("#3b9eff", "#7ec4ff"),
    "green":   ("#34d399", "#7be7c4"),
    "red":     ("#ff5d6c", "#ff9aa0"),
    "teal":    ("#22d3ee", "#7df3f7"),
    "pink":    ("#ff6ec7", "#ffb4df"),
}
DEFAULT_ACCENT = "orange"

# Appearance settings live in the same JSON as the rest of the app settings, so the
# persisted keys are: accent, theme ("dark" | "light" | "system"), reduced_motion,
# animation_enabled.
APPEARANCE_KEYS = ("accent", "theme", "reduced_motion", "animation_enabled")
_app_state = {"accent": DEFAULT_ACCENT, "theme": "dark", "palette": "dark", "reduced_motion": False, "animation_enabled": True}
STATUS_COLORS = {}      # mutated in place (never rebound): other modules hold a reference to this dict


def _hex_to_qcolor(hex_color):
    return QColor(hex_color)


def _luminance(hex_color):
    c = QColor(hex_color)
    f = lambda v: v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    return 0.2126 * f(c.redF()) + 0.7152 * f(c.greenF()) + 0.0722 * f(c.blueF())


def _contrast(a, b):
    hi, lo = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def _fit_contrast(hex_color, against="#ffffff", minimum=4.6):
    """Darken `hex_color` (keeping its hue) until it reads at >= `minimum`:1 on `against`."""
    c = QColor(hex_color)
    h, s, l, _ = c.getHsl()
    while _contrast(c.name(), against) < minimum and l > 0:
        l -= 4
        c = QColor.fromHsl(h, s, l)
    return c.name()


def _status_colors():
    return {
        "DOWNLOADING": ACCENT, "ACTIVE": ACCENT, "SEEDING": ACCENT,
        "PAUSED": WARN, "STOPPED": S_STOPPED, "COMPLETED": SUCCESS, "ONLINE": SUCCESS,
        "ERROR": DANGER, "NO INTERNET": DANGER, "QUEUED": S_QUEUED, "METADATA": S_META,
        "CHECKING": S_QUEUED, "DISABLED": S_META, "DISCONNECTED": FAINT, "RETRYING": S_RETRY,
    }


def _sync_tokens():
    """Derive ACCENT/ACCENT2 (contrast-fitted on the light palette) and STATUS_COLORS from the
    current accent + palette. Cheap; called whenever either changes."""
    global ACCENT, ACCENT2
    ACCENT, ACCENT2 = ACCENT_PRESETS[_app_state["accent"]]
    if _app_state["palette"] == "light":
        ACCENT, ACCENT2 = _fit_contrast(ACCENT, SURFACE), _fit_contrast(ACCENT2, SURFACE)
    STATUS_COLORS.clear()
    STATUS_COLORS.update(_status_colors())


def _os_scheme():
    """'dark' | 'light' | None when the OS (or this Qt build) does not say."""
    try:
        from PySide6.QtGui import QGuiApplication
        if QGuiApplication.instance() is not None:
            return {"Dark": "dark", "Light": "light"}.get(QGuiApplication.styleHints().colorScheme().name)
    except Exception:
        pass
    return None


def palette_name():
    """The palette actually in use: 'dark' or 'light' (never 'system')."""
    return _app_state["palette"]


def apply_palette(mode):
    """Select Dark, Light or System ('system' follows the OS colour scheme, dark when unknown;
    anything unrecognised is dark). Rebinds the colour tokens; callers rebuild the stylesheet."""
    mode = str(mode or "").strip().lower()
    if mode not in ("dark", "light", "system"):
        mode = "dark"
    name = (_os_scheme() or "dark") if mode == "system" else mode
    _app_state["theme"], _app_state["palette"] = mode, name
    globals().update(_PALETTES[name])
    _sync_tokens()


def apply_accent(name, rebuild=True):
    """Activate an accent preset. Updates the module-level ACCENT/ACCENT2 tokens that
    build_qss() and the combiner read, and immediately rebuilds the running application
    stylesheet so the change is visible without restarting. Safe to call at any time;
    does not touch the semantic success/warning/error colors. Pass rebuild=False when the
    caller rebuilds the stylesheet itself (apply_theme does), to avoid doing it twice."""
    key = str(name or "").strip().lower()
    if key not in ACCENT_PRESETS:
        key = DEFAULT_ACCENT
    _app_state["accent"] = key
    _sync_tokens()
    # Drop the cached checkmark PNGs: they are painted in the *previous* accent color and
    # build_qss() only regenerates them when the file is missing.
    try:
        from pathlib import Path
        for n in ("check_on", "check_off", "arrow_up", "arrow_down"):
            f = Path.home() / "NexusDownloader" / "cache" / f"{n}.png"
            try:
                f.unlink()
            except OSError:
                pass
    except Exception:
        pass
    # Rebuild the live stylesheet so the recolor is immediate. No QApplication -> nothing
    # to update (e.g. when apply_accent is called before the app exists).
    if rebuild:
        try:
            from PySide6.QtWidgets import QApplication
            app = QApplication.instance()
            if app is not None:
                app.setStyleSheet(build_qss())
        except Exception:
            pass
    return key


def set_appearance(accent=None, theme=None, reduced_motion=None, animation_enabled=None):
    """Apply appearance preferences in one call (used by Settings and on startup)."""
    if accent is not None:
        apply_accent(accent)
    if theme is not None:
        apply_palette(theme)
    if reduced_motion is not None:
        _app_state["reduced_motion"] = bool(reduced_motion)
    if animation_enabled is not None:
        _app_state["animation_enabled"] = bool(animation_enabled)


def appearance_state():
    return dict(_app_state)


def is_reduced_motion():
    return bool(_app_state.get("reduced_motion")) or not bool(_app_state.get("animation_enabled", True))


# Activate the defaults on import so the tokens are never stale.
apply_palette("dark")
apply_accent(DEFAULT_ACCENT, rebuild=False)

# What users read. Engine/database keep the raw status strings.
STATUS_LABELS = {
    "QUEUED": "Queued", "DOWNLOADING": "Downloading", "PAUSED": "Paused", "COMPLETED": "Completed",
    "ERROR": "Failed", "STOPPED": "Cancelled", "RETRYING": "Retrying",
}


def status_label(status):
    s = str(status or "").upper()
    return STATUS_LABELS.get(s, s.title())


def _check_png(name, color, points=((9, 18.5), (15.5, 25), (27, 11))):
    """Draw the checkmark once into a cache dir (QSS can only reference image files)."""
    from pathlib import Path
    from PySide6.QtCore import QPointF, Qt
    from PySide6.QtGui import QColor, QPainter, QPen, QPixmap
    d = Path.home() / "NexusDownloader" / "cache"; d.mkdir(parents=True, exist_ok=True)
    f = d / f"{name}.png"
    pm = QPixmap(36, 36); pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm); p.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen = QPen(QColor(color), 3.6); pen.setCapStyle(Qt.PenCapStyle.RoundCap); pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen); p.drawPolyline([QPointF(*pt) for pt in points]); p.end()
    pm.save(str(f), "PNG")
    return f.as_posix()


def accent_swatches():
    """-> [(name, hex, active)] for the Settings Appearance panel."""
    return [(n, c[0], n == _app_state.get("accent")) for n, c in ACCENT_PRESETS.items()]


def apply_appearance_from_settings(settings):
    """Read the appearance keys out of the app settings dict (missing/invalid keys fall
    back to defaults). Used on startup and when Settings is saved."""
    if not isinstance(settings, dict):
        return
    acc = settings.get("accent")
    theme_mode = settings.get("theme")
    if acc is not None:
        apply_accent(acc, rebuild=False)   # apply_theme() rebuilds the stylesheet once, afterwards
    if theme_mode is not None:
        apply_palette(theme_mode)
    if "reduced_motion" in settings:
        _app_state["reduced_motion"] = bool(settings["reduced_motion"])
    if "animation_enabled" in settings:
        _app_state["animation_enabled"] = bool(settings["animation_enabled"])


def tint(alpha):
    """Faint overlay colour for hover/track fills: white on dark, black on light (alpha 0-255)."""
    r, g, b = (int(v) for v in TINT.split(","))
    return QColor(r, g, b, alpha)


def accent_alpha(alpha):
    """The current accent as a translucent overlay (selected rows, pressed buttons), alpha 0-255."""
    c = QColor(ACCENT); c.setAlpha(alpha)
    return c


def _lighten(hex_color, amount=18):
    c = QColor(hex_color); h, s, l, _ = c.getHsl()
    return QColor.fromHsl(h, s, min(255, l + int(255 * amount / 100))).name()


def themed_icon(widget, name, token, size=0):
    """Give `widget` the icon `name` in the *current* value of colour token `token` ("TEXT", "ACCENT",
    ...) and remember the recipe, so retint_icons() can recolour it when the palette or accent changes."""
    widget.setProperty("nxIcon", f"{name}:{token}:{size}")
    _paint_icon(widget)


def _paint_icon(widget):
    spec = widget.property("nxIcon")
    if not spec:
        return
    from .icons import icon
    name, token, size = str(spec).split(":")
    ic = icon(name, globals()[token])
    if isinstance(widget, QLabel):
        widget.setPixmap(ic.pixmap(int(size), int(size)))
    else:
        widget.setIcon(ic)


def retint_icons(root):
    for w in root.findChildren(QWidget):
        _paint_icon(w)


def _rgba(hex_color, alpha):
    c = QColor(hex_color)
    return f"rgba({c.red()},{c.green()},{c.blue()},{alpha})"


def build_qss():
    CHECK_ON = _check_png("check_on", ACCENT); CHECK_OFF = _check_png("check_off", IND_CHECK_OFF)
    # Selected/highlighted states use the *active accent* with a low alpha, so an accent
    # change recolors them consistently instead of leaving a hardcoded cyan behind.
    def _a(hex_color, alpha):
        c = QColor(hex_color)
        return f"rgba({c.red()}, {c.green()}, {c.blue()}, {alpha})"
    A = _a(ACCENT, 0.16); A2 = _a(ACCENT, 0.26); A12 = _a(ACCENT, 0.12); A14 = _a(ACCENT, 0.14)
    A10 = _a(ACCENT, 0.10); A07 = _a(ACCENT, 0.07); A08 = _a(ACCENT, 0.08)
    A18 = _a(ACCENT, 0.18); A35 = _a(ACCENT, 0.35); A45 = _a(ACCENT, 0.45)
    ARROW_UP = _check_png("arrow_up", MUTED, ((9, 23), (18, 13), (27, 23))); ARROW_DOWN = _check_png("arrow_down", MUTED, ((9, 13), (18, 23), (27, 13)))
    return f"""
* {{ font-family: {FONT}; }}
QMainWindow, QDialog, QWidget {{ background: {BG}; color: {TEXT}; font-size: 13px; }}
QLabel {{ background: transparent; }}
QToolTip {{ background: {SURFACE2}; color: {TEXT}; border: 1px solid {BORDER}; padding: 5px 8px; border-radius: 6px; }}

/* sidebar */
#sidebar {{ background: {SIDEBAR}; border-right: 1px solid {BORDER}; }}
#sidebar QLabel {{ background: transparent; }}
#brand {{ font-size: 17px; font-weight: 700; color: {TEXT}; }}
#brandSub {{ font-size: 11px; color: {FAINT}; }}
#sideFooter {{ color: {MUTED}; font-size: 11px; background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 10px; padding: 9px 10px; }}
QPushButton#navButton {{
    background: transparent; border: none; border-radius: 9px; padding: 9px 12px; text-align: left;
    color: {MUTED}; font-weight: 600; min-height: 20px;
}}
QPushButton#navButton:hover {{ background: {SURFACE}; color: {TEXT}; }}
QPushButton#navButton:checked {{ background: {A10}; color: {ACCENT}; }}
QPushButton#navButton:focus {{ border: 1px solid {ACCENT}; }}

/* top bar */
#topbar {{ background: {BG}; border-bottom: 1px solid {BORDER}; }}
#pageTitle {{ font-size: 20px; font-weight: 700; letter-spacing: -0.2px; }}
#sectionTitle {{ font-size: 15px; font-weight: 700; }}
#muted {{ color: {MUTED}; }}
#warning {{ color: {WARN}; padding: 10px 12px; background: {_rgba(WARN,'0.10')}; border: 1px solid {_rgba(WARN,'0.28')}; border-radius: 8px; }}
#networkMode {{ color: {ACCENT}; font-weight: 600; padding: 8px 12px; background: {A07}; border: 1px solid {A18}; border-radius: 8px; }}

/* inputs */
QLineEdit, QSpinBox, QComboBox {{
    background: {SURFACE2}; border: 1px solid {BORDER}; border-radius: 8px; padding: 7px 11px;
    color: {TEXT}; min-height: 20px; selection-background-color: {A35};
}}
QLineEdit:hover, QSpinBox:hover, QComboBox:hover {{ border-color: {INPUT_HOVER_BORDER}; }}
QLineEdit:focus, QSpinBox:focus, QComboBox:focus {{ border: 1px solid {ACCENT}; }}
QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox QAbstractItemView {{ background: {SURFACE2}; border: 1px solid {BORDER}; selection-background-color: {A18}; outline: 0; }}
QSpinBox::up-button, QSpinBox::down-button {{ width: 20px; border: none; background: transparent; }}
QSpinBox::up-button {{ subcontrol-origin: border; subcontrol-position: top right; }}
QSpinBox::down-button {{ subcontrol-origin: border; subcontrol-position: bottom right; }}
QSpinBox::up-arrow {{ image: url({ARROW_UP}); width: 9px; height: 9px; }}
QSpinBox::down-arrow {{ image: url({ARROW_DOWN}); width: 9px; height: 9px; }}
#searchBox {{ padding-left: 12px; min-width: 240px; }}

/* buttons */
QPushButton, QToolButton {{
    background: {SURFACE2}; border: 1px solid {BORDER}; border-radius: 8px; padding: 7px 14px;
    color: {TEXT}; font-weight: 600; min-height: 20px;
}}
QPushButton:hover, QToolButton:hover {{ background: {HOVER_BG}; border-color: {HOVER_BORDER}; }}
QPushButton:pressed, QToolButton:pressed {{ background: {SUNKEN}; }}
QPushButton:focus, QToolButton:focus {{ border: 1px solid {ACCENT}; }}
QPushButton:disabled, QToolButton:disabled {{ color: {FAINT}; background: {SURFACE}; border-color: {BORDER}; }}
QPushButton#primaryButton {{
    background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 {ACCENT2}, stop:1 {ACCENT});
    color: {ON_ACCENT}; border: none; font-weight: 700; padding: 8px 18px;
}}
QPushButton#primaryButton:hover {{ background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 {_lighten(ACCENT2)}, stop:1 {_lighten(ACCENT)}); }}
QPushButton#primaryButton:pressed {{ background: {ACCENT2}; }}
QPushButton#dangerButton, QPushButton#dangerGhostButton {{ background: transparent; border: 1px solid {_rgba(DANGER,'0.35')}; color: {DANGER}; }}
QPushButton#dangerButton:hover, QPushButton#dangerGhostButton:hover {{ background: {_rgba(DANGER,'0.14')}; }}
QPushButton#successGhostButton {{ background: transparent; border: 1px solid {_rgba(SUCCESS,'0.35')}; color: {SUCCESS}; }}
QPushButton#successGhostButton:hover {{ background: {_rgba(SUCCESS,'0.14')}; }}
QPushButton#removeOnlyButton {{ background: {SURFACE2}; }}
QToolButton#toolAction {{ padding: 6px 10px; }}
QPushButton#chip {{ background: transparent; border: 1px solid {BORDER}; border-radius: 14px; padding: 4px 12px; color: {MUTED}; font-weight: 600; min-height: 18px; }}
QPushButton#chip:hover {{ color: {TEXT}; border-color: {HOVER_BORDER}; }}
QPushButton#chip:checked {{ background: {A12}; border-color: {A45}; color: {ACCENT}; }}
QPushButton[pill="on"] {{ background: {_rgba(DANGER,'0.12')}; border: 1px solid {_rgba(DANGER,'0.32')}; color: {DANGER}; border-radius: 14px; padding: 5px 14px; }}
QPushButton[pill="off"] {{ background: {_rgba(SUCCESS,'0.12')}; border: 1px solid {_rgba(SUCCESS,'0.32')}; color: {SUCCESS}; border-radius: 14px; padding: 5px 14px; }}

/* tables */
QTableWidget, QTableView {{
    background: {SURFACE}; alternate-background-color: {SURFACE}; border: 1px solid {BORDER}; border-radius: 12px;
    gridline-color: transparent; outline: 0; selection-background-color: transparent;
}}
QTableWidget::item {{ padding: 4px 10px; border-bottom: 1px solid {ROW_LINE}; }}
QTableWidget::item:selected {{ background: {A12}; color: {TEXT}; }}
QTableWidget::item:hover {{ background: rgba({TINT},0.025); }}
QHeaderView {{ background: {SURFACE}; }}
QHeaderView::section {{
    background: {SURFACE}; color: {MUTED}; border: none; border-bottom: 1px solid {BORDER};
    padding: 9px 10px; font-weight: 700; font-size: 11px; letter-spacing: 0.3px;
}}
QHeaderView::section:hover {{ color: {TEXT}; }}
QTableCornerButton::section {{ background: {SURFACE}; border: none; }}

QScrollBar:vertical {{ background: transparent; width: 11px; margin: 2px; }}
QScrollBar:horizontal {{ background: transparent; height: 11px; margin: 2px; }}
QScrollBar::handle {{ background: {SCROLL}; border-radius: 4px; min-height: 28px; min-width: 28px; }}
QScrollBar::handle:hover {{ background: {SCROLL_HOVER}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

/* tabs / groups / checks (dialogs) */
QTabWidget::pane {{ border: 1px solid {BORDER}; background: {SURFACE}; border-radius: 10px; top: -1px; }}
QTabBar::tab {{ background: transparent; color: {MUTED}; padding: 9px 16px; border: none; border-bottom: 2px solid transparent; margin-right: 2px; font-weight: 600; }}
QTabBar::tab:selected {{ color: {TEXT}; border-bottom: 2px solid {ACCENT}; }}
QTabBar::tab:hover:!selected {{ color: {TEXT}; }}
QGroupBox {{ border: 1px solid {BORDER}; border-radius: 10px; margin-top: 14px; padding: 14px; font-weight: 700; background: {SURFACE}; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 12px; padding: 0 6px; color: {MUTED}; }}
QCheckBox {{ background: transparent; spacing: 10px; padding: 5px 2px; color: {TEXT}; }}
QCheckBox:disabled {{ color: {FAINT}; }}
QCheckBox::indicator {{ width: 18px; height: 18px; border-radius: 5px; border: 1px solid {IND_BORDER}; background: {SUNKEN}; }}
QCheckBox::indicator:hover {{ border-color: {IND_HOVER_BORDER}; background: {IND_HOVER_BG}; }}
QCheckBox::indicator:checked {{ background: {A}; border: 1px solid {ACCENT}; image: url({CHECK_ON}); }}
QCheckBox::indicator:checked:hover {{ background: {A2}; }}
QCheckBox::indicator:disabled {{ background: {IND_DIS_BG}; border-color: {IND_DIS_BORDER}; }}
QCheckBox::indicator:checked:disabled {{ background: {IND_DIS_BG}; border-color: {IND_CHK_DIS_BORDER}; image: url({CHECK_OFF}); }}
QCheckBox:focus {{ color: {TEXT}; }}
QCheckBox::indicator:focus {{ border: 1px solid {ACCENT}; }}
QRadioButton {{ spacing: 10px; padding: 5px 2px; }}
QRadioButton::indicator {{ width: 16px; height: 16px; border-radius: 9px; border: 1px solid {IND_BORDER}; background: {SUNKEN}; }}
QRadioButton::indicator:hover {{ border-color: {IND_HOVER_BORDER}; }}
QRadioButton::indicator:checked {{ border: 1px solid {ACCENT}; background: qradialgradient(cx:0.5, cy:0.5, radius:0.5, fx:0.5, fy:0.5, stop:0 {ACCENT}, stop:0.45 {ACCENT}, stop:0.5 {SUNKEN}, stop:1 {SUNKEN}); }}
#settingsNav {{ background: {SIDEBAR}; border: none; border-right: 1px solid {BORDER}; padding: 10px 8px; outline: 0; }}
#settingsNav::item {{ padding: 8px 10px; border-radius: 8px; color: {MUTED}; }}
#settingsNav::item:selected {{ background: {A12}; color: {ACCENT}; }}
#settingsNav::item:hover:!selected {{ background: rgba({TINT},0.04); color: {TEXT}; }}
#filterNote {{ background: {_rgba(WARN,'0.08')}; border: 1px solid {_rgba(WARN,'0.3')}; border-radius: 8px; color: {WARN}; }}
QDialog {{ background: {SURFACE}; }}
QDialogButtonBox QPushButton {{ min-width: 96px; }}
#deletePath {{ color: {LONG_TEXT}; background: {BG}; border: 1px solid {BORDER}; border-radius: 7px; padding: 8px 10px; }}
#dialogTitle {{ font-size: 17px; font-weight: 700; }}
#detailKey {{ color: {MUTED}; }}
#detailVal {{ color: {TEXT}; }}

QLabel[tone="ok"] {{ color: {SUCCESS}; }}
QLabel[tone="warn"] {{ color: {WARN}; }}
QLabel[tone="bad"] {{ color: {DANGER}; }}
QLabel[tone="muted"] {{ color: {MUTED}; }}

/* empty state */
DropZone {{ background: transparent; border: 1.5px dashed {DASH}; border-radius: 14px; }}
DropZone[dragging="true"] {{ background: {A07}; border: 1.5px dashed {ACCENT}; }}
#emptyTitle {{ font-size: 16px; font-weight: 700; }}
#emptyHint {{ color: {MUTED}; }}
QToolButton#rowAction {{ padding: 0; border: none; background: transparent; border-radius: 6px; }}
QToolButton#rowAction:hover {{ background: rgba({TINT},0.09); }}
#dropOverlay {{ border: 2px dashed {ACCENT}; border-radius: 10px; background: {A08}; color: {ACCENT}; font-size: 15px; font-weight: 600; }}

/* footer / summary */
#footer {{ background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 10px; }}
#detailLong {{ background: {BG}; border: 1px solid {BORDER}; border-radius: 7px; padding: 4px 6px; color: {LONG_TEXT}; }}
#summary {{ color: {MUTED}; }}
#summary b {{ color: {TEXT}; }}

QMenu {{ background: {SURFACE2}; border: 1px solid {BORDER}; border-radius: 10px; padding: 6px; }}
QMenu::item {{ padding: 7px 22px 7px 12px; border-radius: 6px; margin: 1px 0; }}
QMenu::item:selected {{ background: {A14}; }}
QMenu::item:disabled {{ color: {FAINT}; }}
QMenu::separator {{ height: 1px; background: {BORDER}; margin: 5px 8px; }}
QMessageBox {{ background: {SURFACE}; }}
"""


class DialogFader(QObject):
    """App-wide event filter: every dialog fades in over ~160 ms."""
    DURATION = 160

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Type.Show and isinstance(obj, QDialog) and not isinstance(obj, QFileDialog) and obj.isWindow():
            if not getattr(obj, "_nx_faded", False):
                obj._nx_faded = True
                obj.setWindowOpacity(0.0)
                anim = QPropertyAnimation(obj, b"windowOpacity", obj)
                anim.setDuration(self.DURATION)
                anim.setStartValue(0.0); anim.setEndValue(1.0)
                anim.setEasingCurve(QEasingCurve.Type.OutCubic)
                anim.finished.connect(lambda o=obj: o.setWindowOpacity(1.0))
                obj._nx_anim = anim
                anim.start()
        return False


# ---- Live Animated Network Combiner Dashboard Widget --------------------
class NetworkCombiner(QWidget):
    """Reference-inspired multi-network combiner dashboard.

    Renders:
    - Animated directional particle paths for active adapters converging into a central
      combiner node.
    - Large actual total throughput display.
    - Compact live throughput history chart.
    - Per-network status and contribution indicators.

    Animation is driven by real adapter rates and respects the reduced-motion / disable-
    animation preferences. Idle/disconnected adapters show neutral gray and do not flow.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.adapters = []          # [{name, ip, rate, enabled, state, virtual, ...}]
        self.total_rate = 0.0
        self.history = deque(maxlen=60)
        self.phase = 0.0
        self.setMinimumHeight(210)
        self.torrent_rate = 0.0
        self.unattributed = 0.0
        self.setAccessibleName("Network combiner")

        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)       # ~25 FPS, only while visible with traffic (see _sync_timer)

    def update_metrics(self, adapters, total_rate, torrent_rate=0.0, unattributed=0.0):
        """Update metrics from real backend state.

        total_rate is HTTP download throughput only; torrent_rate is reported separately and
        unattributed is HTTP traffic no adapter report accounts for. An adapter rate of None
        means "not measured" and is shown as a dash, never as 0."""
        self.adapters = list(adapters or [])
        self.total_rate = max(0.0, float(total_rate or 0.0))
        self.torrent_rate = max(0.0, float(torrent_rate or 0.0))
        self.unattributed = max(0.0, float(unattributed or 0.0))
        self.history.append(self.total_rate)
        self._sync_timer()
        self._sync_accessible()
        self.update()

    def _sync_timer(self):
        run = self.isVisible() and self.total_rate > 0 and not is_reduced_motion()
        if run != self.timer.isActive():
            self.timer.start(40) if run else self.timer.stop()

    def showEvent(self, e):
        super().showEvent(e); self._sync_timer()

    def hideEvent(self, e):
        super().hideEvent(e); self._sync_timer()

    def _sync_accessible(self):
        parts = [f"HTTP download throughput {self._fmt_rate(self.total_rate)}"]
        for a in self.adapters:
            r = a.get("rate")
            parts.append(f"{a.get('name') or 'Adapter'}: " + ("not measured" if r is None else self._fmt_rate(r)))
        if self.unattributed > 0:
            parts.append(f"{self._fmt_rate(self.unattributed)} not attributed to an adapter")
        parts.append(f"Torrent traffic {self._fmt_rate(self.torrent_rate)}, shown separately")
        self.setAccessibleDescription(". ".join(parts))

    def _tick(self):
        if is_reduced_motion() or self.total_rate <= 0:
            return
        # Flow speed depends gently on total download rate
        speed = min(0.05, 0.01 + math.sqrt(self.total_rate / 10_000_000.0) * 0.01)
        self.phase = (self.phase + speed) % 1.0
        self.update()

    @staticmethod
    def _fmt_rate(b_s):
        if b_s is None:
            return "—"
        if b_s <= 0:
            return "0 B/s"
        if b_s < 1000:
            return f"{b_s:.0f} B/s"
        if b_s < 1000 * 1000:
            return f"{b_s / 1000.0:.1f} KB/s"
        if b_s < 1000 * 1000 * 1000:
            return f"{b_s / 1_000_000.0:.2f} MB/s"
        return f"{b_s / 1_000_000_000.0:.2f} GB/s"

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        rect = self.rect()
        w, h = rect.width(), rect.height()

        # Surface background card
        p.setPen(QPen(QColor(BORDER), 1))
        p.setBrush(QColor(SURFACE))
        p.drawRoundedRect(rect.adjusted(1, 1, -1, -1), 12, 12)

        accent_q = QColor(ACCENT)
        muted_q = QColor(MUTED)
        faint_q = QColor(FAINT)
        border_q = QColor(BORDER)

        # Center combiner node
        center_x = w * 0.48
        center_y = h * 0.48
        node_r = 28

        # Draw left-hand adapter paths into the combiner
        usable = [a for a in self.adapters if a.get("enabled", True) and a.get("state") != "DISCONNECTED"]
        if not usable:
            usable = [{"name": "No active adapters", "rate": 0.0, "state": "DISCONNECTED"}]

        n_paths = min(len(usable), 5)
        path_y_spacing = max(26, min(42, int((h - 40) / max(1, n_paths))))
        start_y = center_y - (n_paths - 1) * path_y_spacing * 0.5

        for idx, a in enumerate(usable[:n_paths]):
            sy = start_y + idx * path_y_spacing
            sx = 20.0
            rate = a.get("rate", 0.0)
            active = (rate or 0) > 0 and a.get("state") in (None, "ACTIVE", "ONLINE", "DOWNLOADING")

            # Curved path from adapter to combiner
            path = QPainterPath()
            path.moveTo(sx + 140, sy)
            path.cubicTo(center_x * 0.5, sy, center_x * 0.7, center_y, center_x - node_r, center_y)

            path_col = accent_q if active else border_q
            pen_w = 2.5 if active else 1.2
            p.setPen(QPen(path_col, pen_w, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawPath(path)

            # Flowing dash particles along active paths
            if active and not is_reduced_motion():
                p.save()
                p.setPen(QPen(QColor(ACCENT2), 4.0, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
                # 3 particles spaced along length
                for p_idx in range(3):
                    t = (self.phase + p_idx / 3.0) % 1.0
                    pt = path.pointAtPercent(t)
                    p.drawPoint(pt)
                p.restore()

            # Adapter label + speed on left
            p.setPen(QPen(QColor(TEXT) if active else muted_q, 1))
            lbl = str(a.get("name") or "Adapter")
            if len(lbl) > 18:
                lbl = lbl[:16] + "…"
            p.drawText(int(sx), int(sy - 3), lbl)

            p.setPen(QPen(accent_q if active else faint_q, 1))
            f = p.font()
            f.setPointSize(9)
            p.setFont(f)
            p.drawText(int(sx), int(sy + 12), self._fmt_rate(rate))

        # Central combiner symbol
        p.setPen(QPen(accent_q if self.total_rate > 0 else border_q, 2))
        p.setBrush(QColor(SURFACE2))
        p.drawEllipse(QPointF(center_x, center_y), node_r, node_r)

        # Inner icon in combiner
        p.setPen(QPen(accent_q if self.total_rate > 0 else muted_q, 2.0, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawLine(int(center_x - 10), int(center_y), int(center_x + 10), int(center_y))
        p.drawLine(int(center_x), int(center_y - 10), int(center_x), int(center_y + 10))

        # Main total throughput readout on right
        rx = center_x + node_r + 24
        p.setPen(QPen(muted_q, 1))
        f = p.font()
        f.setPointSize(10)
        p.setFont(f)
        p.drawText(int(rx), int(center_y - 20), "HTTP DOWNLOAD THROUGHPUT")

        p.setPen(QPen(QColor(TEXT), 1))
        f = p.font()
        f.setPointSize(22)
        f.setBold(True)
        p.setFont(f)
        p.drawText(int(rx), int(center_y + 12), self._fmt_rate(self.total_rate))

        # Honest breakdown: torrents are not part of the HTTP total; unattributed traffic is not
        # assigned to any adapter.
        f = p.font(); f.setPointSize(9); f.setBold(False); p.setFont(f)
        p.setPen(QPen(muted_q, 1))
        sub = f"Torrents: {self._fmt_rate(self.torrent_rate)} (not included)"
        if self.unattributed > 0:
            sub += f"  ·  {self._fmt_rate(self.unattributed)} not attributed to an adapter"
        p.drawText(int(rx), int(center_y + 32), sub)

        # Live throughput sparkline chart on far right
        chart_x = w - 180
        chart_w = 160
        chart_y = 20
        chart_h = h - 40

        if chart_x > rx + 180 and self.history:
            p.setPen(QPen(border_q, 1))
            p.setBrush(QColor(BG))
            p.drawRoundedRect(chart_x, chart_y, chart_w, chart_h, 8, 8)

            max_val = max(max(self.history), 1.0)
            c_path = QPainterPath()
            points = list(self.history)
            n_pts = len(points)

            for i, val in enumerate(points):
                px = chart_x + (i / max(1, n_pts - 1)) * chart_w
                py = chart_y + chart_h - (val / max_val) * (chart_h - 10) - 5
                if i == 0:
                    c_path.moveTo(px, py)
                else:
                    c_path.lineTo(px, py)

            p.setPen(QPen(accent_q, 1.8))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawPath(c_path)
