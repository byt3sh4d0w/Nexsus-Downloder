# Nexus Downloader v10 — Security Test Results

Date: 2026-11-08. Environment: Linux x86-64 container (kernel 6.18), Python 3.13.16, PySide6 6.12.0, httpx 0.28.1, psutil 7.2.2, libtorrent 2.1.1.0, `QT_QPA_PLATFORM=offscreen`. **Not Windows.** Baseline archive SHA-256 (audited): `4c24a6a8006add69d9e86966505218ecc5b86c7397485b8aeff80a5cfaba91ff`. The results in section 4 were produced after the fixes in `SECURITY_AUDIT.md` §18.

Categories used below: **[static]** analysis tools, **[dep]** dependency scan, **[manual]** code review, **[runtime]** executed against local servers/files, **[tests]** automated test suite.

## 1. Tools

| Tool | Version | Category |
|---|---|---|
| Bandit | 1.9.4 | static |
| Ruff | 0.17.3 (config: `pyproject.toml`, rules E9, F63, F7, F82 only) | static |
| pip-audit | 2.10.1 (PyPI advisory data, queried 2026-11-08) | dep |
| `python -m compileall`, `pip check` | Python 3.13.16 | static |
| OpenSSL | system `openssl` (local test CA) | runtime support |
| unittest | stdlib | tests |

## 2. Commands and results

```text
python3.13 -m bandit -r core gui database main.py -f txt
  -> High 1, Medium 2, Low 42  (see §3)
python3.13 -m pip_audit -r requirements.txt
  -> "No known vulnerabilities found"
     resolved: PySide6 6.12.0, httpx 0.28.1, httpcore 1.0.9, h11 0.16.0, anyio 4.16.0, idna 3.20,
               certifi 2026.10.23, psutil 7.2.2, libtorrent 2.1.1, typing-extensions 4.16.0
python3.13 -m ruff check .                 -> All checks passed!
python3.13 -m compileall -q core gui database tests main.py -> exit 0
python3.13 -m pip check                    -> No broken requirements found.
```

Note: `requirements.txt` uses lower bounds, so pip-audit resolved the newest versions on the scan date; it does not describe what a user installed earlier. `pip-audit --local` against the container's own site-packages skipped one OS package (`python-apt`, not on PyPI) and is not meaningful for this project.

## 3. Bandit findings and triage [static]

| Rule | Location | Triage |
|---|---|---|
| B324 weak MD5 (High) | `core/downloader.py:601` | **Not a vulnerability in context:** MD5 only compares against the server's `Content-MD5` header; it is not used for authentication. Limitation recorded as SEC-06 |
| B608 SQL string building (Medium ×2) | `database/db.py:149`, `:197` | **False positives:** only column names from fixed allow-lists are interpolated; values are bound with `?` |
| B603/B606/B607 subprocess/startfile (Low) | `gui/main_window.py:1845, 1860–1862` | Reviewed: argument lists, no `shell=True`; the Defender call was a real finding (SEC-02) and is fixed |
| B110 try/except/pass (Low ×~30) | GUI and engine | Mostly best-effort cleanup/UI code; not individually security-relevant |
| B311 `random` (Low ×2) | `core/scheduler.py` | Used for scheduling jitter, not security |
| B404 subprocess import (Low) | `gui/main_window.py:3` | Informational |

Bandit did **not** find SEC-01 or SEC-02's root cause (the latter appears only as a generic subprocess notice); both came from manual review.

## 4. Automated test suite [tests]

Run one module at a time: `QT_QPA_PLATFORM=offscreen python3.13 -W ignore -m unittest -v tests.<module>`

| Module | Tests | Result |
|---|---|---|
| test_collection_guard | 1 | OK |
| test_security_fixes (new) | 4 | OK |
| test_layout | 3 | OK |
| test_network_buttons | 10 | OK |
| test_responsive | 5 | OK |
| test_adapters | 5 | OK |
| test_combiner | 8 | OK |
| test_nexus | 19 | OK |
| test_ratelimit | 8 | OK |
| test_integrity | 11 | OK |
| test_appearance | 21 | OK |
| test_ui | 14 | OK |
| test_torrent | 32 | OK |
| test_torrent_flow | 28 | OK |
| test_regressions | 49 | OK |
| test_multinet | 33 | OK |
| **Total** | **251** | **0 failed, 0 skipped** |

