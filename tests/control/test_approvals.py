import threading
from pathlib import Path

import pytest

from dbot.control.approvals import ApprovalError, Approvals


class Clock:
    def __init__(self) -> None:
        self.now = 1_000_000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def store(tmp_path: Path, clock: Clock) -> Approvals:
    return Approvals(tmp_path / "dbot.db", clock=clock)


ARGS = {"ip": "1.2.3.4", "duration": 60}


def _approved(store: Approvals) -> str:
    a = store.request("fw", "fw-block-ip", ARGS, "malicious", requested_by="agent:mcp", on_behalf_of="alert:7")
    store.decide(a.id, "lead", approve=True)
    return a.id


def test_consume_is_single_use(store: Approvals) -> None:
    aid = _approved(store)
    assert store.consume(aid, "fw", "fw-block-ip", dict(ARGS))
    assert not store.consume(aid, "fw", "fw-block-ip", dict(ARGS))
    assert store.get(aid).status == "consumed"  # type: ignore[union-attr]


def test_args_hash_must_match(store: Approvals) -> None:
    aid = _approved(store)
    assert not store.consume(aid, "fw", "fw-block-ip", {"ip": "5.6.7.8", "duration": 60})
    assert not store.consume(aid, "fw2", "fw-block-ip", ARGS)
    assert not store.consume(aid, "fw", "fw-unblock-ip", ARGS)
    # Mismatches do not burn the approval; key order is irrelevant.
    assert store.consume(aid, "fw", "fw-block-ip", {"duration": 60, "ip": "1.2.3.4"})


def test_expired_approval_rejected(store: Approvals, clock: Clock) -> None:
    aid = _approved(store)
    clock.now += 3600
    assert store.get(aid).status == "expired"  # type: ignore[union-attr]
    assert not store.consume(aid, "fw", "fw-block-ip", ARGS)


def test_just_before_expiry_still_valid(store: Approvals, clock: Clock) -> None:
    aid = _approved(store)
    clock.now += 3599
    assert store.consume(aid, "fw", "fw-block-ip", ARGS)


def test_expired_pending_cannot_be_decided(store: Approvals, clock: Clock) -> None:
    a = store.request("fw", "fw-block-ip", ARGS, "r", requested_by="agent:mcp")
    clock.now += 3600
    assert store.pending() == []
    with pytest.raises(ApprovalError, match="expired"):
        store.decide(a.id, "lead", approve=True)


def test_denied_cannot_be_consumed_or_redecided(store: Approvals) -> None:
    a = store.request("fw", "fw-block-ip", ARGS, "r", requested_by="agent:mcp")
    assert store.decide(a.id, "lead", approve=False).status == "denied"
    assert not store.consume(a.id, "fw", "fw-block-ip", ARGS)
    with pytest.raises(ApprovalError, match="denied"):
        store.decide(a.id, "other-lead", approve=True)


def test_pending_cannot_be_consumed(store: Approvals) -> None:
    a = store.request("fw", "fw-block-ip", ARGS, "r", requested_by="agent:mcp")
    assert not store.consume(a.id, "fw", "fw-block-ip", ARGS)
    assert [p.id for p in store.pending()] == [a.id]


def test_self_approval_blocked(store: Approvals) -> None:
    a = store.request("fw", "fw-block-ip", ARGS, "r", requested_by="alice")
    with pytest.raises(ApprovalError, match="own request"):
        store.decide(a.id, "alice", approve=True)
    b = store.request("fw", "fw-block-ip", ARGS, "r", requested_by="agent:mcp", on_behalf_of="bob")
    with pytest.raises(ApprovalError, match="own request"):
        store.decide(b.id, "bob", approve=True)
    assert store.get(a.id).status == "pending"  # type: ignore[union-attr]
    assert store.decide(a.id, "carol", approve=True).approver == "carol"


def test_unknown_id(store: Approvals) -> None:
    assert store.get("nope") is None
    assert not store.consume("nope", "fw", "fw-block-ip", ARGS)
    with pytest.raises(ApprovalError, match="not found"):
        store.decide("nope", "lead", approve=True)


def _race_consume(db: Path, approval_id: str) -> list[bool]:
    barrier = threading.Barrier(2)
    results: list[bool] = []
    lock = threading.Lock()

    def worker() -> None:
        other = Approvals(db)
        barrier.wait()
        ok = other.consume(approval_id, "fw", "fw-block-ip", ARGS)
        with lock:
            results.append(ok)

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return results


def test_concurrent_consume_exactly_one_wins(tmp_path: Path) -> None:
    store = Approvals(tmp_path / "dbot.db")
    for _ in range(10):
        assert sorted(_race_consume(tmp_path / "dbot.db", _approved(store))) == [False, True]


def test_persists_across_instances(tmp_path: Path) -> None:
    aid = _approved(Approvals(tmp_path / "dbot.db"))
    again = Approvals(tmp_path / "dbot.db")
    assert again.get(aid).status == "approved"  # type: ignore[union-attr]
    assert again.consume(aid, "fw", "fw-block-ip", ARGS)
