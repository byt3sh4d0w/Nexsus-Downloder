# Nexus Downloader — version history notes (moved from README.md)

Per-release engineering notes written during v10.1–v10.5. The README now only describes the current state; see CHANGELOG.md for a summary.

Run: `pip install -r requirements.txt` then `python main.py`
Tests: `QT_QPA_PLATFORM=offscreen python -m unittest discover -s tests -v`

## Running on Windows

Double-click `run_nexus.bat`. First run creates `.venv` and installs `requirements.txt`; later runs just start the app.
Manual: `pip install -r requirements.txt` then `python main.py`. Torrents need `libtorrent` (in requirements.txt; wheels exist for Python 3.10-3.13).

## Multi-network HTTP downloads (v10.5)

Version lives in one place: `core/version.py` (`APP_VERSION`). Settings, history DB schema and user data are unchanged.

**What was wrong (v10.4.1).** `Downloader._multi` cut a file into roughly one range per connection (a few big ranges below ~128 MB), pinned each worker to one adapter forever (`candidates[worker_id % len]`) and opened a new connection per range. With unequal adapters the slow one kept a large range and the whole job waited for it (8 + 1 MB/s, 24 MB: 12.3 s vs 3.3 s for the fast adapter alone).

**What v10.5 does** (`core/scheduler.py`, `core/downloader.py`):
- Shared queue of small byte ranges (512 KiB - 16 MiB, ~8 per connection). A worker that finishes claims the next range at once; each range has exactly one owner, so no two writers ever touch the same bytes and nothing is stolen mid-write.
- Per-adapter throughput is measured (sliding window over real bytes). Fast adapters naturally take more ranges. A slow adapter is only held back near the tail, when its range would finish long after the whole job at the combined rate, never excluded on one noisy sample.
- Failures: bounded attempts per range (6), exponential backoff with jitter, per-adapter circuit breaker with cooldown and automatic re-admission. If every selected adapter vanishes the download errors out; it never silently falls back to the default route.
- Adapters appearing mid-download are adopted (list re-read every ~2 s). Virtual adapters (VPN, VM, container, loopback-like names) are listed but off by default and never counted as independent bandwidth. Manual selection is respected; routes, VPN and firewall are never touched.
- Correctness: `Content-Range` and length checks, 200/206/416 handling, ignore-Range servers fall back to a single stream, ETag/Last-Modified captured and sent as `If-Range`; a file that changes mid-download falls back to a single stream instead of mixing versions. Part files are merged to a temp file then atomically renamed; disk errors are reported, not retried forever.
- Networks page shows per-adapter type, selected/virtual state, measured speed, contribution and failures.

**Limits (please read).**
- Combining only helps when adapters are truly independent paths. Behind one shared uplink or a server-side cap there is no gain, and combined is not guaranteed to beat the fastest adapter alone.
- Binding uses the adapter's source IP and the app verifies the bound address. On Windows a source IP does not strictly force the egress NIC (routing table decides). `IP_UNICAST_IF` is not implemented because it could not be tested here.
- Torrents use libtorrent's own networking. The HTTP scheduler does not aggregate bandwidth for torrents.
- Results in tests/benchmarks come from a loopback simulation (`tests/netsim.py`, `tests/bench_multinet.py`), not real Wi-Fi/Ethernet hardware.

**Verify on Windows with real hardware.**
1. Enable Wi-Fi and Ethernet on different networks/uplinks; open Networks and tick both.
2. Download a large file from a fast server with each adapter alone (untick the other), then both. Note times.
3. While it runs, watch per-adapter traffic in Task Manager > Performance (Ethernet and Wi-Fi graphs) to confirm both carry data.
4. Unplug one adapter mid-download: it should continue on the other and finish with a correct file (compare SHA-256 with `Get-FileHash`).

Benchmark: `python tests/bench_multinet.py 3`. Multi-network tests: `python -m unittest tests.test_multinet`.

## v10.1 bug-fix round (root causes)

- **Rows "disappearing"**: the search box kept stale text (or an old status chip stayed selected) and hid the new row, while the
  All/Active counters (read from the database) still counted it. Adding a download now clears whatever hides it, selects it, and
  a banner ("Showing 1 of 3 ...") plus a "Show all downloads" button announces any hidden rows. Search also matches the link.
- **Duplicates**: links are compared after normalisation (`core/links.py`: scheme/host case, default ports, fragments, escape
  spelling) but path and query (signed tokens) are kept, so differently signed URLs are not duplicates. A duplicate shows its
  status and relevant actions; "Download again" is an explicit choice. A different link to an existing file name is never
  overwritten: Nexus offers `name (1).ext`. Torrents/magnets are matched by info-hash before the engine is touched.
