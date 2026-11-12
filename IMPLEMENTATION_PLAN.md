# Implementation Plan — Nexus Downloader v10

This plan is documentation-first and intentionally does not authorize a wholesale rewrite. Run phases in order, keep a clean Git diff, and verify each phase before moving on.

## Phase 0 — Repository baseline and safe workspace

**Goal:** establish exactly what is present and protect the user's work.

Tasks:
- Inspect Git status, branch, recent commits, remotes, and uncommitted changes.
- Confirm Python/Qt/libtorrent versions and current supported environments.
- Read `AGENTS.md` and all product/design/architecture/security/test documents.
- Run compilation and focused baseline tests; record known failures and hangs.

Likely files: none initially; report files only.  
Tests: baseline compile, targeted tests, current full-suite attempt.  
Completion: reproducible baseline report and protected working tree.

## Phase 1 — Reproduce and fix critical correctness defects

**Goal:** fix reproducible data loss, hangs, broken lifecycle, or state inconsistencies.

Tasks:
- Reproduce the dropped-file dialog hang if present.
- Audit Qt signal/slot thread boundaries, dialog execution, worker cancellation, and shutdown.
- Recheck magnet lifecycle, existing-file verification, path normalization, metrics attribute assumptions, and cleanup safety.
- Add regression tests before or alongside minimal fixes.

Likely files: `gui/main_window.py`, `gui/torrent_dialog.py`, `gui/torrent_page.py`, `gui/torrent_details.py`, `core/torrent.py`, `core/cleanup.py`, related tests.  
Completion: focused regressions pass repeatedly; no tests weakened.

## Phase 2 — Audit and validate the multi-network engine

**Goal:** prove scheduler correctness and avoidable performance regressions.

Tasks:
- Trace interface discovery, source binding, range workers, scheduler observations, metrics, and error handling.
- Verify actual response validation and output assembly.
- Run `tests.test_multinet` and benchmark scenarios.
- Add missing tests for unstable interfaces, range failures, stale metrics, and recovery.
- Compare fastest-alone versus fast-plus-slow using controlled conditions.
- Document OS routing limitations and real hardware validation requirements.

Likely files: `core/scheduler.py`, `core/downloader.py`, `core/network_monitor.py`, `tests/test_multinet.py`, `tests/netsim.py`, `tests/bench_multinet.py`, README/docs.  
Completion: deterministic tests pass and benchmark claims match outputs.

## Phase 3 — Settings and appearance foundation

**Goal:** implement a consistent theme configuration without affecting downloads.

Tasks:
- Centralize palette tokens and define approved accent presets.
- Add appearance settings with backward-compatible defaults.
- Persist selection using the existing settings JSON flow.
- Apply theme updates without rebuilding the engine or losing active jobs.
- Add contrast, keyboard, restart-persistence, and active-download tests.

Likely files: `gui/theme.py`, `gui/settings_dialog.py`, `gui/main_window.py`, GUI tests.  
Completion: every preset persists and applies consistently; no active-download regression.

## Phase 4 — Live animated network-combiner dashboard

**Goal:** implement the reference-inspired visualization using real metrics.

Tasks:
- Define a bounded throughput-history model and clear units.
- Render per-adapter paths, central combiner, total rate, chart, and contribution rows.
- Animate only active contributors; show idle/offline states accurately.
- Bound update frequency/history and implement reduced-motion behavior.
- Test resize/scaling, UI responsiveness, thread safety, and cleanup.

Likely files: `gui/` widgets, `gui/theme.py`, `gui/main_window.py`, GUI tests.  
Completion: live metrics are accurate, animations are optional and inexpensive, and existing navigation/actions remain functional.

## Phase 5 — Full regression and platform matrix

**Goal:** verify integration after changes.

Tasks:
- Run compile/import checks.
- Run focused tests and related modules.
- Run the complete suite on supported Python versions.
- Run multi-network benchmark and record medians and hash verification.
- Test packaging/startup and inspect resource cleanup.

Completion: no unexplained failures/hangs; limitations documented with evidence.

## Phase 6 — Security, dependency, and MIT release review

**Goal:** prepare accurate release metadata.

Tasks:
- Review path handling, redirects, TLS, logging, and dependency risks.
- Verify copyright ownership and complete MIT text.
- Inventory direct/transitive dependency licenses and packaged assets.
- Confirm third-party notices and packaging obligations.
- Scan for secrets and inspect release contents.

Completion: no unresolved ownership ambiguity presented as resolved; all known obligations documented.

## Phase 7 — Final review and release decision

**Goal:** produce a truthful readiness verdict.

Tasks:
- Review full diff and `git diff --check`.
- Confirm all planned docs match implemented behavior.
- Update test/bug reports with exact commands, counts, and limitations.
- Check Git status and remote state; do not push without authorization.

Completion: verdict is READY, READY WITH WARNINGS, or NOT READY, backed by evidence.

## Recommended first task

Start with Phase 0: inspect the actual checkout and establish the baseline before editing code. Do not begin the GUI redesign or scheduler tuning until the baseline and critical hanging test are understood.
