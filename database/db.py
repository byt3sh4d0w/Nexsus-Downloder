import sqlite3
import threading
from pathlib import Path
from datetime import datetime

DB_PATH = Path.home() / "NexusDownloader" / "downloads.db"

class Database:
    def __init__(self, path=DB_PATH):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        try:
            self._open()
        except sqlite3.DatabaseError:
            # Damaged file: keep it for recovery (never delete user data) and start a fresh database
            # instead of crashing at launch.
            getattr(self, "conn", None) and self.conn.close()
            for suffix in ("", "-wal", "-shm"):
                p = Path(str(self.path) + suffix)
                if p.exists():
                    p.replace(Path(f"{self.path}.corrupt-{datetime.now():%Y%m%d-%H%M%S}{suffix}"))
            self._open()

    def _open(self):
        self.conn = sqlite3.connect(self.path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        # WAL + NORMAL: a commit no longer waits for a full disk flush, which
        # is what made every progress update stall the UI on a real disk.
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self._init()

    def _init(self):
        with self._lock:
            self.conn.execute("""
            CREATE TABLE IF NOT EXISTS downloads (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                url TEXT NOT NULL,
                name TEXT NOT NULL,
                path TEXT NOT NULL,
                size INTEGER DEFAULT 0,
                downloaded INTEGER DEFAULT 0,
                status TEXT DEFAULT 'QUEUED',
                created TEXT NOT NULL,
                started TEXT,
                completed TEXT,
                duration REAL DEFAULT 0,
                avg_speed REAL DEFAULT 0,
                peak_speed REAL DEFAULT 0,
                networks_used TEXT DEFAULT '',
                network_bind_ip TEXT DEFAULT '',
                network_mode TEXT DEFAULT 'COMBINED',
                network_ips TEXT DEFAULT '',
                connections INTEGER DEFAULT 0,
                history_hidden INTEGER NOT NULL DEFAULT 0
            )
            """)
            existing = {r[1] for r in self.conn.execute("PRAGMA table_info(downloads)").fetchall()}
            additions = {
                "started": "TEXT",
                "completed": "TEXT",
                "duration": "REAL DEFAULT 0",
                "avg_speed": "REAL DEFAULT 0",
                "peak_speed": "REAL DEFAULT 0",
                "networks_used": "TEXT DEFAULT ''",
                "network_bind_ip": "TEXT DEFAULT ''",
                "network_mode": "TEXT DEFAULT 'COMBINED'",
                "network_ips": "TEXT DEFAULT ''",
                "connections": "INTEGER DEFAULT 0",     # per-download setting; 0 = not chosen yet
                "history_hidden": "INTEGER NOT NULL DEFAULT 0",
            }
            for col, definition in additions.items():
                if col not in existing:
                    self.conn.execute(f"ALTER TABLE downloads ADD COLUMN {col} {definition}")

            self.conn.execute("""
            CREATE TABLE IF NOT EXISTS torrents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                info_hash TEXT UNIQUE NOT NULL,
                source TEXT NOT NULL,
                name TEXT DEFAULT 'Torrent',
                save_path TEXT NOT NULL,
                total INTEGER DEFAULT 0,
                downloaded INTEGER DEFAULT 0,
                status TEXT DEFAULT 'QUEUED',
                created TEXT NOT NULL,
                completed TEXT,
                duration REAL DEFAULT 0,
                avg_download REAL DEFAULT 0,
                peak_download REAL DEFAULT 0
            )
            """)
            # Temp files that could not be deleted when an item was removed from
            # the list; retried on the next start / next removal.
            self.conn.execute("""
            CREATE TABLE IF NOT EXISTS pending_cleanup (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                path TEXT UNIQUE NOT NULL,
                error TEXT DEFAULT '',
                created TEXT NOT NULL
            )
            """)
            self.conn.commit()

    def add_pending_cleanup(self, path, error=""):
        with self._lock:
            self.conn.execute("INSERT OR REPLACE INTO pending_cleanup(path,error,created) VALUES(?,?,?)",
                              (str(path), str(error)[:500], datetime.now().isoformat(timespec="seconds")))
            self.conn.commit()

    def pending_cleanups(self):
        with self._lock:
            return self.conn.execute("SELECT * FROM pending_cleanup ORDER BY id").fetchall()

    def clear_pending_cleanup(self, path):
        with self._lock:
            self.conn.execute("DELETE FROM pending_cleanup WHERE path=?", (str(path),))
            self.conn.commit()

    def paths_in_use(self, exclude_id=None):
        """Target paths of all download records (optionally excluding one)."""
        with self._lock:
            rows = self.conn.execute("SELECT id, path FROM downloads").fetchall()
        return [r["path"] for r in rows if exclude_id is None or int(r["id"]) != int(exclude_id)]

    def add(self, url, name, path, size=0, network_bind_ip="", network_mode="COMBINED", network_ips="", connections=0):
        # network_bind_ip is retained only for migration compatibility with v8.
        # v9 performs automatic multi-network selection at download start.
        with self._lock:
            cur = self.conn.execute(
                """INSERT INTO downloads
                   (url,name,path,size,downloaded,status,created,network_bind_ip,network_mode,network_ips,connections)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (url, name, path, int(size or 0), 0, "QUEUED",
                 datetime.now().isoformat(timespec="seconds"), str(network_bind_ip or ""),
                 str(network_mode or "COMBINED"), str(network_ips or ""), int(connections or 0))
            )
            self.conn.commit()
            return cur.lastrowid

    def update(self, row_id, **kwargs):
        allowed = {"url","name","path","size","downloaded","status",
                   "started","completed","duration","avg_speed","peak_speed","networks_used","network_bind_ip","network_mode","network_ips","connections","history_hidden"}
        pairs = [(k, v) for k, v in kwargs.items() if k in allowed]
        if not pairs:
            return
        with self._lock:
            sql = "UPDATE downloads SET " + ", ".join(f"{k}=?" for k,_ in pairs) + " WHERE id=?"
            self.conn.execute(sql, [v for _,v in pairs] + [row_id])
            self.conn.commit()

    def delete_download(self, row_id):
        with self._lock:
            self.conn.execute("DELETE FROM downloads WHERE id=?", (int(row_id),))
            self.conn.commit()

    def all(self):
        with self._lock:
            return self.conn.execute("SELECT * FROM downloads ORDER BY id DESC").fetchall()

    def get(self, row_id):
        with self._lock:
            return self.conn.execute("SELECT * FROM downloads WHERE id=?", (int(row_id),)).fetchone()

    def queued(self):
        with self._lock:
            return self.conn.execute("SELECT * FROM downloads WHERE status='QUEUED' ORDER BY id DESC").fetchall()

    def counts(self):
        with self._lock:
            return dict(self.conn.execute("SELECT status, COUNT(*) FROM downloads GROUP BY status").fetchall())

    def pause_stale(self):
        """Downloads that were running when the app closed come back paused."""
        with self._lock:
            self.conn.execute("UPDATE downloads SET status='PAUSED' WHERE status IN ('DOWNLOADING','QUEUED')")
            self.conn.commit()


    def get_torrent(self, info_hash):
        with self._lock:
            return self.conn.execute("SELECT * FROM torrents WHERE info_hash=?", (info_hash,)).fetchone()

    def add_torrent(self, info_hash, source, save_path, name="Torrent"):
        with self._lock:
            cur=self.conn.execute("INSERT OR IGNORE INTO torrents(info_hash,source,name,save_path,created) VALUES(?,?,?,?,?)",(info_hash,source,name,save_path,datetime.now().isoformat(timespec="seconds")))
            self.conn.commit()
            row=self.conn.execute("SELECT id FROM torrents WHERE info_hash=?",(info_hash,)).fetchone()
            return row[0]

    def update_torrent(self, info_hash, **kwargs):
        allowed={"source","name","save_path","total","downloaded","status","completed","duration","avg_download","peak_download"}
        pairs=[(k,v) for k,v in kwargs.items() if k in allowed]
        if not pairs:return
        with self._lock:
            self.conn.execute("UPDATE torrents SET "+", ".join(f"{k}=?" for k,_ in pairs)+" WHERE info_hash=?",[v for _,v in pairs]+[info_hash]); self.conn.commit()

    def delete_torrent(self, info_hash):
        with self._lock:
            self.conn.execute("DELETE FROM torrents WHERE info_hash=?", (info_hash,))
            self.conn.commit()

    def all_torrents(self):
        with self._lock:return self.conn.execute("SELECT * FROM torrents ORDER BY id DESC").fetchall()

    def clear_history(self):
        """Hide completed records from History without deleting download records or files."""
        with self._lock:
            self.conn.execute(
                "UPDATE downloads SET history_hidden=1 WHERE status='COMPLETED' AND history_hidden=0"
            )
            self.conn.commit()

    def history(self):
        with self._lock:
            return self.conn.execute(
                "SELECT * FROM downloads WHERE status='COMPLETED' AND history_hidden=0 ORDER BY completed DESC, id DESC"
            ).fetchall()
