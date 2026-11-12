from pathlib import Path
from PySide6.QtCore import Qt
from . import theme
from .icons import icon
from PySide6.QtWidgets import QCheckBox, QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout


class DeleteDownloadsDialog(QDialog):
    """Choose whether selected downloads are removed only from Nexus or also from disk."""

    def __init__(self, rows, parent=None):
        super().__init__(parent)
        self.action = ""
        self.remember = False
        self.setWindowTitle(f"Delete selected downloads ({len(rows)})")
        self.setModal(True)
        self.setMinimumWidth(620)

        root = QVBoxLayout(self)
        root.setContentsMargins(22, 20, 22, 18)
        root.setSpacing(12)
        head = QLabel(f"Remove {len(rows)} download{'s' if len(rows) != 1 else ''}?"); head.setObjectName("dialogTitle")
        root.addWidget(head)
        info = QLabel("Remove from list keeps any finished file on disk and only clears Nexus' record and temporary pieces.\n"
                      "Delete files also deletes the downloaded file permanently.")
        info.setObjectName("emptyHint"); info.setWordWrap(True)
        root.addWidget(info)

        for row in rows[:8]:
            path = str(row.get("path") or "")
            label = QLabel(path or str(row.get("name") or "download"))
            label.setWordWrap(True)
            label.setObjectName("deletePath")
            root.addWidget(label)
        if len(rows) > 8:
            root.addWidget(QLabel(f"… and {len(rows) - 8} more"))

        self.remember_box = QCheckBox("Remember my choice")
        root.addWidget(self.remember_box)

        buttons = QHBoxLayout()
        buttons.addStretch()
        remove = QPushButton("Remove from list"); remove.setIcon(icon("remove", theme.TEXT))
        remove.setObjectName("removeOnlyButton")
        remove.setMinimumHeight(38)
        remove.clicked.connect(lambda: self._finish("remove_only"))
        delete = QPushButton("Delete files"); delete.setIcon(icon("trash", theme.DANGER))
        delete.setObjectName("dangerButton")
        delete.setMinimumHeight(38)
        delete.clicked.connect(lambda: self._finish("delete_files"))
        cancel = QPushButton("Cancel")
        cancel.setMinimumHeight(38)
        cancel.clicked.connect(self.reject)
        buttons.addWidget(remove)
        buttons.addWidget(delete)
        buttons.addWidget(cancel)
        root.addLayout(buttons)

    def _finish(self, action):
        self.action = action
        self.remember = self.remember_box.isChecked()
        self.accept()