New regression tests (`tests/test_security_fixes.py`): redirect HTTPS→HTTP is blocked **before sending** (a mock transport records that the `http://` target is never reached); the new error is non-retryable; plain-HTTP and HTTPS→HTTPS starts are untouched; the Defender path never appears in the PowerShell command line and is passed via `NEXUS_SCAN_PATH`. **Verified to fail** when the hook is made a no-op and when the old `Popen` call is restored (3 failures), and to pass with the fixes.

Existing tests exercising security-relevant behaviour (all passing): `test_integrity` (Content-MD5, same-size file not trusted, redirect checks), `test_regressions` (range violations, cleanup, resume), `test_multinet` (adapter binding, no fallback), `test_torrent*` (preview writes nothing, recheck, resume).

## 5. Runtime verification [runtime]

All against local servers on `127.0.0.1`; scripts are in `docs/security-repro/`.

**5.1 HTTPS→HTTP redirect (SEC-01).** `python docs/security-repro/https_downgrade.py` — a local HTTPS server (own CA supplied via `SSL_CERT_FILE`, real TLS verification) replies `302 → http://localhost:<port>/secret?token=SIGNED123` to an HTTP server that records requests.

| | Exception | Requests received by the HTTP target |
|---|---|---|
| Before the fix | `_NonRetryableError: Refused: …` (raised afterwards) | `HEAD /secret?token=SIGNED123` and `GET /secret?token=SIGNED123` |
| After the fix | `DowngradeRefused: Refused: …` | none |

**5.2 File-name fuzzing.** `python docs/security-repro/filename_fuzz.py` — 24 hostile names (`..`, `../../evil.exe`, `..\..\evil.exe`, `/etc/passwd`, `C:\Windows\x.dll`, `file.txt:stream`, RTL override, NUL byte, 500 characters, `CON`, `con .txt`, `NUL.txt`, `COM1`, `LPT9.log`, `CONIN$`, unicode names…) → **0 escaped** a temporary directory; reserved names were prefixed with `_`. Characters `; $ ( ) &` pass through by design (they are legal in file names), which is why SEC-02 mattered.

**5.3 URL validation.** `https://u:p@host/x`, `ftp://`, `file:///`, `javascript:`, whitespace and host-less URLs are rejected; `http://127.0.0.1/x` and `http://[::1]/x` are accepted (SEC-05).

**5.4 Loopback target (SEC-05).** `Downloader._probe_once` accepted a loopback URL and followed a redirect to it.

**5.5 libtorrent path sanitising.** A `.torrent` built with name `../../evil_top` and file path `../../escape.txt` loaded as `....evil_top/_/_/escape.txt`. Default settings observed: DHT, LSD, UPnP, NAT-PMP enabled. Windows cases not run.

## 6. Manual code review [manual]

Modules read in full or in the relevant parts: `core/security.py`, `core/links.py`, `core/downloader.py` (probe, single, multi, merge, integrity, disk check), `core/cleanup.py`, `core/torrent.py`, `core/network_monitor.py`, `database/db.py`, `gui/new_download.py`, the subprocess/open/Defender/settings parts of `gui/main_window.py`, `run_nexus.bat`, `requirements*.txt`, `.github/workflows`. Search for secrets: pattern scan for key/token/password assignments, AWS/GitHub/OpenAI-style tokens and private-key headers over the tree (docs and tests excluded where they only contain test strings) found **none**; search for personal names/paths found none after the audit text was edited.

## 7. False positives and noise

Bandit B324/B608 (above). No Ruff findings. No dependency advisories.

## 8. Not verified

- Windows: PowerShell command behaviour for SEC-02, reserved-name and drive-letter handling inside libtorrent, UAC behaviour, `run_nexus.bat` execution.
- Real internet hosts, real adapters, real torrent swarms; real display DPI scaling.
- SEC-08 (endless chunked body) and SEC-10 (mistyped settings) were not exercised.
- Python 3.12 and other versions (the CI file lists 3.12; not run here).
- Dependency scan covers known advisories only; it says nothing about unknown or future compromises.
