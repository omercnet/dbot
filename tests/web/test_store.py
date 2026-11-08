import threading
from pathlib import Path

import pytest

from dbot.web.store import AlertStore


def test_enqueue_dedupes_per_source(tmp_path: Path) -> None:
    s = AlertStore(tmp_path / "dbot.db")
    first = s.enqueue("a", "1", "high", "t", [{"type": "ip", "value": "1.2.3.4"}], {"x": 1})
    assert first is not None
    assert s.enqueue("a", "1", "low", "other", [], None) is None
    assert s.enqueue("b", "1", "low", "t", [], None) is not None
    rec = s.get(first)
    assert rec is not None
    assert (rec.severity, rec.title, rec.status, rec.raw) == ("high", "t", "queued", {"x": 1})


def test_claim_order_and_transitions(tmp_path: Path) -> None:
    now = [100.0]
    s = AlertStore(tmp_path / "dbot.db", clock=lambda: now[0])
    ids: list[str] = []
    for i in range(3):
        now[0] += 1
        alert_id = s.enqueue("a", str(i), "low", "t", [], None)
        assert alert_id is not None
        ids.append(alert_id)
    claimed = s.claim_next()
    assert claimed is not None
    assert (claimed.id, claimed.status) == (ids[0], "running")
    assert s.get(ids[0]).status == "running"  # type: ignore[union-attr]
    assert s.set_status(ids[0], "queued", expect="waiting_approval") is False
    assert s.set_status(ids[0], "waiting_approval", expect="running") is True
    assert s.claim_next().id == ids[1]  # type: ignore[union-attr]
    assert s.set_status(ids[0], "queued") is True
    assert s.claim_next().id == ids[0]  # type: ignore[union-attr]
    with pytest.raises(ValueError, match="unknown alert status"):
        s.set_status(ids[0], "bogus")
    assert s.set_status("missing", "done") is False
    assert s.count_by_status() == {"queued": 1, "running": 2, "waiting_approval": 0, "done": 0, "failed": 0}


def test_set_report(tmp_path: Path) -> None:
    s = AlertStore(tmp_path / "dbot.db")
    alert_id = s.enqueue("a", "1", "low", "t", [], None)
    assert alert_id is not None
    assert s.set_report(alert_id, '{"verdict": "benign"}') is True
    assert s.get(alert_id).report == {"verdict": "benign"}  # type: ignore[union-attr]
    with pytest.raises(ValueError, match="JSON object"):
        s.set_report(alert_id, "[1]")
    assert s.set_report("missing", {}) is False


def test_claim_next_is_atomic_across_threads(tmp_path: Path) -> None:
    db = tmp_path / "dbot.db"
    s = AlertStore(db)
    total = 60
    for i in range(total):
        s.enqueue("a", str(i), "low", "t", [], None)
    claimed: list[list[str]] = [[], []]
    start = threading.Barrier(2)

    def worker(n: int) -> None:
        own = AlertStore(db)  # separate instance, separate connections
        start.wait()
        while (rec := own.claim_next()) is not None:
            claimed[n].append(rec.id)

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    every = claimed[0] + claimed[1]
    assert len(every) == total
    assert len(set(every)) == total
    assert s.count_by_status()["running"] == total
