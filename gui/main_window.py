import json
import os
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from PySide6.QtCore import QTimer, Qt, Signal, Slot, QRect, QSize, QUrl
from PySide6.QtGui import QColor, QCursor, QDesktopServices, QFont, QGuiApplication, QKeySequence, QPainter, QPen, QIcon, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QApplication, QAbstractItemView, QCheckBox, QComboBox, QDialog,
    QDialogButtonBox, QFileDialog, QFormLayout, QFrame, QGridLayout,
    QGroupBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMainWindow,
    QMenu, QMessageBox, QPushButton, QSpinBox, QStackedWidget, QTabWidget, QTableWidget, QToolButton,
    QTableWidgetItem, QButtonGroup, QVBoxLayout, QWidget
)

from .new_download import NewDownloadDialog
from .torrent_dialog import TorrentDialog
from . import torrent_page as TP          # torrent column constants stay namespaced: download_table has its own C_*
from .torrent_page import TorrentPage
from .delete_dialog import DeleteDownloadsDialog
from .details_dialog import DownloadDetailsDialog
from .network_table import HistoryTable, NetworkTable
from .download_table import DownloadTable, ACTIVE_STATES, C_NAME, C_PROG, C_ELAPSED, C_ETA, R_SEARCH, row_actions
from .icons import icon
from . import theme
from core.links import normalize_url, unique_path, magnet_info_hash
from .settings_dialog import SettingsDialog
from .choice_dialog import ask_choice
from .conflict_dialog import ask_conflict
from .theme import STATUS_COLORS, NetworkCombiner, accent_swatches, apply_appearance_from_settings, appearance_state
from core import cleanup
from core.downloader import Downloader
from core.network_monitor import NetworkMonitor, attribute_rates, is_combinable
from core.version import APP_NAME, APP_VERSION
from core.security import safe_filename, validate_download_url
from core.torrent import TorrentManager
from database.db import Database


APP_DIR = Path.home() / "NexusDownloader"
SETTINGS_PATH = APP_DIR / "settings.json"


def load_settings():
    defaults = {
        "auto_start": True,
        "confirm_delete": True,
        "open_completed": False,
        "accept_url_drop": True,
        "network_interval": 2,
        "max_speed_kbps": 0,
        "simultaneous_files": 3,
        "connections": 8,
        "defender_scan": False,
        "torrent_port": 6881,
        "torrent_auto_resume": False,
        "multi_network": True,
        "delete_action": "", "conflict_action": "",
        "download_dir": "", "ask_location": True, "read_timeout": 60, "max_retries": 3,
        "torrent_dir": "", "torrent_down_kbps": 0, "torrent_up_kbps": 0,
        # appearance (must be listed here: load_settings() drops every key that has no default)
        "accent": theme.DEFAULT_ACCENT, "theme": "dark", "reduced_motion": False, "animation_enabled": True,
    }
    try:
        data = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
        defaults.update({k: v for k, v in data.items() if k in defaults})
    except Exception:
        pass
    return defaults


def save_settings(settings):
    APP_DIR.mkdir(parents=True, exist_ok=True)
    SETTINGS_PATH.write_text(json.dumps(settings, indent=2), encoding="utf-8")


# P5.15 — sane ceiling on simultaneous_files * connections so a user
# combination can't spawn hundreds of OS worker threads.
MAX_TOTAL_WORKERS = 64

ASSETS = Path(__file__).resolve().parent.parent / "assets"


