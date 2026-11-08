"""Worker tests: real AlertStore and Gateway, fake sandbox, scripted model."""

from pathlib import Path
from typing import Any

from pydantic_ai.messages import ModelMessage, ModelResponse
from pydantic_ai.models.function import AgentInfo, FunctionModel

from dbot.agent.report import IRReport
from dbot.agent.responder import responder
from dbot.agent.worker import run_once
from dbot.gateway import Gateway
from dbot.web.store import AlertStore
from tests.agent.test_responder import ALERT, BLOCK, Script, enrich_steps, final, make_gateway


def enqueue(store: AlertStore) -> str:
    alert_id = store.enqueue("generic", "ext-1", "high", ALERT["title"], ALERT["observables"], ALERT["raw"])
    assert alert_id
    return alert_id


def report_of(store: AlertStore, alert_id: str) -> IRReport:
    rec = store.get(alert_id)
    assert rec is not None
    assert rec.report is not None
    return IRReport.model_validate(rec.report)


def status_of(store: AlertStore, alert_id: str) -> str:
    rec = store.get(alert_id)
    assert rec is not None
    return rec.status


async def run(store: AlertStore, gateway: Gateway, model: Any) -> bool:
    with responder.override(model=model):
        return await run_once(store, gateway, gateway.config)


async def test_empty_queue(tmp_path: Path) -> None:
    gateway, _, _ = make_gateway(tmp_path)
    assert await run(AlertStore(tmp_path / "alerts.db"), gateway, Script(final()).model) is False


async def test_done(tmp_path: Path) -> None:
    gateway, sandbox, _ = make_gateway(tmp_path)
    store = AlertStore(tmp_path / "alerts.db")
    alert_id = enqueue(store)

    assert await run(store, gateway, Script(*enrich_steps(), final()).model) is True

    assert status_of(store, alert_id) == "done"
    assert report_of(store, alert_id).alert_id == alert_id
    assert len(sandbox.calls) == 2
    assert await run(store, gateway, Script(final()).model) is False  # not claimed twice


async def test_waiting_approval_then_resume_to_done(tmp_path: Path) -> None:
    gateway, sandbox, _ = make_gateway(tmp_path)
    store = AlertStore(tmp_path / "alerts.db")
    alert_id = enqueue(store)

    await run(store, gateway, Script([("invoke", BLOCK)], final()).model)

    assert status_of(store, alert_id) == "waiting_approval"
    pending = report_of(store, alert_id).actions_pending
    assert len(pending) == 1
    approval = gateway.get_approval(pending[0].approval_id)
    assert approval is not None
    assert approval.on_behalf_of == f"alert:{alert_id}"

    gateway.approvals.decide(pending[0].approval_id, "lead@soc", True)
    assert store.set_status(alert_id, "queued", expect="waiting_approval")  # what the web layer does on decide
    await run(store, gateway, Script(final()).model)  # the model never mentions the approved action

    assert status_of(store, alert_id) == "done"
    report = report_of(store, alert_id)
    assert report.actions_pending == []
    assert [a.approval_id for a in report.actions_taken] == [pending[0].approval_id]
    assert [req.command for _, req in sandbox.calls] == ["fw-block-ip"]


async def test_approval_decided_during_run_requeues(tmp_path: Path) -> None:
    gateway, _, _ = make_gateway(tmp_path)
    store = AlertStore(tmp_path / "alerts.db")
    alert_id = enqueue(store)

    def decide_then_finish(script: Script, info: AgentInfo) -> dict[str, Any]:
        # An approver acts before the worker reaches waiting_approval, so the web layer's requeue is a no-op.
        gateway.approvals.decide(script.returns[0]["approval_id"], "lead@soc", True)
        assert not store.set_status(alert_id, "queued", expect="waiting_approval")
        return final()

    await run(store, gateway, Script([("invoke", BLOCK)], decide_then_finish).model)

    assert status_of(store, alert_id) == "queued"


async def test_failed(tmp_path: Path) -> None:
    gateway, _, _ = make_gateway(tmp_path)
    store = AlertStore(tmp_path / "alerts.db")
    alert_id = enqueue(store)

    def broken(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        raise RuntimeError("model provider down")

    await run(store, gateway, FunctionModel(broken))

    assert status_of(store, alert_id) == "failed"
    report = report_of(store, alert_id)
    assert report.errors == ["RuntimeError: model provider down"]
    assert report.verdict == "inconclusive"


async def test_failure_after_approved_action_keeps_it_in_report(tmp_path: Path) -> None:
    gateway, sandbox, _ = make_gateway(tmp_path)
    store = AlertStore(tmp_path / "alerts.db")
    alert_id = enqueue(store)
    await run(store, gateway, Script([("invoke", BLOCK)], final()).model)
    approval_id = report_of(store, alert_id).actions_pending[0].approval_id
    gateway.approvals.decide(approval_id, "lead@soc", True)
    store.set_status(alert_id, "queued", expect="waiting_approval")

    def broken(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        raise RuntimeError("model provider down")

    await run(store, gateway, FunctionModel(broken))

    assert status_of(store, alert_id) == "failed"
    report = report_of(store, alert_id)
    assert [a.approval_id for a in report.actions_taken] == [approval_id]  # it ran before the model failed
    assert report.actions_pending == []
    assert report.errors == ["RuntimeError: model provider down"]
    assert len(sandbox.calls) == 1
