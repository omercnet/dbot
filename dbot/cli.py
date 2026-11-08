"""The ``dbot`` command line: serve | worker | mcp | invoke | audit verify | config check | egress write | probe."""

import asyncio
import os
from pathlib import Path
from typing import Annotated

import typer

app = typer.Typer(help="dbot: run XSOAR integrations in sandboxes behind an audited gateway.", no_args_is_help=True)
audit_app = typer.Typer(help="Audit log tools.", no_args_is_help=True)
config_app = typer.Typer(help="Config tools.", no_args_is_help=True)
egress_app = typer.Typer(help="Egress allowlist tools.", no_args_is_help=True)
app.add_typer(audit_app, name="audit")
app.add_typer(config_app, name="config")
app.add_typer(egress_app, name="egress")


@app.command()
def serve(host: str | None = None, port: int | None = None) -> None:
    """Run the web app (ingest, approvals, alerts)."""
    from dbot.web.app import main

    main(host=host, port=port)


@app.command()
def worker() -> None:
    """Run the alert worker (agent responder)."""
    from dbot.agent.worker import main

    main()


@app.command()
def mcp() -> None:
    """Serve MCP over stdio (actor agent:mcp, roles from DBOT_MCP_ROLES)."""
    from dbot.mcp import main

    main()


def _parse_args(pairs: list[str]) -> dict[str, str]:
    out = {}
    for pair in pairs:
        key, sep, value = pair.partition("=")
        if not sep or not key:
            raise typer.BadParameter(f"expected k=v, got {pair!r}", param_hint="--arg")
        out[key] = value
    return out


@app.command()
def invoke(
    instance: str,
    command: str,
    reason: Annotated[str, typer.Option("--reason", "-r", help="Why; recorded in the audit log.")],
    arg: Annotated[list[str] | None, typer.Option("--arg", "-a", help="Command argument as k=v; repeatable.")] = None,
    approval_id: Annotated[str | None, typer.Option(help="Approval for a destructive command.")] = None,
) -> None:
    """Run one command through the gateway as an analyst (id from $USER) and print the result JSON."""
    from dbot.control.config import ConfigError
    from dbot.gateway import Actor, Gateway

    args = _parse_args(arg or [])
    actor = Actor(id=os.environ.get("USER") or "unknown", kind="human", roles={"analyst"})
    try:
        gateway = Gateway.from_env()
    except ConfigError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(1) from None
    result = asyncio.run(gateway.invoke(instance, command, args, reason, actor, approval_id))
    typer.echo(result.model_dump_json(indent=2))
    if result.status not in ("ok", "approval_required"):
        raise typer.Exit(1)


@audit_app.command("verify")
def audit_verify(path: Path) -> None:
    """Verify the hash chain of an audit file or directory. Exits 1 on failure."""
    from dbot.control.audit import verify

    if not path.exists():
        typer.echo(f"{path}: not found", err=True)
        raise typer.Exit(1)
    ok, bad_seq = verify(path)
    if not ok:
        typer.echo(f"FAIL: chain broken at seq {bad_seq}", err=True)
        raise typer.Exit(1)
    typer.echo("OK")


@config_app.command("check")
def config_check(path: Annotated[Path | None, typer.Argument(help="Config file; default DBOT_CONFIG.")] = None) -> None:
    """Load the config and resolve every instance's secrets. Prints OK/error per instance, never values."""
    from dbot.control import secrets
    from dbot.control.config import ConfigError, load_config

    try:
        cfg = load_config(path)
    except ConfigError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(1) from None
    failed = False
    for inst in cfg.instances:
        try:
            secrets.resolve(inst.params)
            status = "OK"
        except secrets.SecretError as e:  # the message names the reference, never a value
            failed = True
            status = f"error: {e}"
        typer.echo(f"{inst.name} ({inst.integration}{'' if inst.enabled else ', disabled'}): {status}")
    for name, source in cfg.ingest.sources.items():
        try:
            secrets.resolve_value(source.hmac_secret)
            status = "OK"
        except secrets.SecretError as e:
            failed = True
            status = f"error: {e}"
        typer.echo(f"ingest source {name}: {status}")
    if failed:
        raise typer.Exit(1)


@egress_app.command("write")
def egress_write(path: Annotated[str, typer.Argument(help="Output file, or - for stdout.")] = "-") -> None:
    """Write the squid allowlist (enabled instances' allow_hosts) from DBOT_CONFIG."""
    from dbot.egress import write_squid_acl

    write_squid_acl(path)


@app.command(context_settings={"allow_extra_args": True, "ignore_unknown_options": True, "help_option_names": []})
def probe(ctx: typer.Context) -> None:
    """Probe integrations in their images (delegates to dbot.sandbox.probe)."""
    try:
        from dbot.sandbox.probe import main
    except ImportError:
        typer.echo("dbot.sandbox.probe is not available", err=True)
        raise typer.Exit(1) from None
    raise typer.Exit(main(list(ctx.args)))


if __name__ == "__main__":
    app()