- **Connections**: the Add Download value was never stored (footer showed the global default). It is now saved per download
  (`connections` column), used on start/resume/restart, shown in Details and under the list; Settings only change defaults.
- **Actions**: the toolbar acts on the selection; each row only has a More menu (state actions as text, file actions with icons).
- **Settings**: General / Downloads / Network / Torrents / Advanced; every control is saved and applied. Removed the old
  checkbox that did nothing. The torrent listen port and rate limits now really reach libtorrent.

## v10.1 UI redesign

- Dark charcoal desktop UI with sidebar (Downloads, Torrents, Networks, History), toolbar, search and status filters.
- Download rows are fully read-only; no internal ID is shown (Details > "Show advanced details" only).
- Per-row Start/Resume, Pause, Stop (Cancelled, keeps partial data), Retry and a More menu
  (open file, open folder, details, copy link, remove from list, delete files). Remove and delete are separate.
- Speeds, sizes, ETA and elapsed time are computed from the real engine; nothing is fabricated.

Known limitations: tested on Linux (offscreen Qt), not on real Windows/high-DPI; no row-removal animation;
HTTP downloads show download speed only (upload exists for torrents).

## v10.1 fixes

**Cleanup (`core/cleanup.py`, `gui/main_window.py`)**
- *Remove from List* stops the worker, waits for it, keeps the finished file, removes only temp artifacts (`.part`, `.merge`, `.parts/` + `layout.txt`) and the DB/UI entry.
- *Delete Downloaded Files* also deletes the final file. Every failure is listed in a warning. If a file can't be deleted the record is kept so you can retry; leftovers of a plain "remove from list" go to a `pending_cleanup` table and are retried at next start.
- Only exact Nexus temp names derived from the recorded path are touched. Foreign files inside a `.parts` folder, similarly named files and files of other downloads are never deleted; symlinks/junctions are unlinked, not followed. Windows lock errors are retried briefly.
- If a worker does not stop within the timeout, nothing of that item is deleted and a warning is shown. Duplicate selections/re-entrant calls are ignored.

**Worker shutdown** — `stop()` also closes open HTTP responses, so a blocked read wakes immediately. Merge checks the stop flag.

**Range integrity (`core/downloader.py`)**
- Every ranged reply is validated: HTTP 206, `Content-Range` start/end/total must match the request. A 200 reply is never appended to a partial file.
- Single-stream: offset re-read from disk before every retry/adapter failover; bad range reply discards the partial and restarts from byte 0; final size is checked before completion.
- Multi-connection: untrustworthy ranges fall back to one safe stream. `layout.txt` (v2) stores size/part-count/part-size; a changed layout wipes stale parts. Each part's size is verified before merging; parts and layout are deleted only after a successful merge; a failed merge keeps recoverable parts.

**Filename resolver** — each lookup carries a request id + URL; results for an older URL are ignored. A name the user typed is never overwritten. Names come from Content-Disposition (`filename*` too), signed-URL query parameters, then the final redirected URL.

**Torrents** — saved magnets are restored; duplicates are not added twice; missing `.torrent` files and failures are reported in a dialog. Removing a task never deletes data; "Delete Torrent + Files" keeps the record if libtorrent refuses.

Bluetooth stays excluded from network aggregation.

## Torrent fix (v10.2)
- **Why torrents were "disabled"**: `libtorrent` was commented out in `requirements.txt`, and `run_nexus.bat` skipped reinstalling once `.venv\.deps_ok` existed, so the engine was never installed. It is now a declared dependency (marker renamed so existing installs pick it up once); if it cannot be installed (e.g. Python 3.14) the app still starts and the Torrents tab says why.
- **Engine fixes** (`core/torrent.py`): paused torrents now show *Paused* with 0 B/s (they used to read "Downloading"); torrents are no longer auto-managed, so libtorrent's queue cannot override Pause/Resume; engine errors surface as *Failed* with the reason as tooltip; invalid magnets / missing or corrupt `.torrent` files give a clear message; the duplicate check also runs before the engine is touched, so a magnet and its `.torrent` are the same torrent.
- **Restart**: resolved metadata is saved to `~/NexusDownloader/torrents/<hash>.torrent`, so a restored magnet no longer needs DHT/peers (or the original `.torrent`) to come back; restored torrents start paused and added paused (no brief traffic).
- Tests: `tests/test_torrent.py` runs the real engine against a local throttled seeder.

