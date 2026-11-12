import os
import re
from core.version import APP_VERSION
from pathlib import Path
from urllib.parse import urlparse, parse_qs, unquote
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QFileDialog, QDialogButtonBox, QGroupBox
)
import threading
from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import QMessageBox, QWidget
from . import theme
from .icons import icon
from core.security import validate_download_url, safe_filename, refuse_https_downgrade
from core.network_monitor import is_combinable


def _filename_from_content_disposition(value):
    """Return a filename from an HTTP Content-Disposition header."""
    if not value:
        return ""

    # RFC 5987/6266 extended form: filename*=UTF-8''...
    m = re.search(r"filename\*\s*=\s*(?:UTF-8''|utf-8'')([^;]+)", value, re.I)
    if m:
        return unquote(m.group(1).strip().strip('"'))

    # Normal quoted form: filename="example.mkv"
    m = re.search(r'filename\s*=\s*"([^"]+)"', value, re.I)
    if m:
        return unquote(m.group(1).strip())

    # Normal unquoted form.
    m = re.search(r"filename\s*=\s*([^;]+)", value, re.I)
    if m:
        return unquote(m.group(1).strip().strip('"'))

    return ""


def _filename_from_url(url):
    """Extract a filename from signed CDN/R2/S3 URLs and normal URL paths."""
    try:
        p = urlparse(url)
        qs = parse_qs(p.query, keep_blank_values=True)

        # Cloudflare R2 / S3 commonly puts the real name here:
        # response-content-disposition=attachment; filename="movie.mkv"
        for key in ("response-content-disposition", "content-disposition"):
            for value in qs.get(key, []):
                name = _filename_from_content_disposition(value)
                if name:
                    return name

        # Other common filename query parameters.
        for key in ("filename", "file_name", "download_name", "name"):
            for value in qs.get(key, []):
                name = unquote(value).strip().strip('"')
                if name and "." in os.path.basename(name):
                    return name

        # Finally try the URL path itself.
        path_name = os.path.basename(unquote(p.path).rstrip("/"))
        if path_name and path_name.lower() not in {"download", "file", "index"}:
            return path_name
    except Exception:
        pass

    return ""


def _looks_like_generated_name(name):
    """Detect the opaque object IDs used by the previous v10 UI."""
    name = name.strip()
    if not name:
        return True

    stem, ext = os.path.splitext(name)

    # Real filenames normally have an extension.
    if ext:
        return False

    # The old UI commonly displayed 32-character hex object IDs.
    if re.fullmatch(r"[0-9a-fA-F]{24,64}", name):
        return True

    return name.lower() in {"download", "file", "index"}


