import os, threading, time
from dataclasses import dataclass, field
from pathlib import Path

try:
    import libtorrent as lt
    _LT_ERROR = ""
except Exception as _exc:          # keep the real reason (missing wheel vs. broken DLL) for the UI message
    lt = None
    _LT_ERROR = f"{type(_exc).__name__}: {_exc}"


@dataclass
class TorrentInfo:
    name: str = "Torrent"
    total: int = 0
    done: int = 0
    progress: float = 0.0
    download_rate: float = 0.0
    upload_rate: float = 0.0
    peers: int = 0
    seeds: int = 0
    state: str = "QUEUED"
    error: str = ""
    eta: int = -1                  # seconds; -1 = unknown / not meaningful
    uploaded: int = 0
    downloaded: int = 0
    added: float = 0.0


@dataclass
class TorrentFile:
    index: int                     # index inside the torrent (what libtorrent priorities/renames use)
    path: str                      # relative, includes the root folder
    size: int


@dataclass
class TorrentMeta:
    name: str
    total: int
    piece_length: int
    num_pieces: int
    files: list = field(default_factory=list)
    info_hash: str = ""
    trackers: list = field(default_factory=list)
    comment: str = ""
    creator: str = ""
    created: int = 0
    private: bool = False
    num_files_raw: int = 0         # including padding files (priority lists cover all of them)


def _is_pad(fs, i):
    try:
        return bool(fs.pad_file_at(i))
    except Exception:
        return ".pad" in Path(fs.file_path(i)).parts


def meta_of(ti):
    """File list of a torrent_info. Hybrid torrents contain `.pad` filler files: they are hidden from users."""
    fs = ti.files()
    files = [TorrentFile(i, fs.file_path(i), fs.file_size(i)) for i in range(fs.num_files()) if not _is_pad(fs, i)]
    try: created = int(ti.creation_date() or 0)
    except Exception: created = 0
    return TorrentMeta(ti.name(), sum(f.size for f in files), ti.piece_length(), ti.num_pieces(), files, str(ti.info_hash()),
                       [t.url for t in ti.trackers()], ti.comment() or "", ti.creator() or "", created, bool(ti.priv()), fs.num_files())


def _key_of(hashes):
    """Handle key = v1 info-hash when the torrent has one (same value `handle.info_hash()` reports)."""
    return str(hashes.v1) if hashes.has_v1() else str(hashes.get_best())