## Torrent page and Add Torrent dialog (v10.3)
- **Add Torrent dialog**: the browse button now sits on the *Magnet / .torrent* field (it used to sit under *Save to*, labelled "Choose folder", and no folder picker existed). Separate buttons pick the `.torrent` file (filters: Torrent files, All files) and the save folder. Input is validated live (bad magnet, missing/corrupt file, wrong type) and **Add Torrent** stays disabled until it is valid. Picker errors are shown, not swallowed; Cancel keeps the previous choice.
- **Drag & drop**: drop a `.torrent` file or magnet link on the Torrents page (dashed drop indicator) or on the dialog. A page drop opens Add Torrent pre-filled; other files are refused with a message. Only one dialog can be open, and duplicates are caught by info-hash, so dropping the same file twice never adds it twice. The Downloads page behaves as before.
- **Torrents page**: same table look as Downloads; one row per torrent (name, size, status, progress, down/up, ETA, peers, seeds, save path) with a per-row "⋯" menu and right-click menu: Start/Resume, Pause, Open folder, Copy magnet link, Remove from list, Delete torrent + files. The bottom toolbar and the second "Add" button are gone; empty state included. Low-priority columns hide as the window narrows. Stop is not offered: BitTorrent has no stop distinct from pause.


## Professional torrent client (v10.4)
- **New torrent dialog**: shows the real file tree *before* anything downloads. `.torrent` files list at once (nested folders, sizes); magnets join the swarm for **metadata only** (nothing is written, no fake list) and the tree appears when peers deliver it, with a 90 s timeout message. Tri-state folder checkboxes, Select all/none, name filter, per-file priority (Low/Normal/High/Maximum; unchecked = skipped), selected/total size, file count, free disk space, save-folder validation. Buttons: **Download**, **Download later** (adds paused), **Cancel** (cancels the metadata fetch; nothing is left behind). The `.torrent` picker and the folder picker are separate buttons.
- **File conflicts (HTTP + torrents, one dialog)**: *Rename* (`name (1).ext`, `(2)`… — for torrents the engine really writes to the renamed path), *Overwrite* (asks again; disabled for a file another running download/torrent is using), *Abort*, plus *Use existing file* for torrents (the engine verifies it piece by piece). "Remember my choice" is stored in settings and can be reset under Settings → Advanced. Torrents are checked per file, with "apply to the remaining files". HTTP behaviour is unchanged by default: it still never overwrites unless you explicitly choose Overwrite.
- **Details panel** (select a row → double-click or ⋯ → *Show details*): GENERAL, DETAILS, PROGRESS, FILES, CONNECTIONS, refreshed every second from the engine; anything the engine does not know reads N/A. File priorities can be changed live from the FILES tab.
- **Resume**: resume data (verified pieces, file priorities, renames, metadata) is saved after adding, every ~30 s, and on exit; restored torrents are re-checked by the engine, so moved/deleted data is noticed. Restored torrents stay paused unless *Resume torrents automatically* is on; a torrent you paused stays paused either way.
- **Row menu** is state-aware (Start/Resume *or* Pause, Show details, Force recheck, Open folder, Copy magnet, Remove, Delete + files). Duplicates are detected by info-hash (file or magnet). ETA comes from the engine and is "—" when unknown.
- Tests: `tests/test_torrent_flow.py` (new), updated `test_torrent.py` / `test_regressions.py`.

## Project documentation

Start with these source-of-truth documents before making changes:

- [`PRD.md`](PRD.md) — product scope and requirements.
- [`DESIGN.md`](DESIGN.md) — desktop visual system and planned animated combiner/theme behavior.
- [`ARCHITECTURE.md`](ARCHITECTURE.md) — current modules and data flow.
- [`DATABASE.md`](DATABASE.md) — current SQLite schema and migration approach.
- [`API.md`](API.md) — actual internal contracts and external protocol integrations; no public REST API is currently defined.
- [`DATA_SOURCES.md`](DATA_SOURCES.md) — metric sources and their limitations.
- [`SECURITY_MODEL.md`](SECURITY_MODEL.md) — trust boundaries and security controls to verify.
- [`TEST_PLAN.md`](TEST_PLAN.md) — tests and acceptance criteria.
- [`AGENTS.md`](AGENTS.md) — coding-agent rules and safe change discipline.
- [`IMPLEMENTATION_PLAN.md`](IMPLEMENTATION_PLAN.md) — phased plan for audit, fixes, GUI enhancements, and release review.
- [`AGENT_WORKFLOW_PROMPTS.md`](AGENT_WORKFLOW_PROMPTS.md) — 20 task prompts adapted to this desktop project.

Documents distinguish observed v10.5 behavior from proposed work. A requirement described as planned is not proof that it is implemented.
