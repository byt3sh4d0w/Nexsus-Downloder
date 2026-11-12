"""Vector icons drawn with QPainter (no image files, no emoji, crisp at any DPI)."""
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPainterPath, QPen, QPixmap

_CACHE = {}
_GRID = 24.0


def _draw(name, p):
    """Draw icon `name` on a 24x24 grid. Pen/brush are already set."""
    if name == "add":
        p.drawLine(12, 5, 12, 19); p.drawLine(5, 12, 19, 12)
    elif name == "play":
        path = QPainterPath(); path.moveTo(8, 5.5); path.lineTo(19, 12); path.lineTo(8, 18.5); path.closeSubpath()
        p.setBrush(p.pen().color()); p.drawPath(path)
    elif name == "pause":
        p.setBrush(p.pen().color()); p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(QRectF(6.5, 5.5, 4, 13), 1.2, 1.2); p.drawRoundedRect(QRectF(13.5, 5.5, 4, 13), 1.2, 1.2)
    elif name == "stop":
        p.setBrush(p.pen().color()); p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(QRectF(6.5, 6.5, 11, 11), 2, 2)
    elif name == "retry":
        p.drawArc(QRectF(5, 5, 14, 14), 40 * 16, 280 * 16)
        path = QPainterPath(); path.moveTo(17.5, 2.8); path.lineTo(17.8, 7.3); path.lineTo(13.4, 6.6)
        p.drawPath(path)
    elif name == "more":
        p.setBrush(p.pen().color()); p.setPen(Qt.PenStyle.NoPen)
        for x in (6, 12, 18): p.drawEllipse(QPointF(x, 12), 1.7, 1.7)
    elif name == "folder":
        path = QPainterPath(); path.moveTo(3.5, 7); path.lineTo(9, 7); path.lineTo(11, 9); path.lineTo(20.5, 9)
        path.lineTo(20.5, 18.5); path.lineTo(3.5, 18.5); path.closeSubpath(); p.drawPath(path)
    elif name == "open":
        p.drawRoundedRect(QRectF(4, 6, 12, 12), 2, 2)
        p.drawLine(11, 13, 20, 4); p.drawLine(14, 4, 20, 4); p.drawLine(20, 4, 20, 10)
    elif name == "trash":
        p.drawLine(4.5, 7, 19.5, 7); p.drawLine(9.5, 7, 9.5, 4.5); p.drawLine(9.5, 4.5, 14.5, 4.5); p.drawLine(14.5, 4.5, 14.5, 7)
        path = QPainterPath(); path.moveTo(6.5, 7); path.lineTo(7.5, 19.5); path.lineTo(16.5, 19.5); path.lineTo(17.5, 7); p.drawPath(path)
        p.drawLine(10.5, 10.5, 10.5, 16); p.drawLine(13.5, 10.5, 13.5, 16)
    elif name == "remove":     # list item with minus
        p.drawLine(4, 8, 20, 8); p.drawLine(4, 12, 14, 12); p.drawLine(4, 16, 14, 16); p.drawLine(16, 16, 21, 16)
    elif name == "search":
        p.drawEllipse(QPointF(10.5, 10.5), 5.5, 5.5); p.drawLine(14.6, 14.6, 19.5, 19.5)
    elif name == "settings":
        p.drawEllipse(QPointF(12, 12), 3.2, 3.2)
        import math
        for i in range(8):
            a = math.radians(i * 45)
            p.drawLine(QPointF(12 + 6.2 * math.cos(a), 12 + 6.2 * math.sin(a)), QPointF(12 + 8.6 * math.cos(a), 12 + 8.6 * math.sin(a)))
        p.drawEllipse(QPointF(12, 12), 6.2, 6.2)
    elif name == "download":
        p.drawLine(12, 4.5, 12, 15); p.drawLine(7.5, 10.8, 12, 15.2); p.drawLine(16.5, 10.8, 12, 15.2)
        p.drawLine(5, 19.5, 19, 19.5)
    elif name == "torrent":
        p.drawArc(QRectF(5, 4, 14, 14), 180 * 16, 180 * 16); p.drawLine(5, 11, 5, 15); p.drawLine(19, 11, 19, 15)
        p.drawLine(3.5, 15, 6.5, 15); p.drawLine(17.5, 15, 20.5, 15)
        p.drawLine(9, 20, 15, 20)
    elif name == "network":
        p.setBrush(p.pen().color()); p.setPen(Qt.PenStyle.NoPen)
        for i, h in enumerate((5, 9, 13, 17)):
            p.drawRoundedRect(QRectF(4 + i * 4.6, 20 - h, 3, h), 1, 1)
    elif name == "history":
        p.drawEllipse(QPointF(12, 12), 8, 8); p.drawLine(12, 7.5, 12, 12); p.drawLine(12, 12, 15.5, 14)
    elif name == "check":
        p.drawLine(5, 12.5, 10, 17.5); p.drawLine(10, 17.5, 19, 7)
    elif name == "info":
        p.drawEllipse(QPointF(12, 12), 8.5, 8.5); p.drawLine(12, 11, 12, 16.5)
        p.setBrush(p.pen().color()); p.drawEllipse(QPointF(12, 7.8), 0.9, 0.9)
    elif name == "list":
        for y in (6.5, 12, 17.5): p.drawLine(5, y, 19, y)
    elif name == "copy":
        p.drawRoundedRect(QRectF(8.5, 8.5, 11, 11), 2, 2); p.drawRoundedRect(QRectF(4.5, 4.5, 11, 11), 2, 2)
    elif name == "close":
        p.drawLine(6, 6, 18, 18); p.drawLine(18, 6, 6, 18)
    elif name == "refresh":
        p.drawArc(QRectF(5, 5, 14, 14), 40 * 16, 280 * 16)
        path = QPainterPath(); path.moveTo(17.5, 2.8); path.lineTo(17.8, 7.3); path.lineTo(13.4, 6.6); p.drawPath(path)
    elif name == "bolt":
        path = QPainterPath(); path.moveTo(13, 3.5); path.lineTo(6.5, 13); path.lineTo(11.5, 13); path.lineTo(10.5, 20.5)
        path.lineTo(17.5, 10.5); path.lineTo(12.5, 10.5); path.closeSubpath(); p.drawPath(path)
    elif name == "link":
        p.drawRoundedRect(QRectF(3.5, 9, 9, 6), 3, 3); p.drawRoundedRect(QRectF(11.5, 9, 9, 6), 3, 3)
    else:                       # unknown: simple dot so a typo is visible, not a crash
        p.drawEllipse(QPointF(12, 12), 3, 3)


