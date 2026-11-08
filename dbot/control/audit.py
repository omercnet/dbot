"""Append-only, hash-chained JSONL audit log with one file per UTC day.

Each record carries ``seq``, ``prev_hash`` and ``hash = sha256(prev_hash + canonical_json(record_without_hash))``.
The chain continues across files: the first record of a day links to the last record of the previous file.
Known secret values are redacted before hashing, so they never reach disk.
"""

import fcntl
import hashlib
import json
import os
import re
import threading
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from dbot.control.config import data_dir
from dbot.control.secrets import redact

GENESIS_HASH = "0" * 64
RESERVED_KEYS = frozenset({"ts", "seq", "prev_hash", "hash"})
_FILE_RE = re.compile(r"^audit-(\d{4}-\d{2}-\d{2})\.jsonl$")


def canonical_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)


def _hash(prev_hash: str, record_without_hash: dict[str, Any]) -> str:
    return hashlib.sha256((prev_hash + canonical_json(record_without_hash)).encode("utf-8")).hexdigest()


def _day_files(directory: Path) -> list[Path]:
    return sorted(p for p in directory.iterdir() if _FILE_RE.match(p.name)) if directory.is_dir() else []


def _last_line(path: Path) -> bytes:
    """Last non-empty line of ``path`` (b"" if none), reading backwards from the end."""
    with path.open("rb") as f:
        f.seek(0, os.SEEK_END)
        pos = f.tell()
        buf = b""
        while pos > 0:
            step = min(8192, pos)
            pos -= step
            f.seek(pos)
            buf = f.read(step) + buf
            stripped = buf.rstrip(b"\r\n")
            if b"\n" in stripped:
                return stripped.rsplit(b"\n", 1)[1]
        return buf.strip()


def _default_clock() -> datetime:
    return datetime.now(UTC)


class AuditLog:
    """Thread- and process-safe appender for ``<dir>/audit-YYYY-MM-DD.jsonl``."""

    def __init__(self, directory: Path | None = None, clock: Callable[[], datetime] = _default_clock) -> None:
        self.directory = directory or data_dir() / "audit"
        self._clock = clock
        self._lock = threading.Lock()

    def _tail(self) -> tuple[Path | None, int, str]:
        """(latest file, last seq, last hash) across all day files."""
        for path in reversed(_day_files(self.directory)):
            line = _last_line(path)
            if line:
                last = json.loads(line)
                return path, int(last["seq"]), str(last["hash"])
        return None, 0, GENESIS_HASH

    def append(self, event: dict[str, Any]) -> dict[str, Any]:
        """Redact, chain and persist ``event``; return the stored record (including ``hash``)."""
        clash = RESERVED_KEYS & event.keys()
        if clash:
            raise ValueError(f"audit event uses reserved keys: {sorted(clash)}")
        now = self._clock().astimezone(UTC)
        self.directory.mkdir(parents=True, exist_ok=True)
        with self._lock, (self.directory / ".audit.lock").open("a") as lock_file:
            fcntl.flock(lock_file, fcntl.LOCK_EX)
            try:
                latest, seq, prev_hash = self._tail()
                path = self.directory / f"audit-{now:%Y-%m-%d}.jsonl"
                # A clock that steps back must not write into an earlier file and fork the chain order.
                if latest is not None and latest.name > path.name:
                    path = latest
                body = {**redact(event), "ts": now.isoformat(), "seq": seq + 1, "prev_hash": prev_hash}
                # Round-trip so the hashed form is exactly what verify() will parse back.
                record: dict[str, Any] = json.loads(canonical_json(body))
                record["hash"] = _hash(prev_hash, record)
                with path.open("a", encoding="utf-8") as f:
                    f.write(canonical_json(record) + "\n")
                    f.flush()
                    os.fsync(f.fileno())
            finally:
                fcntl.flock(lock_file, fcntl.LOCK_UN)
        return record


def _lines(paths: list[Path]) -> Iterator[str]:
    for path in paths:
        with path.open(encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    yield line


def verify(path: str | Path) -> tuple[bool, int | None]:
    """Check the chain in one day file or a whole audit directory. Returns (ok, first bad seq).

    A directory must start at seq 1 from the genesis hash. A single file is anchored on its first record's
    ``seq`` and ``prev_hash``, so deleting that file's first line is only detectable at directory level.
    """
    p = Path(path)
    paths = _day_files(p) if p.is_dir() else [p]
    expected_seq: int | None = 1 if p.is_dir() else None
    prev_hash: str | None = GENESIS_HASH if p.is_dir() else None
    for line in _lines(paths):
        try:
            record = json.loads(line)
            seq = record["seq"]
            stored_hash = record.pop("hash")
            if expected_seq is None:
                expected_seq, prev_hash = int(seq), str(record["prev_hash"])
            ok = (
                type(seq) is int
                and seq == expected_seq
                and record["prev_hash"] == prev_hash
                and stored_hash == _hash(str(prev_hash), record)
            )
        except (ValueError, KeyError, TypeError, AttributeError):
            ok = False
        if not ok:
            return False, expected_seq if expected_seq is not None else 1
        prev_hash = stored_hash
        expected_seq = seq + 1
    return True, None
