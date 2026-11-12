# Third-Party Notices — Nexus Downloader

Nexus Downloader's own source is MIT-licensed (see `LICENSE`). Dependencies are **not** relicensed; each keeps its license. Licenses below were read from the installed package metadata (PySide6 6.12.0, httpx 0.28.1, psutil 7.2.2, libtorrent 2.1.1.0, checked 2026-11-03). Nothing is bundled in this repository except the assets listed below: dependencies are installed by `pip`.

## Direct dependencies (`requirements.txt`)

| Package | License (package metadata) | Notes |
|---|---|---|
| PySide6 (+ shiboken6, Essentials, Addons) | LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only | Qt for Python; Qt itself is LGPL-3.0/GPL/commercial |
| httpx | BSD-3-Clause | earlier drafts of this file said Apache-2.0 — that was wrong |
| psutil | BSD-3-Clause | |
| libtorrent | "BSD" (upstream libtorrent is BSD-3-Clause) | earlier drafts said Boost — that was wrong. The pip wheel ships no license file, so confirm against upstream before redistributing binaries |

Transitive runtime dependencies seen in the verified environment: httpcore (BSD-3-Clause), h11 (MIT), anyio (MIT), idna (BSD-3-Clause), sniffio (MIT OR Apache-2.0), certifi (MPL-2.0).

## Bundled assets

| Asset | Origin | License |
|---|---|---|
| `assets/logo.png`, `logo_64.png`, `logo_source.png`, `nexus.ico` | Unknown: earlier text said "provided with the project" | **Unresolved**. Owner must confirm authorship and license before publishing |

No fonts are bundled (the UI uses system fonts).

## Distribution obligations

- **Source from git/pip (current model):** users install PySide6 themselves; nothing extra is required beyond keeping `LICENSE` and this file.
- **If a packaged executable is ever built:** include the LGPL-3.0 text and Qt/PySide6 notices, keep Qt as replaceable shared libraries (do not statically bundle in a way that prevents swapping), include BSD notices for httpx/httpcore/psutil/libtorrent/idna and the MPL-2.0 text for certifi (file-level copyleft: ship its source/notice). Re-check before each release.

## Open questions for the owner

1. Who holds copyright on the Nexus source? `LICENSE` says "Nexus Downloader Contributors" (a placeholder, not a legal person). Replace with the real holder(s); contributors' rights must also be addressed once outside contributions are merged.
2. Authorship/licence of the logo and icon files.
3. Whether any code was copied from other projects (none identified in this review, but not proven).
