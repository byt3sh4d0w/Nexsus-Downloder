from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout


class ChoiceDialog(QDialog):
    """Headline + detail + one button per action. `result_key` is the chosen action ('cancel' on close)."""

    def __init__(self, parent, title, headline, detail, actions, tone="warn"):
        super().__init__(parent)
        self.setWindowTitle(title); self.setMinimumWidth(520); self.result_key = "cancel"
        v = QVBoxLayout(self); v.setContentsMargins(22, 20, 22, 18); v.setSpacing(10)
        h = QLabel(headline); h.setObjectName("dialogTitle"); h.setWordWrap(True); v.addWidget(h)
        d = QLabel(detail); d.setWordWrap(True); d.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        d.setProperty("tone", tone if tone != "warn" else "muted"); v.addWidget(d)
        row = QHBoxLayout(); row.addStretch(); v.addLayout(row)
        for i, (key, label) in enumerate(actions):
            b = QPushButton(label); b.setAccessibleName(label)
            if i == 0: b.setObjectName("primaryButton"); b.setDefault(True)
            b.clicked.connect(lambda _=False, k=key: (setattr(self, "result_key", k), self.accept()))
            row.addWidget(b)
        c = QPushButton("Cancel"); c.clicked.connect(self.reject); row.addWidget(c)
        need = sum(row.itemAt(i).widget().sizeHint().width() + 10 for i in range(1, row.count())) + 60
        self.setMinimumWidth(max(520, need))


def ask_choice(parent, title, headline, detail, actions):
    d = ChoiceDialog(parent, title, headline, detail, actions)
    d.exec()
    return d.result_key
