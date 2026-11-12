# Coding Agent Rules — Nexus Downloader v10

These rules apply to every coding agent and contributor working in this repository.

## 1. Source of truth

Read `PRD.md`, `DESIGN.md`, `ARCHITECTURE.md`, `DATABASE.md`, `API.md`, `DATA_SOURCES.md`, `SECURITY_MODEL.md`, and `TEST_PLAN.md` before substantial changes. If they conflict with observed code or user-approved requirements, document the discrepancy and update the relevant document deliberately. Do not silently invent features.

## 2. Inspect before changing

- Confirm repository root, branch, Git status, and existing user changes.
- Read the relevant source, tests, dependencies, and callers before editing.
- Reproduce bugs and identify the root cause before implementing fixes.
- Record baseline results and distinguish historical issues from currently reproduced defects.
- Never assume a previous agent's report is correct without checking evidence.

## 3. Preserve architecture and features

- Keep the PySide6 desktop architecture, HTTP engine, SQLite persistence, and libtorrent integration unless an approved and evidence-based change is required.
- Do not rewrite working subsystems or replace the app with React/Electron/web technology.
- Preserve user data, existing settings, download history, partial files, and backward compatibility.
- Protect the v10 adaptive multi-network scheduler; changes require regression tests and benchmark evidence.
- Do not conflate torrent networking with HTTP multi-range aggregation.

## 4. Code quality

- Follow existing naming and formatting conventions.
- Keep functions cohesive and avoid unnecessary abstraction/dependencies.
- Prefer explicit state transitions and type/shape checks at boundaries.
- Avoid broad silent exception swallowing in critical logic.
- Keep UI rendering separate from network scheduling and persistence.
- Do not block the Qt GUI thread with network or large file operations.

## 5. Network and file correctness

- Preserve TLS verification.
- Validate HTTP range status and headers before writing data.
- Prevent overlapping writes, unsafe path traversal, accidental overwrite, and deletion of unrelated files.
- Use bounded retries, timeouts, concurrency, and shutdown waits.
- Verify final output size and hash where applicable.
- Do not claim a network interface contributed based only on its system-wide counters.
- Do not modify OS routes, firewall, VPN, or privileged settings without explicit user approval.

## 6. UI, animation, and settings

- All metrics must come from real backend state; no fake production values.
- Animations must be bounded, optional/reduced-motion-aware, and must not block transfers or the event loop.
- Centralize palette/theme tokens. Persist new settings using the existing settings mechanism with safe defaults.
- Theme changes must not interrupt active downloads.
- Maintain keyboard accessibility, focus visibility, readable contrast, and non-color-only status indicators.

## 7. Dependencies and configuration

- Inspect `requirements.txt` and platform compatibility before adding dependencies.
- Do not hardcode credentials or commit tokens.
- FreeLLMAPI MCP is a coding-environment tool, not a runtime app dependency.
- Verify a real inference call before claiming FreeLLMAPI was used. If unavailable, report the limitation.

## 8. Testing

- Follow systematic debugging and test-driven development.
- Add a regression test for each confirmed bug.
- Do not skip/delete/soften tests to obtain a green run.
- Do not claim unexecuted tests passed.
- Use local deterministic servers/simulators for protocol and scheduling tests.
- Label simulated benchmarks separately from real hardware measurements.
- Test supported Python versions and Qt offscreen behavior where available.
- If a test hangs, capture evidence and identify the blocking operation rather than merely raising timeouts.

## 9. Git/change discipline

- Preserve uncommitted changes and inspect the diff.
- Never run destructive resets, delete the original repository, rewrite history, or force-push.
- Do not push without authorization.
- Run `git diff --check` and inspect `git status` before finalizing.
- Keep changes scoped and explain deviations.

## 10. Documentation and reporting

- Update documentation when behavior, settings, limits, or requirements change.
- Report exact files, commands, environment, pass/fail/skip counts, and unresolved risks.
- Separate verified facts, assumptions, simulations, and untested claims.
- Never claim official MIT certification; verify copyright ownership and third-party license obligations.
- Ask for approval when a decision affects architecture, supported platforms, licensing ownership, or destructive behavior.
