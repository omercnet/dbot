"""The alert queue, stored in SQLite next to the approvals table (``data_dir()/dbot.db``).

Same pattern as ``dbot.control.approvals``: one short connection per operation, and multi-statement work under
``BEGIN IMMEDIATE`` so ``claim_next`` hands each queued alert to exactly one caller.
"""

import json
import sqlite3
import time
import uuid
from collections.abc import Callable, Iterator
from contextlib import closing, contextmanager
from pathlib import Path
from typing import Any, Literal, get_args

from pydantic import BaseModel

from dbot.control.audit import canonical_json
from dbot.control.config import data_dir
from dbot.control.secrets import redact

AlertStatus = Literal["queued", "running", "waiting_approval", "done", "failed"]
STATUSES: tuple[str, ...] = get_args(AlertStatus)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS alerts (
    id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    ext_id TEXT NOT NULL,
    severity TEXT NOT NULL,
    title TEXT NOT NULL,
    observables_json TEXT NOT NULL,
    raw_json TEXT NOT NULL,
    status TEXT NOT NULL,
    received_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    report_json TEXT,
    UNIQUE(source, ext_id)
)
"""
_INDEX = "CREATE INDEX IF NOT EXISTS alerts_status ON alerts(status, received_at)"
_COLUMNS = (
    "id, source, ext_id, severity, title, observables_json, raw_json, status, received_at, updated_at, report_json"
)


class AlertRecord(BaseModel):
    id: str  # store id; agents act with on_behalf_of=f"alert:{id}"
    source: str
    ext_id: str  # the sender's alert id
    severity: str
    title: str
    observables: list[dict[str, str]]
    raw: Any
    status: AlertStatus
    received_at: float
    updated_at: float
    report: dict[str, Any] | None = None

    def alert(self) -> dict[str, Any]:
        """The alert as the sender described it (C8 schema)."""
        return {
            "id": self.ext_id,
            "source": self.source,
            "severity": self.severity,
            "title": self.title,
            "observables": self.observables,
            "raw": self.raw,
        }


def _record(row: tuple[Any, ...]) -> AlertRecord:
    return AlertRecord(
        id=row[0],
        source=row[1],
        ext_id=row[2],
        severity=row[3],
        title=row[4],
        observables=json.loads(row[5]),
        raw=json.loads(row[6]),
        status=row[7],
        received_at=row[8],
        updated_at=row[9],
        report=json.loads(row[10]) if row[10] is not None else None,
    )


def _check_status(status: str) -> None:
    if status not in STATUSES:
        raise ValueError(f"unknown alert status {status!r}")


class AlertStore:
    def __init__(self, db_path: Path | None = None, clock: Callable[[], float] = time.time) -> None:
        self.db_path = db_path or data_dir() / "dbot.db"
        self._clock = clock
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute(_SCHEMA)
            conn.execute(_INDEX)

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path, timeout=30, isolation_level=None)

    @contextmanager
    def _tx(self) -> Iterator[sqlite3.Connection]:
        with closing(self._connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                yield conn
            except BaseException:
                conn.execute("ROLLBACK")
                raise
            conn.execute("COMMIT")

    def enqueue(
        self,
        source: str,
        ext_id: str,
        severity: str,
        title: str,
        observables: list[dict[str, str]],
        raw: Any,
    ) -> str | None:
        """Queue a new alert and return its store id, or None if ``(source, ext_id)`` was already received."""
        now = self._clock()
        alert_id = uuid.uuid4().hex
        try:
            with closing(self._connect()) as conn:
                conn.execute(
                    f"INSERT INTO alerts ({_COLUMNS}) VALUES (?,?,?,?,?,?,?,'queued',?,?,NULL)",  # noqa: S608
                    (
                        alert_id,
                        source,
                        ext_id,
                        severity,
                        title,
                        canonical_json(observables),
                        canonical_json(raw),
                        now,
                        now,
                    ),
                )
        except sqlite3.IntegrityError:
            return None
        return alert_id

    def claim_next(self) -> AlertRecord | None:
        """Atomically move the oldest queued alert to ``running`` and return it."""
        with self._tx() as conn:
            row = conn.execute(
                f"SELECT {_COLUMNS} FROM alerts WHERE status = 'queued' ORDER BY received_at, rowid LIMIT 1"  # noqa: S608
            ).fetchone()
            if row is None:
                return None
            now = self._clock()
            conn.execute("UPDATE alerts SET status = 'running', updated_at = ? WHERE id = ?", (now, row[0]))
        record = _record(row)
        record.status = "running"
        record.updated_at = now
        return record

    def set_status(self, alert_id: str, status: str, expect: str | None = None) -> bool:
        """Set the status; with ``expect``, only if the current status equals it. True if a row changed."""
        _check_status(status)
        sql = "UPDATE alerts SET status = ?, updated_at = ? WHERE id = ?"
        params: tuple[Any, ...] = (status, self._clock(), alert_id)
        if expect is not None:
            _check_status(expect)
            sql += " AND status = ?"
            params += (expect,)
        with closing(self._connect()) as conn:
            return conn.execute(sql, params).rowcount == 1

    def set_report(self, alert_id: str, report: dict[str, Any] | str) -> bool:
        """Store the IRReport (a dict, or a JSON object string). True if the alert exists."""
        data = json.loads(report) if isinstance(report, str) else report
        if not isinstance(data, dict):
            raise ValueError("report must be a JSON object")
        with closing(self._connect()) as conn:
            cur = conn.execute(
                "UPDATE alerts SET report_json = ?, updated_at = ? WHERE id = ?",
                (canonical_json(redact(data)), self._clock(), alert_id),
            )
            return cur.rowcount == 1

    def get(self, alert_id: str) -> AlertRecord | None:
        with closing(self._connect()) as conn:
            row = conn.execute(f"SELECT {_COLUMNS} FROM alerts WHERE id = ?", (alert_id,)).fetchone()  # noqa: S608
        return _record(row) if row else None

    def list(self, limit: int = 100) -> list[AlertRecord]:
        """Most recently received first."""
        with closing(self._connect()) as conn:
            rows = conn.execute(
                f"SELECT {_COLUMNS} FROM alerts ORDER BY received_at DESC, rowid DESC LIMIT ?",  # noqa: S608
                (limit,),
            ).fetchall()
        return [_record(r) for r in rows]

    def count_by_status(self) -> dict[str, int]:
        """Every status, including zero counts."""
        counts = dict.fromkeys(STATUSES, 0)
        with closing(self._connect()) as conn:
            for status, n in conn.execute("SELECT status, COUNT(*) FROM alerts GROUP BY status"):
                counts[status] = n
        return counts