def icon(name, color="#e8edf3", size=64):
    """QIcon for `name` in `color` (cached). Rendered large and scaled by Qt."""
    key = (name, QColor(color).name(QColor.NameFormat.HexArgb), size)
    if key in _CACHE:
        return _CACHE[key]
    pm = QPixmap(size, size); pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.scale(size / _GRID, size / _GRID)
    pen = QPen(QColor(color), 1.9); pen.setCapStyle(Qt.PenCapStyle.RoundCap); pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen); p.setBrush(Qt.BrushStyle.NoBrush)
    _draw(name, p)
    p.end()
    ic = QIcon(pm)
    _CACHE[key] = ic
    return ic


def paint_icon(painter, name, rect, color):
    """Draw an icon straight into a painter (used by item delegates)."""
    icon(name, color).paint(painter, rect.toRect() if hasattr(rect, "toRect") else rect)


_FILE_KINDS = {
    "archive": ("#f5b955", {"zip", "rar", "7z", "tar", "gz", "bz2", "xz", "tgz"}),
    "video": ("#b18cff", {"mkv", "mp4", "avi", "mov", "wmv", "webm", "flv", "m4v", "ts"}),
    "audio": ("#4fc3ff", {"mp3", "flac", "wav", "aac", "ogg", "m4a", "opus"}),
    "image": ("#34d399", {"png", "jpg", "jpeg", "gif", "webp", "bmp", "svg", "heic"}),
    "doc": ("#f7768e", {"pdf", "doc", "docx", "xls", "xlsx", "ppt", "pptx", "txt", "epub", "csv"}),
    "app": ("#22d3ee", {"exe", "msi", "iso", "apk", "dmg", "deb", "rpm", "appimage", "img"}),
}


def _for_palette(color):
    """Dark-palette colours are used as-is; on Light they are darkened to >= 3:1 (graphics contrast)."""
    from . import theme
    if theme.palette_name() == "light":
        return theme._fit_contrast(color, theme.SURFACE, 3.0)
    return color


def file_kind(name):
    ext = (name.rsplit(".", 1)[-1].lower() if "." in name else "")
    for kind, (color, exts) in _FILE_KINDS.items():
        if ext in exts:
            return kind, _for_palette(color), ext
    return "file", _for_palette("#8b96a5"), ext


def paint_file_tile(painter, rect, name, font):
    """Rounded tile tinted by file type, labelled with the extension."""
    kind, color, ext = file_kind(name)
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    bg = QColor(color); bg.setAlpha(36)
    painter.setPen(QPen(QColor(color).darker(150), 1)); painter.setBrush(bg)
    painter.drawRoundedRect(QRectF(rect), 8, 8)
    f = QFont(font); f.setBold(True); f.setPixelSize(max(8, int(rect.height() * 0.26)))
    painter.setFont(f); painter.setPen(QColor(color))
    painter.drawText(QRectF(rect), Qt.AlignmentFlag.AlignCenter, (ext[:4] or "FILE").upper())
    painter.restore()
