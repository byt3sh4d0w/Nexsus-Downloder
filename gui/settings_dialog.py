"""Settings: every control reads and writes a real key of settings.json (see main_window.load_settings)."""
from pathlib import Path

from PySide6.QtCore import Qt, QSize
from PySide6.QtWidgets import (QCheckBox, QDialog, QFileDialog, QFormLayout, QFrame, QHBoxLayout, QLabel, QLineEdit,
                               QListWidget, QListWidgetItem, QMessageBox, QPushButton, QSizePolicy, QSpinBox,
                               QStackedWidget, QVBoxLayout, QWidget)
from PySide6.QtWidgets import QButtonGroup, QComboBox, QGridLayout

from . import theme
from .icons import icon
from .theme import ACCENT_PRESETS, accent_swatches

MAX_TOTAL_WORKERS = 64          # same ceiling the engine scheduler uses (simultaneous x connections)

PAGES = [("General", "settings"), ("Appearance", "paint_palette"), ("Downloads", "download"), ("Network", "network"), ("Torrents", "torrent"), ("Advanced", "info")]


def make_check(text, checked=False, tip=""):
    """Checkbox that is only as wide as its label, so clicking empty space beside it never toggles it."""
    c = QCheckBox(text); c.setChecked(bool(checked)); c.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
    if tip: c.setToolTip(tip)
    return c


def _note(text, tone="muted"):
    l = QLabel(text); l.setWordWrap(True); l.setProperty("tone", tone); return l