class StatusBadge(QWidget):
    """Small rounded pill with a status dot, matching the app's status vocabulary."""

    def __init__(self, status="QUEUED", parent=None):
        super().__init__(parent)
        self.status = str(status or "QUEUED").upper()
        self.setMinimumHeight(26)

    def set_status(self, status):
        self.status = str(status or "QUEUED").upper()
        self.update()
        self.updateGeometry()

    def sizeHint(self):
        fm = self.fontMetrics()
        w = fm.horizontalAdvance(self.status.title()) + 40
        return QSize(max(w, 78), 24)

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        color = QColor(STATUS_COLORS.get(self.status, "#8b929c"))
        bg = QColor(color); bg.setAlpha(32)
        h = min(self.height() - 4, 22)
        text = self.status.title()
        w = self.fontMetrics().horizontalAdvance(text) + 26
        rect = QRect(4, (self.height() - h) // 2, w, h)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(bg)
        p.drawRoundedRect(rect, h / 2, h / 2)
        dot_d = 6
        dot_rect = QRect(rect.left() + 9, rect.center().y() - dot_d // 2, dot_d, dot_d)
        p.setBrush(color)
        p.drawEllipse(dot_rect)
        p.setPen(color)
        font = p.font(); font.setBold(True); font.setPointSize(max(8, font.pointSize() - 1)); p.setFont(font)
        text_rect = QRect(dot_rect.right() + 7, rect.top(), rect.right() - dot_rect.right() - 5, rect.height())
        p.drawText(text_rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, text)
        p.end()


class FlowProgressBar(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.value = 0
        self.status = "QUEUED"
        self.phase = 0.0
        self.setMinimumSize(100, 24)

    def set_progress(self, value, status):
        self.value = max(0, min(100, int(value)))
        self.status = str(status or "QUEUED").upper()
        self.update()

    def advance(self):
        if self.status == "DOWNLOADING" and self.value < 100:
            self.phase = (self.phase + 0.045) % 1.0
            self.update()

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = self.rect().adjusted(2, 4, -2, -4)
        radius = max(5, r.height() // 2)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(theme.TRACK))
        p.drawRoundedRect(r, radius, radius)
        fill_w = int(r.width() * self.value / 100.0)
        if fill_w > 0:
            fill = r.adjusted(0, 0, -(r.width() - fill_w), 0)
            color = STATUS_COLORS.get(self.status, "#64748b")
            p.setBrush(QColor(color))
            p.drawRoundedRect(fill, radius, radius)
            if self.status == "DOWNLOADING" and fill_w > 12:
                p.save()
                p.setClipRect(fill)
                p.setPen(QPen(theme.tint(60), 12))
                spacing = 30
                travel = int((r.width() + spacing) * self.phase)
                x = r.left() - r.height() + travel - r.width() - spacing
                while x < r.right() + r.height():
                    p.drawLine(x, r.bottom(), x + r.height(), r.top())
                    x += spacing
                p.restore()
        p.setPen(QColor(theme.TEXT))
        p.drawText(r, Qt.AlignmentFlag.AlignCenter, f"{self.value}%")
        p.end()


class DropZone(QFrame):
    """Empty state of the download list; also accepts dropped links / .torrent files."""
    urlDropped = Signal(str)
    torrentDropped = Signal(str)
    addClicked = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        layout = QVBoxLayout(self); layout.setContentsMargins(24, 24, 24, 24); layout.setSpacing(8)
        layout.addStretch(1)
        self.icon_label = QLabel(); self.icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        theme.themed_icon(self.icon_label, "download", "ACCENT", 46)
        self.title = QLabel("No downloads yet"); self.title.setObjectName("emptyTitle"); self.title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.hint = QLabel("Click Add Download, or drop a link or .torrent file anywhere in this window.")
        self.hint.setObjectName("emptyHint"); self.hint.setAlignment(Qt.AlignmentFlag.AlignCenter); self.hint.setWordWrap(True)
        self.button = QPushButton("Add Download"); self.button.setObjectName("primaryButton"); theme.themed_icon(self.button, "add", "ON_ACCENT")
        self.button.clicked.connect(self.addClicked)
        layout.addWidget(self.icon_label); layout.addWidget(self.title); layout.addWidget(self.hint)
        row = QHBoxLayout(); row.addStretch(); row.addWidget(self.button); row.addStretch(); layout.addLayout(row)
        layout.addStretch(1)

    def set_mode(self, empty_db):
        if empty_db:
            self.title.setText("No downloads yet")
            self.hint.setText("Click Add Download, or drop a link or .torrent file anywhere in this window.")
            self.button.setVisible(True)
        else:
            self.title.setText("Nothing matches this view")
            self.hint.setText("Try another filter or clear the search box.")
            self.button.setVisible(False)

    @staticmethod
    def values_from_mime(mime):
        """-> [("torrent", path) | ("url", text)] for a drop/paste payload."""
        out = []
        if mime.hasUrls():
            for qurl in mime.urls():
                value = qurl.toLocalFile() or qurl.toString()
                if value.lower().endswith(".torrent") and os.path.isfile(value):
                    out.append(("torrent", value))
                elif value:
                    out.append(("url", value))
        elif mime.hasText() and mime.text().strip():
            out.append(("url", mime.text().strip()))
        return out

    def _set_drag(self, on):
        self.setProperty("dragging", on)
        self.style().unpolish(self); self.style().polish(self)

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls() or event.mimeData().hasText():
            event.acceptProposedAction(); self._set_drag(True)
        else:
            event.ignore()

    def dragLeaveEvent(self, event):
        self._set_drag(False); super().dragLeaveEvent(event)

    def dropEvent(self, event):
        self._set_drag(False)
        for kind, value in self.values_from_mime(event.mimeData()):
            (self.torrentDropped if kind == "torrent" else self.urlDropped).emit(value)
        event.acceptProposedAction()


PreferencesDialog = SettingsDialog   # old name kept for compatibility


class MainWindow(QMainWindow):
    download_update = Signal(int, object)
    network_update = Signal(object, bool, bool, str)           # infos, purge, manual, error text

    def __init__(self):
        super().__init__()
        self.download_update.connect(self.update_job)
        self.network_update.connect(self._apply_network_update)
        self._net_mutex = threading.Lock()          # guards _net_running/_net_next: "busy? else queue" and "finish, take queued" are atomic
        self._net_running = False
        self._net_next = None                       # (purge, manual) wanted while a scan was running: one coalesced re-scan, never dropped
        self._net_manual = ""                       # "refresh" / "test" while a button-started operation is in flight (GUI thread only)
        self._last_scan_thread = None
        # P3.9 — set before anything is torn down; background threads check
        # this before touching any Qt object so nothing fires into a
        # partially-destroyed window during shutdown.
        self._shutting_down = threading.Event()
        # P2.5 — rid -> table row index, so update_job()/_ui_tick() don't
        # linear-scan the whole table on every progress event.
        self._row_of = {}   # legacy; the table now tracks rows itself
        self._bulk = False
        self._removing = set()   # rids currently being removed (guards duplicate/re-entrant calls)
        # P4.13 — queued jobs wait for the first network scan (or this grace
        # window, whichever comes first) before auto-starting, so combined
        # networking isn't silently skipped just because the app only just
        # opened. The window itself is never delayed by this.
        self._startup_time = time.time()
        self._initial_scan_done = False
        # P5.18 — Defender scans are serialized through this queue instead
        # of spawning one powershell.exe per completed download.
        self._defender_queue = []
        self._defender_proc = None
        self.setWindowTitle(f"{APP_NAME} v{APP_VERSION}")
        self.setWindowIcon(QIcon(str(ASSETS / "logo.png")))
        self.setAcceptDrops(True)
        self.resize(1440, 880)
        self.setMinimumSize(980, 640)
        self.settings = load_settings()
        self.db = Database()
        self.jobs = {}
        self.job_meta = {}
        self.net = NetworkMonitor()
        self.net_rows = {}
        self.torrent_manager = None
        self.torrent_meta = {}
        self._build()
        self._retry_pending_cleanup()
        self._restore_downloads()
        self.refresh_networks(purge=False)
        self.timer = QTimer(self); self.timer.timeout.connect(self._ui_tick); self.timer.timeout.connect(self._torrent_tick); self.timer.timeout.connect(self._poll_defender_queue); self.timer.start(1000)
        self.progress_timer = QTimer(self); self.progress_timer.timeout.connect(self._animate_progress_bars); self.progress_timer.start(40)
        self.net_timer = QTimer(self); self.net_timer.timeout.connect(lambda: self.refresh_networks(False)); self.net_timer.start(int(self.settings["network_interval"]) * 1000)

    NAV = [("downloads", "Downloads", "download"), ("torrents", "Torrents", "torrent"),
           ("networks", "Networks", "network"), ("history", "History", "history")]
    FILTERS = [("all", "All", None), ("active", "Active", {"DOWNLOADING", "RETRYING"}), ("queued", "Queued", {"QUEUED"}),
               ("paused", "Paused", {"PAUSED"}), ("completed", "Completed", {"COMPLETED"}),
               ("failed", "Failed", {"ERROR"}), ("cancelled", "Cancelled", {"STOPPED"})]

    def _build(self):
        root = QWidget(); self.setCentralWidget(root)
        outer = QHBoxLayout(root); outer.setContentsMargins(0, 0, 0, 0); outer.setSpacing(0)
        outer.addWidget(self._build_sidebar())

        right = QVBoxLayout(); right.setContentsMargins(0, 0, 0, 0); right.setSpacing(0)
        right_w = QWidget(); right_w.setLayout(right); outer.addWidget(right_w, 1)

        self.topbar = QFrame(); self.topbar.setObjectName("topbar")
        tb = QHBoxLayout(self.topbar); tb.setContentsMargins(24, 14, 24, 14); tb.setSpacing(10)
        self.page_title = QLabel("Downloads"); self.page_title.setObjectName("pageTitle")
        tb.addWidget(self.page_title); tb.addStretch()
        self.search = QLineEdit(); self.search.setObjectName("searchBox"); self.search.setPlaceholderText("Search downloads  (Ctrl+F)")
        self.search.setClearButtonEnabled(True)
        self.search.setAccessibleName("Search downloads"); self.search.textChanged.connect(self._apply_filter)
        tb.addWidget(self.search)
        self.add_torrent_btn = QPushButton("Add Torrent"); theme.themed_icon(self.add_torrent_btn, "torrent", "TEXT")
        self.add_torrent_btn.setToolTip("Add a magnet link or .torrent file"); self.add_torrent_btn.clicked.connect(self.add_torrent)
        self.add_btn = QPushButton("Add Download"); self.add_btn.setObjectName("primaryButton"); theme.themed_icon(self.add_btn, "add", "ON_ACCENT")
        self.add_btn.setToolTip("Add a download (Ctrl+N)"); self.add_btn.clicked.connect(self.add_download)
        tb.addWidget(self.add_torrent_btn); tb.addWidget(self.add_btn)
        right.addWidget(self.topbar)

        self.tabs = QStackedWidget()
        pages = QWidget(); pl = QVBoxLayout(pages); pl.setContentsMargins(24, 16, 24, 18); pl.addWidget(self.tabs)
        self.downloads_tab = QWidget(); self._build_downloads(self.downloads_tab)
        self.torrent_tab = QWidget(); self._build_torrents(self.torrent_tab)
        self.networks_tab = QWidget(); self._build_networks(self.networks_tab)
        self.history_tab = QWidget(); self._build_history(self.history_tab)
        for w in (self.downloads_tab, self.torrent_tab, self.networks_tab, self.history_tab):
            self.tabs.addWidget(w)
        right.addWidget(pages, 1)
        self.tabs.currentChanged.connect(self._page_changed)
        self._page_changed(0)

        QShortcut(QKeySequence("Ctrl+N"), self, self.add_download)
        QShortcut(QKeySequence("Ctrl+F"), self, lambda: (self.nav_buttons["downloads"].click(), self.search.setFocus()))
        QShortcut(QKeySequence(Qt.Key.Key_Delete), self.table, self.delete_selected)

    def _build_sidebar(self):
        side = QFrame(); side.setObjectName("sidebar"); side.setFixedWidth(224)
        lay = QVBoxLayout(side); lay.setContentsMargins(14, 18, 14, 14); lay.setSpacing(4)
        brand = QHBoxLayout(); brand.setSpacing(10)
        mark = QLabel(); mark.setFixedSize(38, 38)
        pm = QPixmap(str(ASSETS / "logo.png"))
        if not pm.isNull():
            mark.setPixmap(pm.scaled(38, 38, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        names = QVBoxLayout(); names.setSpacing(0)
        t = QLabel("Nexus"); t.setObjectName("brand"); sub = QLabel(f"Downloader {APP_VERSION}"); sub.setObjectName("brandSub")
        names.addWidget(t); names.addWidget(sub)
        brand.addWidget(mark); brand.addLayout(names); brand.addStretch()
        lay.addLayout(brand); lay.addSpacing(18)
        self.nav_buttons = {}; self._nav_icons = {}
        group = QButtonGroup(self); group.setExclusive(True)
        for i, (key, label, ic) in enumerate(self.NAV):
            b = QPushButton(label); b.setObjectName("navButton"); b.setCheckable(True); b.setIconSize(QSize(18, 18))
            b.setAccessibleName(label); b.setCursor(Qt.CursorShape.PointingHandCursor)
            self._nav_icons[key] = ic
            b.clicked.connect(lambda _=False, idx=i: self.tabs.setCurrentIndex(idx))
            group.addButton(b); lay.addWidget(b); self.nav_buttons[key] = b
        lay.addStretch()
        pref = QPushButton("Settings"); pref.setObjectName("navButton"); theme.themed_icon(pref, "settings", "MUTED"); pref.setIconSize(QSize(18, 18))
        pref.setToolTip("Preferences"); pref.clicked.connect(self.open_preferences); lay.addWidget(pref)
        self.side_status = QLabel(""); self.side_status.setObjectName("sideFooter"); self.side_status.setWordWrap(True)
        lay.addSpacing(8); lay.addWidget(self.side_status)
        return side

    def _page_changed(self, idx):
        keys = [k for k, _, _ in self.NAV]
        for k, b in self.nav_buttons.items():
            on = keys[idx] == k
            b.setChecked(on)
            theme.themed_icon(b, self._nav_icons[k], "ACCENT" if on else "MUTED")
        self.page_title.setText(self.NAV[idx][1])
        self.search.setVisible(idx == 0)
        self.add_torrent_btn.setVisible(idx == 1)
        self.add_btn.setVisible(idx == 0)

    def add_download(self):
        clip = QGuiApplication.clipboard().text().strip()
        self._open_download_dialog(clip if validate_download_url(clip)[0] else "")

    def _build_downloads(self, page):
        layout = QVBoxLayout(page); layout.setContentsMargins(0, 0, 0, 0); layout.setSpacing(12)

        chips_bar = QHBoxLayout(); chips_bar.setSpacing(6)   # row 2: status filters
        self.filter_group = QButtonGroup(self); self.filter_group.setExclusive(True)
        self.chips = {}
        for key, label, _ in self.FILTERS:
            c = QPushButton(label); c.setObjectName("chip"); c.setCheckable(True); c.setChecked(key == "all")
            c.setCursor(Qt.CursorShape.PointingHandCursor); c.setAccessibleName(f"Show {label} downloads")
            c.clicked.connect(lambda _=False, k=key: self._set_filter(k))
            self.filter_group.addButton(c); chips_bar.addWidget(c); self.chips[key] = c
        self._filter_key = "all"
        chips_bar.addStretch()
        self.sel_info = QLabel(""); self.sel_info.setObjectName("summary"); chips_bar.addWidget(self.sel_info)
        layout.addLayout(chips_bar)
        self.filter_note = QFrame(); self.filter_note.setObjectName("filterNote")
        fnl = QHBoxLayout(self.filter_note); fnl.setContentsMargins(12, 6, 12, 6)
        self.filter_text = QLabel(""); fnl.addWidget(self.filter_text, 1)
        clr = QPushButton("Show all downloads"); clr.clicked.connect(self.clear_filters); fnl.addWidget(clr)
        self.filter_note.hide(); layout.addWidget(self.filter_note)

        self.table = DownloadTable()
        self.table.actionRequested.connect(self._row_action)
        self.table.contextRequested.connect(self._show_more_menu)
        self.table.itemSelectionChanged.connect(self._update_selection_actions)
        self.table.sort_col = 7   # Added, newest first
        self.table.sort_order = Qt.SortOrder.DescendingOrder
        self.table.horizontalHeader().setSortIndicator(7, Qt.SortOrder.DescendingOrder)
        self.dl_stack = QStackedWidget()
        self.dl_stack.addWidget(self.table)
        self.drop_zone = DropZone()
        self.drop_zone.urlDropped.connect(self.handle_dropped_url); self.drop_zone.torrentDropped.connect(self.handle_dropped_torrent)
        self.drop_zone.addClicked.connect(self.add_download)
        self.dl_stack.addWidget(self.drop_zone)
        layout.addWidget(self.dl_stack, 1)

        footer = QFrame(); footer.setObjectName("footer")
        fl = QHBoxLayout(footer); fl.setContentsMargins(14, 8, 14, 8); fl.setSpacing(10)
        self.summary = QLabel(""); self.summary.setObjectName("summary"); self.summary.setTextFormat(Qt.TextFormat.RichText)
        fl.addWidget(self.summary, 1)
        fl.addWidget(QLabel("Defaults for new downloads:")); self.conn = QSpinBox(); self.conn.setRange(1, 32); self.conn.setFixedWidth(104); self.conn.setSuffix(" conn."); self.conn.setValue(int(self.settings["connections"])); fl.addWidget(self.conn)
        fl.addWidget(QLabel("·")); self.simul = QSpinBox(); self.simul.setRange(1, 16); self.simul.setFixedWidth(116); self.simul.setSuffix(" at once"); self.simul.setValue(int(self.settings["simultaneous_files"])); fl.addWidget(self.simul)
        self.conn.setToolTip("Default for downloads you add from now on. Each download keeps its own value; running downloads are not changed.")
        self.simul.setToolTip("How many downloads run at once; the rest wait in the queue.")
        layout.addWidget(footer)
        self.conn.valueChanged.connect(self._save_inline_settings)
        self.simul.valueChanged.connect(self._save_inline_settings)
        self._update_selection_actions()

    def _save_inline_settings(self):
        """P6.20 — self.settings/settings.json is the single source of
        truth; both the inline spin-boxes here and the Preferences dialog
        read/write into it, so neither can go stale relative to the other."""
        self.settings["connections"] = self.conn.value()
        self.settings["simultaneous_files"] = self.simul.value()
        save_settings(self.settings)

    def _build_torrents(self, page):
        lay = QVBoxLayout(page); lay.setContentsMargins(0, 0, 0, 0); lay.setSpacing(0)
        self.torrent_page = TorrentPage(); lay.addWidget(self.torrent_page)
        self.torrent_table = self.torrent_page.table
        self.torrent_table.customContextMenuRequested.connect(self._torrent_context)
        self.torrent_table.itemSelectionChanged.connect(self._torrent_selection_changed)
        self.torrent_table.cellDoubleClicked.connect(lambda r, _c: self.show_torrent_details(self.torrent_table.key_at(r)))
        self.torrent_page.details.priorityRequested.connect(self._torrent_priority)
        self.torrent_page.dropped.connect(lambda v: QTimer.singleShot(0, lambda: self.add_torrent(v)))     # never open a modal inside the drop event
        self.torrent_page.rejected.connect(lambda: QTimer.singleShot(0, lambda: QMessageBox.information(
            self, "Unsupported drop", "Drop a .torrent file or a magnet link here.")))
        try:
            self.torrent_manager = TorrentManager(int(self.settings["torrent_port"]), self.settings["torrent_down_kbps"], self.settings["torrent_up_kbps"], APP_DIR / "torrents")
        except Exception as exc:
            self.torrent_manager = None
            error = QLabel("BitTorrent engine unavailable: " + str(exc)); error.setObjectName("warning"); error.setWordWrap(True); lay.insertWidget(0, error)
        self._restore_torrents()

    def _build_networks(self, page):
        layout = QVBoxLayout(page); layout.setContentsMargins(0, 8, 0, 0); layout.setSpacing(10)
        head = QHBoxLayout(); title = QLabel("Network Connections"); title.setObjectName("sectionTitle"); head.addWidget(title); head.addStretch()
        # Live animated multi-network combiner: per-adapter paths flow into a central
        # node. Driven by real measured throughput; idle/disconnected adapters do not flow.
        self.combiner = NetworkCombiner()
        layout.addWidget(self.combiner)
        network_specs = [
            ("Refresh", lambda: self.manual_network_scan("refresh"), None, "refresh"),
            ("Test internet", lambda: self.manual_network_scan("test"), None, "bolt"),
            ("Enable all online", self.enable_all_online, "successGhostButton", "check"),
            ("Disable all online", self.disable_all_online, "dangerGhostButton", "close"),
        ]
        self.net_head = head; self.net_title = title; self.net_page = page; self.net_buttons = []
        for label, fn, object_name, ic in network_specs:
            b = QPushButton(label); b.clicked.connect(fn); theme.themed_icon(b, ic, "TEXT")
            if object_name: b.setObjectName(object_name)
            b.setProperty("fullText", label); b.setAccessibleName(label); self.net_buttons.append(b)
            head.addWidget(b)
        self.net_refresh_btn, self.net_test_btn = self.net_buttons[0], self.net_buttons[1]
        layout.addLayout(head)
        desc = QLabel("Loopback interfaces are hidden. Network speed shows interface receive traffic, while download speed in Downloads shows Nexus transfer speed.")
        desc.setObjectName("muted"); desc.setWordWrap(True); layout.addWidget(desc)
        self.network_mode_label = QLabel("Combined multi-network: checking adapters…")
        self.network_mode_label.setObjectName("networkMode"); self.network_mode_label.setWordWrap(True)
        layout.addWidget(self.network_mode_label)
        self.network_status = QLabel(""); self.network_status.setObjectName("muted"); self.network_status.setVisible(False)
        self.network_status.setAccessibleName("Network scan status"); self.network_status.setWordWrap(True)
        layout.addWidget(self.network_status)
        self.net_table = NetworkTable()
        self._style_table(self.net_table)
        layout.addWidget(self.net_table, 1)

    def _build_history(self, page):
        layout = QVBoxLayout(page); layout.setContentsMargins(0, 8, 0, 0)
        head = QHBoxLayout(); title = QLabel("Download History"); title.setObjectName("sectionTitle"); head.addWidget(title); head.addStretch()
        clear = QPushButton("Clear completed history"); theme.themed_icon(clear, "trash", "DANGER"); clear.setObjectName("dangerGhostButton"); clear.clicked.connect(self.clear_history); head.addWidget(clear); layout.addLayout(head)
        self.history_table = HistoryTable()
        self._style_table(self.history_table)
        layout.addWidget(self.history_table, 1)
        self.refresh_history()

    def _style_table(self, table, multi=False):
        """Common table setup: strictly read-only, no context menu, clean rows."""
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
        table.setAlternatingRowColors(False)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection if multi else QAbstractItemView.SelectionMode.SingleSelection)
        table.verticalHeader().hide()
        table.verticalHeader().setDefaultSectionSize(int(table.fontMetrics().height() * 2.7))
        table.setShowGrid(False); table.setWordWrap(False); table.setSortingEnabled(False)
        table.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        table.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)

    def _restore_downloads(self):
        self.db.pause_stale()
        self._bulk = True
        try:
            for row in self.db.all():
                data = dict(row)
                self.add_row(data["id"], data, restored=True)
        finally:
            self._bulk = False
        self._rows_changed()

    def analyze_link(self):
        """Kept for compatibility: open the Add Download dialog for a pasted link."""
        self.add_download()

    def _path_active_in_db(self, path, exclude_rid=None):
        """P1.1 — true if some other download is already queued/active/
        paused for this exact destination path. Checked at add-time (covers
        double-click / re-adding the same URL) and again in start_job()
        (covers restored duplicates from before this check existed, and the
        rare case where two rows race to start on the same tick)."""
        for row in self.db.all():
            if exclude_rid is not None and int(row["id"]) == exclude_rid:
                continue
            if row["path"] == path and row["status"] in ("QUEUED", "DOWNLOADING", "PAUSED"):
                return True
        return False

    def default_dir(self):
        return self.settings.get("download_dir") or str(Path.home() / "Downloads")

    def find_duplicate(self, url):
        """Existing download record for the same link (persisted records and live workers share rids)."""
        key = normalize_url(url); hits = [dict(r) for r in self.db.all() if normalize_url(r["url"]) == key]
        live = [h for h in hits if h["id"] in self.jobs]
        return (live or hits or [None])[0]

    def _destination_taken(self, path):
        p = str(path)
        return (Path(p).exists() or Path(p + ".parts").exists() or Path(p + ".part").exists()
                or any(r["path"] == p for r in self.db.all()))

    def _handle_duplicate(self, dup, new_path):
        """Ask what to do with an already-known link. -> 'again' to add anyway, else None (handled/cancelled)."""
        rid = int(dup["id"]); st = self.table.status_of(rid) or dup["status"]
        job = self.jobs.get(rid)
        total = job.info.total if job else int(dup["size"] or 0); done = job.info.done if job else int(dup["downloaded"] or 0)
        pct = f" ({done * 100.0 / total:.0f}%)" if total and st != "COMPLETED" else ""
        label = theme.status_label(st)
        if st in ("DOWNLOADING", "RETRYING"):
            detail = f"This link is already downloading{pct}. Nexus will not start a second copy."
            acts = [("show", "Show download")]
        elif st == "QUEUED":
            detail = "This link is already in the queue."; acts = [("show", "Show download"), ("start", "Start now")]
        elif st == "PAUSED":
            detail = f"This download is paused{pct}."; acts = [("start", "Resume"), ("show", "Show download")]
        elif st == "STOPPED":
            detail = f"This download was cancelled{pct}; its partial data is kept."; acts = [("start", "Start again"), ("show", "Show download")]
        elif st == "ERROR":
            detail = "This download failed earlier."; acts = [("retry", "Retry"), ("show", "Show download")]
        else:
            detail = "This file was already downloaded."
            acts = [("show", "Show download")] + ([("open", "Open file")] if Path(dup["path"]).exists() else [])
        again_name = Path(new_path).name
        taken = self._destination_taken(new_path)
        again_path = unique_path(new_path, lambda q: self._destination_taken(q)) if taken else new_path
        acts.append(("again", "Download again" + (f" as {Path(again_path).name}" if taken else "")))
        detail += f"\n\nStatus: {label}\nSaved as: {dup['path']}"
        choice = ask_choice(self, "Duplicate download", "This download link already exists.", detail, acts)
        if choice == "show": self._reveal(rid)
        elif choice == "start": self._reveal(rid); self.start_one(rid)
        elif choice == "retry": self._reveal(rid); self.retry_one(rid)
        elif choice == "open": self.open_file(rid)
        return again_path if choice == "again" else None

    def _open_download_dialog(self, url):
        dialog = NewDownloadDialog(url, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        data = dialog.data()
        data["name"] = safe_filename(data["name"])
        path = str(Path(data["directory"] or self.default_dir()) / data["name"])
        dup = self.find_duplicate(data["url"])
        if dup:
            path = self._handle_duplicate(dup, path)          # link duplicate: never creates a record unless asked
            if path is None:
                return
        elif self._destination_taken(path):                   # different link, same destination: shared conflict dialog
            # Overwrite is offered only for a plain file nobody is downloading to; the default (and remembered-answer fallback) is Rename.
            can_over = Path(path).is_file() and not self._path_active_in_db(path)
            choice, _ = self._resolve_conflict(path, "Download: " + data["name"], allow_overwrite=can_over,
                                               overwrite_hint="This name belongs to a download that is still queued, running or paused.")
            if choice == "abort":
                return
            if choice == "rename":
                path = unique_path(path, self._destination_taken)
            elif choice == "overwrite":                        # explicitly confirmed in the dialog
                err = self._delete_for_overwrite(path)
                if err:
                    QMessageBox.critical(self, "Overwrite failed", f"The existing file could not be deleted:\n\n{err}\n\nThe download was not added."); return
        data["name"] = Path(path).name
        try:
            row_id = self.db.add(data["url"], data["name"], path, network_mode="COMBINED", network_ips="",
                                 connections=int(data.get("connections") or self.settings["connections"]))
        except Exception as exc:
            QMessageBox.critical(self, "Database error", str(exc)); return
        data["path"] = path
        self.add_row(row_id, dict(self.db.get(row_id)))
        self._reveal(row_id)
        if self.settings["auto_start"]:
            self.start_job(row_id, dict(self.db.get(row_id)))

    def _reveal(self, rid):
        """Filters and search only hide rows. Whatever hides this one is lifted so the user can see it."""
        self.nav_buttons["downloads"].click()
        group = next((g for k, _l, g in self.FILTERS if k == self._filter_key), None)
        if group is not None and (self.table.status_of(rid) not in group):
            self.chips["all"].click()
        if self.search.text().strip():
            self.search.clear()
        self._apply_filter()
        r = self.table.row_of(rid)
        if r is not None:
            self.table.clearSelection(); self.table.selectRow(r)
            self.table.scrollTo(self.table.model().index(r, C_NAME))

    def clear_filters(self):
        self.search.clear(); self.chips["all"].click(); self._apply_filter()

    def handle_dropped_url(self, value):
        if not self.settings["accept_url_drop"]:
            return
        value = value.strip()
        if value.lower().startswith("magnet:"):
            QTimer.singleShot(0, lambda: self.add_torrent(value))      # through the New torrent dialog, never added blindly
        elif validate_download_url(value)[0]:
            self._open_download_dialog(value)
        else:
            QMessageBox.warning(self, "Unsupported drop", "Drop a valid HTTP/HTTPS URL, magnet link, or .torrent file.")

    def handle_dropped_torrent(self, path):
        QTimer.singleShot(0, lambda: self.add_torrent(path))

    def add_row(self, row_id, data, restored=False):
        status = data.get("status", "QUEUED") if restored else "QUEUED"
        added = data.get("created") or datetime.now().isoformat(timespec="seconds")
        size = int(data.get("size", 0) or 0); done = int(data.get("downloaded", 0) or 0)
        pct = (done * 100.0 / size) if size else 0.0
        if status == "COMPLETED":
            pct = 100.0
        duration = float(data.get("duration", 0) or 0)
        self.table.add_download(row_id, animate=not self._bulk)
        self.table.set_fields(
            row_id, name=data.get("name", "download"), search=f'{data.get("name", "")} {data.get("url", "")}', sub=self._host_of(data.get("url", "")),
            size_text=self._size_text(done, size, status), size_sort=size, status=status, pct=pct, snap=True,
            speed_text="—", speed_sort=0.0, eta_text="—", eta_sort=1e18,
            added_text=self._nice_datetime(added), added_sort=f"{added}-{int(row_id):012d}",
            elapsed_text=self.fmt_duration(duration), elapsed_sort=duration)
        if not self._bulk:
            self._rows_changed()

    @staticmethod
    def _host_of(url):
        try:
            return urlparse(str(url or "")).hostname or ""
        except Exception:
            return ""

    def _size_text(self, done, total, status):
        if status == "COMPLETED" and total:
            return self.fmt_bytes(total)
        if total:
            return f"{self.fmt_bytes(done)} / {self.fmt_bytes(total)}"
        return self.fmt_bytes(done) if done else "—"

    def _rows_changed(self):
        """After rows were added/removed/re-sorted: re-apply sort + filter + empty state."""
        self.table.resort()
        self._apply_filter()
        self.update_metrics()

    def selected_ids(self):
        return self.table.selected_rids()

    STOP_TIMEOUT = 8.0   # seconds to wait for a worker before refusing to touch its files

    def _retry_pending_cleanup(self):
        """Retry temp-file deletions that failed when items were removed earlier."""
        for row in self.db.pending_cleanups():
            path = row["path"]
            if cleanup.shared_with_other(path, self.db.paths_in_use()):
                self.db.clear_pending_cleanup(path)   # path reused by a live download; leave its temps alone
                continue
            if cleanup.remove_temp_files(path).ok:
                self.db.clear_pending_cleanup(path)

    def _remove_downloads(self, ids, action):
        """Remove downloads from the list ('remove_only': keeps the finished
        file) or delete their files too ('delete_files').

        Returns dict(done=[rid], busy=[rid], failed=[(path, reason)], skipped=[(path, reason)]).
        Order of operations per item: stop worker -> confirm it is gone ->
        delete files -> only then drop the DB record / UI row."""
        report = {"done": [], "busy": [], "failed": [], "skipped": []}
        seen = set()
        for rid in [int(x) for x in ids]:
            if rid in seen or rid in self._removing:
                continue                                   # duplicate selection / re-entrant callback
            seen.add(rid)
            self._removing.add(rid)
            try:
                row = self.db.get(rid)
                if row is None:                            # already removed
                    self._drop_row_ui(rid)
                    continue
                row = dict(row)
                job = self.jobs.get(rid)
                if job is not None:
                    if not cleanup.stop_job(job, self.STOP_TIMEOUT):
                        report["busy"].append(rid)         # still writing: touch nothing
                        continue
                    job.on_update = None
                    self.jobs.pop(rid, None)
                path = row.get("path") or ""
                if not path:
                    self.db.delete_download(rid); self._drop_row_ui(rid); report["done"].append(rid)
                    continue
                if cleanup.shared_with_other(path, self.db.paths_in_use(exclude_id=rid)):
                    # Another record owns this exact path: never delete its files.
                    report["skipped"].append((path, "another download uses the same file"))
                    self.db.delete_download(rid); self._drop_row_ui(rid); report["done"].append(rid)
                    continue
                if action == "delete_files":
                    res = cleanup.delete_download_files(path)
                else:
                    res = cleanup.remove_temp_files(path)
                report["skipped"] += res.skipped
                if action == "delete_files" and res.failed:
                    # Keep the record (as STOPPED) so cleanup can be retried.
                    report["failed"] += res.failed
                    if str(row.get("status")) not in ("COMPLETED",):
                        self.db.update(rid, status="STOPPED")
                        self._set_row_status(rid, "STOPPED")
                    continue
                if res.failed:                             # remove_only: record goes, leftovers retried later
                    report["failed"] += res.failed
                    self.db.add_pending_cleanup(path, "; ".join(f"{os.path.basename(f)}: {why}" for f, why in res.failed))
                self.db.delete_download(rid)
                self._drop_row_ui(rid)
                report["done"].append(rid)
            finally:
                self._removing.discard(rid)
        self._defender_queue = [q for q in self._defender_queue if os.path.exists(q)]
        self.refresh_history()
        self.update_metrics()
        return report

    def _drop_row_ui(self, rid):
        self.job_meta.pop(rid, None)
        self.jobs.pop(rid, None)
        for r in range(self.table.rowCount() - 1, -1, -1):
            if self.table.rid_at(r) == rid:
                self.table.removeRow(r)
        self.table.forget(rid)
        self._apply_filter()

    def delete_selected(self, ids=None, preset=None):
        """Toolbar/Delete key: choose Remove-from-list vs Delete-files in a dialog.
        Row menu passes `preset` to go straight to one of the two actions."""
        if not isinstance(ids, (list, tuple)):
            ids = None
        ids = list(ids) if ids is not None else self.selected_ids()
        if not ids:
            QMessageBox.information(self, "Remove downloads", "Select one or more downloads first.")
            return
        rows = {int(r["id"]): dict(r) for r in self.db.all() if int(r["id"]) in ids}
        action = ""
        if preset:
            action = preset
            if preset == "delete_files" and self.settings.get("confirm_delete", True):
                names = ", ".join(rows[i]["name"] for i in ids if i in rows)[:200]
                if QMessageBox.question(self, "Delete downloaded files", f"Permanently delete the file(s) for:\n{names}\n\nThis cannot be undone.",
                                        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                        QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
                    return
        else:
            remembered = str(self.settings.get("delete_action", "") or "")
            if not self.settings.get("confirm_delete", True):
                action = remembered or "delete_files"
            elif remembered in {"delete_files", "remove_only"}:
                action = remembered
            else:
                dialog = DeleteDownloadsDialog([rows[r] for r in ids if r in rows], self)
                if dialog.exec() != QDialog.DialogCode.Accepted:
                    return
                action = dialog.action
                if dialog.remember:
                    self.settings["delete_action"] = action
                    save_settings(self.settings)

        report = self._remove_downloads(ids, action)
        lines = []
        if report["busy"]:
            lines.append(f"{len(report['busy'])} download(s) are still stopping and were NOT touched. Try again in a moment.")
        if report["failed"]:
            lines.append("Could not delete:\n" + "\n".join(f"  • {f}\n    {why}" for f, why in report["failed"]))
            lines.append("These items were kept so you can retry." if action == "delete_files"
                         else "They will be retried automatically at next start.")
        if lines:
            QMessageBox.warning(self, "Remove downloads", "\n\n".join(lines))

    def selected_id(self):
        ids = self.selected_ids(); return ids[0] if ids else None

    def _active_download_count(self):
        return sum(1 for job in self.jobs.values() if getattr(job.info, "status", "") == "DOWNLOADING")

    def _start_queued_jobs(self):
        limit = self.simul.value()
        if self._active_download_count() >= limit: return
        for raw in self.db.queued():
            row = dict(raw); rid = int(row["id"])
            if rid in self.jobs: continue
            if self._active_download_count() >= limit: break
            self.start_job(rid, row, force=True)

    def start_job(self, row_id, data, force=False):
        if row_id in self.jobs:
            job = self.jobs[row_id]
            if job.info.status in ("PAUSED", "STOPPED", "QUEUED", "RETRYING", "ERROR"):
                if not force and self._active_download_count() >= self.simul.value():
                    self.db.update(row_id, status="QUEUED"); self._set_row_status(row_id, "QUEUED"); return
                job.resume(); self.db.update(row_id, status="DOWNLOADING")
            return
        if not force and self._active_download_count() >= self.simul.value():
            self.db.update(row_id, status="QUEUED"); self._set_row_status(row_id, "QUEUED"); return
        path = data.get("path") or str(Path(data.get("directory") or Path.home() / "Downloads") / data["name"])
        # P1.1 — defense in depth: also checked at add-time, but this covers
        # duplicate rows that already existed (e.g. from before this check
        # was added) and the case where two rows race to start on the same
        # tick. Defer this one; it'll retry automatically once the path
        # frees up.
        if self._path_active_in_db(path, exclude_rid=row_id):
            self.db.update(row_id, status="QUEUED"); self._set_row_status(row_id, "QUEUED"); return
        speed_limit = self.settings["max_speed_kbps"] * 1024
        network_ips = self._combined_network_ips()
        # P5.15 — cap total worker threads across simultaneous_files x
        # connections instead of letting the product spawn hundreds of OS
        # threads.
        # Per-download value wins; only a download that never chose one (0, e.g. an old record) takes the global default.
        requested_conn = max(1, min(32, int(data.get("connections") or 0) or int(self.settings["connections"])))
        simul_limit = max(1, self.simul.value())
        effective_conn = max(1, min(requested_conn, MAX_TOTAL_WORKERS // simul_limit))
        if int(data.get("connections") or 0) != requested_conn:
            self.db.update(row_id, connections=requested_conn)
        job = Downloader(
            data["url"], path, effective_conn,
            self.net, speed_limit, network_ips=network_ips, network_provider=self._combined_network_ips,
            read_timeout=self.settings["read_timeout"], max_retries=self.settings["max_retries"]
        )
        job.requested_connections = requested_conn
        self.job_meta[row_id] = {
            "started": time.time(), "paused": 0.0, "peak": 0.0,
            "network_ips": list(network_ips),
        }
        job.on_update = lambda info, rid=row_id: self._safe_emit_download_update(rid, info)
        self.jobs[row_id] = job
        self.db.update(row_id, status="DOWNLOADING", started=datetime.now().isoformat(timespec="seconds"), network_mode="COMBINED" if len(network_ips) > 1 else ("SINGLE-NETWORK" if network_ips else "AUTO"), network_ips=", ".join(network_ips))
        job.start()

    def _safe_emit_download_update(self, rid, info):
        """P3.9 — a download's on_update fires from its worker thread; don't
        emit into a window that's already being torn down."""
        if self._shutting_down.is_set():
            return
        self.download_update.emit(rid, info)

    def _row_for_rid(self, rid):
        return self.table.row_of(rid)

    @Slot(int, object)
    def update_job(self, rid, info):
        r = self._row_for_rid(rid)
        if r is not None:
            prev = self.table.status_of(rid)
            pct = 100.0 if info.status == "COMPLETED" else ((info.done * 100.0 / info.total) if info.total else 0.0)
            err = info.status in ("ERROR", "RETRYING") and bool(info.error)
            self.table.set_fields(
                rid, size_text=self._size_text(info.done, info.total, info.status), size_sort=info.total,
                status=info.status, pct=pct,
                speed_text=(self.fmt_bytes(info.speed) + "/s") if (info.status == "DOWNLOADING" and info.speed) else "—",
                speed_sort=float(info.speed or 0), sub=(info.error if err else self._host_of(info.url)), sub_err=err)
            meta = self.job_meta.setdefault(rid, {"started": time.time(), "paused": 0.0, "peak": 0.0})
            meta["peak"] = max(meta.get("peak", 0.0), info.speed)
            if info.status == "PAUSED":
                self.db.update(rid, size=info.total, downloaded=info.done, status="PAUSED")
            elif info.status == "STOPPED":
                self.db.update(rid, size=info.total, downloaded=info.done, status="STOPPED")
            elif info.status in ("ERROR", "RETRYING"):
                self.db.update(rid, size=info.total, downloaded=info.done, status=info.status)
            elif info.status == "COMPLETED":
                elapsed = max(0, time.time() - meta["started"] - meta.get("paused", 0))
                avg = info.done / elapsed if elapsed else 0
                used_ips = list(getattr(info, "networks_used", ()) or ())
                if used_ips:
                    used_networks = ", ".join(dict.fromkeys(self._network_name_for_ip(ip) or ip for ip in used_ips))
                else:
                    used_networks = self._active_network_names()
                self.db.update(rid, size=info.total, downloaded=info.done, status="COMPLETED", completed=datetime.now().isoformat(timespec="seconds"), duration=elapsed, avg_speed=avg, peak_speed=meta["peak"], networks_used=used_networks, history_hidden=0)
                self.table.set_fields(rid, elapsed_text=self.fmt_duration(elapsed), elapsed_sort=elapsed, eta_text="—", speed_text="—")
                if self.settings["defender_scan"]: self.scan_with_defender(self._path_for_id(rid))
                if self.settings["open_completed"]: self.open_folder(self._path_for_id(rid))
                self.jobs.pop(rid, None); self.job_meta.pop(rid, None)
                completed_row = self.db.get(rid)
                if completed_row:
                    self._prepend_history_row(dict(completed_row))
            else:
                self.db.update(rid, size=info.total, downloaded=info.done, status=info.status)
            if info.status != prev:
                self._rows_changed()
        self.update_metrics()

    def _combined_network_ips(self):
        """Return all currently enabled/online, combinable adapter IPv4s (Wi-Fi/Ethernet; Bluetooth excluded)."""
        if not bool(self.settings.get("multi_network", True)):
            return []
        values = []
        for info in self.net_rows.values():
            ip = str(getattr(info, "ip", "") or "").strip()
            if ip and is_combinable(getattr(info, "name", "")) and bool(getattr(info, "enabled", True)) and str(getattr(info, "internet", "")).upper() == "ONLINE" and str(getattr(info, "state", "")).upper() == "ACTIVE":
                if ip not in values:
                    values.append(ip)
        return values

    def _active_network_usage(self):
        usage = {}
        for job in self.jobs.values():
            try:
                for ip, count in job.network_usage().items():
                    usage[ip] = usage.get(ip, 0) + int(count)
            except Exception:
                pass
        return usage

    def _active_network_detail(self):
        """{ip: (bytes/s, suspended, last error)} summed over running downloads (each byte belongs to exactly one adapter)."""
        out = {}
        for job in list(self.jobs.values()):
            try:
                for ip, d in job.adapter_report().items():
                    r, s_, e = out.get(ip, (0.0, False, ""))
                    out[ip] = (r + d["rate"], s_ or d["suspended"], d["error"] or e)
            except Exception:
                pass
        return out

    def _path_for_id(self, rid):
        row = self.db.get(rid)
        return Path(row["path"]) if row else None

    def _network_ready_for_autostart(self):
        """P4.13 — if combined networking is on, wait for the first scan
        (or a short grace window, whichever comes first) before
        auto-starting queued jobs, so they don't silently fall back to
        single-adapter routing just because the app only just opened."""
        if not bool(self.settings.get("multi_network", True)):
            return True
        if self._initial_scan_done:
            return True
        return (time.time() - self._startup_time) > 3.0

    def _ui_tick(self):
        if self._network_ready_for_autostart():
            self._start_queued_jobs()
        now = time.time()
        for rid, meta in list(self.job_meta.items()):
            if self._row_for_rid(rid) is None:
                continue
            row = self.db.get(rid)
            if not row:
                continue
            status = self.table.status_of(rid) or ""
            job = self.jobs.get(rid)
            duration = float(row["duration"] or 0)
            if job and status == "DOWNLOADING":
                duration = max(duration, now - meta["started"] - meta.get("paused", 0))
            total = job.info.total if job else int(row["size"] or 0)
            done = job.info.done if job else int(row["downloaded"] or 0)
            speed = job.info.speed if job else 0
            eta = (total - done) / speed if (speed > 0 and total > done and status == "DOWNLOADING") else None
            self.table.set_fields(rid, elapsed_text=self.fmt_duration(duration), elapsed_sort=duration,
                                  eta_text=self.fmt_duration(eta) if eta is not None else "—",
                                  eta_sort=eta if eta is not None else 1e18)
        if self.table.sort_col not in (None, 1, 7):      # dynamic sort keys: keep the order current
            self.table.resort(); self._apply_filter()
        self.update_metrics()

    def update_metrics(self):
        active = sum(1 for j in self.jobs.values() if j.info.status == "DOWNLOADING")
        counts = self.db.counts()
        speed = sum(j.info.speed for j in self.jobs.values() if j.info.status == "DOWNLOADING")
        self.summary.setText(
            f"<b>{active}</b> active &nbsp;·&nbsp; <b>{counts.get('QUEUED', 0)}</b> queued &nbsp;·&nbsp; "
            f"<b>{counts.get('COMPLETED', 0)}</b> completed &nbsp;·&nbsp; <b>{self.fmt_bytes(speed)}/s</b>")
        total = sum(counts.values())
        for key, label, group in self.FILTERS:
            n = total if group is None else sum(counts.get(g, 0) for g in group)
            self.chips[key].setText(f"{label}  {n}" if n else label)
        n_net = len(self._combined_network_ips())
        if not bool(self.settings.get("multi_network", True)):
            net = "Multi-network off"
        elif n_net > 1:
            net = f"{n_net} networks combined"
        elif n_net == 1:
            net = "1 network online"
        else:
            net = "Checking networks…"
        self.side_status.setText(f"{net}\n{self.fmt_bytes(speed)}/s  ·  {active} active")
        # Feed the live animated combiner: per-adapter measured throughput and the real
        # aggregate download speed. Idle/disconnected adapters are shown neutral and do
        # not flow (the widget reads `state`/`rate`, never fabricates activity).
        if getattr(self, "combiner", None) is not None:
            reports = []
            for job in self.jobs.values():
                if job.info.status != "DOWNLOADING":
                    continue                    # a finished job's last report is stale
                try:
                    reports.append(job.adapter_report() or {})
                except Exception:
                    pass
            per_ip, unattributed = attribute_rates(reports, speed)
            torrent_rate = sum(i.download_rate for _, i in self.torrent_manager.all_status()) if self.torrent_manager else 0.0
            adapters = []
            for info in self.net_rows.values():
                ip = str(getattr(info, "ip", "") or "")
                rate = per_ip.get(ip) if reports else None      # None = nothing measuring this adapter
                adapters.append({
                    "name": str(getattr(info, "name", "") or ""),
                    "ip": ip,
                    "rate": rate,
                    "enabled": bool(getattr(info, "enabled", True)),
                    "state": str(getattr(info, "state", "") or ""),
                    "virtual": bool(getattr(info, "virtual", False)),
                })
            self.combiner.update_metrics(adapters, float(speed), torrent_rate, unattributed)

    def refresh_networks(self, purge=False, manual=False):
        if self._shutting_down.is_set():
            return
        with self._net_mutex:
            if self._net_running:
                # A scan is already running: remember ONE re-scan (a burst of requests still queues a single extra scan)
                # instead of dropping the request. A queued manual request keeps its manual flag so its feedback is delivered.
                p, m = self._net_next or (False, False)
                self._net_next = (p or purge, m or manual)
                return
            self._net_running = True
        self._run_network_scan(purge, manual)

    def _run_network_scan(self, purge, manual):
        def worker(purge=purge, manual=manual):
            while True:
                try:
                    infos, err = self.net.test_all(), ""
                except Exception as e:                  # reported to the GUI, never lost with the thread
                    infos, err = [], f"{type(e).__name__}: {e}"
                with self._net_mutex:
                    nxt, self._net_next = self._net_next, None
                    self._net_running = nxt is not None
                if self._shutting_down.is_set():
                    return
                self.network_update.emit(infos, purge, manual, err)       # queued signal: widgets are only touched on the GUI thread
                if nxt is None:
                    return
                purge, manual = nxt
        t = threading.Thread(target=worker, name="NexusNetScan", daemon=True)
        self._last_scan_thread = t
        t.start()

    def manual_network_scan(self, kind):
        """Refresh / Test internet button: immediate loading state, one operation at a time, result shown inline."""
        if self._net_manual or self._shutting_down.is_set():
            return
        self._net_manual = kind
        refresh = kind == "refresh"
        for b in (self.net_refresh_btn, self.net_test_btn): b.setEnabled(False)
        (self.net_refresh_btn if refresh else self.net_test_btn).setText("Refreshing…" if refresh else "Testing…")
        self._set_network_status("Refreshing adapters…" if refresh else "Testing connections…")
        self._fit_network_header()
        QTimer.singleShot(30000, lambda: self._finish_manual_scan([], "timed out after 30 s", kind))      # a hung probe never leaves the buttons disabled
        self.refresh_networks(purge=refresh, manual=True)

    def _set_network_status(self, text):
        self.network_status.setText(text); self.network_status.setVisible(bool(text))

    def _finish_manual_scan(self, infos, err, only_kind=None):
        kind = self._net_manual
        if not kind or (only_kind and only_kind != kind):
            return                                                  # already finished (or a stale watchdog)
        self._net_manual = ""
        for b in self.net_buttons[:2]: b.setText(b.property("fullText")); b.setEnabled(True)
        now = datetime.now().strftime("%H:%M:%S"); n = len(infos)
        if err:
            text = f"⚠ {'Refresh' if kind == 'refresh' else 'Internet test'} failed: {err}"
        elif kind == "test":
            eligible = [i for i in infos if i.enabled and i.state != "DISCONNECTED"]
            bad = [i.name for i in eligible if i.internet != "ONLINE"]
            text = (f"Tested {now}: {len(eligible) - len(bad)} of {len(eligible)} adapters reached the internet"
                    + (f" · Failed: {', '.join(bad)}" if bad else "")) if eligible else f"Tested {now}: no enabled adapter to test"
        else:
            text = f"Refreshed {now} · {n} adapter{'s' * (n != 1)} · {sum(i.internet == 'ONLINE' for i in infos)} online"
        self._set_network_status(text); self._fit_network_header()

    def _fit_network_header(self):
        """The two bulk buttons switch to short labels when the header row would not fit (full text stays as tooltip/accessible name)."""
        bulk = self.net_buttons[2:]
        for b in bulk: b.setText(b.property("fullText")); b.setToolTip(b.property("fullText"))
        m = self.net_page.layout().contentsMargins()
        need = self.net_title.sizeHint().width() + sum(b.sizeHint().width() for b in self.net_buttons) + 4 * self.net_head.spacing()
        if need > self.net_page.width() - m.left() - m.right():
            for b in bulk: b.setText(b.property("fullText").replace(" online", ""))

    @Slot(object, bool, bool, str)
    def _apply_network_update(self, infos, purge, manual=False, err=""):
        try:
            if not err:
                self._render_network_rows(infos, purge)
        except Exception as e:                                       # a UI failure must not leave the buttons disabled
            if not manual:
                raise
            err = f"{type(e).__name__}: {e}"
        finally:
            if manual:
                self._finish_manual_scan(infos, err)

    def _render_network_rows(self, infos, purge):
        self._initial_scan_done = True
        current = {i.ifname: i for i in infos}
        if purge: self.net_rows = current
        else:
            self.net_rows.update(current)
            for key, value in list(self.net_rows.items()):
                if key not in current:
                    value.internet = "DISCONNECTED"; value.state = "DISCONNECTED"; value.latency_ms = None; value.speed_bps = 0
        self.net_table.setRowCount(0)
        usage = self._active_network_usage(); detail = self._active_network_detail()
        combined_count = len(self._combined_network_ips())
        if hasattr(self, "network_mode_label"):
            if bool(self.settings.get("multi_network", True)) and combined_count > 1:
                self.network_mode_label.setText(f"Combined multi-network active · {combined_count} adapters reach the internet · HTTP ranges go to whichever adapter is fastest at that moment. Adapters sharing one uplink or router will not add speed (independence is not verified).")
            elif bool(self.settings.get("multi_network", True)) and combined_count == 1:
                self.network_mode_label.setText("Combined multi-network ready · only one verified adapter is online right now")
            else:
                self.network_mode_label.setText("Multi-network aggregation is off · Windows routing is used")
        for ifname, info in self.net_rows.items():
            r = self.net_table.rowCount(); self.net_table.insertRow(r)
            used = usage.get(str(getattr(info, "ip", "") or ""), 0)
            rate, susp, err = detail.get(str(getattr(info, "ip", "") or ""), (0.0, False, ""))
            used_text = ("Suspended · retrying" if susp else f"YES · {self.fmt_bytes(rate)}/s · {used} ranges") if used or susp else "—"
            vals = [info.name + (" (virtual · not auto-used)" if getattr(info, "virtual", False) else ""), info.ip, f"{info.link_mbps:.0f} Mbps" if info.link_mbps else "Unknown", None, f"{info.latency_ms:.0f} ms" if info.latency_ms is not None else "—", self.fmt_bytes(info.speed_bps) + "/s", used_text, None, ""]
            for c, v in enumerate(vals):
                if v is None: continue
                cell = QTableWidgetItem(str(v)); cell.setToolTip(str(v)); self.net_table.setItem(r, c, cell)       # elided text stays readable
            self.net_table.item(r, 0).setToolTip(f"{vals[0]} · link {vals[2]}")
            self.net_table.setItem(r, 3, QTableWidgetItem(""))
            self.net_table.setCellWidget(r, 3, StatusBadge(info.internet))
            if err and not rate:
                self.net_table.item(r, 6).setToolTip(err)
            self.net_table.setItem(r, 7, QTableWidgetItem(""))
            self.net_table.setCellWidget(r, 7, StatusBadge(info.state))
            if info.state != "DISCONNECTED":
                b = QPushButton("Disable" if info.enabled else "Enable")
                b.setMinimumSize(84, 32)
                b.setProperty("ifname", ifname)
                b.setProperty("pill", "on" if info.enabled else "off")
                b.clicked.connect(self._toggle_network)
                cell = QWidget(); cell.setObjectName("cellWrap"); cell.setStyleSheet("#cellWrap { background: transparent; }"); lay = QHBoxLayout(cell); lay.setContentsMargins(4, 0, 4, 0); lay.addWidget(b)      # padding: the pill is never cut by the cell edge
                self.net_table.setCellWidget(r, 8, cell)
        self.net_table.fit()

    def _optimistic_set_enabled(self, ifname, value):
        info = self.net_rows.get(ifname)
        if info is None:
            return
        info.enabled = value
        if not value:
            info.internet = "DISABLED"; info.state = "DISABLED"

    def enable_all_online(self):
        for info in self.net_rows.values():
            if info.state != "DISCONNECTED":
                self.net.set_enabled(info.ifname, True)
                self._optimistic_set_enabled(info.ifname, True)
        self._apply_network_update(list(self.net_rows.values()), False)
        self.refresh_networks(False)

    def disable_all_online(self):
        for info in self.net_rows.values():
            if info.internet == "ONLINE" or info.state == "ACTIVE":
                self.net.set_enabled(info.ifname, False)
                self._optimistic_set_enabled(info.ifname, False)
        self._apply_network_update(list(self.net_rows.values()), False)
        self.refresh_networks(False)

    def _toggle_network(self):
        sender = self.sender(); ifname = sender.property("ifname")
        if not ifname:
            return
        new_value = not self.net.is_enabled(ifname)
        self.net.set_enabled(ifname, new_value)
        self._optimistic_set_enabled(ifname, new_value)
        self._apply_network_update(list(self.net_rows.values()), False)
        self.refresh_networks(False)

    def _network_name_for_ip(self, ip):
        ip = str(ip or "").strip()
        if not ip:
            return ""
        for info in self.net_rows.values():
            if getattr(info, "ip", "") == ip:
                return str(getattr(info, "name", "") or "")
        return ""

    def _active_network_names(self):
        return ", ".join(dict.fromkeys(i.name for i in self.net_rows.values() if i.enabled and i.internet == "ONLINE")) or "—"

    def _prepend_history_row(self, row):
        self.history_table.insertRow(0)
        vals = [row["name"], self.fmt_bytes(row["size"] or 0), self._nice_datetime(row["completed"]), self.fmt_duration(row["duration"] or 0), self.fmt_bytes(row["avg_speed"] or 0) + "/s", self.fmt_bytes(row["peak_speed"] or 0) + "/s", row["networks_used"] or "—", row["path"], None]
        for c, v in enumerate(vals):
            if v is None: continue
            self.history_table.setItem(0, c, self._text_item(v))
        self.history_table.setItem(0, 8, QTableWidgetItem(""))
        self.history_table.setCellWidget(0, 8, StatusBadge("COMPLETED"))

    def refresh_history(self):
        self.history_table.setRowCount(0)
        for row in self.db.history():
            r = self.history_table.rowCount(); self.history_table.insertRow(r)
            vals = [row["name"], self.fmt_bytes(row["size"] or 0), self._nice_datetime(row["completed"]), self.fmt_duration(row["duration"] or 0), self.fmt_bytes(row["avg_speed"] or 0) + "/s", self.fmt_bytes(row["peak_speed"] or 0) + "/s", row["networks_used"] or "—", row["path"], None]
            for c, v in enumerate(vals):
                if v is None: continue
                self.history_table.setItem(r, c, self._text_item(v))
            self.history_table.setItem(r, 8, QTableWidgetItem(""))
            self.history_table.setCellWidget(r, 8, StatusBadge("COMPLETED"))

    @staticmethod
    def _text_item(v):
        it = QTableWidgetItem(str(v)); it.setToolTip(str(v)); return it          # text may be elided in a narrow window

    def clear_history(self):
        # Clear only the History view. Keep download records, Downloads rows,
        # and on-disk files intact so they remain available after restart.
        self.db.clear_history()
        self.refresh_history()

    def _set_row_status(self, rid, status):
        self.table.set_fields(rid, status=status)

    def _animate_progress_bars(self):
        """Torrent/network tables still use small progress widgets."""
        if getattr(self, "torrent_table", None):
            for r in range(self.torrent_table.rowCount()):
                p = self.torrent_table.cellWidget(r, 7)
                if isinstance(p, FlowProgressBar): p.advance()

    # ---- filtering / search -------------------------------------------------
    def _set_filter(self, key):
        self._filter_key = key
        self._apply_filter()

    def _apply_filter(self, *_):
        group = next((g for k, _l, g in self.FILTERS if k == self._filter_key), None)
        q = self.search.text().strip().lower()
        visible = 0
        sm = self.table.selectionModel()
        for r in range(self.table.rowCount()):
            status = self.table.status_at(r)
            it = self.table.item(r, C_NAME)
            hay = ((it.data(R_SEARCH) or it.text()) if it else "").lower()
            ok = (group is None or status in group) and (not q or q in hay)
            self.table.setRowHidden(r, not ok)
            if not ok and sm.isRowSelected(r, self.table.rootIndex()):
                self.table.selectionModel().select(self.table.model().index(r, 0),
                                                   sm.SelectionFlag.Deselect | sm.SelectionFlag.Rows)
            visible += ok
        total = self.table.rowCount()
        self.filter_note.setVisible(0 < visible < total)
        self.filter_text.setText(f"Showing {visible} of {total} downloads — a filter or search is hiding {total - visible}.")
        self.dl_stack.setCurrentIndex(0 if visible else 1)
        self.drop_zone.set_mode(self.table.rowCount() == 0)
        self._update_selection_actions()

    def _update_selection_actions(self):
        ids = self.table.selected_rids()
        self.sel_info.setText(f"Selected: {self._connections_text(ids[0])}" if len(ids) == 1 else (f"{len(ids)} selected" if ids else ""))

    # ---- download actions (every button routes here; each maps to the engine) ----
    def _row_action(self, action, rid):
        {"start": self.start_one, "pause": self.pause_one, "stop": self.stop_one, "retry": self.retry_one,
         "open": self.open_file, "details": self.show_details, "more": self._show_more_menu}[action](rid)

    def start_one(self, rid):
        """Start a queued download or resume a paused / cancelled one (partial data is reused)."""
        if self.table.status_of(rid) in (None, "COMPLETED", "DOWNLOADING"):
            return
        row = self.db.get(rid)
        if row:
            self.start_job(rid, dict(row))

    def pause_one(self, rid):
        status = self.table.status_of(rid)
        job = self.jobs.get(rid)
        if job and status == "DOWNLOADING":
            job.pause()
        elif not job and status == "QUEUED":
            self.db.update(rid, status="PAUSED"); self._set_row_status(rid, "PAUSED"); self._rows_changed()

    def stop_one(self, rid):
        """Stop/cancel: ends the transfer; partial data is kept so Start can resume it."""
        status = self.table.status_of(rid)
        if status not in ACTIVE_STATES:
            return
        job = self.jobs.get(rid)
        if job:
            job.stop()
        else:
            self.db.update(rid, status="STOPPED"); self._set_row_status(rid, "STOPPED"); self._rows_changed()

    def retry_one(self, rid):
        """Retry a failed / expired download using whatever partial state exists."""
        if self.table.status_of(rid) not in ("ERROR", "RETRYING"):
            return
        row = self.db.get(rid)
        if row:
            self.start_job(rid, dict(row), force=True)

    # ---- open / details / more menu -------------------------------------------------
    def open_file(self, rid):
        path = self._path_for_id(rid)
        if path and path.exists():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))
        else:
            QMessageBox.warning(self, "Open file", "The file does not exist (it may not be finished yet, or was moved).")

    def open_folder_of(self, rid):
        path = self._path_for_id(rid)
        folder = path.parent if path else None
        if folder and folder.exists():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))
        else:
            QMessageBox.warning(self, "Open folder", "The download folder does not exist.")

    def copy_link(self, rid):
        row = self.db.get(rid)
        if row:
            QGuiApplication.clipboard().setText(str(row["url"]))

    def _details_data(self, rid, advanced=False):
        """Everything shown in Details, read from the record and the live worker. Never writes anything."""
        row = self.db.get(rid)
        if not row:
            return None
        row = dict(row); job = self.jobs.get(rid); info = job.info if job else None
        status = self.table.status_of(rid) or row["status"]
        total = info.total if info else int(row["size"] or 0)
        done = info.done if info else int(row["downloaded"] or 0)
        if status == "COMPLETED" and total:
            done = total
        speed = info.speed if (info and status == "DOWNLOADING") else 0
        tr = self.table.row_of(rid)
        cell = lambda col: (self.table.item(tr, col).text() if tr is not None else "—") or "—"
        err = info.error if (info and status in ("ERROR", "RETRYING")) else ""
        general = [("File name", row["name"], 2), ("Save to", row["path"], True), ("Status", theme.status_label(status), False)]
        if err: general.append(("Error", err, True))
        general += [("Downloaded", self.fmt_bytes(done), False), ("Total size", self.fmt_bytes(total) if total else "Unknown", False),
                    ("Added", self._nice_datetime(row["created"]), False), ("Download URL", row["url"], True)]
        if info and info.final_url and info.final_url != row["url"]:
            general.append(("Final URL (after redirects)", info.final_url, True))
        if advanced: general.append(("Download ID", row["id"], False))
        remaining = max(0, total - done) if total else None
        progress = [("Progress", f"{done * 100.0 / total:.1f}%" if total else "Unknown (server did not report a size)", False),
                    ("Downloaded", self.fmt_bytes(done), False), ("Remaining", self.fmt_bytes(remaining) if remaining is not None else "Unknown", False),
                    ("Speed", (self.fmt_bytes(speed) + "/s") if speed else "—", False), ("Elapsed", cell(C_ELAPSED), False),
                    ("Time remaining", cell(C_ETA), False)]
        for label, key, fmt in (("Started", "started", self._nice_datetime), ("Completed", "completed", self._nice_datetime),
                                ("Average speed", "avg_speed", lambda v: self.fmt_bytes(v) + "/s"), ("Peak speed", "peak_speed", lambda v: self.fmt_bytes(v) + "/s")):
            if row[key]: progress.append((label, fmt(row[key]), False))
        usage = {}
        try: usage = job.network_usage() if job else {}
        except Exception: pass
        names = [f"{self._network_name_for_ip(ip) or ip}  ({ip}) — {n} connection{'s' if n != 1 else ''}" for ip, n in usage.items()]
        if not names and row["networks_used"]: names = [row["networks_used"]]
        alive = bool(job and job.is_alive() and status == "DOWNLOADING")
        conn_state = (f"Active — {job.connections} parallel connection{'s' if job.connections != 1 else ''}" if alive and (info.range_supported or job.connections == 1)
                      else "Active — single stream" if alive else {"PAUSED": "Paused (no open connections)", "STOPPED": "Cancelled (no open connections)",
                      "COMPLETED": "Finished (closed)", "ERROR": "Failed (closed)", "QUEUED": "Waiting in queue", "RETRYING": "Waiting to retry"}.get(status, "—"))
        conns = [("Connections", self._connections_text(rid), False), ("Connection status", conn_state, False),
                 ("Network interfaces used", "\n".join(names) if names else "—", False),
                 ("Range requests", ("Supported" if info.range_supported else "Not supported (single stream)") if info and info.total else "—", False),
                 ("Encryption", ("HTTPS, certificate verified" if info.tls_verified else "Not encrypted (HTTP)") if info and info.final_url else "—", False),
                 ("Per-connection detail", "Not reported by the download engine", False)]
        if advanced: conns.append(("Network mode", row["network_mode"], False))
        return {"name": row["name"], "status": theme.status_label(status), "status_raw": status,
                "tabs": {"General": general, "Progress": progress, "Connections": conns}}

    def _connections_text(self, rid):
        """Effective connections for one download: its own stored value, noting when the worker cap lowered it."""
        job = self.jobs.get(rid); row = self.db.get(rid)
        want = int((row["connections"] if row else 0) or 0) or int(self.settings["connections"])
        got = job.connections if job else min(want, MAX_TOTAL_WORKERS // max(1, self.simul.value()))
        return f"{got} connections" + (f" (requested {want}, limited by simultaneous downloads)" if got != want else "")

    def show_details(self, rid):
        if self.db.get(rid):
            DownloadDetailsDialog(lambda adv: self._details_data(rid, adv), self).exec()

    def _build_more_menu(self, rid):
        row = self.db.get(rid)
        if not row:
            return None
        path = Path(row["path"]); status = self.table.status_of(rid) or ""
        m = QMenu(self); acts = {}
        def add(key, text, ic, fn, enabled=True, color=theme.TEXT):
            a = m.addAction(icon(ic, color if enabled else theme.FAINT), text)
            a.setEnabled(enabled); a.triggered.connect(lambda _=False: fn()); acts[key] = a
        head = m.addAction("This download"); head.setEnabled(False)
        def state(key, text, fn, enabled, ic):         # the single place for Start/Pause/Stop/Retry
            a = m.addAction(icon(ic, theme.TEXT if enabled else theme.FAINT), text); a.setEnabled(enabled); a.triggered.connect(lambda _=False: fn()); acts[key] = a
        state("start", "Start / Resume", lambda: self.start_one(rid), status in ("QUEUED", "PAUSED", "STOPPED"), "play")
        state("pause", "Pause", lambda: self.pause_one(rid), status in ("DOWNLOADING", "QUEUED"), "pause")
        state("stop", "Stop (keep partial data)", lambda: self.stop_one(rid), status in ACTIVE_STATES, "stop")
        state("retry", "Retry", lambda: self.retry_one(rid), status in ("ERROR", "RETRYING"), "retry")
        m.addSeparator()
        add("open", "Open file", "open", lambda: self.open_file(rid), enabled=(status == "COMPLETED" and path.exists()))
        add("folder", "Open containing folder", "folder", lambda: self.open_folder_of(rid), enabled=path.parent.exists())
        add("details", "Details…", "info", lambda: self.show_details(rid))
        add("copy", "Copy link", "copy", lambda: self.copy_link(rid))
        m.addSeparator()
        add("remove", "Remove from list", "remove", lambda: self.delete_selected([rid], "remove_only"))
        add("delete", "Delete downloaded files…", "trash", lambda: self.delete_selected([rid], "delete_files"), color=theme.DANGER)
        m._acts = acts
        return m

    def _show_more_menu(self, rid, pos=None):
        try:
            m = self._build_more_menu(rid)
        except Exception as e:                       # never fail silently: a swallowed slot error looks like "the dots do nothing"
            QMessageBox.warning(self, "Menu unavailable", f"Could not open the actions menu: {e}")
            return
        if m:
            at = pos if pos is not None else QCursor.pos()
            QTimer.singleShot(0, lambda: m.exec(at))    # open after the mouse-release event has finished (a popup opened inside it can be dismissed at once)

    # ---- window-level drag & drop -------------------------------------------
    def dragEnterEvent(self, event):
        md = event.mimeData()
        if md.hasUrls() or md.hasText():
            event.acceptProposedAction()

    def dropEvent(self, event):
        for kind, value in DropZone.values_from_mime(event.mimeData()):
            (self.handle_dropped_torrent if kind == "torrent" else self.handle_dropped_url)(value)
        event.acceptProposedAction()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.table.fit_columns()
        self._fit_network_header()

    # Torrent actions
    _torrent_dialog_open = False

    # ---- shared "file exists already" policy (HTTP and torrents use the same dialog and the same remembered answer) ----
    def _resolve_conflict(self, path, what="", *, allow_overwrite=True, overwrite_hint="", allow_use=False, rest=0, remembered_ok=True):
        """-> (choice, apply_rest). choice in rename/overwrite/use/abort. A remembered answer is applied silently when it is
        usable here; an unusable one (e.g. overwrite of a file in use) falls back to the safe Rename instead of asking nothing."""
        mem = self.settings.get("conflict_action", "") if remembered_ok else ""
        if mem == "overwrite" and not allow_overwrite: mem = "rename"
        if mem == "use" and not allow_use: mem = "rename"
        if mem in ("rename", "overwrite", "use"): return mem, False
        choice, remember, apply_rest = ask_conflict(self, path, what, allow_overwrite=allow_overwrite, overwrite_hint=overwrite_hint,
                                                   allow_use=allow_use, rest=rest)
        if remember and choice in ("rename", "overwrite", "use"):
            self.settings["conflict_action"] = choice; save_settings(self.settings)
        return choice, apply_rest

    def _delete_for_overwrite(self, path):
        """Delete a file (and stale partial leftovers of it) the user agreed to replace. -> error text or ''."""
        for p in (path, path + ".parts", path + ".part"):
            try:
                if os.path.isfile(p): os.remove(p)
            except OSError as exc:
                return f"{p}: {exc.strerror or exc}"
        return ""

    def add_torrent(self, source=""):
        """Open New torrent (optionally pre-filled by a drop). One dialog at a time, so repeated drops can't stack."""
        if self.torrent_manager is None:
            QMessageBox.warning(self, "BitTorrent unavailable", "Install the BitTorrent engine with: python -m pip install libtorrent"); return
        if self._torrent_dialog_open: return
        self._torrent_dialog_open = True
        try:
            dialog = TorrentDialog(self, source if isinstance(source, str) else "", self.torrent_manager)
            if dialog.exec() == QDialog.DialogCode.Accepted:
                d = dialog.data()
                self.add_torrent_source(d["source"], d["save_path"], d["paused"], d["priorities"], d["preview"])
        finally:
            self._torrent_dialog_open = False

    def _torrent_files_in_use(self):
        """Absolute paths other torrents / active HTTP downloads are writing or seeding right now."""
        used = {os.path.normcase(str(r["path"])) for r in self.db.all() if r["status"] in ("QUEUED", "DOWNLOADING", "PAUSED")}
        for k, m in list(self.torrent_manager.meta.items()):
            for f in self.torrent_manager.files(k):
                used.add(os.path.normcase(str(Path(m["save_path"]) / f["path"])))
        return used

    def _torrent_conflicts(self, meta, save_path, priorities, preview):
        """Resolve existing destination files per file. -> (renames, overwrite_paths) or None when the user aborts."""
        used = self._torrent_files_in_use(); renames, overwrite, chosen = {}, [], set()
        files = [f for f in meta.files if not priorities or priorities[f.index] > 0]
        todo = [f for f in files if (Path(save_path) / f.path).is_file()]
        sticky = None
        for n, f in enumerate(todo):
            target = str(Path(save_path) / f.path); in_use = os.path.normcase(target) in used
            if sticky and not (sticky == "overwrite" and in_use): choice = sticky
            else:
                choice, rest = self._resolve_conflict(
                    target, f"Torrent: {meta.name}  ·  file {n + 1} of {len(todo)} with a name conflict",
                    allow_overwrite=not in_use, overwrite_hint="This file is in use by another download; it cannot be replaced.",
                    allow_use=True, rest=len(todo) - n - 1)
                if rest: sticky = choice
            if choice == "abort": return None
            if choice == "overwrite" and in_use: choice = "rename"       # never replace a file another download is writing
            if choice == "overwrite": overwrite.append(target)
            elif choice == "rename":
                new = unique_path(target, lambda q: os.path.normcase(q) in used or os.path.normcase(q) in chosen)
                chosen.add(os.path.normcase(new)); renames[f.index] = os.path.relpath(new, save_path)
        return renames, overwrite

    def add_torrent_source(self, source, save_path=None, paused=False, priorities=None, preview=None):
        tm = self.torrent_manager
        if tm is None:
            QMessageBox.warning(self, "BitTorrent unavailable", "Install libtorrent first."); return
        source = str(source).strip(); save_path = save_path or self.settings.get("torrent_dir") or self.default_dir()
        if not source:
            if preview: tm.cancel_preview(preview)
            return
        try:
            known = tm.info_hash_of(source)
            if known and (known in tm.handles or self.db.get_torrent(known)):
                if preview: tm.cancel_preview(preview)
                self._torrent_duplicate(known); return          # same torrent: never a second session
            meta = tm.preview_state(preview)[0] if preview else tm.inspect(source)
            renames = {}
            if meta:
                found = self._torrent_conflicts(meta, save_path, priorities, preview)
                if found is None:                              # Abort: nothing was added, the metadata-only handle is gone
                    if preview: tm.cancel_preview(preview)
                    return
                renames, overwrite = found
                for p in overwrite:
                    err = self._delete_for_overwrite(p)
                    if err:
                        if preview: tm.cancel_preview(preview)
                        QMessageBox.critical(self, "Overwrite failed", f"The existing file could not be deleted (is it open in another program?):\n\n{err}\n\nThe torrent was not added."); return
            key, _ = tm.add(source, save_path, paused=paused, priorities=priorities, renames=renames, preview=preview)
            if self.db.get_torrent(key) is None:
                self.db.add_torrent(key, source, save_path)
            self.torrent_meta[key] = {"started": time.time(), "peak": 0.0}
            self._torrent_refresh_row(key)
            tm.save_resume(key)
            self.tabs.setCurrentWidget(self.torrent_tab)
        except Exception as exc:
            if preview: tm.cancel_preview(preview)
            if "already added" in str(exc).lower() or "duplicate" in str(exc).lower():
                QMessageBox.information(self, "Torrent", "This torrent is already in your list.")
            else:
                QMessageBox.critical(self, "Torrent error", str(exc))

    def _torrent_duplicate(self, key):
        row = self.db.get_torrent(key)
        info = self.torrent_manager.status(key) if key in self.torrent_manager.handles else None
        name = (info.name if info and info.name != "Torrent" else None) or (row["name"] if row else "Torrent")
        state = (info.state if info else (row["status"] if row else "UNKNOWN")).title()
        pct = f"{info.progress:.0f}%" if info and info.progress else ""
        detail = f"{name}\nStatus: {state}{' · ' + pct if pct else ''}\n\nNexus will not start a second session for the same torrent."
        paused = self.torrent_manager.is_paused(key)
        acts = [("show", "Show torrent")] + ([("resume", "Resume")] if paused else [])
        choice = ask_choice(self, "Duplicate torrent", "This torrent was already added.", detail, acts)
        self.nav_buttons["torrents"].click()
        for r in range(self.torrent_table.rowCount()):
            if self.torrent_table.key_at(r) == key:
                self.torrent_table.selectRow(r); self.torrent_table.scrollToItem(self.torrent_table.item(r, 0))
        if choice == "resume":
            self.torrent_manager.resume(key)

    def _restore_torrents(self):
        """Re-add every saved torrent with its resume data (verified pieces, priorities, renames). The engine re-checks the files
        on disk, so moved/deleted data shows up as progress dropping or an error instead of being trusted."""
        if self.torrent_manager is None: return
        failed = []
        for row in self.db.all_torrents():
            src, key = row["source"], row["info_hash"]
            try:
                if not (self.torrent_manager.is_magnet(src) or (Path(src).exists() and Path(src).suffix.lower() == ".torrent")):
                    if not self.torrent_manager._resume_file(key).exists():
                        failed.append(f"{str(src)[:70]}: .torrent file and resume data are gone"); continue
                    src = f"magnet:?xt=urn:btih:{key}"          # the resume file carries the metadata
                paused = not self.settings["torrent_auto_resume"] or row["status"] in ("PAUSED", "COMPLETED")
                self.torrent_manager.add(src, row["save_path"], paused=paused, restore=True)
                self.torrent_meta[key] = {"started": time.time(), "peak": 0.0}
                self._torrent_refresh_row(key)
            except Exception as exc:
                if "already added" in str(exc).lower() or "duplicate" in str(exc).lower():
                    continue          # already running this session; not an error
                failed.append(f"{(row['name'] or str(src))[:70]}: {exc}")
        if failed:
            QTimer.singleShot(0, lambda: QMessageBox.warning(
                self, "Torrent restore", "Some torrents could not be restored:\n\n" + "\n".join(failed)))

    def _torrent_refresh_row(self, key):
        if not self.torrent_manager: return
        info = self.torrent_manager.status(key)
        r = self.torrent_table.row_of(key)
        if r is not None:
            self._set_torrent_row(r, key, info); return
        r = self.torrent_table.rowCount(); self.torrent_table.insertRow(r); self._set_torrent_row(r, key, info)

    def _put(self, t, r, c, text, right=False, tip=None):
        it = t.item(r, c)
        if it is None:
            it = QTableWidgetItem(); t.setItem(r, c, it)
            if right: it.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        if it.text() != text: it.setText(text)
        if tip is not None and it.toolTip() != tip: it.setToolTip(tip)

    def _set_torrent_row(self, r, key, info):
        t = self.torrent_table; meta = self.torrent_manager.meta.get(key, {}); save = meta.get("save_path", "")
        eta = self.fmt_duration(info.eta) if info.eta >= 0 else "—"
        self._put(t, r, TP.C_NAME, info.name, tip=info.name)
        self._put(t, r, TP.C_SIZE, self.fmt_bytes(info.total) if info.total else "—", True)
        self._put(t, r, TP.C_DOWN, self.fmt_bytes(info.download_rate) + "/s", True)
        self._put(t, r, TP.C_UP, self.fmt_bytes(info.upload_rate) + "/s", True)
        self._put(t, r, TP.C_PEERS, str(info.peers), True); self._put(t, r, TP.C_SEEDS, str(info.seeds), True)
        self._put(t, r, TP.C_ETA, eta, True); self._put(t, r, TP.C_PATH, save, tip=save); self._put(t, r, TP.C_KEY, key)
        badge = t.cellWidget(r, TP.C_STATUS)
        if isinstance(badge, StatusBadge):
            badge.set_status(info.state)
        else:
            self._put(t, r, TP.C_STATUS, ""); t.setCellWidget(r, TP.C_STATUS, badge := StatusBadge(info.state))
        badge.setToolTip(info.error)
        progress = t.cellWidget(r, TP.C_PROG)
        if not isinstance(progress, FlowProgressBar):
            self._put(t, r, TP.C_PROG, ""); progress = FlowProgressBar(t); t.setCellWidget(r, TP.C_PROG, progress)
        progress.set_progress(info.progress, info.state)
        self._put(t, r, TP.C_ACT, "")
        t.action_button(r, lambda k=key: self._show_torrent_menu(k))

    _resume_saved_at = 0.0

    def _torrent_tick(self):
        if not self.torrent_manager: return
        tm = self.torrent_manager
        tm.poll()
        if time.time() - self._resume_saved_at > 30:             # periodic, so a crash loses at most ~30 s of verified-piece state
            self._resume_saved_at = time.time(); tm.save_resume()
        stats = tm.all_status()
        n = len(stats)
        self.torrent_page.summary.setText(f"{n} torrent{'s' if n != 1 else ''}  ·  ↓ {self.fmt_bytes(sum(i.download_rate for _, i in stats))}/s  ·  ↑ {self.fmt_bytes(sum(i.upload_rate for _, i in stats))}/s" if n else "")
        for key, info in stats:
            self._torrent_refresh_row(key)
            row = self.db.get_torrent(key)
            if row:
                try:
                    cached = tm.cache_metadata(key)
                    if cached and row["source"] != cached:          # restart-proof: no magnet/DHT lookup or original file needed
                        self.db.update_torrent(key, source=cached)
                except Exception:
                    pass
                meta = self.torrent_meta.setdefault(key, {"started": time.time(), "peak": 0.0})
                meta["peak"] = max(meta.get("peak", 0.0), info.download_rate)
                if info.state == "SEEDING" and row["completed"] is None:
                    elapsed = max(0, time.time() - meta["started"]); avg = info.done / elapsed if elapsed else 0
                    self.db.update_torrent(key, name=info.name, total=info.total, downloaded=info.done, status=info.state, completed=datetime.now().isoformat(timespec="seconds"), duration=elapsed, avg_download=avg, peak_download=meta["peak"])
                else:
                    self.db.update_torrent(key, name=info.name, total=info.total, downloaded=info.done, status=info.state, peak_download=meta["peak"])
        self._refresh_torrent_details()

    # ---- details panel ----------------------------------------------------------------------------
    def show_torrent_details(self, key):
        if key not in (self.torrent_manager.handles if self.torrent_manager else ()): return
        r = self.torrent_table.row_of(key)
        if r is not None: self.torrent_table.selectRow(r)
        self._details_key = key; self.torrent_page.details_box.show(); self._refresh_torrent_details()

    _details_key = None

    def _refresh_torrent_details(self):
        page = self.torrent_page
        if not page.details_box.isVisible() and not page.details_box.isVisibleTo(page): return
        d = self.torrent_manager.details(self._details_key) if self._details_key else None
        if d is None:
            page.details_box.hide(); self._details_key = None; return
        page.details.update_data(d, self.torrent_manager.files(self._details_key))

    def _torrent_selection_changed(self):
        key = self._selected_torrent_key()
        if key and self.torrent_page.details_box.isVisibleTo(self.torrent_page): self.show_torrent_details(key)

    def _torrent_priority(self, key, indices, prio):
        self.torrent_manager.set_file_priority(key, indices, prio); self.torrent_manager.save_resume(key); self._refresh_torrent_details()

    def _selected_torrent_key(self):
        rows = self.torrent_table.selectionModel().selectedRows()
        return self.torrent_table.key_at(rows[0].row()) if rows else None

    def _tkey(self, key):
        """Row actions pass their own key; toolbar-less callers fall back to the selected row."""
        return key if isinstance(key, str) else self._selected_torrent_key()

    def pause_torrent(self, key=None):
        key = self._tkey(key)
        if key and self.torrent_manager: self.torrent_manager.pause(key)

    def resume_torrent(self, key=None):
        key = self._tkey(key)
        if key and self.torrent_manager: self.torrent_manager.resume(key)

    def remove_torrent(self, key=None):
        key = self._tkey(key)
        if not key or not self.torrent_manager: return
        # Task removal only: downloaded data stays on disk.
        if not self.torrent_manager.remove(key, False):
            QMessageBox.warning(self, "Remove torrent", "The torrent engine could not remove this torrent; it was kept in the list."); return
        self.db.delete_torrent(key); self._remove_torrent_row(key)

    def delete_torrent_files(self, key=None):
        key = self._tkey(key)
        if not key or not self.torrent_manager: return
        row = self.db.get_torrent(key)
        name = row["name"] if row else "Torrent"
        if QMessageBox.question(self, "Delete torrent + files", f"Delete '{name}' from Nexus and remove its downloaded files?", QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes: return
        if not self.torrent_manager.remove(key, True):
            QMessageBox.warning(self, "Delete torrent + files", "The torrent engine could not delete this torrent's files; it was kept in the list so you can retry."); return
        self.db.delete_torrent(key); self._remove_torrent_row(key)

    def open_torrent_folder(self, key):
        path = Path(self.torrent_manager.meta.get(key, {}).get("save_path", ""))
        if path.is_dir(): QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))
        else: QMessageBox.warning(self, "Open folder", "The save folder does not exist.")

    def torrent_magnet(self, key):
        row = self.db.get_torrent(key); src = (row["source"] if row else "") or ""
        if src.lower().startswith("magnet:"): return src
        from urllib.parse import quote
        info = self.torrent_manager.status(key)
        return f"magnet:?xt=urn:btih:{key}" + (f"&dn={quote(info.name)}" if info.name != "Torrent" else "")

    def _build_torrent_menu(self, key):
        tm = self.torrent_manager; paused = tm.is_paused(key); info = tm.status(key)
        m = QMenu(self); acts = {}
        head = m.addAction("This torrent"); head.setEnabled(False)
        def add(k, text, ic, fn, enabled=True, color=theme.TEXT):
            a = m.addAction(icon(ic, color if enabled else theme.FAINT), text); a.setEnabled(enabled)
            a.triggered.connect(lambda _=False: fn()); acts[k] = a
        if paused:                                           # only the action that applies to the current state
            add("resume", "Start" if info.done == 0 and info.state == "PAUSED" else "Resume", "play", lambda: self.resume_torrent(key))
        else:
            add("pause", "Pause", "pause", lambda: self.pause_torrent(key), info.state != "ERROR")
        add("details", "Show details", "info", lambda: self.show_torrent_details(key))
        add("recheck", "Force recheck", "refresh", lambda: tm.recheck(key), info.state not in ("METADATA", "CHECKING"))
        m.addSeparator()
        add("folder", "Open containing folder", "folder", lambda: self.open_torrent_folder(key), Path(tm.meta.get(key, {}).get("save_path", "")).is_dir())
        add("copy", "Copy magnet link", "copy", lambda: QGuiApplication.clipboard().setText(self.torrent_magnet(key)))
        m.addSeparator()
        add("remove", "Remove from list", "remove", lambda: self.remove_torrent(key))
        add("delete", "Delete torrent + files…", "trash", lambda: self.delete_torrent_files(key), color=theme.DANGER)
        m._acts = acts
        return m

    def _show_torrent_menu(self, key, pos=None):
        r = self.torrent_table.row_of(key)
        if r is None or not self.torrent_manager: return
        self.torrent_table.selectRow(r)
        self._build_torrent_menu(key).exec(pos if pos is not None else QCursor.pos())

    def _torrent_context(self, pos):
        r = self.torrent_table.rowAt(pos.y())
        key = self.torrent_table.key_at(r) if r >= 0 else None
        if key: self._show_torrent_menu(key, self.torrent_table.viewport().mapToGlobal(pos))

    def _remove_torrent_row(self, key):
        for r in range(self.torrent_table.rowCount() - 1, -1, -1):
            if self.torrent_table.key_at(r) == key: self.torrent_table.removeRow(r)

    def open_preferences(self):
        SettingsDialog(self.settings, self).exec()

    def apply_settings(self, settings, interfaces=None):
        """Settings only change defaults / limits for what starts next; running downloads are never touched."""
        self.settings = dict(settings); save_settings(self.settings)
        for ifname, on in (interfaces or {}).items():
            self.net.set_enabled(ifname, on); self._optimistic_set_enabled(ifname, on)
        if self.torrent_manager is not None:
            self.torrent_manager.configure(self.settings["torrent_port"], self.settings["torrent_down_kbps"], self.settings["torrent_up_kbps"])
        for box, key in ((self.conn, "connections"), (self.simul, "simultaneous_files")):
            box.blockSignals(True); box.setValue(int(self.settings[key])); box.blockSignals(False)
        self._update_selection_actions()
        self.net_timer.setInterval(int(self.settings["network_interval"]) * 1000)
        self.refresh_networks(False)
        # Appearance (accent / theme / reduced motion) is applied live: changing it must
        # not restart the app or disturb active downloads, only recolor the UI.
        apply_theme(QApplication.instance(), self.settings)

    def scan_with_defender(self, path):
        """P5.18/P7.23 -- scans are queued and run one at a time instead of
        spawning an uncontrolled powershell.exe per simultaneous
        completion, and the command is invoked as a plain argument list
        (PowerShell binds -ScanPath as a literal value) instead of a
        hand-built, manually-quoted -Command string."""
        if not path or os.name != "nt" or not path.exists(): return
        self._defender_queue.append(str(path))
        self._pump_defender_queue()

    def _pump_defender_queue(self):
        if self._defender_proc is not None or not self._defender_queue:
            return
        target = self._defender_queue.pop(0)
        try:
            # `-Command` joins its arguments into ONE script string, so a file name such as "a;calc.exe;.txt" would run code.
            # The path travels in an environment variable instead; PowerShell reads it as data, never as script text.
            self._defender_proc = subprocess.Popen(
                ["powershell", "-NoProfile", "-Command", "Start-MpScan -ScanPath $env:NEXUS_SCAN_PATH"],
                env={**os.environ, "NEXUS_SCAN_PATH": target},
            )
        except Exception:
            self._defender_proc = None

    def _poll_defender_queue(self):
        if self._defender_proc is not None and self._defender_proc.poll() is not None:
            self._defender_proc = None
            self._pump_defender_queue()

    @staticmethod
    def open_folder(path):
        try:
            target = str(path.parent if path and path.exists() else Path.home() / "Downloads")
            if os.name == "nt": os.startfile(target)
            elif sys.platform == "darwin": subprocess.Popen(["open", target])
            else: subprocess.Popen(["xdg-open", target])
        except Exception: pass

    @staticmethod
    def _nice_datetime(value):
        if not value: return "—"
        try: return datetime.fromisoformat(str(value)).strftime("%Y-%m-%d %H:%M:%S")
        except Exception: return str(value)

    @staticmethod
    def fmt_duration(seconds):
        if seconds is None: return "—"
        try: s = max(0, int(seconds))
        except Exception: return "—"
        if s < 60: return f"{s}s"
        m, s = divmod(s, 60)
        if m < 60: return f"{m}m {s:02d}s"
        h, m = divmod(m, 60)
        if h < 24: return f"{h}h {m:02d}m"
        d, h = divmod(h, 24); return f"{d}d {h:02d}h"

    @staticmethod
    def fmt_bytes(n):
        n = float(n or 0)
        for unit in ("B", "KB", "MB", "GB", "TB"):
            if n < 1024: return f"{n:.1f} {unit}"
            n /= 1024
        return f"{n:.1f} PB"

    def closeEvent(self, event):
        self._shutting_down.set()
        if self.torrent_manager is not None:
            try:
                for k, i in self.torrent_manager.all_status(): self.db.update_torrent(k, status=i.state)      # so a Pause survives the restart
                self.torrent_manager.shutdown()                  # flush resume data atomically before the session goes away
            except Exception: pass
        self.net.stop()
        for job in list(self.jobs.values()):
            try:
                job.stop()
                job.wait(2.0)
            except Exception:
                pass
        scan_thread = self._last_scan_thread
        if scan_thread is not None and scan_thread.is_alive():
            scan_thread.join(1.5)
        super().closeEvent(event)


def run():
    QGuiApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setWindowIcon(QIcon(str(ASSETS / "logo.png")))
    font = QFont(); font.setFamilies(["Segoe UI Variable Text", "Segoe UI", "Inter", "Helvetica Neue", "Arial"]); font.setPointSize(10)
    app.setFont(font)
    apply_theme(app, load_settings())
    window = MainWindow(); window.show(); sys.exit(app.exec())


def apply_theme(app, settings=None):
    """Appearance (accent + light/dark/system) + stylesheet + dialog fade-in.

    Re-applied whenever Settings is saved so the accent change takes effect without
    restarting; running downloads are never touched by this."""
    if settings:
        theme.apply_appearance_from_settings(settings)
    from PySide6.QtGui import QPalette
    pal = QPalette()
    for role, color in ((QPalette.ColorRole.Window, theme.BG), (QPalette.ColorRole.WindowText, theme.TEXT),
                        (QPalette.ColorRole.Base, theme.SURFACE), (QPalette.ColorRole.AlternateBase, theme.SURFACE2),
                        (QPalette.ColorRole.Text, theme.TEXT), (QPalette.ColorRole.Button, theme.SURFACE2),
                        (QPalette.ColorRole.ButtonText, theme.TEXT), (QPalette.ColorRole.Highlight, theme.ACCENT),
                        (QPalette.ColorRole.HighlightedText, theme.ON_ACCENT), (QPalette.ColorRole.ToolTipBase, theme.SURFACE2),
                        (QPalette.ColorRole.ToolTipText, theme.TEXT), (QPalette.ColorRole.PlaceholderText, theme.FAINT)):
        pal.setColor(role, QColor(color))
    app.setPalette(pal)
    app.setStyleSheet(theme.build_qss())
    if not isinstance(getattr(app, "_nx_fader", None), theme.DialogFader):   # install once, not per call
        app._nx_fader = theme.DialogFader(app)
        app.installEventFilter(app._nx_fader)
    for w in app.topLevelWidgets():                   # recolour icons + custom-painted widgets already on screen
        theme.retint_icons(w); w.update()
    hints = app.styleHints()
    if not getattr(app, "_nx_scheme_hooked", False) and hasattr(hints, "colorSchemeChanged"):
        app._nx_scheme_hooked = True                  # theme = System: follow the OS when it flips light/dark
        hints.colorSchemeChanged.connect(lambda *_: theme.appearance_state()["theme"] == "system" and apply_theme(app, {"theme": "system"}))
