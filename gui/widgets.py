"""Reusable Nexus UI components (all painted from theme tokens)."""
import os

from PySide6.QtCore import (QEasingCurve, QPoint, QPointF, QPropertyAnimation, QRect, QRectF, QSize, Qt,
                            QVariantAnimation, Signal)
from PySide6.QtGui import QBrush, QColor, QFontMetrics, QLinearGradient, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (QAbstractButton, QDialog, QFrame, QGraphicsOpacityEffect, QHBoxLayout, QLabel,
                               QPushButton, QScrollArea, QStackedWidget, QVBoxLayout, QWidget)

from . import theme
from .theme import C, RADIUS, STATUS_COLORS, dur, font, icon_pixmap, qcolor

STATUS_TEXT = {"DOWNLOADING": "Downloading", "QUEUED": "Queued", "PAUSED": "Paused", "COMPLETED": "Completed",
               "ERROR": "Failed", "RETRYING": "Retrying", "STOPPED": "Stopped", "SEEDING": "Seeding",
               "METADATA": "Metadata", "CHECKING": "Checking", "ONLINE": "Online", "ACTIVE": "Active",
               "DISCONNECTED": "Disconnected", "DISABLED": "Disabled", "NO INTERNET": "No internet"}


def _mix(a, b, t):
    a, b = QColor(a), QColor(b)
    return QColor(int(a.red() + (b.red() - a.red()) * t), int(a.green() + (b.green() - a.green()) * t),
                  int(a.blue() + (b.blue() - a.blue()) * t), int(a.alpha() + (b.alpha() - a.alpha()) * t))


class _Hoverable:
    """Animated 0..1 hover value shared by the custom-painted widgets."""

    def _init_hover(self):
        self._hv = 0.0
        self._hanim = QVariantAnimation(self)
        self._hanim.valueChanged.connect(self._set_hv)

    def _set_hv(self, v):
        self._hv = float(v)
        self.update()

    def _hover_to(self, target):
        self._hanim.stop()
        d = dur("fast")
        if d == 0:
            self._set_hv(target)
            return
        self._hanim.setDuration(d)
        self._hanim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._hanim.setStartValue(self._hv)
        self._hanim.setEndValue(float(target))
        self._hanim.start()


def draw_pill(p, rect, text, color, dot=True):
    bg = qcolor(color, 34)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(bg)
    p.drawRoundedRect(rect, rect.height() / 2, rect.height() / 2)
    x = rect.left() + 10
    if dot:
        p.setBrush(QColor(color))
        p.drawEllipse(QPointF(x + 3, rect.center().y()), 3, 3)
        x += 12
    p.setPen(QColor(color))
    p.setFont(font(11, 600))
    p.drawText(QRectF(x, rect.top(), rect.right() - x - 8, rect.height()),
               Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, text)


def pill_width(text, dot=True):
    return QFontMetrics(font(11, 600)).horizontalAdvance(text) + 20 + (12 if dot else 0)


