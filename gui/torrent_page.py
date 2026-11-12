"""Torrents page: one row per torrent, row-level actions, empty state, drag-over indicator.
Only layout/drop plumbing lives here; every action is performed by MainWindow through the real engine."""
import os
from PySide6.QtCore import Qt, Signal, QSize
from PySide6.QtWidgets import (QAbstractItemView, QHeaderView, QLabel, QSplitter, QStackedLayout, QTableWidget, QToolButton,
                               QVBoxLayout, QWidget)
from . import theme
from .icons import icon
from .torrent_dialog import torrent_payloads
from .torrent_details import TorrentDetailsPanel

# logical columns (the info-hash column 9 is the row key; keep it stable, it is hidden)
C_NAME, C_SIZE, C_STATUS, C_DOWN, C_UP, C_PEERS, C_SEEDS, C_PROG, C_PATH, C_KEY, C_ETA, C_ACT = range(12)
HEADERS = ["Name", "Size", "Status", "Download", "Upload", "Peers", "Seeds", "Progress", "Save Path", "Info Hash", "ETA", ""]
VISUAL = [C_NAME, C_SIZE, C_STATUS, C_PROG, C_DOWN, C_UP, C_ETA, C_PEERS, C_SEEDS, C_PATH, C_KEY, C_ACT]
CHARS = {C_SIZE: 8, C_PROG: 12, C_DOWN: 9, C_UP: 9, C_ETA: 7, C_PEERS: 5, C_SEEDS: 5, C_PATH: 18}      # status width is measured from the pill
DROP_ORDER = [C_PATH, C_UP, C_SEEDS, C_PEERS, C_SIZE, C_ETA, C_DOWN]       # hidden first when the window is narrow
NAME_MIN_CHARS = 18


class TorrentTable(QTableWidget):
    def __init__(self, parent=None):
        super().__init__(0, len(HEADERS), parent)
        self.setHorizontalHeaderLabels(HEADERS)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setShowGrid(False); self.setWordWrap(False); self.setMouseTracking(True)
        self.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        self.setAcceptDrops(False)                            # drops are handled by the page, never by the cells
        self.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.verticalHeader().hide(); self.verticalHeader().setDefaultSectionSize(int(self.fontMetrics().height() * 2.7))
        h = self.horizontalHeader(); h.setSectionsMovable(False); h.setStretchLastSection(False); h.setHighlightSections(False)
        h.setSectionResizeMode(QHeaderView.ResizeMode.Fixed); h.setSectionResizeMode(C_NAME, QHeaderView.ResizeMode.Stretch)
        h.setMinimumSectionSize(24)
        for visual, logical in enumerate(VISUAL):
            h.moveSection(h.visualIndex(logical), visual)
        self.setColumnHidden(C_KEY, True)
        self._fit()

    # -- responsive columns: Name takes the slack; low-priority columns drop out before Name gets cramped
    def _fit(self):
        ch = self.fontMetrics().horizontalAdvance("0")
        for col in DROP_ORDER: self.setColumnHidden(col, False)
        for col, n in CHARS.items(): self.setColumnWidth(col, ch * n + 20)
        self.setColumnWidth(C_STATUS, self.fontMetrics().horizontalAdvance("Downloading") + 52)      # widest status pill (text + dot + padding)
        self.setColumnWidth(C_ACT, 40)
        for col in DROP_ORDER:
            fixed = sum(self.columnWidth(c) for c in range(len(HEADERS)) if c != C_NAME and not self.isColumnHidden(c))
            if self.viewport().width() - fixed >= ch * NAME_MIN_CHARS: break
            self.setColumnHidden(col, True)

    def resizeEvent(self, e):
        super().resizeEvent(e); self._fit()

    def showEvent(self, e):
        super().showEvent(e); self._fit()

    def key_at(self, row):
        it = self.item(row, C_KEY); return it.text() if it else None

    def row_of(self, key):
        return next((r for r in range(self.rowCount()) if self.key_at(r) == key), None)

    def action_button(self, row, on_click):
        """One '⋯' button per row (created once); on_click() gets no args and knows its own torrent key."""
        b = self.cellWidget(row, C_ACT)
        if b is None:
            b = QToolButton(); b.setObjectName("rowAction"); b.setIcon(icon("more", theme.MUTED)); b.setAutoRaise(True); b.setIconSize(QSize(18, 18))
            b.setToolTip("More actions for this torrent"); b.setAccessibleName("More actions"); b.setFixedSize(32, 30)
            b.setCursor(Qt.CursorShape.PointingHandCursor); b.clicked.connect(lambda _=False: on_click())
            self.setCellWidget(row, C_ACT, b)
        return b


