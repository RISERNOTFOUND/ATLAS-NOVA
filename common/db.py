"""SQLite storage with migrations, validated backups and per-guild settings.

* One connection per process, guarded by a lock (sqlite3 calls here are short).
* Every pending migration triggers a validated backup of the database first.
* Settings are stored as JSON per (guild_id, module) so every server is isolated.
"""
import json
import shutil
import sqlite3
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

KEEP_BACKUPS = 20


class Database:
    def __init__(self, path, backup_dir):
        self.path = Path(path)
        self.backup_dir = Path(backup_dir)
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.conn = sqlite3.connect(str(self.path), check_same_thread=False, isolation_level=None, timeout=15)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.execute("PRAGMA busy_timeout=15000")
        self.conn.execute("CREATE TABLE IF NOT EXISTS _migrations (name TEXT PRIMARY KEY, applied_at TEXT NOT NULL)")
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS settings (guild_id INTEGER NOT NULL, module TEXT NOT NULL, data TEXT NOT NULL,"
            " updated_at TEXT NOT NULL, PRIMARY KEY (guild_id, module))")
        self._backed_up_this_run = False
        self.errors = 0

    # ------------------------------------------------------------ basic access
    def execute(self, sql, params=()):
        with self.lock:
            return self.conn.execute(sql, params)

    def executemany(self, sql, rows):
        with self.lock:
            return self.conn.executemany(sql, rows)

    def fetchone(self, sql, params=()):
        with self.lock:
            return self.conn.execute(sql, params).fetchone()

    def fetchall(self, sql, params=()):
        with self.lock:
            return self.conn.execute(sql, params).fetchall()

    def scalar(self, sql, params=(), default=None):
        row = self.fetchone(sql, params)
        return default if row is None or row[0] is None else row[0]

    def insert(self, sql, params=()):
        with self.lock:
            return self.conn.execute(sql, params).lastrowid

    @contextmanager
    def transaction(self):
        with self.lock:
            self.conn.execute("BEGIN IMMEDIATE")
            try:
                yield self.conn
            except BaseException:
                self.conn.execute("ROLLBACK")
                raise
            else:
                self.conn.execute("COMMIT")

    def claim(self, table, row_id, column="status", expected="pending", new="done"):
        """Atomically flip a row's status. Returns True only for the one caller that wins (duplicate-delivery guard)."""
        with self.lock:
            cur = self.conn.execute(f"UPDATE {table} SET {column}=? WHERE id=? AND {column}=?", (new, row_id, expected))
            return cur.rowcount == 1

    # ------------------------------------------------------------ migrations
    def migrate(self, name, statements):
        """Apply a named migration once. `statements` is SQL text or a callable(conn)."""
        with self.lock:
            if self.conn.execute("SELECT 1 FROM _migrations WHERE name=?", (name,)).fetchone():
                return False
            has_data = self.conn.execute("SELECT count(*) FROM _migrations").fetchone()[0] > 0
            if has_data and not self._backed_up_this_run:
                self.backup("pre-migration")
                self._backed_up_this_run = True
            with self.transaction() as conn:
                if callable(statements):
                    statements(conn)
                else:
                    for statement in [s.strip() for s in statements.split(";") if s.strip()]:
                        conn.execute(statement)
                conn.execute("INSERT INTO _migrations(name, applied_at) VALUES (?, ?)", (name, now_iso()))
            return True

    # ------------------------------------------------------------ backups
    def backup(self, label="manual"):
        """Copy the live DB with SQLite's online backup API, then integrity-check the copy."""
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        target = self.backup_dir / f"{self.path.stem}-{stamp}-{label}.db"
        with self.lock:
            dest = sqlite3.connect(str(target))
            try:
                self.conn.backup(dest)
                result = dest.execute("PRAGMA integrity_check").fetchone()[0]
            finally:
                dest.close()
        if result != "ok":
            target.unlink(missing_ok=True)
            raise RuntimeError(f"Backup validation failed: {result}")
        backups = sorted(self.backup_dir.glob(f"{self.path.stem}-*.db"))
        for old in backups[:-KEEP_BACKUPS]:
            old.unlink(missing_ok=True)
        return target

    def list_backups(self):
        return sorted(self.backup_dir.glob(f"{self.path.stem}-*.db"), reverse=True)

    def integrity(self):
        return self.scalar("PRAGMA integrity_check", default="unknown")

    def table_counts(self):
        names = [r[0] for r in self.fetchall("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        return {n: self.scalar(f"SELECT count(*) FROM \"{n}\"", default=0) for n in names}

    # ------------------------------------------------------------ settings
    def get_settings(self, guild_id, module):
        row = self.fetchone("SELECT data FROM settings WHERE guild_id=? AND module=?", (int(guild_id), module))
        if not row:
            return {}
        try:
            data = json.loads(row["data"])
            return data if isinstance(data, dict) else {}
        except ValueError:
            return {}

    def set_settings(self, guild_id, module, data):
        self.execute(
            "INSERT INTO settings(guild_id, module, data, updated_at) VALUES (?,?,?,?) "
            "ON CONFLICT(guild_id, module) DO UPDATE SET data=excluded.data, updated_at=excluded.updated_at",
            (int(guild_id), module, json.dumps(data), now_iso()))

    def set_setting(self, guild_id, module, key, value):
        with self.lock:
            data = self.get_settings(guild_id, module)
            old = data.get(key)
            data[key] = value
            self.set_settings(guild_id, module, data)
            return old

    def all_settings(self, guild_id):
        return {r["module"]: json.loads(r["data"]) for r in self.fetchall("SELECT module, data FROM settings WHERE guild_id=?", (int(guild_id),))}

    def close(self):
        with self.lock:
            self.conn.close()


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def now_ts():
    return time.time()


def backup_file(path, backup_dir, label):
    """Copy a JSON/data file into backup_dir and verify the copy byte-for-byte. Returns the backup path."""
    path, backup_dir = Path(path), Path(backup_dir)
    if not path.exists():
        return None
    backup_dir.mkdir(parents=True, exist_ok=True)
    target = backup_dir / f"{path.stem}-{datetime.now():%Y%m%d-%H%M%S}-{label}{path.suffix}"
    shutil.copy2(path, target)
    if target.read_bytes() != path.read_bytes():
        target.unlink(missing_ok=True)
        raise RuntimeError(f"Backup of {path.name} did not verify")
    return target
