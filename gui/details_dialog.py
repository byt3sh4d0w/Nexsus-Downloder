"""Read-only download details (General / Progress / Connections). Opening it never changes a download."""
from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtGui import QFont, QGuiApplication, QPixmap
from PySide6.QtWidgets import (QCheckBox, QDialog, QGridLayout, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton,
                               QTabWidget, QVBoxLayout, QWidget)

from . import theme
from .icons import paint_file_tile


def _tile(name, size=52):
    pm = QPixmap(size, size); pm.fill(Qt.GlobalColor.transparent)
    from PySide6.QtGui import QPainter
    p = QPainter(pm); paint_file_tile(p, pm.rect().adjusted(1, 1, -1, -1), name, QFont()); p.end()
    return pm


class DownloadDetailsDialog(QDialog):
    """`provider(advanced)` -> {"name", "status", "tabs": {tab: [(label, value, long?)]}}; polled once a second (read only)."""

    def __init__(self, provider, parent=None):
        super().__init__(parent)
        self.provider = provider
        self.setWindowTitle("Download details"); self.setMinimumSize(620, 520)
        root = QVBoxLayout(self); root.setContentsMargins(20, 18, 20, 16); root.setSpacing(12)
        head = QHBoxLayout(); head.setSpacing(14)
        self.icon = QLabel(); self.icon.setFixedSize(52, 52)
        names = QVBoxLayout(); names.setSpacing(4)
        self.title = QLabel(); self.title.setObjectName("dialogTitle"); self.title.setWordWrap(True)
        self.title.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.pill = QLabel(); names.addWidget(self.title); names.addWidget(self.pill, 0, Qt.AlignmentFlag.AlignLeft)
        head.addWidget(self.icon); head.addLayout(names, 1); root.addLayout(head)
        self.tabs = QTabWidget(); root.addWidget(self.tabs, 1)
        self._pages = {}                                   # tab -> (keys, {label: widget})
        foot = QHBoxLayout()
        self.adv_box = QCheckBox("Show advanced details"); self.adv_box.toggled.connect(lambda _: self.refresh()); foot.addWidget(self.adv_box)
        foot.addStretch()
        close = QPushButton("Close"); close.setObjectName("primaryButton"); close.setDefault(True); close.clicked.connect(self.accept); foot.addWidget(close)
        root.addLayout(foot)
        self._icon_for = None
        self.refresh()
        self.timer = QTimer(self); self.timer.timeout.connect(self.refresh); self.timer.start(1000)

    # ---- rendering ----------------------------------------------------------
    def refresh(self):
        data = self.provider(self.adv_box.isChecked())
        if not data:
            self.timer.stop(); return                                  # the download was removed while open
        if self._icon_for != data["name"]:
            self._icon_for = data["name"]; self.icon.setPixmap(_tile(data["name"]))
        self.title.setText(data["name"])
        color = theme.STATUS_COLORS.get(data["status_raw"], theme.MUTED)
        self.pill.setText(f"  {data['status']}  "); self.pill.setStyleSheet(f"color:{color}; background: rgba(255,255,255,0.06); border-radius: 9px; padding: 2px 6px; font-weight:700;")
        for tab, rows in data["tabs"].items():
            keys = [(l, lg) for l, _, lg in rows]
            if tab not in self._pages or self._pages[tab][0] != keys:
                self._build_tab(tab, rows, keys)
            for label, value, _ in rows:
                w = self._pages[tab][1][label]
                text = str(value)
                if isinstance(w, QPlainTextEdit):
                    if w.toPlainText() != text:
                        w.setPlainText(text)
                elif w.text() != text:
                    w.setText(text)

    def _build_tab(self, tab, rows, keys):
        old = self._pages.get(tab)
        page = old[2] if old else QWidget()
        if old is None:
            self.tabs.addTab(page, tab)
            QVBoxLayout(page).setContentsMargins(16, 14, 16, 12)
        lay = page.layout()
        while lay.count():
            it = lay.takeAt(0); w = it.widget()
            if w: w.deleteLater()
            elif it.layout():
                sub = it.layout()
                while sub.count():
                    x = sub.takeAt(0).widget()
                    if x: x.deleteLater()
        grid = QGridLayout(); grid.setHorizontalSpacing(16); grid.setVerticalSpacing(9); grid.setColumnStretch(1, 1)
        widgets = {}
        for i, (label, _v, long) in enumerate(rows):
            k = QLabel(label); k.setObjectName("detailKey"); k.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
            if long:                                           # paths / URLs: wrap anywhere, selectable, own copy button
                v = QPlainTextEdit(); v.setReadOnly(True); v.setWordWrapMode(v.wordWrapMode().__class__.WrapAnywhere)
                v.setFixedHeight(self.fontMetrics().height() * (4 if long is True else int(long)) + 12); v.setObjectName("detailLong")
                box = QHBoxLayout(); box.setContentsMargins(0, 0, 0, 0); box.addWidget(v, 1)
                cp = QPushButton("Copy"); cp.setToolTip(f"Copy {label.lower()}"); cp.setAccessibleName(f"Copy {label}")
                cp.clicked.connect(lambda _=False, w=v: QGuiApplication.clipboard().setText(w.toPlainText()))
                box.addWidget(cp, 0, Qt.AlignmentFlag.AlignTop)
                grid.addWidget(k, i, 0); grid.addLayout(box, i, 1)
            else:
                v = QLabel(); v.setObjectName("detailVal"); v.setWordWrap(True)
                v.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
                grid.addWidget(k, i, 0, Qt.AlignmentFlag.AlignTop); grid.addWidget(v, i, 1)
            widgets[label] = v
        lay.addLayout(grid); lay.addStretch()
        self._pages[tab] = (keys, widgets, page)