# ------------------------------------------------------------------ buttons
class Button(QPushButton, _Hoverable):
    """primary | secondary | danger | ghost, with animated hover/press."""

    def __init__(self, text="", variant="secondary", icon_name=None, parent=None):
        super().__init__(text, parent)
        self._init_hover()
        self.variant = variant
        self.icon_name = icon_name
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMinimumHeight(38)
        self.setFlat(True)

    def set_icon_name(self, n):
        self.icon_name = n
        self.update()

    def sizeHint(self):
        w = QFontMetrics(font(13, 600)).horizontalAdvance(self.text()) + 34
        if self.icon_name:
            w += 26 if self.text() else 6
        return QSize(max(w, 40), 38)

    def enterEvent(self, e):
        self._hover_to(1.0); super().enterEvent(e)

    def leaveEvent(self, e):
        self._hover_to(0.0); super().leaveEvent(e)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        en = self.isEnabled()
        down = self.isDown()
        h = self._hv if en else 0
        v = self.variant
        if v == "primary":
            g = QLinearGradient(r.topLeft(), r.bottomRight())
            g.setColorAt(0, _mix(C["accent"], C["accent2"], 0.15 + 0.35 * h))
            g.setColorAt(1, _mix("#2f7fe6", C["accent"], 0.2 + 0.5 * h))
            if h > 0:  # soft hover glow
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(qcolor(C["accent"], int(40 * h)))
                p.drawRoundedRect(r.adjusted(-1, -1, 1, 1), 11, 11)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(g) if en else QColor(C["surface3"]))
            fg = QColor("#04101f") if en else QColor(C["text3"])
        elif v == "danger":
            p.setPen(QPen(qcolor(C["error"], int(90 + 80 * h)), 1))
            p.setBrush(qcolor(C["error"], int(18 + 40 * h)))
            fg = QColor(C["error"]) if en else QColor(C["text3"])
        elif v == "ghost":
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(qcolor(C["text"], int(14 * h)))
            fg = QColor(C["text2"] if h < 0.5 else C["text"])
        else:
            p.setPen(QPen(_mix(C["border2"], "#3a4868", h), 1))
            p.setBrush(_mix(C["surface2"], C["surface3"], h))
            fg = QColor(C["text"]) if en else QColor(C["text3"])
        if down and en:
            p.translate(0, 0.5)
            p.setOpacity(0.88)
        p.drawRoundedRect(r, 10, 10)
        p.setOpacity(1)
        if self.hasFocus() and self.focusPolicy() != Qt.FocusPolicy.NoFocus:
            p.setPen(QPen(qcolor(C["accent2"], 200), 1.5))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(r.adjusted(-1, -1, 1, 1), 11, 11)
        txt = self.text()
        fm = QFontMetrics(font(13, 600))
        iw = 18 if self.icon_name else 0
        gap = 8 if (iw and txt) else 0
        total = iw + gap + fm.horizontalAdvance(txt)
        x = r.center().x() - total / 2
        if self.icon_name:
            pm = icon_pixmap(self.icon_name, fg.name(), 18, self.devicePixelRatioF())
            p.drawPixmap(QPointF(x, r.center().y() - 9), pm)
            x += iw + gap
        p.setPen(fg)
        p.setFont(font(13, 600))
        p.drawText(QRectF(x, r.top(), r.right() - x, r.height()), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, txt)
        p.end()


class IconButton(QAbstractButton, _Hoverable):
    def __init__(self, icon_name, tooltip="", danger=False, size=34, parent=None):
        super().__init__(parent)
        self._init_hover()
        self.icon_name = icon_name
        self.danger = danger
        self.setToolTip(tooltip)
        self.setAccessibleName(tooltip)
        self.setFixedSize(size, size)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def set_icon_name(self, name, tooltip=None):
        self.icon_name = name
        if tooltip is not None:
            self.setToolTip(tooltip); self.setAccessibleName(tooltip)
        self.update()

    def enterEvent(self, e):
        self._hover_to(1.0); super().enterEvent(e)

    def leaveEvent(self, e):
        self._hover_to(0.0); super().leaveEvent(e)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        accent = C["error"] if self.danger else C["accent"]
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(qcolor(accent, int((46 if self.isDown() else 28) * self._hv)))
        p.drawRoundedRect(r, 9, 9)
        if self.hasFocus():
            p.setPen(QPen(qcolor(C["accent2"], 200), 1.5)); p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(r, 9, 9)
        col = _mix(C["text2"], accent if self.danger else C["accent2"], self._hv)
        pm = icon_pixmap(self.icon_name, col.name(), 18, self.devicePixelRatioF())
        p.drawPixmap(QPointF(r.center().x() - 9, r.center().y() - 9), pm)
        p.end()


