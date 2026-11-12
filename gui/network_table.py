"""Tables that fit the window (Networks, History): columns are sized from the font, the low-priority flex column ("Used" /
"Location") shrinks first, then the DROP_ORDER columns disappear. The key columns (name, address, status, Action button) are never
hidden or clipped. A horizontal scrollbar only appears when even that cannot fit (very large fonts / tiny windows)."""
from PySide6.QtCore import QEvent, Qt
from PySide6.QtWidgets import QHeaderView, QTableWidget

PAD = 22          # the theme's `QTableWidget::item` padding (10 px each side) also shrinks cell widgets: that clipped the Action button

C_NET, C_IP, C_LINK, C_INET, C_LAT, C_SPEED, C_USED, C_STATE, C_ACT = range(9)


class FitTable(QTableWidget):
    HEADERS = (); NAME = 0; FLEX = 1; DROP_ORDER = ()

    def __init__(self, parent=None):
        super().__init__(0, len(self.HEADERS), parent)
        self.setHorizontalHeaderLabels(list(self.HEADERS))
        self.setTextElideMode(Qt.TextElideMode.ElideRight)
        h = self.horizontalHeader(); h.setStretchLastSection(False); h.setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
        h.setMinimumSectionSize(40); h.setTextElideMode(Qt.TextElideMode.ElideRight)
        self._fitting = False

    def widths(self):
        """(fixed widths of the other columns, name_min, name_pref, flex_min, flex_pref) from the current font."""
        raise NotImplementedError

    def fit(self):
        if self._fitting:
            return
        self._fitting = True
        try:
            fixed, name_min, name_pref, flex_min, flex_pref = self.widths()
            avail = self.viewport().width(); hidden = set()
            for col in self.DROP_ORDER:
                if avail >= sum(w for c, w in fixed.items() if c not in hidden) + name_min + flex_min:
                    break
                hidden.add(col)
            room = avail - sum(w for c, w in fixed.items() if c not in hidden)
            flex = min(flex_pref, max(flex_min, room - name_pref))          # the flex column absorbs the squeeze first
            name = max(name_min, room - flex)
            for c in range(len(self.HEADERS)):
                self.setColumnHidden(c, c in hidden)
            for c, w in fixed.items():
                self.setColumnWidth(c, w)
            self.setColumnWidth(self.FLEX, flex); self.setColumnWidth(self.NAME, name)
        finally:
            self._fitting = False

    def resizeEvent(self, e):
        super().resizeEvent(e); self.fit()

    def showEvent(self, e):
        super().showEvent(e); self.fit()

    def changeEvent(self, e):
        super().changeEvent(e)
        if e.type() in (QEvent.Type.FontChange, QEvent.Type.StyleChange):
            self.fit()


class NetworkTable(FitTable):
    HEADERS = ("Network", "Local IP", "Link", "Internet", "Latency", "Speed", "Used", "State", "Action")
    NAME, FLEX, DROP_ORDER = C_NET, C_USED, (C_LINK, C_SPEED, C_LAT)

    def widths(self):
        fm = self.fontMetrics(); ch = max(6, fm.horizontalAdvance("0")); badge = fm.horizontalAdvance("No Internet") + 26 + 8 + PAD
        fixed = {C_IP: ch * 15 + PAD, C_LINK: ch * 10 + PAD, C_LAT: ch * 7 + PAD, C_SPEED: ch * 10 + PAD,
                 C_INET: badge, C_STATE: badge, C_ACT: max(84, fm.horizontalAdvance("Disable") + 40) + 8 + PAD}
        return fixed, ch * 11 + PAD, ch * 22, ch * 6 + PAD, ch * 30


class HistoryTable(FitTable):
    HEADERS = ("File Name", "Size", "Completed", "Duration", "Average Speed", "Peak Speed", "Networks Used", "Location", "Status")
    NAME, FLEX, DROP_ORDER = 0, 7, (5, 3, 6, 4)          # Peak, Duration, Networks Used, Average speed go first; Location shrinks before any of them

    def widths(self):
        fm = self.fontMetrics(); ch = max(6, fm.horizontalAdvance("0"))
        fixed = {1: ch * 9 + PAD, 2: ch * 17 + PAD, 3: ch * 8 + PAD, 4: ch * 11 + PAD, 5: ch * 11 + PAD, 6: ch * 16 + PAD,
                 8: fm.horizontalAdvance("Completed") + 26 + 8 + PAD}
        return fixed, ch * 11 + PAD, ch * 26, ch * 6 + PAD, ch * 30