class TorrentPage(QWidget):
    dropped = Signal(str)          # a validated-by-type .torrent path or magnet link
    rejected = Signal()            # something was dropped that is neither

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        lay = QVBoxLayout(self); lay.setContentsMargins(0, 8, 0, 0); lay.setSpacing(8)
        self.summary = QLabel(""); self.summary.setObjectName("muted"); lay.addWidget(self.summary)
        host = QWidget(); self.stack = QStackedLayout(host)
        self.split = QSplitter(Qt.Orientation.Vertical); self.split.setChildrenCollapsible(False); lay.addWidget(self.split, 1)
        self.split.addWidget(host)
        self.table = TorrentTable()
        empty = QWidget(); el = QVBoxLayout(empty); el.setAlignment(Qt.AlignmentFlag.AlignCenter); el.setSpacing(8)
        pic = QLabel(); pic.setPixmap(icon("torrent", theme.ACCENT).pixmap(46, 46)); pic.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title = QLabel("No torrents yet"); title.setObjectName("emptyTitle"); title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hint = QLabel("Click Add Torrent, or drop a .torrent file or magnet link here."); hint.setObjectName("emptyHint")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter); hint.setWordWrap(True)
        for w in (pic, title, hint): el.addWidget(w)
        self.empty = empty; self.stack.addWidget(empty); self.stack.addWidget(self.table)
        self.details = TorrentDetailsPanel(); self.details_box = self.details      # hidden until a torrent's details are requested
        self.details.setMinimumHeight(220); self.details.hide(); self.details.closed.connect(self.details.hide)
        self.split.addWidget(self.details); self.split.setStretchFactor(0, 3); self.split.setStretchFactor(1, 2)
        self.overlay = QLabel("Drop a .torrent file or magnet link to add it", self)
        self.overlay.setObjectName("dropOverlay"); self.overlay.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.overlay.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents); self.overlay.hide()
        m = self.table.model()
        for sig in (m.rowsInserted, m.rowsRemoved, m.modelReset): sig.connect(self.sync)
        self.sync()

    def sync(self, *_):
        self.stack.setCurrentWidget(self.table if self.table.rowCount() else self.empty)

    def resizeEvent(self, e):
        super().resizeEvent(e); self.overlay.setGeometry(self.rect().adjusted(0, 8, 0, 0))

    # -- drag & drop: only .torrent files / magnet links are accepted; anything else is refused by the OS cursor
    def dragEnterEvent(self, e):
        md = e.mimeData()
        if not (md.hasUrls() or md.hasText()):
            e.ignore(); return
        e.acceptProposedAction()                      # accept everything so it never bubbles to the window's link handler
        if torrent_payloads(md):                      # indicator only for what we can really add
            self.overlay.setGeometry(self.rect().adjusted(0, 8, 0, 0)); self.overlay.raise_(); self.overlay.show()

    def dragLeaveEvent(self, e):
        self.overlay.hide()

    def dropEvent(self, e):
        self.overlay.hide()
        items = torrent_payloads(e.mimeData())
        if not items:
            self.rejected.emit(); return
        e.acceptProposedAction()
        for v in dict.fromkeys(items):                 # same file twice in one drop -> once
            self.dropped.emit(v)
