"""Approval requests for destructive commands, stored in SQLite.

Every operation opens its own short connection, so the store is safe to share across threads and to call from
asyncio code. ``consume`` is a single conditional UPDATE, which makes it atomic and single-use.
"""

import hashlib
import json
import sqlite3
import time
import uuid
from collections.abc import Callable, Iterator
from contextlib import closing, contextmanager
from datetime import timedelta
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel

from dbot.control.audit import canonical_json
from dbot.control.config import data_dir
from dbot.control.secrets import redact

Status = Literal["pending", "approved", "denied", "consumed", "expired"]
DEFAULT_TTL = timedelta(hours=1)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS approvals (
    id TEXT PRIMARY KEY,
    instance TEXT NOT NULL,
    command TEXT NOT NULL,
    args_json TEXT NOT NULL,
    args_hash TEXT NOT NULL,
    reason TEXT NOT NULL,
    requested_by TEXT NOT NULL,
    on_behalf_of TEXT,
    status TEXT NOT NULL,
    approver TEXT,
    created_at REAL NOT NULL,
    expires_at REAL NOT NULL,
    decided_at REAL,
    consumed_at REAL
)
"""
_COLUMNS = (
    "id, instance, command, args_json, reason, requested_by, on_behalf_of, status, approver,"
    " created_at, expires_at, decided_at, consumed_at"
)


class ApprovalError(ValueError):
    """An approval cannot be decided (unknown, already decided, expired, or self-approval)."""


class Approval(BaseModel):
    id: str
    instance: str
    command: str
    args: dict[str, Any]
    reason: str
    requested_by: str
    on_behalf_of: str | None = None
    status: Status
    approver: str | None = None
    created_at: float
    expires_at: float
    decided_at: float | None = None
    consumed_at: float | None = None


def args_hash(instance: str, command: str, args: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_json([instance, command, args]).encode("utf-8")).hexdigest()


class Approvals:
    def __init__(
        self,
        db_path: Path | None = None,
        ttl: timedelta = DEFAULT_TTL,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.db_path = db_path or data_dir() / "dbot.db"
        self._ttl = ttl.total_seconds()
        self._clock = clock
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute(_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        # Autocommit mode; multi-statement work uses explicit BEGIN IMMEDIATE.
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

    def _row(self, row: tuple[Any, ...]) -> Approval:
        a = Approval(
            id=row[0],
            instance=row[1],
            command=row[2],
            args=json.loads(row[3]),
            reason=row[4],
            requested_by=row[5],
            on_behalf_of=row[6],
            status=row[7],
            approver=row[8],
            created_at=row[9],
            expires_at=row[10],
            decided_at=row[11],
            consumed_at=row[12],
        )
        if a.status in ("pending", "approved") and self._clock() >= a.expires_at:
            a.status = "expired"
        return a

    def request(
        self,
        instance: str,
        command: str,
        args: dict[str, Any],
        reason: str,
        requested_by: str,
        on_behalf_of: str | None = None,
    ) -> Approval:
        """Create a pending approval. ``on_behalf_of`` names the human an agent acts for, if any."""
        now = self._clock()
        approval_id = uuid.uuid4().hex
        with closing(self._connect()) as conn:
            conn.execute(
                "INSERT INTO approvals (id, instance, command, args_json, args_hash, reason, requested_by,"
                " on_behalf_of, status, created_at, expires_at) VALUES (?,?,?,?,?,?,?,?,'pending',?,?)",
                (
                    approval_id,
                    instance,
                    command,
                    # Stored for display only; the hash binds the exact args.
                    canonical_json(redact(args)),
                    args_hash(instance, command, args),
                    redact(reason),
                    requested_by,
                    on_behalf_of,
                    now,
                    now + self._ttl,
                ),
            )
        approval = self.get(approval_id)
        assert approval is not None
        return approval

    def decide(self, approval_id: str, approver: str, approve: bool) -> Approval:
        """Approve or deny a pending request. Nobody may decide a request they made or that was made for them."""
        with self._tx() as conn:
            row = conn.execute(f"SELECT {_COLUMNS} FROM approvals WHERE id = ?", (approval_id,)).fetchone()  # noqa: S608
            if row is None:
                raise ApprovalError(f"approval {approval_id} not found")
            current = self._row(row)
            if current.status != "pending":
                raise ApprovalError(f"approval {approval_id} is {current.status}, not pending")
            if approver in (current.requested_by, current.on_behalf_of):
                raise ApprovalError(f"{approver} cannot decide their own request")
            conn.execute(
                "UPDATE approvals SET status = ?, approver = ?, decided_at = ? WHERE id = ?",
                ("approved" if approve else "denied", approver, self._clock(), approval_id),
            )
        decided = self.get(approval_id)
        assert decided is not None
        return decided

    def get(self, approval_id: str) -> Approval | None:
        with closing(self._connect()) as conn:
            row = conn.execute(f"SELECT {_COLUMNS} FROM approvals WHERE id = ?", (approval_id,)).fetchone()  # noqa: S608
        return self._row(row) if row else None

    def pending(self) -> list[Approval]:
        """Unexpired pending approvals, oldest first."""
        with closing(self._connect()) as conn:
            rows = conn.execute(
                f"SELECT {_COLUMNS} FROM approvals WHERE status = 'pending' AND expires_at > ?"  # noqa: S608
                " ORDER BY created_at",
                (self._clock(),),
            ).fetchall()
        return [self._row(r) for r in rows]

    def consume(self, approval_id: str, instance: str, command: str, args: dict[str, Any]) -> bool:
        """True exactly once for an approved, unexpired approval whose instance/command/args match the request."""
        with closing(self._connect()) as conn:
            cur = conn.execute(
                "UPDATE approvals SET status = 'consumed', consumed_at = ?"
                " WHERE id = ? AND status = 'approved' AND expires_at > ? AND args_hash = ?",
                (self._clock(), approval_id, self._clock(), args_hash(instance, command, args)),
            )
            return cur.rowcount == 1