# ------------------------------------------------------------------ sidebar
class NavButton(QAbstractButton, _Hoverable):
    def __init__(self, key, label, icon_name, parent=None):
        super().__init__(parent)
        self._init_hover()
        self.key, self.label, self.icon_name = key, label, icon_name
        self.badge = 0
        self.setCheckable(True)
        self.setFixedHeight(40)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName(label)

    def set_badge(self, n):
        if n != self.badge:
            self.badge = n; self.update()

    def enterEvent(self, e):
        self._hover_to(1.0); super().enterEvent(e)

    def leaveEvent(self, e):
        self._hover_to(0.0); super().leaveEvent(e)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        sel = self.isChecked()
        r = QRectF(self.rect()).adjusted(10, 2, -10, -2)
        p.setPen(Qt.PenStyle.NoPen)
        if sel:
            g = QLinearGradient(r.topLeft(), r.topRight())
            g.setColorAt(0, qcolor(C["accent"], 44)); g.setColorAt(1, qcolor(C["violet"], 18))
            p.setBrush(QBrush(g))
        else:
            p.setBrush(qcolor(C["text"], int(10 * self._hv)))
        p.drawRoundedRect(r, 10, 10)
        if self.hasFocus():
            p.setPen(QPen(qcolor(C["accent2"], 170), 1.3)); p.setBrush(Qt.BrushStyle.NoBrush); p.drawRoundedRect(r, 10, 10)
        col = C["accent2"] if sel else _mix(C["text2"], C["text"], self._hv).name()
        p.drawPixmap(QPointF(r.left() + 14, r.center().y() - 9), icon_pixmap(self.icon_name, col, 18, self.devicePixelRatioF()))
        p.setPen(QColor(C["text"] if sel else col))
        p.setFont(font(13, 600 if sel else 500))
        p.drawText(QRectF(r.left() + 42, r.top(), r.width() - 70, r.height()), Qt.AlignmentFlag.AlignVCenter, self.label)
        if self.badge:
            t = str(self.badge) if self.badge < 100 else "99+"
            w = max(20, QFontMetrics(font(10, 700)).horizontalAdvance(t) + 12)
            br = QRectF(r.right() - w - 8, r.center().y() - 9, w, 18)
            p.setPen(Qt.PenStyle.NoPen); p.setBrush(qcolor(C["accent"], 60 if not sel else 90))
            p.drawRoundedRect(br, 9, 9)
            p.setPen(QColor(C["accent2"])); p.setFont(font(10, 700)); p.drawText(br, Qt.AlignmentFlag.AlignCenter, t)
        p.end()


class Sidebar(QFrame):
    navigated = Signal(str)

    def __init__(self, logo_pixmap, items, parent=None):
        super().__init__(parent)
        self.setObjectName("sidebar")
        self.setFixedWidth(214)
        self.buttons = {}
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 18, 0, 14)
        lay.setSpacing(2)
        brand = QHBoxLayout(); brand.setContentsMargins(18, 0, 14, 0); brand.setSpacing(10)
        logo = QLabel(); logo.setFixedSize(38, 38)
        if logo_pixmap and not logo_pixmap.isNull():
            logo.setPixmap(logo_pixmap.scaled(38, 38, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        brand.addWidget(logo)
        tb = QVBoxLayout(); tb.setSpacing(0)
        t = QLabel("Nexus"); t.setObjectName("appTitle"); s = QLabel("Downloader"); s.setObjectName("appSub")
        tb.addWidget(t); tb.addWidget(s); brand.addLayout(tb); brand.addStretch()
        lay.addLayout(brand); lay.addSpacing(18)
        for entry in items:
            if entry == "-":
                lay.addSpacing(10)
                sep = QFrame(); sep.setFixedHeight(1); sep.setStyleSheet(f"background:{C['border']};margin:0 18px;"); lay.addWidget(sep)
                lay.addSpacing(10)
                continue
            key, label, ic = entry
            b = NavButton(key, label, ic, self)
            b.clicked.connect(lambda _=False, k=key: self.select(k))
            self.buttons[key] = b
            lay.addWidget(b)
        lay.addStretch()
        # selection indicator (animated)
        self.indicator = QWidget(self)
        self.indicator.setFixedSize(3, 22)
        self.indicator.setStyleSheet(f"background:{C['accent2']};border-radius:1px;")
        self._ind_anim = QPropertyAnimation(self.indicator, b"pos", self)
        self.current = None

    def select(self, key, emit=True):
        if key not in self.buttons:
            return
        for k, b in self.buttons.items():
            b.setChecked(k == key)
        self.current = key
        self._place_indicator(animated=self.isVisible())
        if emit:
            self.navigated.emit(key)

    def _target(self):
        b = self.buttons[self.current]
        return QPoint(8, b.y() + (b.height() - 22) // 2)

    def _place_indicator(self, animated=True):
        if self.current is None:
            return
        tgt = self._target()
        self._ind_anim.stop()
        if animated and dur("base"):
            self._ind_anim.setDuration(dur("base")); self._ind_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
            self._ind_anim.setStartValue(self.indicator.pos()); self._ind_anim.setEndValue(tgt); self._ind_anim.start()
        else:
            self.indicator.move(tgt)
        self.indicator.show(); self.indicator.raise_()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._place_indicator(animated=False)

    def showEvent(self, e):
        super().showEvent(e)
        self._place_indicator(animated=False)


# ------------------------------------------------------------- page / dialog
class FadeStack(QStackedWidget):
    def setCurrentIndex(self, i):
        if i == self.currentIndex():
            return
        super().setCurrentIndex(i)
        w = self.currentWidget()
        d = dur("base")
        if w is None or d == 0 or not self.isVisible():
            return
        eff = QGraphicsOpacityEffect(w)
        w.setGraphicsEffect(eff)
        a = QPropertyAnimation(eff, b"opacity", w)
        a.setDuration(d); a.setStartValue(0.0); a.setEndValue(1.0); a.setEasingCurve(QEasingCurve.Type.OutCubic)
        a.finished.connect(lambda w=w: w.setGraphicsEffect(None))
        a.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)


class BaseDialog(QDialog):
    """Common dialog chrome: title + subtitle header, body area, fade/slide-in."""

    def __init__(self, title, subtitle="", parent=None, icon_name=None, width=560):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)
        self.setMinimumWidth(width)
        self.root = QVBoxLayout(self)
        self.root.setContentsMargins(24, 22, 24, 20)
        self.root.setSpacing(14)
        head = QHBoxLayout(); head.setSpacing(12)
        if icon_name:
            tile = QLabel(); tile.setFixedSize(40, 40)
            tile.setPixmap(icon_pixmap(icon_name, C["accent2"], 22, 2.0)); tile.setAlignment(Qt.AlignmentFlag.AlignCenter)
            tile.setStyleSheet(f"background:rgba(61,165,255,0.12);border-radius:12px;")
            head.addWidget(tile)
        tb = QVBoxLayout(); tb.setSpacing(2)
        t = QLabel(title); t.setObjectName("dlgTitle"); tb.addWidget(t)
        if subtitle:
            s = QLabel(subtitle); s.setObjectName("dlgSub"); s.setWordWrap(True); tb.addWidget(s)
        head.addLayout(tb, 1)
        self.root.addLayout(head)
        self.body = QVBoxLayout(); self.body.setSpacing(10)
        self.root.addLayout(self.body, 1)
        self.footer = QHBoxLayout(); self.footer.setSpacing(10); self.footer.addStretch()
        self.root.addLayout(self.footer)

    def showEvent(self, e):
        super().showEvent(e)
        d = dur("base")
        if not d or getattr(self, "_shown_once", False):
            return
        self._shown_once = True
        self.setWindowOpacity(0.0)
        a = QPropertyAnimation(self, b"windowOpacity", self)
        a.setDuration(d); a.setStartValue(0.0); a.setEndValue(1.0); a.setEasingCurve(QEasingCurve.Type.OutCubic)
        end = self.pos()
        s = QPropertyAnimation(self, b"pos", self)
        s.setDuration(d); s.setStartValue(end + QPoint(0, 12)); s.setEndValue(end); s.setEasingCurve(QEasingCurve.Type.OutCubic)
        a.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped); s.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)


