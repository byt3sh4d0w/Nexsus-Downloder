"""'File exists already' dialog shared by HTTP downloads and torrents (Rename / Overwrite / Abort [/ Use existing])."""
from PySide6.QtCore import QEvent, Qt, QTimer
from PySide6.QtWidgets import QCheckBox, QDialog, QHBoxLayout, QLabel, QMessageBox, QPushButton, QVBoxLayout


class ConflictDialog(QDialog):
    """`choice` in {'rename','overwrite','use','abort'}. Rename is the safe default: it counts down and fires if nobody answers."""

    def __init__(self, parent, path, what="", *, allow_overwrite=True, overwrite_hint="", allow_use=False, rest=0, countdown=60):
        super().__init__(parent)
        self.setWindowTitle("File exists already"); self.setModal(True); self.setMinimumWidth(560)
        self.choice, self.remember, self.apply_rest, self._path = "abort", False, False, str(path)
        v = QVBoxLayout(self); v.setContentsMargins(22, 20, 22, 18); v.setSpacing(10)
        head = QLabel("Warning: file exists already"); head.setObjectName("dialogTitle"); v.addWidget(head)
        p = QLabel(str(path)); p.setWordWrap(True); p.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse); v.addWidget(p)
        if what:
            w = QLabel(what); w.setObjectName("muted"); w.setWordWrap(True); v.addWidget(w)
        self.remember_box = QCheckBox("Remember my choice for all downloads"); v.addWidget(self.remember_box)
        self.rest_box = None
        if rest > 0:
            self.rest_box = QCheckBox(f"Apply to the remaining {rest} conflicting file{'s' if rest != 1 else ''} of this torrent"); v.addWidget(self.rest_box)
        row = QHBoxLayout(); row.addStretch(); v.addLayout(row)
        self.rename_btn = self._btn(row, "RENAME", "rename", primary=True)
        if allow_use: self._btn(row, "Use existing file", "use", tip="Keep the file and let the torrent engine verify it piece by piece; only damaged parts are downloaded again.")
        ov = self._btn(row, "Overwrite", "overwrite", tip="Delete the existing file and download it again.")
        if not allow_overwrite:
            ov.setEnabled(False); ov.setToolTip(overwrite_hint or "This file is in use by another download.")
        self._btn(row, "Abort", "abort")
        self.rename_btn.setDefault(True)
        self._left = countdown if (countdown and not allow_use) else 0
        if self._left:
            self._tick_label(); self._timer = QTimer(self); self._timer.timeout.connect(self._tick); self._timer.start(1000)
            self.installEventFilter(self); self.remember_box.toggled.connect(self._stop)

    def _btn(self, row, text, key, primary=False, tip=""):
        b = QPushButton(text); b.setAccessibleName(text); b.setAutoDefault(False)
        if primary: b.setObjectName("primaryButton")
        if tip: b.setToolTip(tip)
        b.clicked.connect(lambda _=False, k=key: self._pick(k)); row.addWidget(b); return b

    # ---- countdown: any click/key by the user cancels the automatic Rename ----------------------------
    def _tick_label(self): self.rename_btn.setText(f"RENAME ({self._left})")

    def _tick(self):
        self._left -= 1
        if self._left <= 0: self._pick("rename")
        else: self._tick_label()

    def _stop(self, *_):
        if self._left: self._timer.stop(); self._left = 0; self.rename_btn.setText("RENAME")

    def eventFilter(self, obj, ev):
        if ev.type() in (QEvent.Type.MouseButtonPress, QEvent.Type.KeyPress): self._stop()
        return super().eventFilter(obj, ev)

    def _pick(self, key):
        if key == "overwrite" and QMessageBox.question(
                self, "Overwrite file", f"Delete the existing file and replace it?\n\n{self._path}\n\nThis cannot be undone.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            return
        self.choice = key; self.remember = self.remember_box.isChecked() and key != "abort"
        self.apply_rest = bool(self.rest_box and self.rest_box.isChecked())
        self.accept()

    def reject(self):                                   # Esc / window close = Abort
        self._stop(); super().reject()


def ask_conflict(parent, path, what="", **kw):
    """-> (choice, remember, apply_rest). Closing the dialog = ('abort', False, False)."""
    d = ConflictDialog(parent, path, what, **kw)
    d.exec()
    return d.choice, d.remember, d.apply_rest
