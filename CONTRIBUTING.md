# Contributing to Nexus Downloader

Thanks for helping. This guide assumes you know basic Python and Git.

## 1. Get the code and run it

```bash
git clone <repository URL> && cd nexus-downloader
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
python main.py
```

Use Python 3.12 or 3.13 (the versions the tests cover). Without libtorrent the app still runs, torrent tests are not meaningful.

## 2. Run the checks (before every pull request)

```bash
python -m compileall -q .
ruff check .
QT_QPA_PLATFORM=offscreen python -m unittest discover -s tests          # Windows PowerShell: $env:QT_QPA_PLATFORM="offscreen"
```

Run a single module while you work, e.g. `python -m unittest tests.test_ratelimit`. `tests/test_collection_guard.py` fails if a test module stops importing or loses tests, so do not delete or shrink tests to get green. Fix the code instead.

## 3. Code layout

| Folder | Responsibility |
|---|---|
| `core/downloader.py` | HTTP download engine: probing, ranges, resume, merge, rate limit, integrity |
| `core/scheduler.py` | Multi-adapter range scheduler (**change only with a reproducing test**, see AGENTS.md) |
| `core/network_monitor.py` | Adapter discovery and statistics |
| `core/torrent.py` | libtorrent wrapper |
| `core/security.py`, `core/links.py`, `core/cleanup.py` | Filename/URL safety, link normalisation, safe temp-file cleanup |
| `database/db.py` | SQLite history, schema migration, corruption recovery |
| `gui/` | PySide6 interface; `theme.py` holds palettes, accent, QSS and the combiner widget |
| `tests/` | `unittest` suites; `netsim.py` simulates adapters on loopback |

Bigger picture: [ARCHITECTURE.md](ARCHITECTURE.md).

## 4. Conventions

- Keep changes small and focused. Do not reformat unrelated code.
- Bug fix = a test that fails first, then the fix. Every confirmed bug gets a regression test.
- Colours come from `gui/theme.py` tokens (`theme.ACCENT`, `theme.TEXT`, `theme.accent_alpha()`), never hard-coded hex in widgets, so Light/Dark/accent keep working.
- Never touch the user's network settings (routes, VPN, firewall). Never delete files the app did not write.
- Test results you claim must come from a run you did. Say which OS and Python.
- Files in `gui/theme.py` and `tests/test_regressions.py` use Windows (CRLF) line endings: keep them.

## 5. Branches and pull requests

1. Fork, then branch from `main` (`fix/short-name` or `feat/short-name`).
2. One topic per pull request. Fill in the PR template, including which OS/Python you tested.
3. CI (compile, lint, tests on Linux) must pass. Windows CI is not available yet; say if you tested on Windows.
4. Be kind in review, see the [Code of Conduct](CODE_OF_CONDUCT.md).

## 6. Reporting bugs

Use the bug-report form. Include app version (`core/version.py`), OS, Python version, steps, expected vs actual, and a **sanitised** log. Remove tokens, signed URLs, passwords and personal paths. Security problems go through [SECURITY.md](SECURITY.md), not public issues.