# ------------------------------------------------------------- small widgets
class StatusBadge(QWidget):
    """Status pill for table cells (torrent / network tables)."""

    def __init__(self, status="QUEUED", parent=None):
        super().__init__(parent)
        self.status = str(status or "QUEUED").upper()
        self.setMinimumHeight(26)

    def set_status(self, status):
        self.status = str(status or "QUEUED").upper()
        self.update(); self.updateGeometry()

    def sizeHint(self):
        return QSize(pill_width(STATUS_TEXT.get(self.status, self.status.title())) + 8, 26)

    def paintEvent(self, _e):
        p = QPainter(self); p.setRenderHint(QPainter.RenderHint.Antialiasing)
        t = STATUS_TEXT.get(self.status, self.status.title())
        w = pill_width(t)
        draw_pill(p, QRectF(4, (self.height() - 22) / 2, w, 22), t, STATUS_COLORS.get(self.status, C["text3"]))
        p.end()


class FlowProgressBar(QWidget):
    """Compact bar for table cells (torrents)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.value = 0; self.status = "QUEUED"; self.phase = 0.0
        self.setMinimumSize(100, 24)

    def set_progress(self, value, status):
        self.value = max(0, min(100, int(value))); self.status = str(status or "QUEUED").upper(); self.update()

    def advance(self):
        pass  # no per-frame animation: keeps idle CPU near zero

    def paintEvent(self, _e):
        p = QPainter(self); p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(2, 8, -44, -8)
        p.setPen(Qt.PenStyle.NoPen); p.setBrush(QColor(C["surface3"])); p.drawRoundedRect(r, 4, 4)
        if self.value:
            f = QRectF(r.left(), r.top(), r.width() * self.value / 100.0, r.height())
            p.setBrush(QColor(STATUS_COLORS.get(self.status, C["accent"]))); p.drawRoundedRect(f, 4, 4)
        p.setPen(QColor(C["text2"])); p.setFont(font(11, 600))
        p.drawText(QRectF(r.right() + 6, 0, 38, self.height()), Qt.AlignmentFlag.AlignVCenter, f"{self.value}%")
        p.end()


class PulseDot(QWidget):
    """Status dot with an optional slow breathing pulse (one animation, only while active)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(12, 12)
        self._color = C["text3"]; self._t = 0.0
        self._anim = QVariantAnimation(self)
        self._anim.setStartValue(0.0); self._anim.setEndValue(1.0); self._anim.setDuration(1800); self._anim.setLoopCount(-1)
        self._anim.valueChanged.connect(self._tick)

    def _tick(self, v):
        self._t = float(v); self.update()

    def set_state(self, color, pulse=False):
        self._color = color
        if pulse and dur("slow"):
            if self._anim.state() != QVariantAnimation.State.Running:
                self._anim.start()
        else:
            self._anim.stop(); self._t = 0.0
        self.update()

    def paintEvent(self, _e):
        p = QPainter(self); p.setRenderHint(QPainter.RenderHint.Antialiasing)
        c = QColor(self._color)
        if self._t:
            ring = QColor(c); ring.setAlpha(int(110 * (1 - self._t)))
            p.setPen(Qt.PenStyle.NoPen); p.setBrush(ring)
            rad = 3 + 3 * self._t
            p.drawEllipse(QPointF(6, 6), rad, rad)
        p.setBrush(c); p.setPen(Qt.PenStyle.NoPen); p.drawEllipse(QPointF(6, 6), 3.2, 3.2)
        p.end()