class TorrentManager:
    """libtorrent-backed manager used by the Nexus Torrent tab."""
    def __init__(self, listen_port=6881, down_kbps=0, up_kbps=0, cache_dir=None):
        if lt is None:
            raise RuntimeError(f"libtorrent could not be loaded ({_LT_ERROR}). Run: python -m pip install libtorrent")
        self.session = lt.session()
        self.configure(listen_port, down_kbps, up_kbps)
        self.handles = {}
        self.previews = {}             # metadata-only magnet handles while the Add dialog is open (never listed, never download)
        self.meta = {}
        self.lock = threading.RLock()
        self._pending_resume = set()
        self.cache_dir = Path(cache_dir) if cache_dir else Path.home() / "NexusDownloader" / "torrents"

    def configure(self, port, down_kbps=0, up_kbps=0):
        """Apply listen port and rate limits (0 = unlimited) to the live session."""
        try:
            self.session.apply_settings({
                "listen_interfaces": f"0.0.0.0:{int(port)},[::]:{int(port)}",
                "download_rate_limit": int(down_kbps) * 1024, "upload_rate_limit": int(up_kbps) * 1024})
        except Exception:
            pass

    @staticmethod
    def info_hash_of(source):
        """Info-hash (as used for handle keys) of a magnet or .torrent *before* it is added; '' if unknown."""
        source = str(source).strip()
        if source.lower().startswith("magnet:"):
            from .links import magnet_info_hash
            return magnet_info_hash(source)
        try:
            return str(lt.torrent_info(source).info_hash())
        except Exception:
            return ""

    @staticmethod
    def is_magnet(value: str) -> bool:
        return value.strip().lower().startswith("magnet:")

    @staticmethod
    def is_torrent_file(value: str) -> bool:
        return Path(value).suffix.lower() == ".torrent"

    # ---- inspecting before adding ----------------------------------------------------------------
    def inspect(self, source):
        """TorrentMeta of a .torrent file; None for a magnet (its metadata must come from peers: see start_preview)."""
        if self.is_magnet(source): return None
        try:
            return meta_of(lt.torrent_info(str(source).strip()))
        except Exception as exc:
            raise ValueError(f"Invalid .torrent file: {exc}") from exc

    def start_preview(self, magnet, save_path):
        """Join the swarm for metadata only (`default_dont_download`: no file is created). -> key."""
        try:
            params = lt.parse_magnet_uri(magnet.strip())
        except Exception as exc:
            raise ValueError(f"Invalid magnet link: {exc}") from exc
        key = _key_of(params.info_hashes)
        if key in self.handles:
            raise ValueError("This torrent is already added.")
        if key in self.previews:
            return key
        params.save_path = str(Path(save_path).expanduser())
        params.flags |= lt.torrent_flags.default_dont_download
        params.flags &= ~(lt.torrent_flags.auto_managed | lt.torrent_flags.paused)
        self.previews[key] = self.session.add_torrent(params)
        return key

    def preview_state(self, key):
        """-> (TorrentMeta | None, peers). None until real metadata has arrived."""
        h = self.previews.get(key)
        if not (h and h.is_valid()): return None, 0
        s = h.status()
        return (meta_of(h.torrent_file()) if s.has_metadata else None), int(s.num_peers)

    def cancel_preview(self, key):
        h = self.previews.pop(key, None)
        if h is not None and h.is_valid():
            try: self.session.remove_torrent(h)          # nothing was written, so no files option is needed
            except Exception: pass

    # ---- adding ---------------------------------------------------------------------------------
    def _resume_file(self, key):
        return self.cache_dir / f"{key}.resume"

    def add(self, source, save_path, paused=False, priorities=None, renames=None, preview=None, restore=False):
        """Add a torrent. priorities: per-file list (0 = skip) over *all* files; renames: {file_index: new_relative_path}.
        preview: key from start_preview() whose live handle is adopted. restore=True: use saved resume data."""
        source = source.strip()
        save_path = str(Path(save_path).expanduser())
        if preview:
            return self._adopt(preview, save_path, paused, priorities, renames, source)
        magnet = self.is_magnet(source)
        if not (magnet or self.is_torrent_file(source)):
            raise ValueError("Torrent source must be a .torrent file or magnet link.")
        try:
            params = lt.parse_magnet_uri(source) if magnet else lt.add_torrent_params()
            if not magnet: params.ti = lt.torrent_info(source)
        except Exception as exc:
            raise ValueError(f"Invalid {'magnet link' if magnet else '.torrent file'}: {exc}") from exc
        key = _key_of(params.info_hashes) if magnet else str(params.ti.info_hash())
        with self.lock:
            if key in self.handles or key in self.previews:
                raise ValueError("This torrent is already added.")
        rf = self._resume_file(key)
        if restore and rf.exists():                       # resume data = verified-piece map, priorities, renames, metadata
            try:
                saved = lt.read_resume_data(rf.read_bytes())
                if saved.ti is None and params.ti is not None: saved.ti = params.ti
                params = saved
            except Exception:
                pass                                       # unreadable -> fall back to the source; libtorrent re-checks the files
        elif rf.exists():
            rf.unlink()                                    # a brand-new add must not inherit stale state
        Path(save_path).mkdir(parents=True, exist_ok=True)
        params.save_path = save_path
        if params.ti is not None and not restore:
            if priorities: params.file_priorities = list(priorities)
            if renames: params.renamed_files = dict(renames)
        # Not auto-managed: libtorrent's queue would otherwise override the user's pause/resume.
        # Default add-flags include `paused` (the queue normally un-pauses), so set it explicitly both ways.
        params.flags &= ~lt.torrent_flags.auto_managed
        if paused: params.flags |= lt.torrent_flags.paused
        else: params.flags &= ~lt.torrent_flags.paused
        handle = self.session.add_torrent(params)
        return self._register(str(handle.info_hash()), handle, source, save_path, dict(params.renamed_files or {}))

    def _adopt(self, key, save_path, paused, priorities, renames, source):
        h = self.previews.pop(key, None)
        if not (h and h.is_valid()):
            raise ValueError("The metadata download was cancelled; add the torrent again.")
        with self.lock:
            if key in self.handles:
                raise ValueError("This torrent is already added.")
        Path(save_path).mkdir(parents=True, exist_ok=True)
        if Path(h.status().save_path) != Path(save_path): h.move_storage(save_path)
        if priorities: h.prioritize_files(list(priorities))
        for i, p in (renames or {}).items(): h.rename_file(int(i), p)
        if paused: h.pause()
        else: h.force_reannounce()                         # the metadata-only phase made us a "seed"; peers dropped us
        return self._register(key, h, source, save_path, {int(i): p for i, p in (renames or {}).items()})

    def _register(self, key, handle, source, save_path, renames=None):
        with self.lock:
            if key in self.handles:
                raise ValueError("This torrent is already added.")
            self.handles[key] = handle
            self.meta[key] = {"source": source, "save_path": save_path, "started": time.time(), "renames": renames or {}}   # libtorrent does not expose renamed paths on torrent_info
        return key, handle

    # ---- control ---------------------------------------------------------------------------------
    def is_paused(self, key):
        h = self.handles.get(key)
        return bool(h and h.is_valid() and h.flags() & lt.torrent_flags.paused)

    def pause(self, key):
        h = self.handles.get(key)
        if h and h.is_valid():
            h.pause(); self.save_resume(key)

    def resume(self, key):
        h = self.handles.get(key)
        if h and h.is_valid(): h.resume()

    def recheck(self, key):
        """Re-verify every piece on disk against the torrent's hashes (never trusts file size alone)."""
        h = self.handles.get(key)
        if h and h.is_valid(): h.force_recheck()

    def set_file_priority(self, key, indices, priority):
        h = self.handles.get(key)
        if h and h.is_valid():
            for i in indices: h.file_priority(int(i), int(priority))
            self.save_resume(key)

    def cache_metadata(self, key):
        """Once metadata is known (magnet resolved, or a .torrent the user may later move), keep our own
        .torrent copy so the torrent restores offline. Returns its path, or None while metadata is missing."""
        h = self.handles.get(key)
        if not (h and h.is_valid() and h.status().has_metadata): return None
        path = self.cache_dir / f"{key}.torrent"
        if not path.exists():
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            path.write_bytes(lt.bencode(lt.create_torrent(h.torrent_file()).generate()))
        return str(path)

    def remove(self, key, delete_files=False):
        """Remove the torrent task. Data files are deleted ONLY when
        delete_files=True. Returns False if libtorrent refused the removal."""
        h = self.handles.get(key)
        if not h: return True
        try:
            self.session.remove_torrent(h, option=(lt.options_t.delete_files if delete_files else 0))
        except Exception:
            if delete_files:
                return False          # never fall back to a plain remove and claim the files are gone
            try: self.session.remove_torrent(h)
            except Exception: return False
        with self.lock:
            self.handles.pop(key, None); self.meta.pop(key, None); self._pending_resume.discard(key)
        for f in (self.cache_dir / f"{key}.torrent", self._resume_file(key)):
            try: f.unlink()
            except OSError: pass
        return True

    # ---- resume data -----------------------------------------------------------------------------
    def save_resume(self, key=None):
        """Ask libtorrent for resume data (async); poll() writes it. Only torrents that changed are asked."""
        for k in ([key] if key else list(self.handles)):
            h = self.handles.get(k)
            if not (h and h.is_valid() and h.status().has_metadata): continue
            try:
                if key is None and not h.need_save_resume_data(): continue
            except Exception:
                pass
            h.save_resume_data(lt.torrent_handle.save_info_dict)
            self._pending_resume.add(k)

    def poll(self):
        """Drain engine alerts (keeps the queue from growing) and persist resume data atomically."""
        for a in self.session.pop_alerts():
            if isinstance(a, lt.save_resume_data_alert):
                key = _key_of(a.params.info_hashes)
                self._pending_resume.discard(key)
                if key not in self.handles: continue        # removed meanwhile: must not resurrect its file
                try:
                    self.cache_dir.mkdir(parents=True, exist_ok=True)
                    rf = self._resume_file(key); tmp = rf.with_suffix(".tmp")
                    tmp.write_bytes(lt.write_resume_data_buf(a.params)); os.replace(tmp, rf)
                except OSError:
                    pass
            elif isinstance(a, lt.save_resume_data_failed_alert):
                self._pending_resume.clear()

    def shutdown(self, timeout=5.0):
        """Flush resume data for every torrent (interrupted-shutdown safe: files are replaced atomically)."""
        for k in list(self.previews): self.cancel_preview(k)
        self.save_resume()
        end = time.time() + timeout
        while self._pending_resume and time.time() < end:
            self.poll(); time.sleep(0.05)
        self.poll()

    # ---- status ----------------------------------------------------------------------------------
    def _info(self, h, s):
        state_map = {
            lt.torrent_status.checking_files: "CHECKING",
            lt.torrent_status.downloading_metadata: "METADATA",
            lt.torrent_status.downloading: "DOWNLOADING",
            lt.torrent_status.finished: "SEEDING",
            lt.torrent_status.seeding: "SEEDING",
            lt.torrent_status.allocating: "ALLOCATING",
            lt.torrent_status.checking_resume_data: "CHECKING",
        }
        state = state_map.get(s.state, "DOWNLOADING")
        err = s.errc.message() if s.errc.value() else ""
        paused = bool(s.flags & lt.torrent_flags.paused)
        if err: state = "ERROR"
        elif paused: state = "COMPLETED" if s.is_finished and s.has_metadata else "PAUSED"
        total = int(s.total_wanted or 0); done = int(s.total_wanted_done or 0)
        # payload rates; a paused torrent moves nothing (libtorrent's averages decay slowly)
        rate = 0.0 if paused else float(s.download_payload_rate or 0)
        eta = int((total - done) / rate) if state == "DOWNLOADING" and rate > 0 and total > done else -1
        return TorrentInfo(name=s.name or "Torrent", total=total, done=done, progress=float(s.progress_ppm or 0) / 10000.0,
                           download_rate=rate, upload_rate=0.0 if paused else float(s.upload_payload_rate or 0),
                           peers=int(s.num_peers or 0), seeds=int(s.num_seeds or 0), state=state, error=err, eta=eta,
                           uploaded=int(s.all_time_upload or 0), downloaded=int(s.all_time_download or 0), added=float(s.added_time or 0))

    def status(self, key):
        h = self.handles.get(key)
        if not h or not h.is_valid():
            return TorrentInfo(state="ERROR", error="Torrent handle is no longer valid")
        return self._info(h, h.status())

    def all_status(self):
        with self.lock:
            keys = list(self.handles)
        return [(k, self.status(k)) for k in keys]

    # ---- details (real engine values; empty when the engine does not know) -------------------------
    def files(self, key):
        """[{index, path, size, done, priority}] for real (non-padding) files; empty before metadata."""
        h = self.handles.get(key)
        if not (h and h.is_valid() and h.status().has_metadata): return []
        ti = h.torrent_file(); prog = h.file_progress(); prios = h.get_file_priorities()
        ren = (self.meta.get(key) or {}).get("renames", {})
        return [{"index": f.index, "path": ren.get(f.index, f.path), "size": f.size, "done": int(prog[f.index]), "priority": int(prios[f.index])}
                for f in meta_of(ti).files]

    def details(self, key):
        h = self.handles.get(key)
        if not (h and h.is_valid()): return None
        s = h.status(); info = self._info(h, s)
        out = {"info": info, "key": key, "save_path": (self.meta.get(key) or {}).get("save_path", ""),
               "source": (self.meta.get(key) or {}).get("source", ""), "has_metadata": bool(s.has_metadata),
               "pieces_have": int(s.num_pieces or 0), "connections": int(s.num_connections or 0),
               "failed_bytes": int(s.total_failed_bytes or 0), "meta": None, "selected": 0,
               "trackers": [], "peer_list": []}
        if s.has_metadata:
            m = meta_of(h.torrent_file()); out["meta"] = m
            prios = h.get_file_priorities(); out["selected"] = sum(1 for f in m.files if prios[f.index] > 0)
        for t in h.trackers():
            eps = t.get("endpoints") or []
            msgs = [e.get("message") for e in eps if e.get("message")]
            fails = max([int(e.get("fails") or 0) for e in eps] or [0])
            err = next((e["last_error"].get("message") for e in eps
                        if isinstance(e.get("last_error"), dict) and e["last_error"].get("value")), None)
            status = ("Error" if (err or fails) else "Working" if (t.get("verified") or msgs or any(e.get("start_sent") for e in eps)) else "Not contacted")
            out["trackers"].append({"url": t.get("url", ""), "status": status, "message": err or (msgs[0] if msgs else "")})
        try:
            out["peer_list"] = [{"addr": f"{p.ip[0]}:{p.ip[1]}", "client": p.client if isinstance(p.client, str) else bytes(p.client).decode("utf-8", "replace"),
                                 "down": int(p.down_speed), "up": int(p.up_speed), "progress": float(p.progress) * 100.0}
                                for p in h.get_peer_info()]
        except Exception:
            pass
        return out
