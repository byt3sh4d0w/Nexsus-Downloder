"""Details panel for the selected torrent (General / Details / Progress / Files / Connections).
Shows only values the engine reports; anything it does not know reads 'N/A'."""
from datetime import datetime
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (QAbstractItemView, QGridLayout, QHBoxLayout, QHeaderView, QLabel, QMenu, QProgressBar,
                               QPushButton, QTableWidget, QTableWidgetItem, QTabWidget, QToolButton, QTreeWidget,
                               QVBoxLayout, QWidget)
from . import theme
from .icons import icon
from .torrent_dialog import PRIORITIES, PRIORITY_NAME, ROLE_INDEX, build_tree, fmt_size

NA = "N/A"
STATE_TEXT = {"CHECKING": "Checking files", "METADATA": "Fetching metadata", "DOWNLOADING": "Downloading", "SEEDING": "Seeding",
              "PAUSED": "Paused", "COMPLETED": "Completed", "ERROR": "Error", "ALLOCATING": "Allocating", "QUEUED": "Queued"}


def fmt_rate(n): return f"{fmt_size(n)}/s"


def fmt_eta(s):
    if s is None or s < 0: return NA
    h, r = divmod(int(s), 3600); m, sec = divmod(r, 60)
    return f"{h}h {m:02d}m" if h else (f"{m}m {sec:02d}s" if m else f"{sec}s")


def fmt_time(ts): return datetime.fromtimestamp(ts).strftime("%d %b %Y, %H:%M") if ts and ts > 0 else NA


class _Grid(QWidget):
    """label/value rows with selectable values; rows are created once and updated in place."""
    def __init__(self, labels):
        super().__init__()
        g = QGridLayout(self); g.setContentsMargins(0, 0, 0, 0); g.setHorizontalSpacing(18); g.setVerticalSpacing(6)
        g.setColumnStretch(1, 1); self.values = {}
        for r, name in enumerate(labels):
            k = QLabel(name + ":"); k.setObjectName("muted"); k.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop)
            v = QLabel(NA); v.setWordWrap(True); v.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            g.addWidget(k, r, 0); g.addWidget(v, r, 1); self.values[name] = v
        g.setRowStretch(len(labels), 1)

    def set(self, **kv):
        for k, v in kv.items():
            w = self.values[k.replace("_", " ")]
            if w.text() != str(v): w.setText(str(v))


