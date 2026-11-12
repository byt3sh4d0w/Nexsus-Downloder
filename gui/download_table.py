"""Read-only, delegate-painted download list.

All cells are painted by DownloadDelegate (no per-row child widgets), which keeps
many rows light, lets native header sorting stay consistent and makes it
impossible to edit a cell: items are never editable and edit triggers are off.
"""
from urllib.parse import urlparse

from PySide6.QtCore import QEvent, QPoint, QRect, QRectF, QSize, Qt, QTimer, QVariantAnimation, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import (QAbstractItemView, QHeaderView, QStyle, QStyledItemDelegate,
                               QTableWidget, QTableWidgetItem, QToolTip)

from . import theme
from .icons import icon, paint_file_tile

C_ID, C_NAME, C_SIZE, C_STATUS, C_PROG, C_SPEED, C_ETA, C_ADDED, C_ELAPSED, C_ACT = range(10)
HEADERS = ["", "Name", "Size", "Status", "Progress", "Speed", "ETA", "Added", "Elapsed", ""]

R_RID = Qt.ItemDataRole.UserRole          # on column 0
R_STATUS = R_RID + 1
R_SORT = R_RID + 2
R_PCT = R_RID + 3                         # target percent (float)
R_SHOWN = R_RID + 4                       # animated percent actually painted
R_SUB = R_RID + 5
R_SUBERR = R_RID + 6
R_SEARCH = R_RID + 7                      # text the search box matches (name + link)

ACTIVE_STATES = {"QUEUED", "DOWNLOADING", "PAUSED", "RETRYING"}


def row_actions(status):
    """-> (primary_action, primary_icon, primary_tip, stop_enabled)."""
    s = str(status or "").upper()
    if s == "DOWNLOADING":
        return "pause", "pause", "Pause", True
    if s == "PAUSED":
        return "start", "play", "Resume", True
    if s == "QUEUED":
        return "start", "play", "Start now", True
    if s in ("ERROR", "RETRYING"):
        return "retry", "retry", "Retry", s == "RETRYING"
    if s == "STOPPED":
        return "start", "play", "Start again (resumes partial data)", False
    if s == "COMPLETED":
        return "open", "open", "Open file", False
    return "start", "play", "Start", False


class SortItem(QTableWidgetItem):
    """Sorts by a numeric/real key (R_SORT) instead of the display text."""
    def __lt__(self, other):
        a, b = self.data(R_SORT), other.data(R_SORT)
        if a is not None and b is not None:
            try:
                return a < b
            except TypeError:
                pass
        return self.text().lower() < other.text().lower()