class SettingsDialog(QDialog):
    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.settings = dict(settings)           # edited copy: Cancel/close never touches the live settings
        self.setWindowTitle("Settings")
        self.setMinimumSize(780, 560)
        root = QVBoxLayout(self); root.setContentsMargins(0, 0, 0, 0); root.setSpacing(0)
        body = QHBoxLayout(); body.setSpacing(0); root.addLayout(body, 1)
        self.nav = QListWidget(); self.nav.setObjectName("settingsNav"); self.nav.setFixedWidth(190); self.nav.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff); self.nav.setIconSize(QSize(18, 18))
        self.stack = QStackedWidget()
        body.addWidget(self.nav); body.addWidget(self.stack, 1)
        self._pages = {}
        for name, ic in PAGES:
            it = QListWidgetItem(icon(ic, theme.MUTED), name); it.setSizeHint(QSize(190, 38)); self.nav.addItem(it)
        self.nav.currentRowChanged.connect(self.stack.setCurrentIndex)
        self.stack.addWidget(self._page(self._general())); self.stack.addWidget(self._page(self._appearance())); self.stack.addWidget(self._page(self._downloads()))
        self.stack.addWidget(self._page(self._network())); self.stack.addWidget(self._page(self._torrents()))
        self.stack.addWidget(self._page(self._advanced()))
        self.nav.setCurrentRow(0)

        foot = QFrame(); foot.setObjectName("footer"); fl = QHBoxLayout(foot); fl.setContentsMargins(18, 10, 18, 10)
        self.error = QLabel(""); self.error.setProperty("tone", "bad"); self.error.setWordWrap(True)
        fl.addWidget(self.error, 1)
        cancel = QPushButton("Cancel"); cancel.clicked.connect(self.reject)
        self.save_btn = QPushButton("Save"); self.save_btn.setObjectName("primaryButton"); self.save_btn.clicked.connect(self.accept_settings); self.save_btn.setDefault(True)
        fl.addWidget(cancel); fl.addWidget(self.save_btn)
        root.addWidget(foot)

    # ---- layout helpers -------------------------------------------------
    def _page(self, widget):
        w = QWidget(); l = QVBoxLayout(w); l.setContentsMargins(26, 22, 26, 18); l.addWidget(widget); l.addStretch(); return w

    def _section(self, title):
        w = QWidget(); v = QVBoxLayout(w); v.setContentsMargins(0, 0, 0, 0); v.setSpacing(10)
        t = QLabel(title); t.setObjectName("dialogTitle"); v.addWidget(t)
        return w, v

    def _field(self, layout, label, widget, scope):
        """label + control + a caption saying exactly what the setting affects."""
        box = QVBoxLayout(); box.setSpacing(3)
        l = QLabel(label); box.addWidget(l)
        box.addWidget(widget, 0, Qt.AlignmentFlag.AlignLeft if isinstance(widget, QSpinBox) else Qt.AlignmentFlag(0)); box.addWidget(_note(scope)); layout.addLayout(box)

    def _check(self, layout, text, key, scope):
        c = make_check(text, self.settings.get(key, False))
        layout.addWidget(c); layout.addWidget(_note(scope))
        return c

    def _spin(self, lo, hi, key, suffix=""):
        s = QSpinBox(); s.setRange(lo, hi); s.setValue(int(self.settings.get(key, lo))); s.setSuffix(suffix); s.setMinimumWidth(130)
        s.setKeyboardTracking(False); return s

    def _dir_row(self, key, placeholder):
        e = QLineEdit(str(self.settings.get(key, "") or "")); e.setPlaceholderText(placeholder)
        b = QPushButton(); b.setIcon(icon("folder", theme.TEXT)); b.setToolTip("Choose folder"); b.setAccessibleName("Choose folder"); b.setFixedWidth(52)       # 48 was 2 px under the style's minimum for an icon button
        b.clicked.connect(lambda: (lambda d: d and e.setText(d))(QFileDialog.getExistingDirectory(self, "Choose folder", e.text() or str(Path.home()))))
        w = QWidget(); h = QHBoxLayout(w); h.setContentsMargins(0, 0, 0, 0); h.addWidget(e, 1); h.addWidget(b)
        w.edit = e
        return w

    # ---- pages ----------------------------------------------------------
    def _general(self):
        w, v = self._section("General")
        self.dl_dir = self._dir_row("download_dir", str(Path.home() / "Downloads"))
        self._field(v, "Default download folder", self.dl_dir, "Used as the starting folder for new downloads. Existing downloads keep their own folder.")
        self.ask_location = self._check(v, "Ask where to save each new download", "ask_location",
                                        "When off, the Add Download window hides the folder picker and uses the default folder.")
        self.auto_start = self._check(v, "Start new downloads automatically", "auto_start", "Applies to downloads added from now on.")
        self.open_completed = self._check(v, "Open the folder when a download completes", "open_completed", "Applies to every download that finishes while this is on.")
        self.confirm_delete = self._check(v, "Ask before removing or deleting downloads", "confirm_delete", "Global. Turning it off uses your remembered choice (see Advanced).")
        self.accept_drop = self._check(v, "Accept dropped links and .torrent files", "accept_url_drop", "Global. Dropping onto the window or the empty list adds a download.")
        return w

    def _downloads(self):
        w, v = self._section("Downloads")
        self.connections = self._spin(1, 32, "connections")
        self._field(v, "Default connections per file", self.connections, "New downloads only. Each download stores its own value (Add Download window); running downloads are never changed.")
        self.simultaneous = self._spin(1, 16, "simultaneous_files")
        self._field(v, "Maximum simultaneous downloads", self.simultaneous, "Global. Extra downloads wait in the queue and start automatically.")
        self.max_speed = self._spin(0, 1024 * 1024, "max_speed_kbps", " KB/s")
        self._field(v, "Speed limit per download (0 = unlimited)", self.max_speed, "Applies to downloads started or resumed from now on; running downloads keep their current limit.")
        self.read_timeout = self._spin(5, 300, "read_timeout", " s")
        self._field(v, "Server response timeout", self.read_timeout, "Downloads started or resumed from now on.")
        self.max_retries = self._spin(0, 10, "max_retries")
        self._field(v, "Automatic retries after a network error", self.max_retries, "Downloads started or resumed from now on. Expired links are never retried automatically.")
        v.addWidget(_note("Partial data is always kept when you pause or stop a download, so it can resume where it left off."))
        return w

    def _appearance(self):
        w, v = self._section("Appearance")
        self.accent_group = QButtonGroup(self); self.accent_group.setExclusive(True)
        self.accent_buttons = {}
        grid = QGridLayout(); grid.setColumnStretch(0, 1); grid.setColumnStretch(1, 1)
        for i, (name, hex_color, active) in enumerate(accent_swatches()):
            b = QPushButton(); b.setCheckable(True); b.setChecked(active); b.setFixedWidth(116); b.setMinimumHeight(60)
            b.setAccessibleName(f"{name} accent")
            b._swatch = hex_color
            b._swatch_name = name
            b.setStyleSheet(self._swatch_style(b))
            b.clicked.connect(lambda _=False, n=name: self._select_accent(n))
            self.accent_group.addButton(b); self.accent_buttons[name] = b
            lbl = QLabel(name.capitalize()); lbl.setAccessibleName(f"{name} accent")
            row, col = divmod(i, 2)
            grid.addWidget(b, row, col * 2); grid.addWidget(lbl, row, col * 2 + 1)
        v.addLayout(grid)
        self.theme_mode = QComboBox(); self.theme_mode.addItems(["Dark", "Light", "System"])
        self.theme_mode.setCurrentText({"dark": "Dark", "light": "Light", "system": "System"}.get(
            str(self.settings.get("theme", "dark")).lower(), "Dark"))
        self._field(v, "Theme", self.theme_mode, "Dark is the default. Light switches the base palette; System follows the OS.")
        self.reduced_motion = make_check("Reduced motion", bool(self.settings.get("reduced_motion", False)),
                                         "Disables the animated network-combiner flow and dialog fades.")
        v.addWidget(self.reduced_motion)
        self.animation_enabled = make_check("Enable animations", bool(self.settings.get("animation_enabled", True)),
                                            "Turns the combiner flow and transitions back on when Reduced motion is off.")
        v.addWidget(self.animation_enabled)
        v.addWidget(_note("Accent colors recolor active paths, progress, the combiner and selected states. Success, warning and error colors are never recolored."))
        return w

    def _select_accent(self, name):
        self.accent_buttons[name].setChecked(True)          # exclusive group: this unchecks the others
        for b in self.accent_buttons.values():
            b.setStyleSheet(self._swatch_style(b))

    def _swatch_style(self, b):
        """A solid colored square with a selection ring when checked."""
        sel = theme.TEXT if b.isChecked() else theme.BORDER     # TEXT: a ring in the accent colour would vanish against its own fill
        return (f"background: {b._swatch}; border: 2px solid {sel}; border-radius: 10px; "
                f"min-height: 60px; min-width: 116px;")

    def _network(self):
        w, v = self._section("Network")
        self.multi_network = self._check(v, "Combine all enabled online adapters for HTTP/HTTPS downloads", "multi_network",
                                         "New and resumed downloads. Byte-range connections are spread across adapters; this is not OS-level bonding.")
        self.net_interval = self._spin(1, 15, "network_interval", " s")
        self._field(v, "Adapter check interval", self.net_interval, "Global. How often adapters and internet access are re-tested.")
        v.addWidget(QLabel("Adapters used for downloads"))
        self.iface_checks = {}
        parent = self.parentWidget()
        monitor = getattr(parent, "net", None); rows = getattr(parent, "net_rows", {}) or {}
        from core.network_monitor import is_combinable
        if not rows:
            v.addWidget(_note("No adapters detected yet."))
        for ifname, info in sorted(rows.items(), key=lambda kv: str(getattr(kv[1], "name", kv[0])).lower()):
            name = str(getattr(info, "name", ifname)); ip = str(getattr(info, "ip", "") or "no address")
            c = make_check(f"{name}  —  {ip}  ({str(getattr(info, 'state', '')).title()})", monitor.is_enabled(ifname) if monitor else True)
            if not is_combinable(name):
                c.setChecked(False); c.setEnabled(False); c.setToolTip("Bluetooth is never combined: too slow to help.")
            else:
                self.iface_checks[ifname] = c
            v.addWidget(c)
        v.addWidget(_note("Unchecked adapters are skipped by new and resumed downloads. Bluetooth is always excluded."))
        return w

    def _torrents(self):
        w, v = self._section("Torrents")
        from core.torrent import lt
        if lt is None:
            v.addWidget(_note("The BitTorrent engine (libtorrent) is not installed, so these settings are saved but unused until it is.", "warn"))
        self.torrent_dir = self._dir_row("torrent_dir", "Same as the default download folder")
        self._field(v, "Torrent download folder", self.torrent_dir, "Starting folder in the Add Torrent window.")
        self.torrent_port = self._spin(1024, 65535, "torrent_port")
        self._field(v, "Listening port", self.torrent_port, "Applied to the torrent engine when you save.")
        self.torrent_down = self._spin(0, 1024 * 1024, "torrent_down_kbps", " KB/s")
        self._field(v, "Download limit (0 = unlimited)", self.torrent_down, "All torrents, applied when you save.")
        self.torrent_up = self._spin(0, 1024 * 1024, "torrent_up_kbps", " KB/s")
        self._field(v, "Upload limit (0 = unlimited)", self.torrent_up, "All torrents, applied when you save.")
        self.torrent_auto = self._check(v, "Resume torrents automatically when Nexus starts", "torrent_auto_resume", "Applies at the next start. Off = restored torrents stay paused.")
        return w

    def _advanced(self):
        w, v = self._section("Advanced")
        v.addWidget(_note("These settings are for troubleshooting. The defaults are right for most people."))
        self.defender = self._check(v, "Scan completed files with Windows Defender", "defender_scan", "Windows only. Uses CPU/disk while scanning; applies to downloads that finish from now on.")
        v.addWidget(QLabel("Remembered answer for “Remove / Delete files”"))
        self.delete_action = QLabel({"remove_only": "Always remove from list only", "delete_files": "Always delete files"}.get(self.settings.get("delete_action", ""), "None — Nexus asks each time"))
        reset = QPushButton("Forget remembered answer"); reset.setEnabled(bool(self.settings.get("delete_action")))
        def forget():
            self.settings["delete_action"] = ""; self.delete_action.setText("None — Nexus asks each time"); reset.setEnabled(False)
        reset.clicked.connect(forget)
        row = QHBoxLayout(); row.addWidget(self.delete_action); row.addStretch(); row.addWidget(reset); v.addLayout(row)
        v.addWidget(QLabel("Remembered answer for “File exists already”"))
        names = {"rename": "Always rename", "overwrite": "Always overwrite", "use": "Always use the existing file"}
        self.conflict_action = QLabel(names.get(self.settings.get("conflict_action", ""), "None — Nexus asks each time"))
        reset2 = QPushButton("Forget remembered answer"); reset2.setEnabled(bool(self.settings.get("conflict_action")))
        def forget2():
            self.settings["conflict_action"] = ""; self.conflict_action.setText("None — Nexus asks each time"); reset2.setEnabled(False)
        reset2.clicked.connect(forget2)
        row2 = QHBoxLayout(); row2.addWidget(self.conflict_action); row2.addStretch(); row2.addWidget(reset2); v.addLayout(row2)
        v.addWidget(QLabel("Fixed by Nexus"))
        v.addWidget(_note("TLS certificate verification: always on.   Redirect limit: 10.   Remote access: not available."))
        return w

    # ---- save -----------------------------------------------------------
    def _check_dir(self, widget, label):
        text = widget.edit.text().strip()
        if not text:
            return ""
        p = Path(text).expanduser()
        try:
            p.mkdir(parents=True, exist_ok=True)
            probe = p / ".nexus_write_test"; probe.write_text("x"); probe.unlink()
        except Exception as exc:
            raise ValueError(f"{label}: cannot write to “{text}” ({exc.__class__.__name__}). Choose another folder.")
        return str(p)

    def accept_settings(self):
        try:
            download_dir = self._check_dir(self.dl_dir, "Default download folder")
            torrent_dir = self._check_dir(self.torrent_dir, "Torrent download folder")
        except ValueError as exc:
            self.error.setText(str(exc)); self.nav.setCurrentRow(0 if "Default" in str(exc) else 3); return
        simul, conns = self.simultaneous.value(), self.connections.value()
        if simul * conns > MAX_TOTAL_WORKERS:
            conns = max(1, MAX_TOTAL_WORKERS // simul); self.connections.setValue(conns)
            QMessageBox.information(self, "Connections adjusted",
                                    f"Default connections per file was lowered to {conns} so that {simul} simultaneous downloads x {conns} connections stays within {MAX_TOTAL_WORKERS} workers.")
        self.error.setText("")
        self.settings.update({
            "download_dir": download_dir, "ask_location": self.ask_location.isChecked(),
            "auto_start": self.auto_start.isChecked(), "open_completed": self.open_completed.isChecked(),
            "confirm_delete": self.confirm_delete.isChecked(), "accept_url_drop": self.accept_drop.isChecked(),
            "connections": conns, "simultaneous_files": simul, "max_speed_kbps": self.max_speed.value(),
            "read_timeout": self.read_timeout.value(), "max_retries": self.max_retries.value(),
            "multi_network": self.multi_network.isChecked(), "network_interval": self.net_interval.value(),
            "torrent_dir": torrent_dir, "torrent_port": self.torrent_port.value(),
            "torrent_down_kbps": self.torrent_down.value(), "torrent_up_kbps": self.torrent_up.value(),
            "torrent_auto_resume": self.torrent_auto.isChecked(), "defender_scan": self.defender.isChecked(),
            "accent": self._selected_accent(), "theme": self.theme_mode.currentText().lower(),
            "reduced_motion": self.reduced_motion.isChecked(), "animation_enabled": self.animation_enabled.isChecked(),
        })
        self.interface_states = {k: c.isChecked() for k, c in self.iface_checks.items()}
        self.parent().apply_settings(self.settings, interfaces=self.interface_states)
        self.accept()

    def _selected_accent(self):
        for name, b in self.accent_buttons.items():
            if b.isChecked():
                return name
        return "orange"
