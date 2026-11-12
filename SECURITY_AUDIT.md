# Nexus Downloader v10 — Security Audit

Evidence-based review of the current source. Nothing here is carried over from earlier reports; every statement was checked against the code listed in each finding. This is a code review and a set of local tests, not a formal certification.

## 1. Executive summary

Nexus Downloader is a local PySide6 desktop application that runs with normal user privileges, downloads over HTTP/HTTPS with `httpx` (TLS verification on) and over BitTorrent with `libtorrent`, and keeps its state in SQLite and a JSON file under `~/NexusDownloader`. It has no server component, no accounts, no update check and no telemetry code.

The review found **11 items: 0 Critical, 0 High, 2 Medium, 6 Low, 3 Informational.** The two Medium defects were reproduced or confirmed in source and **fixed in this change** with regression tests:

- **SEC-01** An HTTPS link that redirected to `http://` was refused, but only *after* the plain-HTTP request (full URL, query token, headers) had already been sent. Reproduced against local servers before the fix; after the fix no request reaches the HTTP target.
- **SEC-02** The optional Windows Defender scan put the downloaded file's path into a PowerShell `-Command` string. A server-supplied file name such as `a;calc.exe;.txt` would have been executed as script text. Confirmed from the source and the documented behaviour of `-Command`; **not run on Windows** (no PowerShell/Windows here). Fixed by passing the path through an environment variable.

The remaining findings are privacy and hardening items (periodic connectivity probe, torrent network exposure defaults, optional integrity checking, plaintext local state, and so on). Dependency scanning found no known vulnerabilities in the resolved dependency set on the scan date.

**Posture:** reasonable for a single-user desktop tool: TLS verified, URL scheme allow-list, file names flattened to one path component, atomic promotion of finished files, bounded redirects/retries/timeouts, parameterised SQL, no auto-execution of downloads. Limits are listed in section 19. This is not a claim that the application is "secure" or certified.

## 2. Current project identity and inspected archive

| Item | Value |
|---|---|
| Product / version used in documentation | Nexus Downloader **v10** |
| Version string in `core/version.py` | `10.5` at inspection time; changed to `10` in this change at the owner's request (affects the window title, the sidebar label and the `User-Agent` header only) |
| Inspected archive | `Nexus-Downloader-v10.5-OpenSource-networkfix.zip` (the working-copy export from the preceding task; no other archive was supplied with this request) |
| Archive size | 1,717,446 bytes |
| Archive SHA-256 | `4c24a6a8006add69d9e86966505218ecc5b86c7397485b8aeff80a5cfaba91ff` |
| Extracted to | a separate working directory; source then reviewed in the working copy that produced the archive |
| Inspection date / environment | 2026-11-08, Linux (x86-64) container, Python 3.13.16, PySide6 6.12.0, httpx 0.28.1, psutil 7.2.2, libtorrent 2.1.1.0 |
| Size of reviewed code | about 8,200 lines in `core/`, `gui/`, `database/`; about 4,000 lines of tests |

The audited baseline is the hash above. Files changed afterwards are listed in section 18; the delivered archive has a different hash (reported with the delivery).

## 3. Scope

In scope: `core/`, `gui/`, `database/`, `main.py`, `run_nexus.bat`, `requirements*.txt`, `pyproject.toml`, `.github/workflows`, `assets/`, tests.
Out of scope: the Python interpreter, Qt, libtorrent internals, the OS, remote servers, and real-Windows behaviour (not available).

## 4. Methodology

1. Inventory and entry-point tracing (`main.py` → `gui.main_window.run`).
2. Manual review of every module that touches the network, file system, subprocesses, SQL or persisted data, using OWASP ASVS V5/V9/V12/V14 topics and CWE classes as a checklist (no compliance claim).
3. Targeted searches for dangerous APIs (`subprocess`, `os.startfile`, `eval/exec`, `pickle`, `shell=`, `verify=`, URLs, `chmod`, archive modules).
4. Static analysis: Bandit 1.9.4, Ruff 0.17.3.
5. Dependency scan: pip-audit 2.10.1 (PyPI data).
6. Runtime checks with local servers: HTTPS→HTTP redirect (own test CA, real TLS), loopback redirect, file-name fuzzing, URL validation, libtorrent path sanitising.
7. Regression tests for each fix. Full detail: `SECURITY_TEST_RESULTS.md`.

