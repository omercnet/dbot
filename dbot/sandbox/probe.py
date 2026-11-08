"""Import probe: run a nonexistent command in every python3 integration's own image.

An integration "reaches main" when anything other than an import-time failure happens. This is the
health metric for the content submodule and the sandbox shim.

    python -m dbot.sandbox.probe [--limit N] [--concurrency 8] [--json out.json]
"""

import argparse
import asyncio
import json
import os
import sys
from collections import Counter
from collections.abc import Callable
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from dbot.catalog.index import index_content
from dbot.catalog.models import IntegrationDef
from dbot.sandbox.docker import DockerSandbox
from dbot.sandbox.types import CommandResult, ExecRequest, Sandbox

PROBE_COMMAND = "dbot-probe-nonexistent-command"
Outcome = Literal["reached_main", "import_error", "timeout", "sandbox_error"]
OUTCOMES: tuple[Outcome, ...] = ("reached_main", "import_error", "timeout", "sandbox_error")


class ProbeRow(BaseModel):
    name: str
    pack: str
    image: str
    outcome: Outcome
    error_kind: str | None = None
    error: str | None = None
    duration_ms: int = 0


def classify(result: CommandResult) -> Outcome:
    if result.error_kind == "import":
        return "import_error"
    if result.error_kind == "timeout":
        return "timeout"
    if result.error_kind in ("sandbox", "killed"):
        return "sandbox_error"
    return "reached_main"


async def probe(
    integrations: list[IntegrationDef],
    sandbox: Sandbox,
    *,
    concurrency: int = 8,
    timeout_s: float = 60.0,
    on_row: Callable[[ProbeRow], None] | None = None,
) -> list[ProbeRow]:
    """Probe each integration with at most `concurrency` containers at once."""
    sem = asyncio.Semaphore(concurrency)

    async def one(integ: IntegrationDef) -> ProbeRow:
        async with sem:
            result = await sandbox.run(integ, ExecRequest(command=PROBE_COMMAND, timeout_s=timeout_s))
        row = ProbeRow(
            name=integ.name,
            pack=integ.pack,
            image=integ.dockerimage,
            outcome=classify(result),
            error_kind=result.error_kind,
            error=(result.error or "")[:500] or None,
            duration_ms=result.duration_ms,
        )
        if on_row:
            on_row(row)
        return row

    return list(await asyncio.gather(*(one(i) for i in integrations)))


def summarize(rows: list[ProbeRow]) -> str:
    counts = Counter(r.outcome for r in rows)
    total = len(rows) or 1
    lines = [f"probed {len(rows)} integrations"]
    lines += [f"  {o:<14} {counts[o]:>5}  ({100 * counts[o] / total:.1f}%)" for o in OUTCOMES]
    for outcome in ("import_error", "sandbox_error", "timeout"):
        top = Counter((r.error or "")[:120].splitlines()[0] if r.error else "" for r in rows if r.outcome == outcome)
        if top:
            lines.append(f"top {outcome} messages:")
            lines += [f"  {n:>4}  {msg}" for msg, n in top.most_common(10)]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="dbot probe", description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--content", type=Path, default=Path(os.environ.get("DBOT_CONTENT_ROOT", "content")))
    parser.add_argument("--limit", type=int, default=0, help="probe only the first N integrations")
    parser.add_argument("--concurrency", type=int, default=8)
    parser.add_argument("--timeout", type=float, default=60.0, help="per-integration deadline in seconds")
    parser.add_argument("--include-deprecated", action="store_true")
    parser.add_argument("--json", type=Path, help="write per-integration rows to this file")
    args = parser.parse_args(argv)

    integrations = index_content(args.content, include_deprecated=args.include_deprecated)
    if args.limit:
        integrations = integrations[: args.limit]
    if not integrations:
        sys.stderr.write(f"no python3 integrations found under {args.content}\n")
        return 1

    done = 0

    def progress(row: ProbeRow) -> None:
        nonlocal done
        done += 1
        sys.stderr.write(f"[{done}/{len(integrations)}] {row.outcome:<14} {row.name}\n")

    rows = asyncio.run(
        probe(
            integrations,
            DockerSandbox(args.content),
            concurrency=args.concurrency,
            timeout_s=args.timeout,
            on_row=progress,
        )
    )
    if args.json:
        args.json.write_text(json.dumps([r.model_dump() for r in rows], indent=1))
    sys.stdout.write(summarize(rows) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
