"""Safe cleanup of download artifacts.

Everything this module may delete is derived from the recorded download path
and the downloader's own naming scheme (see core/downloader.py):

    <path>            final file
    <path>.part       single-connection partial file
    <path>.merge      temporary merge output
    <path>.parts/     multi-connection range directory containing only
                      "<n>.part" files and "layout.txt"

Nothing else is ever touched: no globbing on similar names, no recursive
deletes, and symlinks/junctions are unlinked, never followed.
"""
import os
import re
import time
from dataclasses import dataclass, field

_PART_NAME = re.compile(r"^\d+\.part$")
LAYOUT_NAME = "layout.txt"


@dataclass
class CleanupResult:
    removed: list = field(default_factory=list)
    failed: list = field(default_factory=list)    # (path, reason)
    skipped: list = field(default_factory=list)   # (path, reason)

    @property
    def ok(self):
        return not self.failed

    def merge(self, other):
        self.removed += other.removed
        self.failed += other.failed
        self.skipped += other.skipped


def temp_names(path):
    p = str(path)
    return p + ".part", p + ".merge", p + ".parts"


def _is_link(p):
    """True for symlinks and (on Windows) junctions."""
    try:
        if os.path.islink(p):
            return True
        isj = getattr(os.path, "isjunction", None)
        return bool(isj and isj(p))
    except OSError:
        return False


def _unlink(p, res, attempts=4):
    """Delete one file (or link) with short retries for Windows file locks."""
    last = None
    for i in range(attempts):
        try:
            os.unlink(p)
            res.removed.append(p)
            return True
        except FileNotFoundError:
            return True
        except OSError as exc:        # PermissionError / sharing violation
            last = exc
            time.sleep(0.15 * (i + 1))
    res.failed.append((p, f"{type(last).__name__}: {last}"))
    return False


def _remove_parts_dir(d, res):
    if not os.path.lexists(d):
        return
    if _is_link(d):                   # never follow into the link target
        try:
            os.unlink(d)              # symlink
            res.removed.append(d)
        except OSError:
            _rmdir_link(d, res)       # Windows junction
        return
    if not os.path.isdir(d):
        res.skipped.append((d, "not a directory"))
        return
    foreign = False
    try:
        entries = list(os.scandir(d))
    except OSError as exc:
        res.failed.append((d, f"{type(exc).__name__}: {exc}"))
        return
    for e in entries:
        if e.name == LAYOUT_NAME or _PART_NAME.match(e.name):
            _unlink(e.path, res)
        else:
            foreign = True
            res.skipped.append((e.path, "not a Nexus temp file; left in place"))
    if foreign:
        return
    try:
        os.rmdir(d)
        res.removed.append(d)
    except FileNotFoundError:
        pass
    except OSError as exc:
        res.failed.append((d, f"{type(exc).__name__}: {exc}"))


def _rmdir_link(d, res):
    try:
        os.rmdir(d)                   # removes a junction without touching its target
        res.removed.append(d)
    except OSError as exc:
        res.failed.append((d, f"{type(exc).__name__}: {exc}"))


def remove_temp_files(path):
    """Delete .part, .merge and the .parts directory belonging to `path`."""
    res = CleanupResult()
    part, merge, parts = temp_names(path)
    for f in (part, merge):
        if os.path.lexists(f):
            if os.path.isdir(f) and not _is_link(f):
                res.skipped.append((f, "is a directory; not a Nexus temp file"))
            else:
                _unlink(f, res)
    _remove_parts_dir(parts, res)
    return res


def delete_download_files(path):
    """Delete the final file and all temp artifacts of `path`."""
    res = CleanupResult()
    path = str(path)
    if os.path.lexists(path):
        if os.path.isdir(path) and not _is_link(path):
            res.skipped.append((path, "recorded path is a directory; not deleted"))
        else:
            _unlink(path, res)
    res.merge(remove_temp_files(path))
    return res


def _norm(p):
    return os.path.normcase(os.path.abspath(str(p)))


def shared_with_other(path, other_paths):
    """True if another download record uses the same target path."""
    me = _norm(path)
    return any(_norm(o) == me for o in other_paths if o)


def stop_job(job, timeout=8.0):
    """Cancel a Downloader and wait for ALL of its threads. True only when the
    worker is confirmed gone (safe to touch its files)."""
    try:
        job.stop()
        job.wait(timeout)
    except Exception:
        pass
    return not job.is_alive()
