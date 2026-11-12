import os, shutil, time
from pathlib import Path
from . import theme
from .icons import icon
from core.links import magnet_info_hash
from core.torrent import lt
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QAbstractItemView, QDialog, QDialogButtonBox, QFileDialog, QHBoxLayout, QHeaderView, QLabel,
                               QLineEdit, QMenu, QMessageBox, QPushButton, QStackedLayout, QTreeWidget, QTreeWidgetItem,
                               QVBoxLayout, QWidget)

TORRENT_FILTER = "Torrent files (*.torrent);;All files (*.*)"
PRIORITIES = [("Low", 1), ("Normal", 4), ("High", 6), ("Maximum", 7)]       # libtorrent file priorities (0 = skipped = unchecked)
PRIORITY_NAME = {p: n for n, p in PRIORITIES}
DEFAULT_PRIORITY = 4
ROLE_INDEX, ROLE_SIZE, ROLE_PRIO = Qt.ItemDataRole.UserRole, Qt.ItemDataRole.UserRole + 1, Qt.ItemDataRole.UserRole + 2


def fmt_size(n):
    n = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024


def validate_torrent_source(text):
    """-> (ok, message). Cheap, engine-free checks a user can act on; the engine re-validates on add."""
    v = str(text).strip()
    if not v:
        return False, "Paste a magnet link or choose a .torrent file."
    if v.lower().startswith("magnet:"):
        if not magnet_info_hash(v):
            return False, "This magnet link has no valid info-hash (xt=urn:btih:…)."
        return True, "Magnet link detected"
    p = Path(v)
    if p.suffix.lower() != ".torrent":
        return False, "Enter a magnet link or the path of a .torrent file."
    if not p.is_file():
        return False, "This .torrent file does not exist or is not accessible."
    try:
        if lt is not None:
            lt.torrent_info(str(p))
        elif p.stat().st_size == 0:
            raise ValueError("empty file")
    except Exception as exc:
        return False, f"Not a valid .torrent file ({exc})."
    return True, ".torrent file detected"


def torrent_payloads(mime):
    """Drop/paste payload -> [value, ...] of local .torrent paths and magnet links. Everything else is ignored."""
    out = []
    if mime.hasUrls():
        for u in mime.urls():
            f = u.toLocalFile()
            if f and f.lower().endswith(".torrent"):
                out.append(f)
            elif u.toString().lower().startswith("magnet:"):
                out.append(u.toString())
    if not out and mime.hasText():
        t = mime.text().strip()
        if t.lower().startswith("magnet:"):
            out.append(t)
        elif t.lower().endswith(".torrent") and os.path.isfile(t):
            out.append(t)
    return out


def build_tree(tree, files, folder_cb=None, leaf_cb=None):
    """Fill a QTreeWidget with the torrent's folders/files (columns: name, size). -> {file index: leaf item}.
    Folders show the summed size of everything below them."""
    right = Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
    folders, leaves = {}, {}
    def folder(parts):
        if not parts: return tree.invisibleRootItem()
        key = tuple(parts)
        if key not in folders:
            it = QTreeWidgetItem(folder(parts[:-1])); it.setText(0, parts[-1]); it.setIcon(0, icon("folder", theme.MUTED)); folders[key] = it
            if folder_cb: folder_cb(it)
        return folders[key]
    for f in files:
        parts = f.path.replace("\\", "/").split("/")
        it = QTreeWidgetItem(folder(parts[:-1])); it.setText(0, parts[-1]); it.setToolTip(0, f.path)
        it.setData(0, ROLE_INDEX, f.index); it.setData(0, ROLE_SIZE, f.size); it.setText(1, fmt_size(f.size)); it.setTextAlignment(1, right)
        leaves[f.index] = it
        if leaf_cb: leaf_cb(it, f)
    def total(it):
        if it.childCount() == 0: return int(it.data(0, ROLE_SIZE) or 0)
        n = sum(total(it.child(i)) for i in range(it.childCount())); it.setText(1, fmt_size(n)); it.setTextAlignment(1, right); return n
    root = tree.invisibleRootItem()
    for i in range(root.childCount()): total(root.child(i)); root.child(i).setExpanded(True)
    return leaves


