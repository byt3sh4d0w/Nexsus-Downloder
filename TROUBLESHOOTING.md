# Troubleshooting

## Install
- **`pip install libtorrent` fails** (e.g. Python 3.14): use Python 3.12/3.13. Or install the rest (`pip install PySide6 httpx psutil`); the app starts and the Torrents tab explains torrents are unavailable.
- **Qt: "could not load the Qt platform plugin xcb"** (Linux): install `libxcb-cursor0` and the usual xcb libraries. For headless runs set `QT_QPA_PLATFORM=offscreen`.
- **`run_nexus.bat` says Python not found**: install Python 3.10+ and tick "Add python.exe to PATH".

## Downloads
- **Same-size file gets downloaded again**: by design when the server sends no `Content-MD5`, the app cannot prove the existing file is the same.
- **"Not enough free disk space"**: multi-connection downloads need room for the parts *and* the merged file (about 2× the size).
- **"Invalid or unreachable link" / redirect refused**: an HTTPS link redirecting to plain HTTP is refused on purpose.
- **Speed limit looks low**: the limit is shared by all connections of one download (and is per download, not global).

## Network
- **Combined speed is no faster**: both adapters must be independent paths. Behind one router/uplink there is nothing to add. On Windows a source IP may still leave through the default route.
- **An adapter is missing or greyed**: virtual adapters (VPN, VM, Docker) are off by default; Bluetooth is excluded. Re-scan on the Networks page.
- **A virtual adapter (e.g. "Ethernet 7", 192.168.56.1) is still listed**: hiding uses the adapter's MAC prefix. If yours has an unusual MAC it is kept visible on purpose. Check with `Get-NetAdapter | ft Name,InterfaceDescription,MacAddress` and open an issue with those three columns.
- **Combiner shows "—" for an adapter**: nothing measures it right now (idle, or only single-connection jobs). It is not a fault.

## Database
- **History looks empty after a crash**: a corrupt `downloads.db` is renamed to `downloads.db.corrupt-<time>` in `~/NexusDownloader/` and a new one is created. Keep the renamed file if you want to recover rows with `sqlite3`.

## GUI
- **Light/System theme wrong after changing OS theme**: System mode follows the OS live; if it doesn't, choose Light or Dark in Settings → Appearance and report the OS/Qt version.
- **Animations feel heavy**: Settings → Appearance → Reduced motion.

Still stuck? Open a bug report (sanitised logs, no tokens or signed URLs).
