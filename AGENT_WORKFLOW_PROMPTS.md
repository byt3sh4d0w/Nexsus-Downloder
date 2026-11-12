# Agent Workflow Prompt Library — Nexus Downloader v10

These prompts are tailored to this local PySide6/Python desktop application. Read `AGENTS.md` and relevant project documents before using any prompt. Replace bracketed fields with the actual task. These prompts are instructions, not evidence that work has been completed.

## 1. PRD creation
Read the existing repository and create/update `PRD.md` describing the actual download manager, user journeys, requirements, error states, MVP boundaries, and approval-needed assumptions. Do not invent cloud accounts, web pages, or server features.

## 2. Design system
Use `PRD.md` and the reference dashboard image to maintain `DESIGN.md`. Specify desktop layout, palette tokens, typography, animation behavior, accessibility, and persisted accent presets. Mark proposed features separately from implemented behavior. Do not write application code during a documentation-only task.

## 3. Architecture
Read PRD and DESIGN, inspect the real modules, and update `ARCHITECTURE.md`. Preserve PySide6, `httpx`, `psutil`, SQLite, and libtorrent boundaries unless evidence supports an approved change. Do not invent a frontend/backend server.

## 4. Database design
Inspect `database/db.py` and write/update `DATABASE.md` using the actual SQLite schema and migrations. Preserve user data and describe only justified indexes and migrations. Do not write application code during documentation-only work.

## 5. API/internal contracts
Inspect real engine, GUI, database, and monitor call boundaries. Update `API.md` without inventing REST endpoints. Describe HTTP/HTTPS and libtorrent integrations and internal error/state contracts.

## 6. Agent coding rules
Maintain `AGENTS.md` with source-of-truth order, safe Git discipline, root-cause debugging, dependency policy, security boundaries, and test/report requirements.

## 7. Implementation planning
Read all source-of-truth documents and produce a small-phase plan. Each phase must include goal, likely files, prerequisites, tests, and completion criteria. Do not implement code in a planning-only task.

## 8. Project setup
Inspect the current tree before changing setup. Verify dependency declarations, startup scripts, configuration, and tests. Do not recreate or replace a working project scaffold.

## 9. Feature implementation
Task: [FEATURE]. Inspect callers and tests, state a short implementation approach, implement only the approved feature, handle failure states, add regression tests, and report changed files and actual commands/results.

## 10. Database/backend work
Task: [DATABASE OR ENGINE CHANGE]. Follow DATABASE.md and ARCHITECTURE.md. Preserve migrations and local data; use parameterized SQL, bounded transactions, and tests against temporary databases.

## 11. Authentication/authorization review
The current product is a single-user local desktop application and has no account system. Do not implement signup/login by default. Audit OS-permission boundaries and local file access; ask for approval before introducing remote/multi-user functionality.

## 12. MCP connection
Verify the requested MCP server's real tools and permissions. For FreeLLMAPI, perform an actual inference request before claiming use. Never expose keys; report the exact failure if unavailable. MCP is for coding workflow, not a runtime dependency unless explicitly approved.

## 13. External service integration
Task: [SERVICE]. Verify that the service is actually required by PRD. Keep credentials out of source/settings, use timeouts and safe retries, validate responses, add tests, and document operational limitations.

## 14. Testing and QA
Read TEST_PLAN.md, run critical user journeys and failure cases, create meaningful unit/integration/Qt tests, and report exact counts and commands. Never weaken or skip failing tests to obtain a pass.

## 15. Root-cause debugging
Problem: [BUG/LOG/TEST]. Reproduce it, inspect relevant code and state transitions, identify the root cause, implement the smallest correct fix, add a regression test, and rerun focused plus related tests. If it cannot be reproduced, report evidence and uncertainty.

## 16. Security audit
Review SECURITY_MODEL.md and inspect URL parsing, TLS, redirects, file paths, torrent metadata, cleanup, concurrency, logs, settings, and dependencies. Rank findings by severity, fix verified issues, test fixes, and document residual risks.

## 17. Code review/refactoring
Review the current diff against PRD, DESIGN, ARCHITECTURE, and AGENTS. Fix only justified correctness, safety, or maintainability problems. Do not rewrite working code for style; run regression tests.

## 18. Deployment/packaging
Prepare only the approved desktop packaging targets. Verify build/start commands, dependencies, runtime data paths, license notices, and clean-machine behavior. Do not deploy a web service because none is currently part of the architecture.

## 19. Monitoring/maintenance
For a local desktop application, focus on actionable logs, safe diagnostics, bounded metrics history, crash/failure visibility, and backup/recovery guidance. Do not add cloud telemetry or collect personal data without explicit approval.

## 20. Final production audit
Audit every PRD requirement against implementation and test evidence. Return READY, READY WITH WARNINGS, or NOT READY. List blockers, tests run, unsupported environments, licensing gaps, and manual real-hardware checks. Code existence alone is not proof of behavior.

## Master execution guardrails

- Verify FreeLLMAPI MCP and a genuine inference call before claiming use.
- Read the required skills that actually exist in the configured Skills directory; report missing files honestly.
- Inspect Git status and preserve user changes.
- Reproduce before fixing; add regression tests.
- Protect adaptive multi-network scheduling and verify fast-plus-slow behavior using controlled simulations and, where possible, real hardware.
- Make the reference-inspired animated GUI data-driven and add persistent accent presets only through tested settings flows.
- Never fabricate test passes, throughput measurements, license compliance, or push status.
