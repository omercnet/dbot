"""CLI entrypoints — dbot-chat, dbot-respond, dbot-watch, dbot-web."""

import asyncio
import os
from pathlib import Path

import typer
from rich.console import Console
from rich.markdown import Markdown

from dbot.agent.deps import IRDeps
from dbot.agent.guardrails import GuardrailConfig
from dbot.audit import AuditLogger

console = Console()

# ── Shared helpers ────────────────────────────────────────────────────


def _build_deps(
    model: str,
    audit_log: Path,
    guardrails: GuardrailConfig,
) -> IRDeps:
    """Build IRDeps from the dbot server components."""
    from dbot.credentials.store import CredentialStore
    from dbot.registry.catalog import Catalog
    from dbot.registry.indexer import index_content
    from dbot.runtime.common_server import bootstrap_common_modules
    from dbot.runtime.executor import execute_inprocess

    content_root = Path(__file__).parent.parent.parent / "content"
    bootstrap_common_modules(content_root)
    integrations = index_content(content_root)
    catalog = Catalog(integrations)
    cred_path = Path(__file__).parent.parent.parent / "config" / "credentials.yaml"
    credential_store = CredentialStore(cred_path if cred_path.exists() else None)

    return IRDeps(
        catalog=catalog,
        credential_store=credential_store,
        executor=execute_inprocess,
        audit=AuditLogger(audit_path=audit_log),
        guardrails=guardrails,
        model_name=model,
    )


# ── dbot-chat ─────────────────────────────────────────────────────────

chat_app = typer.Typer(name="dbot-chat", help="Interactive IR investigation chat.")


@chat_app.command()
def chat(
    model: str = typer.Option(None, "--model", envvar="DBOT_LLM_MODEL", help="LLM model name"),
    audit_log: Path = typer.Option("dbot-agent-audit.log", "--audit-log", help="Audit log path"),
    no_stream: bool = typer.Option(False, "--no-stream", help="Disable streaming"),
) -> None:
    """Start an interactive IR investigation chat session."""
    model_name = model or os.environ.get("DBOT_LLM_MODEL", "openai:gpt-4o")
    console.print(f"[bold]dbot chat[/bold] (model: {model_name})")
    console.print("Type your investigation queries. Ctrl+C to exit.\n")

    config = GuardrailConfig.chat_default()
    deps = _build_deps(model_name, audit_log, config)

    from dbot.agent.chat import ChatAgent

    agent = ChatAgent(config=config, model=model_name)

    async def _loop() -> None:
        while True:
            try:
                user_input = console.input("[bold green]you>[/bold green] ")
            except (EOFError, KeyboardInterrupt):
                console.print("\n[dim]Session ended.[/dim]")
                break

            if not user_input.strip():
                continue

            if user_input.strip().lower() in ("exit", "quit", "/quit"):
                break

            if user_input.strip().lower() == "/reset":
                agent.reset()
                console.print("[dim]History cleared.[/dim]")
                continue

            console.print("[bold blue]dbot>[/bold blue] ", end="")

            if no_stream:
                response = await agent.send(user_input, deps=deps)
                console.print(Markdown(response))
            else:
                chunks: list[str] = []
                async for chunk in agent.send_stream(user_input, deps=deps):
                    console.print(chunk, end="")
                    chunks.append(chunk)
                console.print()  # newline after stream

    asyncio.run(_loop())


# ── dbot-respond ──────────────────────────────────────────────────────

respond_app = typer.Typer(name="dbot-respond", help="Autonomous alert investigation.")


@respond_app.command()
def respond(
    alert_file: Path = typer.Argument(None, help="Path to alert JSON file (or stdin)"),
    model: str = typer.Option(None, "--model", envvar="DBOT_LLM_MODEL", help="LLM model name"),
    audit_log: Path = typer.Option("dbot-agent-audit.log", "--audit-log", help="Audit log path"),
    output_format: str = typer.Option("markdown", "--output", "-o", help="Output format: markdown, json, jsonl"),
    output_file: Path = typer.Option(None, "--output-file", "-f", help="Write report to file"),
    max_calls: int = typer.Option(30, "--max-calls", help="Max tool invocations"),
    block_category: list[str] = typer.Option([], "--block-category", help="Block tools from these categories"),
    no_hitl: bool = typer.Option(False, "--no-hitl", help="Deny all deferred tools"),
) -> None:
    """Investigate an alert autonomously."""
    model_name = model or os.environ.get("DBOT_LLM_MODEL", "openai:gpt-4o")

    # Load alert
    from dbot.agent.ingestion.cli import load_alert_from_file, load_alert_from_stdin

    if alert_file:
        alert = load_alert_from_file(alert_file)
    else:
        console.print("[dim]Reading alert from stdin...[/dim]")
        alert = load_alert_from_stdin()

    console.print(f"[bold]Investigating:[/bold] {alert.title} ({alert.severity.value})")

    config = GuardrailConfig.autonomous_default()
    config.max_tool_calls = max_calls
    config.blocked_categories.update(block_category)
    deps = _build_deps(model_name, audit_log, config)

    from dbot.agent.responder import ResponderAgent

    agent = ResponderAgent(config=config, model=model_name)

    async def _on_deferred(deferred: object) -> dict[str, bool]:
        """CLI HITL handler — prompt user for each deferred tool."""
        if no_hitl:
            return {call.tool_call_id: False for call in deferred.approvals}  # type: ignore[union-attr]

        approvals: dict[str, bool] = {}
        for call in deferred.approvals:  # type: ignore[union-attr]
            console.print(f"\n[bold yellow]Approval required:[/bold yellow] {call.tool_name}")
            console.print(f"  Args: {call.args}")
            answer = console.input("  Approve? [y/N] ").strip().lower()
            approvals[call.tool_call_id] = answer in ("y", "yes")
        return approvals

    async def _run() -> None:
        result = await agent.investigate(
            alert=alert,
            deps=deps,
            on_deferred=_on_deferred if not no_hitl else None,
        )

        # Render report
        from dbot.agent.report import to_json, to_jsonl_event, to_markdown

        if output_format == "json":
            output = to_json(result.report)
        elif output_format == "jsonl":
            output = to_jsonl_event(result.report)
        else:
            output = to_markdown(result.report)

        if output_file:
            output_file.write_text(output, encoding="utf-8")
            console.print(f"\n[dim]Report written to {output_file}[/dim]")
        else:
            console.print()
            console.print(Markdown(output) if output_format == "markdown" else output)

    asyncio.run(_run())


