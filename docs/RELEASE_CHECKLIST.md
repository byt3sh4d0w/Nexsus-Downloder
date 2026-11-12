# Release Checklist - Nexus Downloader v10.5

## 1. License
- [x] LICENSE present, MIT text complete.
- [ ] Copyright holder is confirmed (currently placeholder "Nexus Downloader Contributors").

## 2. Third-party notices
- [x] THIRD_PARTY_NOTICES.md lists direct dependencies and their licenses.
- [ ] Transitive dependencies not inventoried; Qt/LGPL obligations to check before binary packaging.

## 3. Packaging
- [x] requirements.txt present; `pip check` clean on 3.12.3 and 3.13.16 (Linux sandbox).
- [ ] No build/packaging script exists. A PyInstaller/PySide6 recipe is needed before a binary release.

## 4. Secrets scan
- [x] No API keys, tokens or credentials found in source or settings defaults.
- [x] FreeLLMAPI is a coding-environment tool, not a runtime dependency (its availability was not verified in the repair run).

## 5. Tests (details: docs/TEST_RESULTS.md)
- [x] compileall clean (3.12.3, 3.13.16).
- [x] 224 tests across 11 modules pass on Python 3.12.3 and 3.13.16 (Linux, offscreen), including all 32 multi-network scheduler tests and the restored 32 + 28 torrent tests.
- [ ] **Same matrix on Windows Python 3.12.10 and 3.13.14: not run, must be done by the owner.**
- [ ] Real multi-NIC (Wi-Fi + USB tethering) measurement: not done.
- [ ] Manual look at the Light and System themes on a real display.

## 6. Audit findings (v10.5 audit PDF) status
- [x] C-01 torrent tests restored; collection guard added (tests/test_collection_guard.py).
- [x] C-02 DialogFader accumulation / double stylesheet rebuild.
- [x] C-03 Light and System themes implemented and persisted (appearance keys were also being dropped by load_settings).
- [x] C-04 speed limit enforced (shared monotonic timeline).
- [x] C-05 accent swatch AttributeError; C-06 STATUS_COLORS refresh.
- [x] C-07 same-size destination no longer accepted without a Content-MD5 match.
- [x] Also: HTTPS->HTTP redirect refused, multi-connection disk-space check, `.parts` cleanup never deletes foreign files, unsafe filename edge cases, corrupt-database recovery.
- [ ] Open: see docs/BUG_FIX_REPORT.md section "Remaining issues".

## 7. Documentation
- [x] AGENT_PROGRESS, BUG_FIX_REPORT, PROJECT_AUDIT, MULTI_NETWORK_DESIGN, TEST_RESULTS, RELEASE_CHECKLIST, DESIGN, SECURITY_MODEL updated.

## 8. Git
- [x] Work done on a copy; original ZIP and a pre-repair backup kept.
- [ ] No commit/push made (not authorized).

## 9. Verdict
READY WITH WARNINGS - all critical/high audit findings are fixed and verified on Linux with both Python versions; Windows verification and real-hardware aggregation testing are still required before calling it RELEASE READY.

## 9. Open-source readiness (Nov 2026)
- [x] README, CONTRIBUTING, CODE_OF_CONDUCT (Contributor Covenant 2.1), SECURITY, CHANGELOG, ROADMAP, TROUBLESHOOTING, THIRD_PARTY_NOTICES, .gitignore, issue/PR templates, CI workflows (Linux, Python 3.12/3.13) added.
- [x] Project status: **Alpha**.
- [ ] CI workflows have **not been executed** on GitHub (only YAML-parsed and their commands run locally). First push may need adjustments.
- [ ] Owner decisions: copyright holder in LICENSE, logo/icon authorship, security contact / private vulnerability reporting, Code of Conduct enforcement contact, repository URL in README clone command.
- [ ] No packaged executable exists; PyInstaller not evaluated.