## 5. Architecture and trust boundaries

See `SECURITY_MODEL.md` (diagram `docs/assets/security-boundaries.png`). In short: remote servers and torrent peers are untrusted; the Nexus process, the download folder and `~/NexusDownloader` are inside the user's own trust domain; the OS shell is reached only through user clicks and the optional Defender scan.

## 6. Assets requiring protection

- The user's files and the integrity of what is saved under a final file name.
- The user's file system outside the chosen download folder.
- Signed/private download URLs (they can contain tokens).
- Local state: download history, settings, torrent resume data.
- The user's account on the machine (no privilege escalation, no unexpected code execution).
- Network privacy (which adapter, which hosts, which IP is exposed to peers).

## 7. Threat actors and scenarios

| Actor | Scenario considered |
|---|---|
| Malicious or compromised web server | Hostile `Content-Disposition`/URL file name; redirect to HTTP or to a local address; lying `Content-Range`/`Content-Length`; endless body |
| Network attacker (on-path) | Downgrade of an HTTPS link; tampering with plain-HTTP downloads |
| Malicious torrent / swarm | Crafted paths in metadata, oversized metadata, peer abuse, exposure of the user's IP |
| Local attacker with write access to the user's profile | Edited `settings.json`, `downloads.db`, part files (already runs as the user; limited extra impact) |
| Compromised dependency | Malicious update of PySide6/httpx/psutil/libtorrent |
| Ordinary user | Downloads an executable and opens it; picks an odd destination |

## 8. Attack surface inventory

| Entry point | Code | Input source |
|---|---|---|
| URL field, dropped links, `.torrent` / magnet | `gui/new_download.py`, `gui/main_window.py`, `gui/torrent_dialog.py` | user (content is attacker-influenced) |
| HTTP responses: headers, redirects, bodies | `core/downloader.py`, `gui/new_download.py` | remote server |
| Torrent metadata and peer traffic | `core/torrent.py` (libtorrent) | swarm |
| Adapter list and probes | `core/network_monitor.py` | OS, `www.gstatic.com` |
| Local files | `settings.json`, `downloads.db`, part files, torrent cache | local disk |
| Subprocess / shell | `gui/main_window.py` (`powershell`, `xdg-open`, `open`, `os.startfile`, `QDesktopServices`) | internal, with file paths |
| Listening socket | libtorrent, port 6881 (TCP/UDP, all interfaces) | internet / LAN |

There is no HTTP server, IPC channel, tray service or auto-update path in the application.

## 9. Network security findings

**External endpoints contacted (complete list from the code):**

| Endpoint | Why / when | Data sent | Encrypted | User control | Needed for core download? |
|---|---|---|---|---|---|
| The download URL's host (and redirect targets) | Every download, plus a file-name probe when the Add dialog resolves a name | URL path/query, `User-Agent: NexusDownloader/<version>`, `Range`, `If-Range`; source IP is inevitably visible | Yes for `https://` (certificates verified); no for `http://` URLs the user chose | User chooses the URL | Yes |
| `www.gstatic.com:443` (TCP connect only, `core/network_monitor.py::probe`) | Connectivity test of each enabled adapter; at start-up, every `network_interval` seconds (default 2, range 1–15) and when Refresh/Test is pressed | A TCP handshake bound to the adapter's IPv4 address; **no TLS handshake and no payload**; DNS lookup of the name via the system resolver | No (no data is sent) | Interval is configurable; **no switch turns it off** (an adapter can be disabled) | No (it drives the Networks page and multi-network selection) |
| Torrent trackers, DHT nodes, peers (libtorrent) | Only when a torrent/magnet is added | Info-hash, peer ID, listen port, IP | Not encrypted by Nexus; libtorrent defaults apply | Port and rate limits only | Only for torrents |