# ── dbot-watch ────────────────────────────────────────────────────────

watch_app = typer.Typer(name="dbot-watch", help="Watch directory for alert files.")


@watch_app.command()
def watch(
    watch_dir: Path = typer.Argument(..., help="Directory to watch for alert JSON files"),
    model: str = typer.Option(None, "--model", envvar="DBOT_LLM_MODEL", help="LLM model name"),
    audit_log: Path = typer.Option("dbot-agent-audit.log", "--audit-log", help="Audit log path"),
    output_dir: Path = typer.Option(None, "--output-dir", help="Directory for reports"),
    max_calls: int = typer.Option(30, "--max-calls", help="Max tool invocations per alert"),
    jsonl: bool = typer.Option(False, "--jsonl", help="Also append to investigations.jsonl"),
) -> None:
    """Watch a directory for new alert JSON files and investigate each one."""
    model_name = model or os.environ.get("DBOT_LLM_MODEL", "openai:gpt-4o")
    report_dir = output_dir or (watch_dir / "reports")
    report_dir.mkdir(parents=True, exist_ok=True)

    config = GuardrailConfig.autonomous_default()
    config.max_tool_calls = max_calls
    deps = _build_deps(model_name, audit_log, config)

    from dbot.agent.ingestion.watcher import AlertWatcher
    from dbot.agent.report import to_jsonl_event, to_markdown
    from dbot.agent.responder import ResponderAgent

    agent = ResponderAgent(config=config, model=model_name)

    async def _handle_alert(alert: object) -> None:
        from dbot.agent.models import Alert

        if not isinstance(alert, Alert):
            return

        console.print(f"[bold]New alert:[/bold] {alert.title}")
        result = await agent.investigate(alert=alert, deps=deps)
        report = result.report

        # Write markdown report
        ts = report.started_at.strftime("%Y%m%d_%H%M%S")
        md_path = report_dir / f"{alert.id}_{ts}.md"
        md_path.write_text(to_markdown(report), encoding="utf-8")
        console.print(f"  Report: {md_path}")

        # Optionally append JSONL
        if jsonl:
            jsonl_path = report_dir / "investigations.jsonl"
            with open(jsonl_path, "a", encoding="utf-8") as f:
                f.write(to_jsonl_event(report) + "\n")

    console.print(f"[bold]Watching[/bold] {watch_dir} for alert files...")
    console.print(f"Reports → {report_dir}")
    console.print("Press Ctrl+C to stop.\n")

    loop = asyncio.new_event_loop()
    watcher = AlertWatcher(watch_dir, _handle_alert, loop=loop)
    watcher.start()

    try:
        loop.run_forever()
    except KeyboardInterrupt:
        console.print("\n[dim]Stopping watcher...[/dim]")
    finally:
        watcher.stop()
        loop.close()


# ── dbot-web ───────────────────────────────────────────────────────────────────

web_app_typer = typer.Typer(name="dbot-web", help="Launch web UI for IR investigations.")


@web_app_typer.command()
def web(
    model: str = typer.Option(None, "--model", envvar="DBOT_LLM_MODEL", help="Default LLM model"),
    host: str = typer.Option("127.0.0.1", "--host", help="Bind host"),
    port: int = typer.Option(7932, "--port", help="Bind port"),
    audit_log: Path = typer.Option("dbot-agent-audit.log", "--audit-log", help="Audit log path"),
) -> None:
    """Launch the dbot web UI in the browser."""
    import uvicorn

    from dbot.agent.web import create_app

    model_name = model or os.environ.get("DBOT_LLM_MODEL", "openai:gpt-4o")
    console.print(f"[bold]dbot web UI[/bold] → http://{host}:{port}")
    console.print(f"Model: {model_name}")
    console.print("Press Ctrl+C to stop.\n")

    app = create_app(model=model_name, audit_log=audit_log)
    uvicorn.run(app, host=host, port=port, log_level="warning")