class MetricCard(QFrame):
    def __init__(self, label, icon_name, color, parent=None):
        super().__init__(parent)
        self.setObjectName("metricCard")
        self.setMinimumHeight(86)
        lay = QHBoxLayout(self); lay.setContentsMargins(16, 14, 16, 14); lay.setSpacing(14)
        tile = QLabel(); tile.setFixedSize(42, 42); tile.setAlignment(Qt.AlignmentFlag.AlignCenter)
        tile.setPixmap(icon_pixmap(icon_name, color, 20, 2.0))
        c = QColor(color)
        tile.setStyleSheet(f"background:rgba({c.red()},{c.green()},{c.blue()},0.13);border-radius:12px;")
        lay.addWidget(tile)
        col = QVBoxLayout(); col.setSpacing(0)
        a = QLabel(label); a.setObjectName("metricLabel")
        self.value = QLabel("0"); self.value.setObjectName("metricValue")
        self.sub = QLabel(""); self.sub.setObjectName("metricSub")
        col.addWidget(a); col.addWidget(self.value); col.addWidget(self.sub)
        lay.addLayout(col, 1)

    def set(self, value, sub=""):
        self.value.setText(value)
        self.sub.setText(sub)
        self.sub.setVisible(bool(sub))


class EmptyState(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self); lay.setAlignment(Qt.AlignmentFlag.AlignCenter); lay.setSpacing(8)
        self.art = QLabel(); self.art.setFixedSize(112, 112); self.art.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.title = QLabel(); self.title.setObjectName("sectionTitle"); self.title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.text = QLabel(); self.text.setObjectName("muted"); self.text.setAlignment(Qt.AlignmentFlag.AlignCenter); self.text.setWordWrap(True)
        self.text.setMaximumWidth(380)
        self.button = Button("", "primary"); self.button.setVisible(False)
        lay.addWidget(self.art, 0, Qt.AlignmentFlag.AlignHCenter)
        lay.addWidget(self.title); lay.addWidget(self.text, 0, Qt.AlignmentFlag.AlignHCenter)
        lay.addSpacing(8); lay.addWidget(self.button, 0, Qt.AlignmentFlag.AlignHCenter)
        self._icon = "download"; self._cb = None
        self.button.clicked.connect(lambda: self._cb and self._cb())

    def configure(self, icon_name, title, text, action=None, callback=None, action_icon="plus"):
        self._icon = icon_name
        self.title.setText(title); self.text.setText(text)
        self.button.setVisible(bool(action))
        if action:
            self.button.setText(action); self.button.set_icon_name(action_icon); self._cb = callback
        self.update()

    def paintEvent(self, _e):
        p = QPainter(self); p.setRenderHint(QPainter.RenderHint.Antialiasing)
        c = self.art.geometry().center()
        for rad, a in ((54, 10), (40, 16)):
            p.setPen(Qt.PenStyle.NoPen); p.setBrush(qcolor(C["accent"], a)); p.drawEllipse(QPointF(c), rad, rad)
        g = QLinearGradient(c.x() - 28, c.y() - 28, c.x() + 28, c.y() + 28)
        g.setColorAt(0, qcolor(C["accent"], 70)); g.setColorAt(1, qcolor(C["violet"], 60))
        p.setBrush(QBrush(g)); p.drawRoundedRect(QRectF(c.x() - 28, c.y() - 28, 56, 56), 18, 18)
        p.drawPixmap(QPointF(c.x() - 14, c.y() - 14), icon_pixmap(self._icon, C["text"], 28, self.devicePixelRatioF()))
        p.end()