No update check, analytics, crash reporting or license call exists in `core/`, `gui/`, `database/` (searched for URLs and for `urllib.request`, `requests`, `ftplib`, `smtplib`, telemetry/analytics/sentry names).

**What is done well (verified in code and/or tests):** `verify=True` on every `httpx` client; no code path disables verification; scheme allow-list (`http`, `https`); `max_redirects=10`; connect 6 s / read 60 s (configurable 5–300 s) / pool 15 s timeouts; bounded retries (default 3, configurable) with back-off; `Range`/`Content-Range` validated before data is appended; `If-Range` validator; per-adapter source binding with no silent fallback to the default route.

### SEC-01 — HTTPS→HTTP redirect sent a plain-text request before being refused
- **Severity:** Medium · **Status:** Confirmed, **fixed**
- **Affected:** `core/downloader.py::_client`, `_probe_once`, `_single_request`, `_multi` workers; `gui/new_download.py::_resolve_remote_filename`
- **Evidence (before):** `_check_redirect()` ran on `r.url` *after* `httpx` (`follow_redirects=True`) had already followed the redirect and received the response. The name probe in `new_download.py` had no downgrade check at all.
- **Attack:** an HTTPS server (or a compromised CDN link) answers `302 Location: http://attacker/…?token=…`. The downloader then sent HEAD and GET over plain HTTP, exposing the URL, the query token and headers to the network, and the file was not saved only because the later check refused it.
- **Expected:** the HTTP request is never sent. **Actual (before):** both requests reached the target.
- **Reproduction (safe, local):** `docs/security-repro/https_downgrade.py` (needs `openssl`). A local HTTPS server (own CA via `SSL_CERT_FILE`) redirected to a local HTTP server. Before: the HTTP server logged `HEAD /secret?token=SIGNED123` and `GET /secret?token=SIGNED123`. After: no request logged, `DowngradeRefused` raised.
- **Impact:** confidentiality of URL tokens/headers on downgrade; integrity impossible to exploit because the file was still refused.
- **Fix:** `core/security.py::refuse_https_downgrade` is an `httpx` request hook that raises `DowngradeRefused` before sending any `http://` request when the link started as `https://`; `_classify` treats it as non-retryable. Plain-HTTP links the user typed are unchanged.
- **Verification:** `tests/test_security_fixes.py::HttpsDowngrade` (fails on the old behaviour, passes now) and the real-TLS reproduction re-run.

## 10. File-system and download security findings

Verified positives:
- `safe_filename()` (`core/security.py`) removes `/` and `\`, bidi override characters, `<>:"|?*` and control characters, trailing dots/spaces, prefixes Windows reserved names (CON, PRN, AUX, NUL, COM1–9, LPT1–9, CONIN$, CONOUT$, also with a trailing space), and caps length at 240. **24 hostile names were fuzzed; 0 escaped a temporary directory** (`..`, `../../x`, `C:\Windows\x.dll`, `file.txt:stream`, RTL override, NUL byte, 500 characters, reserved names).
- Downloads write to `<name>.part` or `<name>.parts/` and are promoted with `os.replace()` only after the size check and, when the server supplied it, the `Content-MD5` check. A partially downloaded file is never given the final name.
- Existing files are not silently overwritten (`links.unique_path`, conflict dialog; overwrite needs an explicit choice). An existing same-size file counts as the finished download only if `Content-MD5` proves it.
- Resume data is bound to the exact range layout and remote version (`layout.txt` with size, range count, chunk size, ETag/Last-Modified); a mismatch discards the parts.
- Cleanup deletes only files named `*.part`, `*.merge`, numbered `*.part`, `layout.txt` and `*.tmp`; symlinks and junctions are unlinked, not followed; foreign files in a `.parts` folder are left alone.
- There is no archive extraction code (no `zipfile`/`tarfile` use), so zip-slip does not apply.