def nearest_existing(path):
    p = Path(path).expanduser()
    while not p.exists() and p != p.parent: p = p.parent
    return p


class TorrentDialog(QDialog):
    """Add Torrent: source -> real file list (from the .torrent, or from peers for a magnet) -> choose files/priorities/folder."""
    METADATA_TIMEOUT = 90          # seconds before a magnet is offered "as is"

    def __init__(self, parent=None, source="", manager=None):
        super().__init__(parent)
        self.manager = manager if manager is not None else getattr(parent, "torrent_manager", None)
        self.setWindowTitle("New torrent"); self.setModal(True); self.setAcceptDrops(True)
        self.setMinimumSize(640, 580); self.resize(700, 660); self.setSizeGripEnabled(True)
        ps = getattr(parent, "settings", {}) or {}
        self.meta, self._preview, self._hash, self._paused, self._deadline = None, "", "", False, 0.0
        self._leaves = []
        self.source = QLineEdit(); self.source.setPlaceholderText("Magnet link or .torrent file path")
        self.save = QLineEdit(ps.get("torrent_dir") or ps.get("download_dir") or str(Path.home() / "Downloads"))
        self.browse_file = self._button("open", "Browse for a .torrent file…", self.pick)
        self.browse_dir = self._button("folder", "Choose the save folder…", self.pick_dir)
        self.kind = QLabel(); self.kind.setWordWrap(True)
        self.dest_note = QLabel(); self.dest_note.setWordWrap(True)
        self.filter = QLineEdit(); self.filter.setPlaceholderText("Filter by name…"); self.filter.setClearButtonEnabled(True)
        self.sel_all = QPushButton("Select all"); self.sel_none = QPushButton("Select none"); self.prio_btn = QPushButton("Priority")
        for b in (self.sel_all, self.sel_none, self.prio_btn): b.setAutoDefault(False)
        self.tree = QTreeWidget(); self.tree.setColumnCount(3); self.tree.setHeaderLabels(["Name", "Size", "Priority"])
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection); self.tree.setUniformRowHeights(True)
        self.tree.setTextElideMode(Qt.TextElideMode.ElideMiddle); self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        h = self.tree.header(); h.setStretchLastSection(False); h.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        h.setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed); h.setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        self.tree.setColumnWidth(1, 96); self.tree.setColumnWidth(2, 90)
        self.placeholder = QLabel(); self.placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter); self.placeholder.setWordWrap(True); self.placeholder.setObjectName("emptyHint")
        host = QWidget(); self.stack = QStackedLayout(host); self.stack.addWidget(self.placeholder); self.stack.addWidget(self.tree)
        self.summary = QLabel(); self.summary.setObjectName("muted"); self.summary.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.cancel_btn = QPushButton("Cancel"); self.later_btn = QPushButton("Download later"); self.ok = QPushButton("Download")
        self.ok.setObjectName("primaryButton"); self.ok.setDefault(True)
        for b in (self.cancel_btn, self.later_btn): b.setAutoDefault(False)

        lay = QVBoxLayout(self); lay.setContentsMargins(20, 18, 20, 16); lay.setSpacing(8)
        head = QLabel("New torrent"); head.setObjectName("dialogTitle"); lay.addWidget(head)
        lay.addWidget(QLabel("Magnet link or .torrent file"))
        r1 = QHBoxLayout(); r1.addWidget(self.source, 1); r1.addWidget(self.browse_file); lay.addLayout(r1); lay.addWidget(self.kind)
        lay.addWidget(QLabel("Save to"))
        r2 = QHBoxLayout(); r2.addWidget(self.save, 1); r2.addWidget(self.browse_dir); lay.addLayout(r2); lay.addWidget(self.dest_note)
        r3 = QHBoxLayout(); r3.addWidget(QLabel("Files")); r3.addWidget(self.filter, 1); r3.addWidget(self.prio_btn); r3.addWidget(self.sel_all); r3.addWidget(self.sel_none); lay.addLayout(r3)
        lay.addWidget(host, 1)
        r4 = QHBoxLayout(); r4.addWidget(self.summary, 1); lay.addLayout(r4)
        r5 = QHBoxLayout(); r5.addStretch(); r5.addWidget(self.cancel_btn); r5.addWidget(self.later_btn); r5.addWidget(self.ok); lay.addLayout(r5)

        menu = QMenu(self.prio_btn)
        for name, p in PRIORITIES: menu.addAction(name).triggered.connect(lambda _=False, p=p: self._set_priority(p))
        self.prio_btn.setMenu(menu)
        self.tree.customContextMenuRequested.connect(lambda pos: menu.exec(self.tree.viewport().mapToGlobal(pos)))
        self.source.textChanged.connect(self.describe); self.save.textChanged.connect(self._update_summary)
        self.filter.textChanged.connect(self._apply_filter); self.tree.itemChanged.connect(lambda *_: self._update_summary())
        self.sel_all.clicked.connect(lambda: self._check_all(True)); self.sel_none.clicked.connect(lambda: self._check_all(False))
        self.cancel_btn.clicked.connect(self.reject); self.ok.clicked.connect(self.accept); self.later_btn.clicked.connect(self._later)
        self._timer = QTimer(self); self._timer.timeout.connect(self._poll_preview)
        self.source.setText(source) if source else self.describe("")
        self.source.setFocus()

    @staticmethod
    def _button(ic, tip, fn):
        b = QPushButton(); b.setIcon(icon(ic, theme.TEXT)); b.setToolTip(tip); b.setAccessibleName(tip)
        b.setFixedSize(36, 32); b.setAutoDefault(False); b.clicked.connect(lambda _=False: fn())
        return b

    # -- pickers: parented to the dialog, failures are shown, never swallowed ---------------------
    def pick(self):
        cur = self.source.text().strip()
        start = str(Path(cur).parent) if cur and not cur.lower().startswith("magnet:") and Path(cur).parent.is_dir() else str(Path.home())
        try:
            p, _ = QFileDialog.getOpenFileName(self, "Choose a .torrent file", start, TORRENT_FILTER)
        except Exception as exc:
            QMessageBox.warning(self, "File picker", f"Could not open the file picker: {exc}"); return
        if p:                                            # cancel -> keep what was there
            self.source.setText(os.path.normpath(p))

    def pick_dir(self):
        try:
            d = QFileDialog.getExistingDirectory(self, "Choose the save folder", self.save.text() or str(Path.home()))
        except Exception as exc:
            QMessageBox.warning(self, "Folder picker", f"Could not open the folder picker: {exc}"); return
        if d:
            self.save.setText(os.path.normpath(d))

    # -- source -> file list -----------------------------------------------------------------------
    def _tone(self, text, tone):
        self.kind.setText(text); self.kind.setProperty("tone", tone)
        self.kind.style().unpolish(self.kind); self.kind.style().polish(self.kind)

    def _reset(self, placeholder):
        self._timer.stop()
        if self._preview and self.manager: self.manager.cancel_preview(self._preview)
        self.meta, self._preview, self._hash, self._leaves = None, "", "", []
        self.tree.clear(); self.placeholder.setText(placeholder); self.stack.setCurrentWidget(self.placeholder); self._update_summary()

    def describe(self, v):
        ok, msg = validate_torrent_source(v); v = str(v).strip()
        if not ok:
            self._reset("Paste a magnet link or choose a .torrent file to see its files." if not v else "No torrent selected.")
            self._tone(msg, "muted" if not v else "bad"); return
        if v.lower().startswith("magnet:"):
            h = magnet_info_hash(v)
            if h == self._hash: return                                   # same torrent, only the display name / trackers changed
            self._reset("Fetching metadata from peers…\nThe file list appears here once it arrives."); self._hash = h
            if self.manager is None:
                self._tone(msg + " (engine unavailable: files cannot be listed)", "ok"); self._update_summary(); return
            try:
                self._preview = self.manager.start_preview(v, self.save.text().strip() or str(Path.home()))
            except ValueError as exc:
                self._tone(str(exc) + (" Download will show the existing entry." if "already" in str(exc) else ""), "bad" if "already" not in str(exc) else "ok")
                self.placeholder.setText("Nothing to preview."); self._update_summary(); return
            self._deadline = time.time() + self.METADATA_TIMEOUT; self._timer.start(500); self._poll_preview()
        else:
            h = self.manager.info_hash_of(v) if self.manager else ""
            if h and h == self._hash and self.meta: return
            self._reset("")
            try:
                meta = self.manager.inspect(v) if self.manager else None
            except ValueError as exc:
                self._tone(str(exc), "bad"); self.placeholder.setText("This file cannot be read."); self._update_summary(); return
            self._hash = h; self._tone(msg, "ok")
            if meta: self._populate(meta)
            self._update_summary()

    def _poll_preview(self):
        meta, peers = self.manager.preview_state(self._preview) if (self.manager and self._preview) else (None, 0)
        if meta is not None:
            self._timer.stop(); self._tone("Metadata received", "ok"); self._populate(meta); self._update_summary(); return
        left = int(self._deadline - time.time())
        if left > 0:
            self._tone(f"Fetching metadata from peers… {self.METADATA_TIMEOUT - left}s · {peers} peer{'s' if peers != 1 else ''}", "muted")
        else:
            self._timer.stop()
            self._tone(f"Metadata not received after {self.METADATA_TIMEOUT}s (no reachable peers or trackers). You can still add the "
                       "torrent; all files download once the metadata arrives.", "bad")
            self.placeholder.setText("No file list available yet.")
        self._update_summary()

    def _populate(self, meta):
        self.meta = meta; self.tree.blockSignals(True); self.tree.clear()
        def folder(it):
            it.setFlags(it.flags() | Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsAutoTristate); it.setCheckState(0, Qt.CheckState.Checked)
        def leaf(it, f):
            it.setFlags(it.flags() | Qt.ItemFlag.ItemIsUserCheckable); it.setCheckState(0, Qt.CheckState.Checked)
            it.setData(0, ROLE_PRIO, DEFAULT_PRIORITY); it.setText(2, PRIORITY_NAME[DEFAULT_PRIORITY])
        self._leaves = list(build_tree(self.tree, meta.files, folder, leaf).values())
        self.tree.blockSignals(False)
        self.stack.setCurrentWidget(self.tree); self._apply_filter()

    # -- selection, priorities, filter ---------------------------------------------------------------
    def _check_all(self, on):
        self.tree.blockSignals(True)
        for it in self._leaves: it.setCheckState(0, Qt.CheckState.Checked if on else Qt.CheckState.Unchecked)
        self.tree.blockSignals(False)
        root = self.tree.invisibleRootItem()
        def sync(it):                                                    # blockSignals skipped Qt's auto-tristate propagation to folders
            for i in range(it.childCount()):
                c = it.child(i)
                if c.childCount():
                    sync(c); c.setCheckState(0, Qt.CheckState.Checked if on else Qt.CheckState.Unchecked)
        self.tree.blockSignals(True); sync(root); self.tree.blockSignals(False); self._update_summary()

    def _selected_leaves(self):
        out = []
        def walk(it):
            if it.childCount() == 0: out.append(it)
            for i in range(it.childCount()): walk(it.child(i))
        for it in self.tree.selectedItems(): walk(it)
        return [x for x in dict.fromkeys(out) if x.data(0, ROLE_INDEX) is not None]

    def _set_priority(self, p):
        for it in self._selected_leaves(): it.setData(0, ROLE_PRIO, p); it.setText(2, PRIORITY_NAME[p])

    def _apply_filter(self, *_):
        q = self.filter.text().strip().lower()
        def vis(it):
            if it.childCount() == 0: show = (not q) or q in it.toolTip(0).lower() or q in it.text(0).lower()
            else: show = any([vis(it.child(i)) for i in range(it.childCount())])         # list() so every child updates
            it.setHidden(not show); return show
        root = self.tree.invisibleRootItem()
        for i in range(root.childCount()): vis(root.child(i))
        if q: self.tree.expandAll()

    def selected(self):
        """[(TorrentFile index, size)] of checked files."""
        return [(int(it.data(0, ROLE_INDEX)), int(it.data(0, ROLE_SIZE))) for it in self._leaves if it.checkState(0) == Qt.CheckState.Checked]

    def _update_summary(self, *_):
        try: free = shutil.disk_usage(nearest_existing(self.save.text().strip() or ".")).free
        except OSError: free = None
        sel = self.selected(); sel_size = sum(s for _, s in sel); parts = []
        if self.meta:
            parts.append(f"Size: {fmt_size(sel_size)}" + (f" of {fmt_size(self.meta.total)}" if sel_size != self.meta.total else "") + f"  ·  {len(sel)} of {len(self._leaves)} files")
        if free is not None: parts.append(f"(Disk space: {fmt_size(free)})")
        self.summary.setText("  ".join(parts))
        short = bool(self.meta and free is not None and sel_size > free)
        self.summary.setProperty("tone", "bad" if short else ""); self.summary.style().unpolish(self.summary); self.summary.style().polish(self.summary)
        problem = self._dest_problem()
        self.dest_note.setText(problem or ("Not enough free space for the selected files." if short else "")); self.dest_note.setProperty("tone", "bad")
        self.dest_note.style().unpolish(self.dest_note); self.dest_note.style().polish(self.dest_note)
        ok, _ = validate_torrent_source(self.source.text())
        pending = bool(self._preview and self._timer.isActive())                                  # metadata still loading
        can = ok and not pending and not problem and (not self.meta or bool(sel))
        if self.meta and not sel: self.dest_note.setText("Select at least one file.")
        self.ok.setEnabled(can); self.later_btn.setEnabled(can)

    def _dest_problem(self):
        t = self.save.text().strip()
        if not t: return "Choose a save folder."
        p = Path(t).expanduser()
        if p.exists() and not p.is_dir(): return "The save location is a file, not a folder."
        if not os.access(nearest_existing(p), os.W_OK): return "Nexus cannot write to this location."
        return ""

    # -- result --------------------------------------------------------------------------------------
    def _later(self):
        self._paused = True; self.accept()

    def accept(self):
        ok, _ = validate_torrent_source(self.source.text())
        if not ok:
            self.describe(self.source.text()); return
        if self._dest_problem() or (self.meta and not self.selected()): self._update_summary(); return
        self._timer.stop(); super().accept()

    def done(self, r):
        self._timer.stop()
        if r != QDialog.DialogCode.Accepted and self._preview and self.manager:
            self.manager.cancel_preview(self._preview); self._preview = ""
        super().done(r)

    def priorities(self):
        """Per-file priority list over ALL torrent files (padding files keep the default); None while the file list is unknown."""
        if not self.meta: return None
        out = [DEFAULT_PRIORITY] * self.meta.num_files_raw
        for it in self._leaves:
            out[int(it.data(0, ROLE_INDEX))] = int(it.data(0, ROLE_PRIO)) if it.checkState(0) == Qt.CheckState.Checked else 0
        return out

    def data(self):
        return {"source": self.source.text().strip(), "save_path": self.save.text().strip(), "paused": self._paused,
                "priorities": self.priorities(), "preview": self._preview}

    # -- drag & drop ---------------------------------------------------------------------------------
    def dragEnterEvent(self, e):
        (e.acceptProposedAction if torrent_payloads(e.mimeData()) else e.ignore)()

    def dropEvent(self, e):
        items = torrent_payloads(e.mimeData())
        if items:
            self.source.setText(os.path.normpath(items[0]) if not items[0].lower().startswith("magnet:") else items[0])
            e.acceptProposedAction()