# ------------------------------------------------------------ download card
class DownloadCard(QWidget, _Hoverable):
    clicked = Signal(int, object)          # rid, keyboard modifiers
    primary = Signal(int)                  # pause / resume / retry / open file
    secondary = Signal(int)                # remove... / open folder
    menuRequested = Signal(int, object)    # rid, global pos
    activated = Signal(int)                # double click -> details

    H = 92
    RIGHT = 3 * 36 + 16 + 12

    def __init__(self, rid, name, parent=None):
        super().__init__(parent)
        self._init_hover()
        self.rid = int(rid); self.name = name
        self.status = "QUEUED"; self.done = 0; self.total = 0; self.speed = 0.0
        self.eta = ""; self.error = ""; self.meta_extra = ""; self.fmt = None
        self.completed_text = ""; self.location = ""
        self.selected = False
        self._shown = 0.0
        self._panim = QVariantAnimation(self)
        self._panim.valueChanged.connect(self._set_shown)
        self.setFixedHeight(self.H)
        self.setMinimumWidth(480)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.setCursor(Qt.CursorShape.ArrowCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.b1 = IconButton("pause", "Pause", parent=self)
        self.b2 = IconButton("x", "Remove…", danger=True, parent=self)
        self.b3 = IconButton("more", "More options", parent=self)
        self.b1.clicked.connect(lambda: self.primary.emit(self.rid))
        self.b2.clicked.connect(lambda: self.secondary.emit(self.rid))
        self.b3.clicked.connect(lambda: self.menuRequested.emit(self.rid, self.b3.mapToGlobal(QPoint(0, self.b3.height()))))
        self._layout_buttons()
        self._sync_buttons()

    # ---- state
    def pct(self):
        return min(100.0, self.done * 100.0 / self.total) if self.total else (100.0 if self.status == "COMPLETED" else 0.0)

    def _set_shown(self, v):
        self._shown = float(v); self.update()

    def update_state(self, status, done, total, speed=0.0, eta="", error="", meta_extra="", name=None,
                     completed_text="", location=""):
        old = self.pct()
        self.status = status; self.done = done; self.total = total; self.speed = speed
        self.eta = eta; self.error = error; self.meta_extra = meta_extra
        self.completed_text = completed_text or self.completed_text; self.location = location or self.location
        if name and name != self.name:
            self.name = name
        new = self.pct()
        d = dur("base")
        if d and abs(new - old) > 0.05 and self.isVisible():
            self._panim.stop(); self._panim.setDuration(d); self._panim.setEasingCurve(QEasingCurve.Type.OutCubic)
            self._panim.setStartValue(self._shown); self._panim.setEndValue(new); self._panim.start()
        else:
            self._panim.stop(); self._shown = new
        self._sync_buttons()
        self.setToolTip(self.error if self.error else "")
        self.update()

    def set_selected(self, v):
        if v != self.selected:
            self.selected = v; self.update()

    def _sync_buttons(self):
        s = self.status
        if s == "COMPLETED":
            self.b1.set_icon_name("open", "Open file"); self.b2.set_icon_name("folder", "Open containing folder")
            self.b2.danger = False
        else:
            self.b2.set_icon_name("x", "Remove…"); self.b2.danger = True
            if s == "DOWNLOADING":
                self.b1.set_icon_name("pause", "Pause")
            elif s in ("ERROR", "RETRYING"):
                self.b1.set_icon_name("retry", "Retry")
            else:
                self.b1.set_icon_name("play", "Resume" if s in ("PAUSED", "STOPPED") else "Start")

    def _layout_buttons(self):
        w = self.width(); y = (self.H - 34) // 2
        x = w - 12 - 3 * 36 + 2
        for b in (self.b1, self.b2, self.b3):
            b.move(x, y); x += 36

    def resizeEvent(self, e):
        super().resizeEvent(e); self._layout_buttons()

    # ---- interaction
    def enterEvent(self, e):
        self._hover_to(1.0); super().enterEvent(e)

    def leaveEvent(self, e):
        self._hover_to(0.0); super().leaveEvent(e)

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self.setFocus(); self.clicked.emit(self.rid, e.modifiers())
        elif e.button() == Qt.MouseButton.RightButton:
            self.clicked.emit(self.rid, Qt.KeyboardModifier.NoModifier) if not self.selected else None
            self.menuRequested.emit(self.rid, e.globalPosition().toPoint())

    def mouseDoubleClickEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self.activated.emit(self.rid)

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.activated.emit(self.rid)
        elif e.key() == Qt.Key.Key_Space:
            self.primary.emit(self.rid)
        else:
            super().keyPressEvent(e)

    # ---- painting
    def _meta(self):
        f = self.fmt or (lambda n: str(n))
        s = self.status
        if s == "ERROR":
            return (self.error or "Download failed"), C["error"]
        if s == "RETRYING":
            return (self.error or "Retrying…"), C["retry"]
        if s == "COMPLETED":
            parts = [f(self.total or self.done)]
            if self.completed_text: parts.append(self.completed_text)
            if self.location: parts.append(self.location)
            return "  ·  ".join(parts), C["text2"]
        parts = [f"{f(self.done)} of {f(self.total)}" if self.total else f(self.done)]
        if s == "DOWNLOADING":
            parts.append(f"{f(self.speed)}/s")
            if self.eta and self.eta != "—": parts.append(f"{self.eta} left")
        if self.meta_extra: parts.append(self.meta_extra)
        return "  ·  ".join(parts), C["text2"]

    def paintEvent(self, _e):
        p = QPainter(self); p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w = self.width(); h = self.H
        r = QRectF(0.5, 0.5, w - 1, h - 1)
        base = QColor(C["surface"]); hov = QColor(C["surface2"])
        bg = _mix(base, hov, self._hv)
        if self.selected:
            bg = _mix(bg, C["accent"], 0.10)
        p.setPen(QPen(_mix(C["border"], C["accent"] if self.selected else "#2c3858", 1.0 if self.selected else self._hv), 1))
        p.setBrush(bg)
        p.drawRoundedRect(r, RADIUS["md"], RADIUS["md"])
        if self.selected:  # left accent edge
            p.setPen(Qt.PenStyle.NoPen); p.setBrush(QColor(C["accent"]))
            p.drawRoundedRect(QRectF(0, 18, 3, h - 36), 1.5, 1.5)
        color = QColor(STATUS_COLORS.get(self.status, C["text3"]))
        # file tile
        from .theme import file_kind
        ic, kc = file_kind(self.name)
        tile = QRectF(16, (h - 48) / 2, 48, 48)
        p.setPen(Qt.PenStyle.NoPen); p.setBrush(qcolor(kc, 30)); p.drawRoundedRect(tile, 13, 13)
        p.drawPixmap(QPointF(tile.center().x() - 12, tile.center().y() - 12), icon_pixmap(ic, kc, 24, self.devicePixelRatioF()))
        # text columns
        x0 = 80; right = w - self.RIGHT
        status_t = STATUS_TEXT.get(self.status, self.status.title())
        pw = pill_width(status_t)
        p.setPen(QColor(C["text"])); p.setFont(font(14, 600))
        fm = QFontMetrics(font(14, 600))
        p.drawText(QRectF(x0, 14, right - x0 - pw - 12, 22), Qt.AlignmentFlag.AlignVCenter,
                   fm.elidedText(self.name, Qt.TextElideMode.ElideMiddle, int(right - x0 - pw - 12)))
        draw_pill(p, QRectF(right - pw, 14, pw, 22), status_t, color)
        meta, mcol = self._meta()
        mf = font(12, 400); p.setFont(mf); p.setPen(QColor(mcol))
        p.drawText(QRectF(x0, 40, right - x0, 18), Qt.AlignmentFlag.AlignVCenter,
                   QFontMetrics(mf).elidedText(meta.replace("\n", " "), Qt.TextElideMode.ElideRight, int(right - x0)))
        # progress
        bar = QRectF(x0, 68, right - x0 - 54, 8)
        p.setPen(Qt.PenStyle.NoPen); p.setBrush(QColor(C["surface3"])); p.drawRoundedRect(bar, 4, 4)
        val = self._shown if self._panim.state() == QVariantAnimation.State.Running else self.pct()
        if val > 0:
            fill = QRectF(bar.left(), bar.top(), max(8.0, bar.width() * val / 100.0), bar.height())
            if self.status == "DOWNLOADING":
                g = QLinearGradient(fill.topLeft(), fill.topRight())
                g.setColorAt(0, QColor(C["accent"])); g.setColorAt(1, QColor(C["accent2"])); p.setBrush(QBrush(g))
            elif self.status == "COMPLETED":
                p.setBrush(qcolor(C["success"], 150))
            else:
                p.setBrush(qcolor(color.name(), 200))
            p.drawRoundedRect(fill, 4, 4)
        p.setPen(QColor(C["text"] if self.status != "COMPLETED" else C["success"])); p.setFont(font(12, 600))
        p.drawText(QRectF(bar.right() + 8, 62, 46, 20), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight, f"{int(val)}%")
        if self.hasFocus():
            p.setPen(QPen(qcolor(C["accent2"], 160), 1.5)); p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(r.adjusted(1, 1, -1, -1), RADIUS["md"], RADIUS["md"])
        p.end()


# ------------------------------------------------------------ download list
class DownloadList(QScrollArea):
    selectionChanged = Signal()
    deleteKey = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        host = QWidget(); host.setObjectName("listHost"); host.setStyleSheet("#listHost{background:transparent;}")
        self.setWidget(host)
        self._lay = QVBoxLayout(host); self._lay.setContentsMargins(0, 0, 8, 0); self._lay.setSpacing(10)
        self._lay.addStretch(1)
        self.empty = EmptyState(); self._lay.insertWidget(0, self.empty, 1)
        self.empty.hide()
        self.cards = {}
        self._order = []
        self._anchor = None
        self.viewport().setStyleSheet("background:transparent;")

    def count(self):
        return len(self.cards)

    def add(self, card, top=False, animate=False):
        card.clicked.connect(self._on_click)
        self.cards[card.rid] = card
        if top:
            self._order.insert(0, card.rid); self._lay.insertWidget(1, card)
        else:
            self._order.append(card.rid); self._lay.insertWidget(self._lay.count() - 1, card)
        if animate and dur("base"):
            eff = QGraphicsOpacityEffect(card); card.setGraphicsEffect(eff)
            a = QPropertyAnimation(eff, b"opacity", card)
            a.setDuration(dur("base")); a.setStartValue(0.0); a.setEndValue(1.0)
            a.finished.connect(lambda c=card: c.setGraphicsEffect(None))
            a.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)

    def card(self, rid):
        return self.cards.get(int(rid))

    def remove(self, rid):
        c = self.cards.pop(int(rid), None)
        if c is None:
            return
        if int(rid) in self._order: self._order.remove(int(rid))
        self._lay.removeWidget(c); c.setParent(None); c.deleteLater()
        self.selectionChanged.emit()

    def clear(self):
        for rid in list(self.cards): self.remove(rid)

    def visible_ids(self):
        return [r for r in self._order if r in self.cards and not self.cards[r].isHidden()]

    def selected_ids(self):
        return [r for r in self._order if r in self.cards and self.cards[r].selected]

    def select_only(self, ids):
        ids = set(ids)
        for rid, c in self.cards.items(): c.set_selected(rid in ids)
        self.selectionChanged.emit()

    def _on_click(self, rid, mods):
        vis = self.visible_ids()
        if mods & Qt.KeyboardModifier.ShiftModifier and self._anchor in vis and rid in vis:
            a, b = sorted((vis.index(self._anchor), vis.index(rid)))
            self.select_only(vis[a:b + 1])
        elif mods & Qt.KeyboardModifier.ControlModifier:
            c = self.cards[rid]; c.set_selected(not c.selected); self._anchor = rid; self.selectionChanged.emit()
        else:
            self.select_only([rid]); self._anchor = rid

    def apply_filter(self, predicate):
        """predicate(card) -> bool. Returns number visible."""
        n = 0
        for c in self.cards.values():
            ok = bool(predicate(c))
            c.setVisible(ok); n += ok
            if not ok and c.selected: c.set_selected(False)
        self.empty.setVisible(n == 0)
        return n

    def keyPressEvent(self, e):
        if e.key() == Qt.Key.Key_Delete:
            self.deleteKey.emit()
        elif e.key() == Qt.Key.Key_A and e.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self.select_only(self.visible_ids())
        else:
            super().keyPressEvent(e)