class TorrentDetailsPanel(QWidget):
    closed = Signal()
    priorityRequested = Signal(str, list, int)         # key, file indices, libtorrent priority

    def __init__(self, parent=None):
        super().__init__(parent)
        self._key, self._file_sig = None, None
        v = QVBoxLayout(self); v.setContentsMargins(0, 0, 0, 0)
        self.tabs = QTabWidget(); self.tabs.setDocumentMode(True); v.addWidget(self.tabs)
        close = QToolButton(); close.setObjectName("rowAction"); close.setText("✕"); close.setToolTip("Close details"); close.setAccessibleName("Close details")
        close.setFixedSize(28, 26); close.clicked.connect(self.closed.emit); self.tabs.setCornerWidget(close)

        gen = QWidget(); gl = QHBoxLayout(gen); gl.setContentsMargins(16, 12, 16, 8); gl.setSpacing(18)
        pic = QLabel(); pic.setPixmap(icon("folder", theme.ACCENT).pixmap(52, 52)); pic.setAlignment(Qt.AlignmentFlag.AlignTop); gl.addWidget(pic)
        col = QVBoxLayout(); col.setSpacing(6); gl.addLayout(col, 1)
        self.name = QLabel(); self.name.setObjectName("dialogTitle"); self.name.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.path = QLabel(); self.path.setObjectName("muted"); self.path.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        copy = QToolButton(); copy.setObjectName("rowAction"); copy.setIcon(icon("copy", theme.MUTED)); copy.setToolTip("Copy the save location"); copy.setAccessibleName("Copy path")
        copy.clicked.connect(lambda: QGuiApplication.clipboard().setText(self.path.text()))
        prow = QHBoxLayout(); prow.addWidget(self.path); prow.addWidget(copy); prow.addStretch()
        self.bar = QProgressBar(); self.bar.setRange(0, 1000); self.bar.setTextVisible(False); self.bar.setFixedHeight(10)
        self.pct = QLabel(); brow = QHBoxLayout(); brow.addWidget(self.bar, 1); brow.addWidget(self.pct)
        self.general = _Grid(["Downloaded", "Uploaded", "Added at", "Priority", "File"])
        col.addWidget(self.name); col.addLayout(prow); col.addLayout(brow); col.addWidget(self.general, 1)
        self.tabs.addTab(gen, "GENERAL")

        self.details = _Grid(["Total size", "Files", "Selected files", "Pieces", "Piece size", "Info hash", "Comment", "Created by",
                              "Created on", "Private", "Trackers"]); self._wrap(self.details, "DETAILS")
        self.progress = _Grid(["Status", "Progress", "Pieces verified", "Download speed", "Upload speed", "ETA", "Downloaded", "Uploaded",
                               "Discarded data"]); self._wrap(self.progress, "PROGRESS")

        self.files = QTreeWidget(); self.files.setColumnCount(4); self.files.setHeaderLabels(["Name", "Size", "Done", "Priority"])
        self.files.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection); self.files.setUniformRowHeights(True)
        self.files.setTextElideMode(Qt.TextElideMode.ElideMiddle); self.files.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        h = self.files.header(); h.setStretchLastSection(False); h.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for c, w in ((1, 90), (2, 70), (3, 90)): h.setSectionResizeMode(c, QHeaderView.ResizeMode.Fixed); self.files.setColumnWidth(c, w)
        self.files.customContextMenuRequested.connect(self._file_menu); self.tabs.addTab(self.files, "FILES")
        self._leaves = {}

        con = QWidget(); cl = QVBoxLayout(con); cl.setContentsMargins(16, 10, 16, 8)
        self.conn = _Grid(["Peers", "Seeds", "Connections"]); self.conn.setFixedHeight(84); cl.addWidget(self.conn)
        self.trackers = self._table(["Tracker", "Status", "Message"], 2); self.peers = self._table(["Peer", "Client", "Download", "Upload", "Progress"], 0)
        cl.addWidget(QLabel("Trackers")); cl.addWidget(self.trackers, 1); cl.addWidget(QLabel("Connected peers")); cl.addWidget(self.peers, 2)
        self.tabs.addTab(con, "CONNECTIONS")

    def _wrap(self, grid, title):
        w = QWidget(); l = QVBoxLayout(w); l.setContentsMargins(16, 12, 16, 8); l.addWidget(grid); self.tabs.addTab(w, title)

    @staticmethod
    def _table(headers, stretch):
        t = QTableWidget(0, len(headers)); t.setHorizontalHeaderLabels(headers); t.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        t.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows); t.verticalHeader().hide(); t.setShowGrid(False)
        t.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive); t.horizontalHeader().setSectionResizeMode(stretch, QHeaderView.ResizeMode.Stretch)
        t.verticalHeader().setDefaultSectionSize(int(t.fontMetrics().height() * 2.0)); return t

    # ---- file priorities (real engine call via MainWindow) --------------------------------------
    def _file_menu(self, pos):
        idx = [int(it.data(0, ROLE_INDEX)) for it in self._selected_leaves()]
        if not (idx and self._key): return
        m = QMenu(self)
        for name, p in [("Skip (do not download)", 0)] + PRIORITIES:
            m.addAction(name).triggered.connect(lambda _=False, p=p: self.priorityRequested.emit(self._key, idx, p))
        m.exec(self.files.viewport().mapToGlobal(pos))

    def _selected_leaves(self):
        out = []
        def walk(it):
            if it.childCount() == 0 and it.data(0, ROLE_INDEX) is not None: out.append(it)
            for i in range(it.childCount()): walk(it.child(i))
        for it in self.files.selectedItems(): walk(it)
        return list(dict.fromkeys(out))

    # ---- live update -----------------------------------------------------------------------------
    def update_data(self, d, files):
        """d = TorrentManager.details(key); files = TorrentManager.files(key)."""
        info, meta = d["info"], d["meta"]; key = d["key"]
        if key != self._key: self._key, self._file_sig = key, None; self.peers.setRowCount(0)
        self.name.setText(info.name); self.path.setText(d["save_path"] or NA)
        self.bar.setValue(int(info.progress * 10)); self.pct.setText(f"{info.progress:.0f}%  {STATE_TEXT.get(info.state, info.state.title())}")
        prios = {f["priority"] for f in files if f["priority"] > 0}
        prio = NA if not prios else (PRIORITY_NAME.get(next(iter(prios)), str(next(iter(prios)))).lower() if len(prios) == 1 else "mixed (see Files)")
        src = d["source"]; link = "" if not src else (src if src.lower().startswith("magnet:") else "file:///" + src.replace("\\", "/").lstrip("/"))
        total = fmt_size(info.total) if info.total else NA
        ratio = f"{info.uploaded / info.downloaded:.2f}" if info.downloaded > 0 else "0"
        self.general.set(Downloaded=f"{fmt_size(info.done)} / {total}", Uploaded=f"{fmt_size(info.uploaded)} (ratio: {ratio})",
                         Added_at=fmt_time(info.added), Priority=prio, File=link or NA)
        if meta:
            self.details.set(Total_size=fmt_size(meta.total), Files=len(meta.files), Selected_files=d["selected"], Pieces=meta.num_pieces,
                             Piece_size=fmt_size(meta.piece_length), Info_hash=meta.info_hash, Comment=meta.comment or NA,
                             Created_by=meta.creator or NA, Created_on=fmt_time(meta.created), Private="Yes" if meta.private else "No",
                             Trackers="\n".join(meta.trackers) or "None in the torrent file")
        else:
            self.details.set(**{k.replace(" ", "_"): "Waiting for metadata" if k == "Total size" else NA for k in self.details.values})
        self.progress.set(Status=STATE_TEXT.get(info.state, info.state.title()) + (f" — {info.error}" if info.error else ""),
                          Progress=f"{info.progress:.1f}%", Pieces_verified=f"{d['pieces_have']} of {meta.num_pieces}" if meta else NA,
                          Download_speed=fmt_rate(info.download_rate), Upload_speed=fmt_rate(info.upload_rate), ETA=fmt_eta(info.eta),
                          Downloaded=fmt_size(info.downloaded), Uploaded=fmt_size(info.uploaded), Discarded_data=fmt_size(d["failed_bytes"]))
        self.conn.set(Peers=info.peers, Seeds=info.seeds, Connections=d["connections"])
        self._fill(self.trackers, [(t["url"], t["status"], t["message"]) for t in d["trackers"]])
        self._fill(self.peers, [(p["addr"], p["client"], fmt_rate(p["down"]), fmt_rate(p["up"]), f"{p['progress']:.0f}%") for p in d["peer_list"]])
        self._update_files(files)

    @staticmethod
    def _fill(t, rows):
        if t.rowCount() != len(rows): t.setRowCount(len(rows))
        for r, row in enumerate(rows):
            for c, v in enumerate(row):
                it = t.item(r, c)
                if it is None: t.setItem(r, c, QTableWidgetItem(str(v)))
                elif it.text() != str(v): it.setText(str(v))

    def _update_files(self, files):
        sig = [(f["index"], f["path"]) for f in files]
        if sig != self._file_sig:                         # new torrent or metadata just arrived: rebuild the tree once
            self._file_sig = sig; self.files.clear()
            class _F:                                      # minimal stand-in for TorrentFile
                def __init__(s, f): s.index, s.path, s.size = f["index"], f["path"], f["size"]
            self._leaves = build_tree(self.files, [_F(f) for f in files]) if files else {}
        for f in files:
            it = self._leaves.get(f["index"])
            if it is None: continue
            pct = f"{f['done'] * 100 // f['size']}%" if f["size"] else "100%"
            prio = "Skipped" if f["priority"] == 0 else PRIORITY_NAME.get(f["priority"], str(f["priority"]))
            for c, v in ((2, pct), (3, prio)):
                if it.text(c) != v: it.setText(c, v)