class DownloadDelegate(QStyledItemDelegate):
    def __init__(self, table):
        super().__init__(table)
        self.t = table

    # geometry ---------------------------------------------------------
    def sizeHint(self, option, index):
        h = int(option.fontMetrics.height() * 2.9) + 4
        return QSize(60, h)

    @staticmethod
    def button_size(rect):
        return max(26, min(34, rect.height() - 14))

    def button_rects(self, cell):
        s = self.button_size(cell)         # one control per row: the More actions menu
        return [("more", QRect(cell.center().x() - s // 2, cell.center().y() - s // 2, s, s))]

    # painting ---------------------------------------------------------
    def paint(self, p, opt, idx):
        p.save()
        r = opt.rect
        col, row = idx.column(), idx.row()
        sel = bool(opt.state & QStyle.StateFlag.State_Selected)
        hov = (row == self.t.hover_row)
        if sel:
            p.fillRect(r, theme.accent_alpha(30))
        elif hov:
            p.fillRect(r, theme.tint(9))
        p.setPen(QColor(theme.ROW_LINE)); p.drawLine(r.left(), r.bottom(), r.right(), r.bottom())
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        status = self.t.status_at(row)
        pad = 10
        if col == C_NAME:
            self._paint_name(p, r, idx, status, pad)
        elif col == C_STATUS:
            self._paint_pill(p, r, status, pad)
        elif col == C_PROG:
            self._paint_progress(p, r, idx, status, pad)
        elif col == C_ACT:
            self._paint_actions(p, r, row, status)
        else:
            self._paint_text(p, r, idx.data(Qt.ItemDataRole.DisplayRole) or "", pad, opt.font,
                             theme.TEXT if col in (C_SIZE, C_SPEED) else theme.MUTED)
        p.restore()

    @staticmethod
    def _paint_text(p, r, text, pad, font, color):
        p.setFont(font); p.setPen(QColor(color))
        fm = QFontMetrics(font)
        p.drawText(r.adjusted(pad, 0, -pad, 0), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                   fm.elidedText(str(text), Qt.TextElideMode.ElideRight, r.width() - 2 * pad))

    def _paint_name(self, p, r, idx, status, pad):
        name = idx.data(Qt.ItemDataRole.DisplayRole) or ""
        tile = max(30, r.height() - 18)
        tr = QRect(r.left() + pad, r.center().y() - tile // 2, tile, tile)
        font = idx.data(Qt.ItemDataRole.FontRole) or p.font()
        paint_file_tile(p, tr, name, QFont(p.font()))
        x = tr.right() + 11
        w = r.right() - x - pad
        f1 = QFont(p.font()); f1.setWeight(QFont.Weight.DemiBold)
        f2 = QFont(p.font()); f2.setPixelSize(max(10, int(QFontMetrics(p.font()).height() * 0.82)))
        fm1, fm2 = QFontMetrics(f1), QFontMetrics(f2)
        sub = idx.data(R_SUB) or ""
        err = bool(idx.data(R_SUBERR))
        total_h = fm1.height() + (fm2.height() + 1 if sub else 0)
        y = r.center().y() - total_h // 2
        p.setFont(f1); p.setPen(QColor(theme.TEXT))
        p.drawText(QRect(x, y, w, fm1.height()), Qt.AlignmentFlag.AlignVCenter, fm1.elidedText(name, Qt.TextElideMode.ElideMiddle, w))
        if sub:
            p.setFont(f2); p.setPen(QColor(theme.DANGER if err else theme.FAINT))
            p.drawText(QRect(x, y + fm1.height() + 1, w, fm2.height()), Qt.AlignmentFlag.AlignVCenter,
                       fm2.elidedText(sub, Qt.TextElideMode.ElideRight, w))

    def _paint_pill(self, p, r, status, pad):
        color = QColor(theme.STATUS_COLORS.get(status, theme.MUTED))
        text = theme.status_label(status)
        f = QFont(p.font()); f.setWeight(QFont.Weight.DemiBold); f.setPixelSize(max(10, int(QFontMetrics(p.font()).height() * 0.86)))
        fm = QFontMetrics(f)
        h = fm.height() + 8; w = fm.horizontalAdvance(text) + 32
        rect = QRectF(r.left() + pad, r.center().y() - h / 2, min(w, r.width() - 2 * pad), h)
        bg = QColor(color); bg.setAlpha(34)
        p.setPen(Qt.PenStyle.NoPen); p.setBrush(bg); p.drawRoundedRect(rect, h / 2, h / 2)
        p.setBrush(color); p.drawEllipse(QPoint(int(rect.left() + 12), int(rect.center().y())), 3, 3)
        p.setFont(f); p.setPen(color)
        p.drawText(QRectF(rect.left() + 22, rect.top(), rect.width() - 26, h), Qt.AlignmentFlag.AlignVCenter, fm.elidedText(text, Qt.TextElideMode.ElideRight, int(rect.width() - 26)))

    def _paint_progress(self, p, r, idx, status, pad):
        shown = idx.data(R_SHOWN)
        shown = 0.0 if shown is None else float(shown)
        color = QColor(theme.STATUS_COLORS.get(status, theme.MUTED))
        f = QFont(p.font()); f.setPixelSize(max(10, int(QFontMetrics(p.font()).height() * 0.84)))
        fm = QFontMetrics(f)
        bar_h = 6
        total = fm.height() + 5 + bar_h
        y = r.center().y() - total // 2
        p.setFont(f); p.setPen(QColor(theme.MUTED if status != "COMPLETED" else theme.SUCCESS))
        pct = int(round(float(idx.data(R_PCT) or 0)))
        p.drawText(QRect(r.left() + pad, y, r.width() - 2 * pad, fm.height()), Qt.AlignmentFlag.AlignVCenter, f"{pct}%")
        track = QRectF(r.left() + pad, y + fm.height() + 5, r.width() - 2 * pad, bar_h)
        p.setPen(Qt.PenStyle.NoPen); p.setBrush(QColor(theme.TRACK)); p.drawRoundedRect(track, 3, 3)
        w = track.width() * max(0.0, min(100.0, shown)) / 100.0
        if w > 0.5:
            p.setBrush(color); p.drawRoundedRect(QRectF(track.left(), track.top(), max(w, 6), bar_h), 3, 3)

    def _paint_actions(self, p, r, row, status):
        for name, rect in self.button_rects(r):
            hot = (self.t.hover_btn == (row, name))
            down = hot and self.t.pressed_btn == (row, name)
            if hot:
                p.setPen(Qt.PenStyle.NoPen); p.setBrush(theme.accent_alpha(60 if down else 34)); p.drawRoundedRect(QRectF(rect), 8, 8)
            icon("more", theme.ACCENT if hot else theme.MUTED).paint(p, rect.adjusted(6, 6, -6, -6))


class DownloadTable(QTableWidget):
    actionRequested = Signal(str, int)      # action name, download id
    contextRequested = Signal(int, QPoint)  # download id, global pos

    def __init__(self, parent=None):
        super().__init__(0, len(HEADERS), parent)
        self.hover_row = -1
        self.hover_btn = None
        self.pressed_btn = None
        self._items = {}                     # rid -> {col: item}
        self._anim_items = {}                # rid -> progress item still easing
        self.setHorizontalHeaderLabels(HEADERS)
        self.setItemDelegate(DownloadDelegate(self))
        # --- strictly read-only -------------------------------------------------
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._context)
        self.setMouseTracking(True)
        self.setShowGrid(False)
        self.setWordWrap(False)
        self.setAlternatingRowColors(False)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.verticalHeader().hide()
        self.verticalHeader().setDefaultSectionSize(self.row_height())
        self.setColumnHidden(C_ID, True)            # internal database id: never shown
        self.setSortingEnabled(False)               # sorting is applied explicitly (see sort_by)
        h = self.horizontalHeader()
        h.setSectionsClickable(True); h.setSortIndicatorShown(True)
        h.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        h.setSectionResizeMode(C_NAME, QHeaderView.ResizeMode.Interactive)   # the only user-resizable column
        self._name_user = 0; self._fitting = False
        h.sectionResized.connect(self._name_dragged)
        h.setMinimumSectionSize(24)
        h.setStretchLastSection(False)
        h.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        h.sectionClicked.connect(self._header_clicked)
        for c in (C_SIZE, C_STATUS, C_PROG, C_SPEED, C_ETA, C_ADDED, C_ELAPSED, C_ACT):
            h.setSectionResizeMode(c, QHeaderView.ResizeMode.Fixed)      # widths are computed in fit_columns()
        self.sort_col, self.sort_order = None, Qt.SortOrder.DescendingOrder
        h.setSortIndicator(-1, Qt.SortOrder.AscendingOrder)
        self._anim = QTimer(self); self._anim.setInterval(16); self._anim.timeout.connect(self._step)

    # Every column except Name is sized from what it must display (measured with the real font, so it follows the
    # system font and Windows display scaling). Name takes the remaining space and is the only column the user drags.
    SAMPLES = {C_SIZE: "888.8 MB / 888.8 MB", C_SPEED: "888.8 MB/s", C_ETA: "88d 88h", C_ADDED: "2026-10-24 09:17:47", C_ELAPSED: "88m 88s"}
    DROP_ORDER = (C_ADDED, C_ELAPSED, C_ETA, C_SIZE, C_SPEED)    # least important columns leave first
    NAME_MIN_EM = 18

    def preferred_widths(self):
        fm = self.fontMetrics(); em = max(6, fm.horizontalAdvance("0"))
        hf = QFont(self.horizontalHeader().font()); hf.setBold(True); head = QFontMetrics(hf)
        pill = QFont(self.font()); pill.setWeight(QFont.Weight.DemiBold); pill.setPixelSize(max(10, int(fm.height() * 0.86)))
        pad = 20
        w = {c: fm.horizontalAdvance(t) + pad + 6 for c, t in self.SAMPLES.items()}
        w[C_STATUS] = max(QFontMetrics(pill).horizontalAdvance(theme.status_label("DOWNLOADING")) + 32, 0) + pad
        w[C_PROG] = 9 * em + pad
        w[C_ACT] = max(48, 5 * em + 10)
        for c in w:                                                  # header text + sort arrow must fit too
            if c != C_ACT:
                w[c] = max(w[c], head.horizontalAdvance(HEADERS[c]) + 38)
        return w

    def fit_columns(self):
        """Hide least important columns until Name keeps a readable minimum; share the remaining width."""
        if getattr(self, "_fitting", False):
            return
        self._fitting = True
        try:
            em = max(6, self.fontMetrics().horizontalAdvance("0"))
            avail = self.viewport().width(); pref = self.preferred_widths(); name_min = self.NAME_MIN_EM * em
            hidden = set()
            for c in self.DROP_ORDER:
                if avail - sum(w for k, w in pref.items() if k not in hidden) >= name_min:
                    break
                hidden.add(c)
            shown = {c: w for c, w in pref.items() if c not in hidden}
            room = avail - sum(shown.values())                      # what Name could take
            name_w = max(name_min, min(self._name_user or room, room))
            spare = max(0, room - name_w)                           # Name was narrowed by the user: others grow a little
            grow = 1 + min(spare, 0.5 * sum(shown.values())) / max(1, sum(shown.values()))
            for c, w in pref.items():
                self.setColumnHidden(c, c in hidden)
                self.setColumnWidth(c, int(w * grow))
            self.setColumnWidth(C_NAME, int(name_w))
        finally:
            self._fitting = False

    def _name_dragged(self, col, _old, new):
        if col == C_NAME and not getattr(self, "_fitting", False):
            self._name_user = int(new); self.fit_columns()

    def changeEvent(self, e):
        super().changeEvent(e)
        if e.type() in (QEvent.Type.FontChange, QEvent.Type.StyleChange):
            self.fit_columns()

    def resizeEvent(self, e):
        super().resizeEvent(e); self.fit_columns()

    def showEvent(self, e):
        super().showEvent(e); self.fit_columns()
        wh = self.window().windowHandle() if self.window() else None
        if wh is not None and not getattr(self, "_screen_hooked", False):      # moved to a screen with other scaling
            self._screen_hooked = True; wh.screenChanged.connect(lambda *_: QTimer.singleShot(0, self.fit_columns))

    # model helpers -----------------------------------------------------
    def rid_at(self, row):
        it = self.item(row, C_ID)
        v = it.data(R_RID) if it else None
        return int(v) if v is not None else None

    def status_at(self, row):
        it = self.item(row, C_STATUS)
        return (it.data(R_STATUS) if it else "") or ""

    def _live(self, rid, col):
        """Item for (rid, col) or None; forgets rows whose items were destroyed (e.g. setRowCount(0))."""
        it = self._items.get(int(rid), {}).get(col)
        if it is None:
            return None
        try:
            if self.row(it) < 0:
                return None
        except RuntimeError:
            self._items.pop(int(rid), None); self._anim_items.pop(int(rid), None)
            return None
        return it

    def row_of(self, rid):
        it = self._live(rid, C_ID)
        return self.row(it) if it is not None else None

    def status_of(self, rid):
        it = self._live(rid, C_STATUS)
        return it.data(R_STATUS) if it is not None else None

    def selected_rids(self):
        rows = sorted({i.row() for i in self.selectionModel().selectedRows() if not self.isRowHidden(i.row())})
        return [rid for r in rows if (rid := self.rid_at(r)) is not None]

    def add_download(self, rid, animate=True):
        row = self.rowCount()
        self.insertRow(row)
        items = {}
        for c in range(len(HEADERS)):
            it = SortItem("")
            it.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)   # never ItemIsEditable
            self.setItem(row, c, it); items[c] = it
        items[C_ID].setData(R_RID, int(rid)); items[C_ID].setData(R_SORT, int(rid))
        self._items[int(rid)] = items
        if animate and self.isVisible():
            self._grow(items[C_ID])
        return row

    def row_height(self):
        return int(self.fontMetrics().height() * 2.9) + 4        # scales with font / DPI

    def _grow(self, ref_item):
        full = self.row_height()
        anim = QVariantAnimation(self)
        anim.setDuration(180); anim.setStartValue(4); anim.setEndValue(full)
        def step(v):
            try:
                r = self.row(ref_item)
                if r >= 0: self.setRowHeight(r, int(v))
            except RuntimeError:
                pass
        def done():
            try:
                if self.row(ref_item) >= 0: self.setRowHeight(self.row(ref_item), full)
            except RuntimeError:                  # row was removed while it was still growing
                pass
        anim.valueChanged.connect(step)
        anim.finished.connect(done)
        self._grow_anim = anim; anim.start()

    def forget(self, rid):
        items = self._items.pop(int(rid), None)
        if items:
            self._anim_items.pop(int(rid), None)

    def set_fields(self, rid, **f):
        if self._live(rid, C_ID) is None:
            return
        it = self._items.get(int(rid))
        if "name" in f:
            it[C_NAME].setText(f["name"]); it[C_NAME].setData(R_SORT, f["name"].lower())
        if "search" in f:
            it[C_NAME].setData(R_SEARCH, f["search"])
        if "sub" in f:
            it[C_NAME].setData(R_SUB, f["sub"]); it[C_NAME].setData(R_SUBERR, bool(f.get("sub_err", False)))
        if "size_text" in f:
            it[C_SIZE].setText(f["size_text"]); it[C_SIZE].setData(R_SORT, f.get("size_sort", 0))
        if "status" in f:
            s = str(f["status"]).upper()
            it[C_STATUS].setData(R_STATUS, s); it[C_STATUS].setText(theme.status_label(s))
            it[C_STATUS].setData(R_SORT, theme.status_label(s))
            self._repaint(it[C_STATUS])
            self._repaint(it[C_ACT])
        if "pct" in f:
            self.set_progress(rid, f["pct"], snap=f.get("snap", False))
        if "speed_text" in f:
            it[C_SPEED].setText(f["speed_text"]); it[C_SPEED].setData(R_SORT, f.get("speed_sort", 0.0))
        if "eta_text" in f:
            it[C_ETA].setText(f["eta_text"]); it[C_ETA].setData(R_SORT, f.get("eta_sort", 1e18))
        if "added_text" in f:
            it[C_ADDED].setText(f["added_text"]); it[C_ADDED].setData(R_SORT, f.get("added_sort", ""))
        if "elapsed_text" in f:
            it[C_ELAPSED].setText(f["elapsed_text"]); it[C_ELAPSED].setData(R_SORT, f.get("elapsed_sort", 0.0))

    def _repaint(self, item):
        self.viewport().update(self.visualItemRect(item))

    # animated progress ---------------------------------------------------
    def set_progress(self, rid, pct, snap=False):
        it = self._live(rid, C_PROG)
        if it is None:
            return
        pct = max(0.0, min(100.0, float(pct)))
        it.setData(R_PCT, pct); it.setData(R_SORT, pct)
        if snap or it.data(R_SHOWN) is None or not self.isVisible():
            it.setData(R_SHOWN, pct)
        elif abs((it.data(R_SHOWN) or 0.0) - pct) > 0.05:
            self._anim_items[int(rid)] = it
            if not self._anim.isActive():
                self._anim.start()
        self._repaint(it)

    def _step(self):
        done = []
        for rid, it in list(self._anim_items.items()):
            cur, tgt = float(it.data(R_SHOWN) or 0.0), float(it.data(R_PCT) or 0.0)
            nxt = cur + (tgt - cur) * 0.3           # ~180 ms to settle at 60 Hz
            if abs(tgt - nxt) < 0.15:
                nxt = tgt; done.append(rid)
            it.setData(R_SHOWN, nxt)
            self._repaint(it)
        for rid in done:
            self._anim_items.pop(rid, None)
        if not self._anim_items:
            self._anim.stop()

    # sorting ---------------------------------------------------------
    def _header_clicked(self, col):
        if col in (C_ID, C_ACT):
            return
        if self.sort_col == col:
            self.sort_order = Qt.SortOrder.DescendingOrder if self.sort_order == Qt.SortOrder.AscendingOrder else Qt.SortOrder.AscendingOrder
        else:
            self.sort_col, self.sort_order = col, Qt.SortOrder.AscendingOrder
        self.horizontalHeader().setSortIndicator(self.sort_col, self.sort_order)
        self.resort()

    def resort(self):
        if self.sort_col is not None:
            self.sortItems(self.sort_col, self.sort_order)
        self.horizontalHeader().setSortIndicator(self.sort_col if self.sort_col is not None else -1, self.sort_order)

    # mouse -----------------------------------------------------------
    def _hit(self, pos):
        idx = self.indexAt(pos)
        if not idx.isValid():
            return -1, None
        row = idx.row()
        if idx.column() == C_ACT:
            cell = self.visualRect(idx)
            for name, rect in self.itemDelegate().button_rects(cell):
                if rect.contains(pos):
                    return row, name
        return row, None

    def mouseMoveEvent(self, e):
        row, btn = self._hit(e.position().toPoint())
        new = (row, btn) if btn else None
        if row != self.hover_row or new != self.hover_btn:
            self.hover_row, self.hover_btn = row, new
            self.viewport().update()
        self.viewport().setCursor(Qt.CursorShape.PointingHandCursor if btn else Qt.CursorShape.ArrowCursor)
        super().mouseMoveEvent(e)

    def leaveEvent(self, e):
        self.hover_row, self.hover_btn, self.pressed_btn = -1, None, None
        self.viewport().update()
        super().leaveEvent(e)

    def mousePressEvent(self, e):
        row, btn = self._hit(e.position().toPoint())
        if btn and e.button() == Qt.MouseButton.LeftButton:
            self.pressed_btn = (row, btn)
            self.viewport().update()
            return                                   # don't change selection when clicking a control
        super().mousePressEvent(e)

    def mouseReleaseEvent(self, e):
        pressed, self.pressed_btn = self.pressed_btn, None
        row, btn = self._hit(e.position().toPoint())
        if pressed and btn and pressed == (row, btn) and e.button() == Qt.MouseButton.LeftButton:
            rid = self.rid_at(row)
            status = self.status_at(row)
            if rid is not None:
                self.actionRequested.emit("more", rid)
            self.viewport().update()
            return
        super().mouseReleaseEvent(e)

    def mouseDoubleClickEvent(self, e):
        # Double-click never edits: it opens the read-only Details window.
        row, btn = self._hit(e.position().toPoint())
        if row >= 0 and not btn:
            rid = self.rid_at(row)
            if rid is not None:
                self.actionRequested.emit("details", rid)
        e.accept()

    def edit(self, *a, **k):                         # belt and braces: no code path may start an editor
        return False

    def keyPressEvent(self, e):
        # F2 / typing / Enter must never start editing; Enter = primary action, Delete handled by window.
        if e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            for rid in self.selected_rids():
                self.actionRequested.emit(row_actions(self.status_of(rid))[0], rid)
            return
        if e.key() in (Qt.Key.Key_F2,):
            return
        super().keyPressEvent(e)

    def _context(self, pos):
        row = self.indexAt(pos).row()
        if row >= 0:
            if not self.selectionModel().isRowSelected(row, self.rootIndex()):
                self.selectRow(row)
            self.contextRequested.emit(self.rid_at(row), self.viewport().mapToGlobal(pos))

    def viewportEvent(self, e):
        if e.type() == QEvent.Type.ToolTip:
            pos = e.pos()
            row, btn = self._hit(pos)
            tip = ""
            if row >= 0:
                status = self.status_at(row)
                if btn == "more": tip = "More actions for this download"
                else:
                    idx = self.indexAt(pos)
                    if idx.column() == C_NAME:
                        tip = idx.data(Qt.ItemDataRole.DisplayRole) or ""
                        sub = idx.data(R_SUB)
                        if sub: tip += "\n" + sub
                    elif idx.column() == C_STATUS and self.item(row, C_NAME) and self.item(row, C_NAME).data(R_SUBERR):
                        tip = self.item(row, C_NAME).data(R_SUB) or ""
            if tip:
                QToolTip.showText(e.globalPos(), tip, self)
            else:
                QToolTip.hideText(); e.ignore()
            return True
        return super().viewportEvent(e)