### SEC-08 — Free-space check only covers responses with a known size
- **Severity:** Low · **Status:** Potential (not exercised)
- **Affected:** `core/downloader.py::_check_disk_space` (returns when `total <= 0`), `_single_request`
- **Evidence:** a server that omits `Content-Length` (chunked) is streamed into `.part` until it ends; there is no cap.
- **Scenario:** a hostile server streams data indefinitely until the disk is full.
- **Impact:** local denial of service for the volume; the user can pause/cancel.
- **Recommendation:** optional maximum size setting; stop and report when free space falls below a floor.
- **Verification:** local chunked server with an endless body (not run in this audit).

### SEC-06 — Integrity is only checked when the server sends `Content-MD5`
- **Severity:** Low · **Status:** Confirmed (design limitation)
- **Affected:** `core/downloader.py::_check_integrity`
- **Evidence:** the check returns immediately when no header is present; MD5 is used (Bandit B324 is a true observation but not a security misuse here: it only compares against the server's own header).
- **Impact:** without a hash, a finished file is trusted if its size matches. TLS protects the transport; it does not prove the file is the one the publisher intended.
- **Recommendation:** optional user-supplied SHA-256 field with verification before promotion.
- **Verification:** `tests/test_integrity.py` covers the existing MD5 path.

### SEC-09 — "Open file" launches the default handler without an executable-type warning
- **Severity:** Informational · **Status:** Confirmed (by design)
- **Affected:** `gui/main_window.py::open_file` (`QDesktopServices.openUrl`)
- **Evidence:** runs only on a user click or menu choice; the "open folder when a download completes" setting opens the *folder*, not the file. No downloaded file is ever executed automatically.
- **Recommendation:** optional confirmation for executable/script/shortcut extensions.

## 11. Torrent and magnet security findings

Torrent support exists (`core/torrent.py`, libtorrent 2.1.1.0). Magnet links are parsed by libtorrent (`parse_magnet_uri`); a local regex (`links.magnet_info_hash`) is used only for duplicate detection. Metadata for magnets is fetched in a metadata-only mode (`default_dont_download`) so no files are created before the user confirms. Files can be re-verified with `force_recheck` (piece hashes, not size).

Runtime check (libtorrent 2.1.1.0, Linux): a crafted `.torrent` whose name was `../../evil_top` and whose file path was `../../escape.txt` was loaded as `....evil_top/_/_/escape.txt`; each `..` component was neutralised by libtorrent. Windows-specific cases (drive letters, reserved names) were **not tested**; a component `C:` was preserved on Linux.

### SEC-04 — Torrent session uses libtorrent defaults and listens on every interface
- **Severity:** Low · **Status:** Confirmed
- **Affected:** `core/torrent.py::TorrentManager.__init__/configure`
- **Evidence:** `listen_interfaces = "0.0.0.0:<port>,[::]:<port>"`; the session is created with `lt.session()` and no other setting changes. In the tested libtorrent build the defaults are DHT, local peer discovery, UPnP and NAT-PMP **enabled**; upload limit default 0 (unlimited). Torrent traffic is not bound to the adapters chosen on the Networks page.
- **Scenario:** the user is on a VPN or a chosen adapter and expects torrents to follow it; peers see the real IP, the port is opened via UPnP/NAT-PMP, and the machine seeds.
- **Impact:** privacy and network exposure; not a code-execution issue.
- **Recommendation:** settings to disable DHT/LSD/UPnP, to bind the session to a chosen interface, and a clear first-use notice. Documented in the README.
- **Real limitation to state:** peer-to-peer downloads are untrusted content. A torrent whose pieces all verify is exactly what the publisher shared, which may still be malware. Nexus does not scan or sandbox it.

### SEC-11 — Torrent path safety depends on libtorrent
- **Severity:** Informational · **Status:** Not Tested on Windows
- **Evidence:** Nexus passes user renames (`renamed_files`) and the save path to libtorrent and does not re-validate them. Sanitising of metadata paths was observed on Linux (above). A rename containing `..` supplied at add time was not conclusively tested.
- **Recommendation:** validate renamed paths against the save directory in Nexus and add a Windows test.

## 12. Database and settings security

- SQLite via `sqlite3` with `?` placeholders everywhere data is bound. Bandit B608 flagged two f-string statements (`database/db.py` lines 149, 197): both interpolate **column names taken from a fixed allow-list** (`allowed` sets) and bind all values; **false positives**. The `ALTER TABLE … ADD COLUMN` statement uses constants.
- WAL mode, `synchronous=NORMAL`; an `RLock` serialises access; a damaged database is moved aside to `downloads.db.corrupt-<time>` (user data is kept) and a fresh one created.
- Schema changes are additive (`ALTER TABLE ADD COLUMN` when a column is missing); no destructive migration.
- Persisted: URLs (as typed), file names and full local paths, sizes, status, timing, adapter IPs used, torrent info-hashes and sources (including magnet links), pending cleanup paths, settings (folders, ports, limits, theme).

### SEC-07 — Local state is plaintext and may contain signed URLs and local paths
- **Severity:** Low · **Status:** Confirmed
- **Affected:** `database/db.py`, `gui/main_window.py::save_settings`
- **Evidence:** `downloads.db` and `settings.json` are created with default permissions under `~/NexusDownloader`; URLs are stored as entered; no secret store is used. URLs with embedded `user:password` are rejected, but query-string tokens are stored.
- **Impact:** anyone who can read the user's profile can read download history and tokens.
- **Recommendation:** document it; offer "clear history / strip query from stored URL"; keep the data folder user-only on multi-user systems.

### SEC-10 — Settings values are not type-validated on load
- **Severity:** Low · **Status:** Potential (not exercised)
- **Affected:** `gui/main_window.py::load_settings` (keeps known keys, accepts any value type)
- **Scenario:** a hand-edited or corrupted `settings.json` with a wrong type could raise in code that calls `int()`/arithmetic on the value.
- **Impact:** crash or error dialog at worst; no code execution (JSON only).
- **Recommendation:** coerce/clamp against the defaults' types.

## 13. Secrets and privacy findings

- No credentials, keys or tokens in the source or documents (pattern scan, see `SECURITY_TEST_RESULTS.md`); no `.env` handling; no auth headers are ever set by the application; cookies are not used.
- No logging framework and no log files; error text is shown in the UI and may include a URL or path.
- Local data (history, settings, torrent cache) stays on the machine. Information that leaves the machine: requests to the URLs the user downloads, the connectivity probe to `www.gstatic.com`, and torrent traffic.

### SEC-03 — Connectivity probe contacts a third-party host on a timer and cannot be turned off
- **Severity:** Low (privacy) · **Status:** Confirmed
- **Affected:** `core/network_monitor.py::probe` (`socket.create_connection(("www.gstatic.com", 443), source_address=(ip, 0))`), `gui/main_window.py::net_timer`
- **Evidence:** runs for every enabled adapter at start-up and every `network_interval` seconds (default 2) for as long as the app runs, whether or not anything is downloading. (A `TEST_HOST` URL constant exists but the call uses the literal host.)
- **Impact:** the operator of that host and the network path can see periodic connections from the user's address; DNS lookups reveal the app is running. Nothing sensitive is transmitted.
- **Recommendation:** setting to disable or lengthen the probe, and a configurable probe target; document it (done in README).

## 14. Dependency and supply-chain findings

| Package | Declared | Resolved/installed when audited | Role |
|---|---|---|---|
| PySide6 | `>=6.5` | 6.12.0 (+ shiboken6, Essentials, Addons) | Qt GUI |
| httpx | `>=0.25` | 0.28.1 (httpcore 1.0.9, h11 0.16.0, anyio 4.16.0, idna 3.20, certifi 2026.10.23) | HTTP client, TLS |
| psutil | `>=5.9` | 7.2.2 | adapter list and counters |
| libtorrent | `>=2.0.9` | 2.1.1.0 | BitTorrent engine (optional at runtime) |
| ruff | `>=0.5` (dev only, `requirements-dev.txt`) | 0.17.3 | lint |

- **pip-audit 2.10.1** on `requirements.txt` (PyPI advisory data, 2026-11-08): *No known vulnerabilities found.* The dependency list there was resolved at scan time because requirements are lower-bounded, not pinned.
- Findings: all runtime dependencies use `>=` lower bounds with **no lock file and no hashes** (SEC-12 below); a future release of any of them is installed on the next `pip install`. All four are well-known, widely used packages; no typosquat-like or abandoned package was found. `run_nexus.bat` upgrades `pip` and installs from PyPI on first run.
- libtorrent is installed from a PyPI wheel; upstream provenance of that wheel was not verified.
- No packaged build or installer exists in the repository; distribution is "run from source".

### SEC-12 — Unpinned dependencies, no lock file or hashes
- **Severity:** Low · **Status:** Confirmed
- **Recommendation:** publish a tested `requirements.lock` with `--require-hashes` for releases; schedule `pip-audit` in CI.

## 15. Desktop application security

- **Privileges:** no administrator request, no manifest, no UAC elevation, no changes to routes, adapters, firewall or VPN.
- **Subprocess use:** `subprocess.Popen` only for (a) `powershell … Start-MpScan` when the opt-in "scan with Defender" setting is on (Windows only), and (b) `xdg-open`/`open` for folders on Linux/macOS; `os.startfile` for folders on Windows. All use argument lists, never `shell=True`. `eval`, `exec`, `pickle`, `yaml.load`, `os.system` are not used.
- **URL opening:** `QDesktopServices.openUrl(QUrl.fromLocalFile(...))` for local files/folders only.
- **No IPC/local server/tray service.** Drag-and-drop of links and `.torrent` files is accepted when the setting is on.
- **Shutdown:** workers are stopped and joined with timeouts; temp files are kept for resume.

### SEC-02 — Defender scan: file path interpreted as PowerShell script text
- **Severity:** Medium · **Status:** Confirmed by source analysis, **fixed**; Windows behaviour not run
- **Affected:** `gui/main_window.py::_pump_defender_queue`
- **Evidence (before):** `subprocess.Popen(["powershell","-NoProfile","-Command","Start-MpScan","-ScanPath", target])`. `powershell -Command` joins its remaining arguments with spaces and parses the result as script. `safe_filename` keeps `;`, `$`, `(`, `)`, `&`, backtick and `'`; the fuzzing run confirms `x;calc.exe;.txt` and `$(calc).txt` pass through unchanged. The old code comment claiming the path was bound as a literal was incorrect for `-Command`.
- **Attack:** a server (or URL query such as `?filename=`) proposes the name `a;calc.exe;.txt`. If the user keeps it, the Defender scan setting is on and the download completes, PowerShell would execute `calc.exe` (or any command) with the user's rights. Paths with spaces, such as a Windows user folder with a space in its name, would also break the scan.
- **Preconditions:** Windows, the non-default "scan with Defender" option enabled, the user accepting the offered file name.
- **Fix:** the command is now the constant `Start-MpScan -ScanPath $env:NEXUS_SCAN_PATH`; the path is passed in the child's environment (`NEXUS_SCAN_PATH`), where PowerShell reads it as data.
- **Verification:** `tests/test_security_fixes.py::DefenderScanCommand` asserts the path is absent from the command line and present in the environment (fails on the old code). **Please confirm on Windows** that a file with a space and with `;` in its name is scanned.

## 16. Security test results

Summary only; commands and outputs are in `SECURITY_TEST_RESULTS.md`.
- Full Python 3.13 test suite: see section 4 of `SECURITY_TEST_RESULTS.md` for per-module counts.
- New security regression tests: 4 (`tests/test_security_fixes.py`), all pass; they fail against the pre-fix behaviour.
- Bandit: 1 High (MD5, false positive in context), 2 Medium (f-string SQL on fixed column names, false positives), 42 Low (mostly try/except/pass and subprocess notices).
- pip-audit: no known vulnerabilities. Ruff: clean.
- Runtime: downgrade reproduction (before/after), file-name fuzzing (0 escapes of 24), URL validation, libtorrent path sanitising (Linux).

## 17. Risk assessment

| ID | Title | Severity | Status | State after this change |
|---|---|---|---|---|
| SEC-01 | HTTPS→HTTP redirect request sent before refusal | Medium | Confirmed | Fixed + test |
| SEC-02 | Defender scan path executed as script | Medium | Confirmed (source) | Fixed + test; Windows run pending |
| SEC-03 | Unconditional periodic probe of `www.gstatic.com` | Low | Confirmed | Open (documented) |
| SEC-04 | Torrent defaults: all interfaces, DHT/LSD/UPnP, unlimited upload, not adapter-bound | Low | Confirmed | Open (documented) |
| SEC-06 | Integrity only via optional Content-MD5 | Low | Confirmed | Open |
| SEC-07 | Plaintext history/settings, tokens in stored URLs | Low | Confirmed | Open |
| SEC-08 | No cap for unknown-length responses | Low | Potential | Open |
| SEC-10 | Settings values not type-checked | Low | Potential | Open |
| SEC-12 | Unpinned dependencies, no hashes | Low | Confirmed | Open |
| SEC-05 | Redirects/URLs to loopback and private addresses are followed | Informational | Confirmed | Accepted (user-supplied URL on a desktop) |
| SEC-09 | "Open file" has no executable warning | Informational | Confirmed | Open |
| SEC-11 | Torrent rename/path safety relies on libtorrent; Windows untested | Informational | Not Tested | Open |

SEC-05 detail: `Downloader._probe_once` against `http://localhost:<port>/x` and a redirect to it succeeded. There is no private-address filter. On a single-user desktop the user already chooses the URL, so impact is limited to a malicious public page redirecting a download to an internal service the user's machine can reach (the response would be saved as a file, not shown).

## 18. Remediation recommendations and changes made

Implemented now (smallest changes, each with a test):

| File | Change |
|---|---|
| `core/security.py` | `DowngradeRefused`, `refuse_https_downgrade()` |
| `core/downloader.py` | request hook added to both `httpx` clients; `_classify` treats the new error as non-retryable |
| `gui/new_download.py` | same hook on the file-name probe client |
| `gui/main_window.py` | Defender scan path passed via environment variable; Action-button cell wrapper made transparent (visual fix found while capturing screenshots) |
| `core/version.py`, `.github/ISSUE_TEMPLATE/bug_report.yml` | version string `10` (owner request) |
| `tests/test_security_fixes.py`, `tests/test_collection_guard.py` | 4 new tests; guard floor |
| `README.md`, `SECURITY.md`, `SECURITY_MODEL.md`, `SECURITY_AUDIT.md`, `SECURITY_TEST_RESULTS.md`, `docs/assets/`, `docs/diagrams/`, `docs/visual-workflow/` | documentation and visuals |

Recommended, not implemented (outside a documentation task or needs a product decision): switches for the probe and for torrent DHT/UPnP/interface binding (SEC-03/04); optional SHA-256 verification (SEC-06); free-space floor and size cap (SEC-08); settings type validation (SEC-10); lock file with hashes and `pip-audit` in CI (SEC-12); executable-type warning (SEC-09); validation of torrent renames inside Nexus (SEC-11).

## 19. Remaining limitations of this audit

- No Windows machine: PowerShell behaviour (SEC-02), Windows path/reserved-name handling of libtorrent, UAC and real adapter behaviour were not executed.
- Real multi-adapter hardware and real internet hosts were not used; tests use loopback servers and simulated adapters.
- libtorrent, Qt and the interpreter were not audited. Peer-to-peer behaviour with a real swarm was not tested.
- SEC-08 and SEC-10 are reasoned from source, not exercised.
- Dynamic/fuzz testing of HTTP parsing is limited to what `httpx`/`h11` provide.

## 20. Final evidence-based security posture

For its purpose (a single-user desktop downloader), the current source applies the main controls correctly: certificate verification, a scheme allow-list, bounded redirects and retries, range validation, flattened file names, atomic promotion of complete files, parameterised SQL, no auto-execution and no hidden remote services. Two real defects were found and fixed; the rest are documented privacy and hardening gaps with clear owners. Residual risk is dominated by what users choose to download and open (HTTP links, torrents, executables), which no downloader can remove.
