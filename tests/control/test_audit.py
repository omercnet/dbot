import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from dbot.control import secrets
from dbot.control.audit import GENESIS_HASH, AuditLog, verify


class Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 11, 8, 23, 59, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now


def _log(tmp_path: Path, n: int = 5) -> tuple[AuditLog, Path]:
    log = AuditLog(tmp_path, clock=Clock())
    for i in range(n):
        log.append({"event": "invoke", "i": i, "args": {"ip": f"10.0.0.{i}"}})
    return log, tmp_path / "audit-2026-11-08.jsonl"


def _rewrite(path: Path, lines: list[str]) -> None:
    path.write_text("".join(lines), encoding="utf-8")


def test_clean_chain_verifies(tmp_path: Path) -> None:
    _, path = _log(tmp_path)
    assert verify(tmp_path) == (True, None)
    assert verify(path) == (True, None)
    first = json.loads(path.read_text().splitlines()[0])
    assert first["seq"] == 1
    assert first["prev_hash"] == GENESIS_HASH


def test_tampered_field_detected(tmp_path: Path) -> None:
    _, path = _log(tmp_path)
    lines = path.read_text().splitlines(keepends=True)
    rec = json.loads(lines[2])
    rec["args"]["ip"] = "8.8.8.8"
    lines[2] = json.dumps(rec, sort_keys=True, separators=(",", ":")) + "\n"
    _rewrite(path, lines)
    assert verify(tmp_path) == (False, 3)
    assert verify(path) == (False, 3)


def test_rehashed_tamper_breaks_next_link(tmp_path: Path) -> None:
    """An attacker who recomputes the tampered line's hash still breaks the next record's prev_hash."""
    from dbot.control.audit import _hash

    _, path = _log(tmp_path)
    lines = path.read_text().splitlines(keepends=True)
    rec = json.loads(lines[1])
    rec.pop("hash")
    rec["i"] = 99
    rec["hash"] = _hash(rec["prev_hash"], rec)
    lines[1] = json.dumps(rec, sort_keys=True, separators=(",", ":")) + "\n"
    _rewrite(path, lines)
    assert verify(tmp_path) == (False, 3)


def test_deleted_line_detected(tmp_path: Path) -> None:
    _, path = _log(tmp_path)
    lines = path.read_text().splitlines(keepends=True)
    del lines[3]
    _rewrite(path, lines)
    assert verify(tmp_path) == (False, 4)


def test_deleted_first_line_detected_at_dir_level(tmp_path: Path) -> None:
    _, path = _log(tmp_path)
    _rewrite(path, path.read_text().splitlines(keepends=True)[1:])
    assert verify(tmp_path) == (False, 1)


def test_reordered_lines_detected(tmp_path: Path) -> None:
    _, path = _log(tmp_path)
    lines = path.read_text().splitlines(keepends=True)
    lines[1], lines[2] = lines[2], lines[1]
    _rewrite(path, lines)
    assert verify(tmp_path) == (False, 2)


def test_truncated_line_detected(tmp_path: Path) -> None:
    _, path = _log(tmp_path)
    lines = path.read_text().splitlines(keepends=True)
    lines[4] = lines[4][:20]
    _rewrite(path, lines)
    assert verify(tmp_path) == (False, 5)


def test_chain_continues_across_day_boundary(tmp_path: Path) -> None:
    clock = Clock()
    log = AuditLog(tmp_path, clock=clock)
    log.append({"event": "a"})
    last_day1 = log.append({"event": "b"})
    clock.now += timedelta(minutes=2)
    first_day2 = log.append({"event": "c"})
    day1, day2 = tmp_path / "audit-2026-11-08.jsonl", tmp_path / "audit-2026-11-09.jsonl"
    assert day1.exists()
    assert day2.exists()
    assert first_day2["seq"] == 3
    assert first_day2["prev_hash"] == last_day1["hash"]
    assert verify(tmp_path) == (True, None)
    assert verify(day2) == (True, None)
    # A fresh AuditLog (new process) picks up the chain from the files.
    clock.now += timedelta(days=1)
    rec = AuditLog(tmp_path, clock=clock).append({"event": "d"})
    assert rec["seq"] == 4
    assert verify(tmp_path) == (True, None)
    # Deleting a whole day breaks the chain at the next day's first record.
    day2.unlink()
    assert verify(tmp_path) == (False, 3)


def test_clock_going_back_keeps_order(tmp_path: Path) -> None:
    clock = Clock()
    log = AuditLog(tmp_path, clock=clock)
    clock.now += timedelta(days=1)
    log.append({"event": "a"})
    clock.now -= timedelta(days=1)
    log.append({"event": "b"})
    assert [p.name for p in tmp_path.glob("audit-*.jsonl")] == ["audit-2026-11-09.jsonl"]
    assert verify(tmp_path) == (True, None)


def test_canary_secret_never_written(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    canary = "CANARY-audit-7f3a9c1e"
    monkeypatch.setenv("DBOT_TEST_AUDIT_CANARY", canary)
    secrets.resolve({"credentials": {"password": "env://DBOT_TEST_AUDIT_CANARY"}})
    log = AuditLog(tmp_path, clock=Clock())
    rec = log.append({"event": "invoke", "error": f"auth failed for key {canary}", "args": {canary: [canary]}})
    content = (tmp_path / "audit-2026-11-08.jsonl").read_text()
    assert canary not in content
    assert canary not in json.dumps(rec)
    assert secrets.REDACTED in content
    assert verify(tmp_path) == (True, None)


def test_reserved_keys_rejected(tmp_path: Path) -> None:
    log = AuditLog(tmp_path, clock=Clock())
    with pytest.raises(ValueError, match="reserved"):
        log.append({"seq": 1})


def test_concurrent_appenders_keep_one_chain(tmp_path: Path) -> None:
    import threading

    def worker(n: int) -> None:
        log = AuditLog(tmp_path, clock=Clock())  # separate instances, like separate processes
        for i in range(25):
            log.append({"worker": n, "i": i})

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert verify(tmp_path) == (True, None)
    seqs = [json.loads(line)["seq"] for line in (tmp_path / "audit-2026-11-08.jsonl").read_text().splitlines()]
    assert seqs == list(range(1, 101))
