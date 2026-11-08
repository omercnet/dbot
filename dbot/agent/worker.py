"""Alert worker: claims queued alerts, runs the responder, stores the report (C9).

Resume after approval: the web layer requeues a `waiting_approval` alert when one of its approvals is decided. The
next run gets the previous report, so the responder sees its earlier approvals and their current status.
"""

import asyncio
import logging
import os

from dbot.agent.report import IRReport
from dbot.agent.responder import ResponderError, respond
from dbot.control.config import DbotConfig
from dbot.gateway import Gateway
from dbot.web.store import AlertStore

log = logging.getLogger("dbot.worker")
POLL_S = 2.0


async def run_once(store: AlertStore, gateway: Gateway, config: DbotConfig) -> bool:
    """Process the next queued alert. Returns False when the queue is empty."""
    rec = store.claim_next()
    if rec is None:
        return False
    previous = IRReport.model_validate(rec.report) if rec.report else None
    try:
        report = await respond(rec.alert(), gateway, config, alert_id=rec.id, previous=previous)
    except Exception as e:
        log.exception("responder failed for alert %s", rec.id)
        if isinstance(e, ResponderError):
            failed = e.report  # keeps approved actions that already ran before the failure
        else:
            failed = IRReport(
                alert_id=rec.id,
                verdict="inconclusive",
                confidence=0.0,
                summary="The responder failed.",
                actions_taken=previous.actions_taken if previous else [],
                actions_pending=previous.actions_pending if previous else [],
                errors=[f"{type(e).__name__}: {e}"],
            )
        store.set_report(rec.id, failed.model_dump_json())
        store.set_status(rec.id, "failed")
        return True
    store.set_report(rec.id, report.model_dump_json())
    if not report.actions_pending:
        store.set_status(rec.id, "done")
        return True
    store.set_status(rec.id, "waiting_approval")
    # The web layer requeues only alerts already in waiting_approval; an approval decided before that is our job.
    for pa in report.actions_pending:
        approval = gateway.get_approval(pa.approval_id)
        if approval is None or approval.status != "pending":
            store.set_status(rec.id, "queued", expect="waiting_approval")
            break
    return True


async def run_forever(store: AlertStore, gateway: Gateway, config: DbotConfig, poll_s: float = POLL_S) -> None:
    while True:
        if not await run_once(store, gateway, config):
            await asyncio.sleep(poll_s)


def main() -> None:
    logging.basicConfig(level=os.environ.get("DBOT_LOG_LEVEL", "INFO"))
    gateway = Gateway.from_env()
    asyncio.run(run_forever(AlertStore(), gateway, gateway.config))