class NewDownloadDialog(QDialog):
    # Priority 1.2 — filename resolution makes a network call; run it off
    # the UI thread and marshal the result back via this signal instead of
    # blocking the (modal) dialog's event loop on I/O.
    # (request id, url, resolved name): the id/url let us discard stale results.
    _name_resolved = Signal(int, str, str)

    def __init__(self, url="", parent=None):
        super().__init__(parent)
        self.setWindowTitle("New download")
        self.setModal(True)
        self.setWindowModality(Qt.ApplicationModal)
        self.setWindowFlags(Qt.Dialog | Qt.WindowTitleHint | Qt.WindowSystemMenuHint | Qt.WindowCloseButtonHint)
        self.setMinimumSize(600, 600)
        self.url_edit = QLineEdit(url)
        self.url_edit.setMinimumHeight(36)
        self.url_edit.setPlaceholderText("https://...")
        self.dir_edit = QLineEdit()
        self.dir_edit.setMinimumHeight(36)
        self.name_edit = QLineEdit()
        self.name_edit.setMinimumHeight(36)
        self.security_label = QLabel("Transport security: checking URL…")
        self.security_label.setProperty("tone", "muted")

        from PySide6.QtWidgets import QSpinBox
        self.connections = QSpinBox()
        self.connections.setRange(1, 32)
        parent_settings = getattr(parent, "settings", {}) if parent is not None else {}
        self.dir_edit.setText(parent_settings.get("download_dir") or str(Path.home() / "Downloads"))
        self.connections.setValue(int(parent_settings.get("connections", 8) or 8))
        self.connections.setMinimumHeight(36)

        form = QVBoxLayout(self)
        form.setSpacing(7)
        heading = QLabel("Add download"); heading.setObjectName("dialogTitle"); form.addWidget(heading)
        form.addWidget(QLabel("URL")); form.addWidget(self.url_edit)
        self.dir_label = QLabel("Save to"); form.addWidget(self.dir_label)
        row = QHBoxLayout(); row.addWidget(self.dir_edit); browse = QPushButton(); browse.setIcon(icon("folder", theme.TEXT)); browse.setToolTip("Choose folder"); browse.setAccessibleName("Choose folder"); browse.setMinimumSize(48,36); browse.clicked.connect(self.pick_dir); row.addWidget(browse)
        self.dir_box = QWidget(); self.dir_box.setLayout(row); row.setContentsMargins(0, 0, 0, 0); form.addWidget(self.dir_box)
        if not parent_settings.get("ask_location", True):      # Settings > General: use the default folder, don't ask
            self.dir_label.hide(); self.dir_box.hide()
        form.addWidget(QLabel("File name")); form.addWidget(self.name_edit)
        form.addWidget(self.security_label)
        form.addWidget(QLabel("Connections for this download")); form.addWidget(self.connections)
        hint = QLabel(f"Your default is {int(parent_settings.get('connections', 8) or 8)}. This value is stored with this download only.")
        hint.setProperty("tone", "muted"); hint.setWordWrap(True); form.addWidget(hint)

        self.network_group = QGroupBox("Network mode")
        ng = QVBoxLayout(self.network_group)
        multi_enabled = bool(parent_settings.get("multi_network", True))
        self.network_title = QLabel("Combined Multi-Network" if multi_enabled else "Windows Routing (Multi-Network Off)")
        self.network_title.setObjectName("sectionTitle")
        ng.addWidget(self.network_title)
        self.network_summary = QLabel()
        self.network_summary.setWordWrap(True)
        self.network_summary.setObjectName("muted")
        ng.addWidget(self.network_summary)
        note = QLabel("Nexus automatically distributes HTTP byte-range connections across all enabled online Wi-Fi and Ethernet adapters. Bluetooth tethering is excluded — too slow to help." if multi_enabled else "Multi-network aggregation is disabled in Preferences, so Windows chooses the route.")
        note.setWordWrap(True); note.setObjectName("muted"); ng.addWidget(note)
        form.addWidget(self.network_group)

        buttons = QDialogButtonBox()
        self._download_btn = buttons.addButton("DOWNLOAD", QDialogButtonBox.AcceptRole)
        buttons.addButton("Cancel", QDialogButtonBox.RejectRole)
        buttons.accepted.connect(self._accept); buttons.rejected.connect(self.reject)
        form.addWidget(buttons)
        self._name_resolved.connect(self._apply_resolved_name)
        self._req_id = 0                 # bumped on every URL change / new request
        self._resolving_name = None      # name-field text when the active request started
        self._name_user_edited = False   # True once the user typed in the name field
        self.name_edit.textEdited.connect(self._on_name_typed)

        self.url_edit.textChanged.connect(self._on_url_changed)
        self.url_edit.textChanged.connect(self._suggest_name)
        self.url_edit.textChanged.connect(self._update_security)
        self._suggest_name(self.url_edit.text())
        self._update_security(self.url_edit.text())
        self._populate_networks()
        self._fit_to_parent()
        QTimer.singleShot(0, self._bring_to_front)

    def _populate_networks(self):
        parent = self.parentWidget()
        rows = getattr(parent, "net_rows", {}) if parent is not None else {}
        active = []
        try:
            for ifname, info in rows.items():
                ip = str(getattr(info, "ip", "") or "").strip()
                if not ip:
                    continue
                if str(getattr(info, "state", "")).upper() != "ACTIVE":
                    continue
                if str(getattr(info, "internet", "")).upper() != "ONLINE":
                    continue
                if not bool(getattr(info, "enabled", True)):
                    continue
                if not is_combinable(getattr(info, "name", "")):
                    continue
                active.append((str(getattr(info, "name", ifname)), ip))
        except Exception:
            active = []
        active.sort(key=lambda x: x[0].lower())
        if active:
            lines = [f"{name}  —  {ip}" for name, ip in active]
            self.network_summary.setText("\n".join(lines))
        else:
            self.network_summary.setText("No verified online adapters right now. Nexus will use Windows routing until an eligible adapter is available.")

    def _fit_to_parent(self):
        screen = self.screen()
        if screen is None and self.parentWidget() is not None: screen = self.parentWidget().screen()
        if screen is None: return
        avail = screen.availableGeometry()
        parent = self.parentWidget()
        if parent is not None and parent.isVisible():
            pw = parent.width(); ph = parent.height(); target_w = max(560, min(760, int(pw * 0.48))); target_h = max(420, min(520, int(ph * 0.58)))
        else:
            target_w = max(560, min(760, int(avail.width() * 0.42))); target_h = max(420, min(520, int(avail.height() * 0.55)))
        target_w = min(target_w, avail.width() - 40); target_h = min(target_h, avail.height() - 40)
        self.resize(target_w, target_h); self.adjustSize(); self.resize(max(self.width(),560), max(self.height(),420))
        self.move(avail.x() + (avail.width()-self.width())//2, avail.y() + (avail.height()-self.height())//2)

    def _bring_to_front(self):
        self.setWindowState(self.windowState() & ~Qt.WindowMinimized | Qt.WindowActive)
        self.showNormal(); self.raise_(); self.activateWindow(); self.url_edit.setFocus(); self.url_edit.selectAll()

    def showEvent(self, event):
        super().showEvent(event); self._fit_to_parent(); QTimer.singleShot(0, self._bring_to_front)

    def _on_name_typed(self, _text):
        self._name_user_edited = True

    def _on_url_changed(self, _url):
        # Any in-flight filename lookup belongs to the old URL: invalidate it.
        self._req_id += 1
        self._resolving_name = None
        if not self._download_btn.isEnabled():
            self._download_btn.setEnabled(True)
            self._download_btn.setText("DOWNLOAD")

    def _suggest_name(self, url):
        """Show the best filename we can determine from the URL immediately."""
        url = (url or "").strip()
        if not url:
            return

        name = _filename_from_url(url)
        if not name:
            try:
                name = os.path.basename(unquote(urlparse(url).path).rstrip("/"))
            except Exception:
                name = ""

        if name:
            # Don't overwrite a filename the user deliberately typed.
            current = self.name_edit.text().strip()
            # Also refresh when the field still holds our own previous suggestion.
            if (not current or current == getattr(self, "_auto_name", None)
                    or (not self._name_user_edited and _looks_like_generated_name(current))):
                self.name_edit.setText(name)
                self._auto_name = name


    def _resolve_remote_filename(self, url):
        """Ask the server for the real filename without downloading the file."""
        try:
            import httpx

            headers = {"User-Agent": f"NexusDownloader/{APP_VERSION}"}

            with httpx.Client(
                follow_redirects=True,
                max_redirects=10,
                timeout=httpx.Timeout(8.0, connect=5.0),
                verify=True,
                headers=headers,
                event_hooks={"request": [refuse_https_downgrade(url)]},     # a failed probe just falls back to the URL's own file name
            ) as client:

                # HEAD is cheap and normally contains Content-Disposition.
                final_url = ""
                try:
                    r = client.head(url)
                    final_url = str(r.url)
                    cd = r.headers.get("content-disposition", "")
                    name = _filename_from_content_disposition(cd)
                    if name:
                        return name
                except Exception:
                    pass

                # Some CDNs reject HEAD. Ask for only one byte instead.
                try:
                    with client.stream(
                        "GET",
                        url,
                        headers={"Range": "bytes=0-0", **headers},
                    ) as r:
                        final_url = str(r.url) or final_url
                        cd = r.headers.get("content-disposition", "")
                        name = _filename_from_content_disposition(cd)
                        if name:
                            return name

                        # Don't download the file: consume at most one byte.
                        next(r.iter_bytes(1), b"")
                except Exception:
                    pass

                # No Content-Disposition: prefer the name in the original (e.g.
                # signed R2/S3) URL, then the one in the final redirected URL.
                names = [_filename_from_url(url)]
                if final_url and final_url != url:
                    names.append(_filename_from_url(final_url))
                names = [n for n in names if n]
                for n in names:
                    if not _looks_like_generated_name(n):
                        return n
                if names:
                    return names[0]

        except Exception:
            pass

        return _filename_from_url(url)


    def _accept(self):
        ok, reason = validate_download_url(self.url_edit.text())
        if not ok:
            QMessageBox.warning(self, "Invalid download URL", reason)
            return

        url = self.url_edit.text().strip()
        current = self.name_edit.text().strip()

        # If the UI currently contains an opaque object ID, resolve the real
        # server filename before the download record is created. This makes
        # a network call, so it runs on a background thread — the dialog
        # stays responsive instead of freezing on a slow/hanging server.
        if _looks_like_generated_name(current):
            self._download_btn.setEnabled(False)
            self._download_btn.setText("RESOLVING…")
            self._req_id += 1
            req_id = self._req_id
            self._resolving_name = current

            def worker():
                name = self._resolve_remote_filename(url)
                self._name_resolved.emit(req_id, url, name or "")   # queued to the GUI thread

            threading.Thread(target=worker, name="NexusResolveName", daemon=True).start()
            return

        self._finish_accept()

    def _apply_resolved_name(self, req_id, url, name):
        if req_id != self._req_id or url != self.url_edit.text().strip():
            return                                   # stale: URL changed while resolving
        if name and self.name_edit.text().strip() == (self._resolving_name or ""):
            self.name_edit.setText(name)             # field untouched since the request began
        self._download_btn.setEnabled(True)
        self._download_btn.setText("DOWNLOAD")
        self._finish_accept()

    def _finish_accept(self):
        final_name = self.name_edit.text().strip() or "download"
        self.name_edit.setText(safe_filename(final_name))
        self.accept()

    def _tone(self, text, tone):
        self.security_label.setText(text)
        self.security_label.setProperty("tone", tone)
        self.security_label.style().unpolish(self.security_label); self.security_label.style().polish(self.security_label)

    def _update_security(self, url):
        ok, reason = validate_download_url(url)
        if not ok:
            self._tone(f"{reason}", "bad")
        elif url.lower().startswith("https://"):
            self._tone("HTTPS / TLS certificate verification enabled", "ok")
        else:
            self._tone("HTTP is supported, but the connection is not encrypted", "warn")

    def pick_dir(self):
        d = QFileDialog.getExistingDirectory(self, "Select download folder", self.dir_edit.text())
        if d: self.dir_edit.setText(d)

    def data(self):
        return {
            "url": self.url_edit.text().strip(),
            "directory": self.dir_edit.text().strip(),
            "name": self.name_edit.text().strip(),
            "connections": self.connections.value(),
            "network_mode": "COMBINED",
        }
