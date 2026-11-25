"""The durable usage ledger: one SQLite file, standard library only (ADR 026).

Lives in the server layer because it owns a file; the model it fills is
`contexture.core.model.usage`.
"""

from __future__ import annotations

import sqlite3
import threading
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from ..core.model.usage import SubjectUsage, Use, _Coalescer, _score_rows, _Totals, _utc


class SqliteUsageLedger:
    """A durable ledger in one SQLite file (standard library only).

    Writes go to memory first and reach the file in small batches — every
    `batch` uses or `interval` seconds, whichever comes first, on the next use
    or query — so a call never waits for a disk write. Call `flush()` on
    shutdown; uses not yet flushed when a process dies are lost, which costs a
    little evidence and never a business result.
    """

    def __init__(self, path: str | Path, *, coalesce_minutes: float = 10.0, retention_days: int = 90,
                 batch: int = 50, interval: float = 1.0) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(self._path, check_same_thread=False, isolation_level=None)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.executescript("""
            CREATE TABLE IF NOT EXISTS usage_daily (
                subject TEXT NOT NULL, actor TEXT NOT NULL, action TEXT NOT NULL, day TEXT NOT NULL,
                count INTEGER NOT NULL DEFAULT 0, failed INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (subject, actor, action, day));
            CREATE TABLE IF NOT EXISTS usage_last (
                subject TEXT NOT NULL, actor TEXT NOT NULL, action TEXT NOT NULL,
                last_at TEXT NOT NULL, last_principal TEXT NOT NULL,
                PRIMARY KEY (subject, actor, action));
            CREATE INDEX IF NOT EXISTS usage_daily_day ON usage_daily (day);
        """)
        self._coalescer = _Coalescer(timedelta(minutes=coalesce_minutes))
        self._retention = retention_days
        self._batch = max(1, batch)
        self._interval = max(0.0, interval)
        self._pending = _Totals()
        self._pending_count = 0
        self._last_flush = datetime.now(timezone.utc)
        self._expired_on: date | None = None
        self._lock = threading.Lock()

    def add(self, use: Use) -> None:
        at = _utc(use.at)
        stamp, day = at.isoformat(), at.date().isoformat()
        with self._lock:
            for subject in use.subjects:
                key = (subject, use.actor, use.action)
                if use.failed:
                    self._pending.daily.setdefault((*key, day), [0, 0])[1] += 1
                    continue
                previous = self._pending.last.get(key)
                if previous is None or previous[0] <= stamp:
                    self._pending.last[key] = (stamp, use.principal)
                if self._coalescer.counts(use.principal, subject, use.action, at):
                    self._pending.daily.setdefault((*key, day), [0, 0])[0] += 1
            self._pending_count += 1
            if self._pending_count >= self._batch or (datetime.now(timezone.utc) - self._last_flush).total_seconds() >= self._interval:
                self._flush_locked()

    def _flush_locked(self) -> None:
        pending, self._pending, self._pending_count = self._pending, _Totals(), 0
        self._last_flush = datetime.now(timezone.utc)
        if not pending.daily and not pending.last:
            return
        db = self._db
        db.execute("BEGIN")
        try:
            db.executemany(
                "INSERT INTO usage_daily (subject, actor, action, day, count, failed) VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT (subject, actor, action, day) DO UPDATE SET count = count + excluded.count, failed = failed + excluded.failed",
                [(*key, values[0], values[1]) for key, values in pending.daily.items()])
            db.executemany(
                "INSERT INTO usage_last (subject, actor, action, last_at, last_principal) VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT (subject, actor, action) DO UPDATE SET last_at = excluded.last_at, last_principal = excluded.last_principal "
                "WHERE excluded.last_at >= usage_last.last_at",
                [(*key, value[0], value[1]) for key, value in pending.last.items()])
            today = datetime.now(timezone.utc).date()
            if self._expired_on != today:
                db.execute("DELETE FROM usage_daily WHERE day < ?", ((today - timedelta(days=self._retention)).isoformat(),))
                self._expired_on = today
            db.execute("COMMIT")
        except Exception:
            db.execute("ROLLBACK")
            raise

    def flush(self) -> None:
        with self._lock:
            self._flush_locked()

    def query(self, prefix: str = "", *, windows: tuple[int, ...] = (1, 3, 7, 30), actor: str | None = None,
              half_life_days: float = 3.0, write_weight: float = 2.0, now: datetime | None = None) -> list[SubjectUsage]:
        moment = now or _utc()
        oldest = (_utc(moment).date() - timedelta(days=max(windows or (30,)) + 365)).isoformat()
        pattern = prefix.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        with self._lock:
            self._flush_locked()
            rows = self._db.execute(
                "SELECT subject, actor, action, day, count FROM usage_daily WHERE subject LIKE ? ESCAPE '\\' AND day >= ? AND count > 0",
                (pattern, oldest)).fetchall()
            last_rows = self._db.execute(
                "SELECT subject, actor, action, last_at, last_principal FROM usage_last WHERE subject LIKE ? ESCAPE '\\'",
                (pattern,)).fetchall()
        last = {(row[0], row[1], row[2]): (row[3], row[4]) for row in last_rows}
        return _score_rows(rows, last, prefix, windows, actor, half_life_days, write_weight, moment)

    def close(self) -> None:
        with self._lock:
            self._flush_locked()
            self._db.close()


__all__ = ["SqliteUsageLedger"]
