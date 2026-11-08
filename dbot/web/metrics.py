"""``/healthz`` and hand-written Prometheus text exposition for ``/metrics``."""

from starlette.requests import Request
from starlette.responses import PlainTextResponse

from dbot.web.auth import require_viewer
from dbot.web.state import state

CONTENT_TYPE = "text/plain; version=0.0.4; charset=utf-8"
INGEST_RESULTS = ("accepted", "rejected", "duplicate")


def healthz(_request: Request) -> PlainTextResponse:
    return PlainTextResponse("ok")


def metrics(request: Request) -> PlainTextResponse:
    require_viewer(request)
    web = state(request)
    lines = ["# HELP dbot_alerts Alerts in the queue by status.", "# TYPE dbot_alerts gauge"]
    lines += [f'dbot_alerts{{status="{s}"}} {n}' for s, n in web.store.count_by_status().items()]
    lines += [
        "# HELP dbot_approvals_pending Unexpired pending approvals.",
        "# TYPE dbot_approvals_pending gauge",
        f"dbot_approvals_pending {len(web.approvals.pending())}",
        "# HELP dbot_ingest_total Ingest requests since start, by result.",
        "# TYPE dbot_ingest_total counter",
    ]
    lines += [f'dbot_ingest_total{{result="{r}"}} {web.ingest_counts[r]}' for r in INGEST_RESULTS]
    return PlainTextResponse("\n".join(lines) + "\n", media_type=CONTENT_TYPE)
