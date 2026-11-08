"""Contract tests: the certified threat-intel integrations, in their own images, behind the replay proxy.

Real Gateway -> real DockerSandbox -> demisto image -> mitmdump answering from fixtures/ (never the internet).
"""

from typing import Any

import pytest

from dbot.catalog import Catalog
from dbot.control.config import load_config
from dbot.control.policy import risk
from dbot.gateway import Actor, Gateway, Pending
from dbot.sandbox.types import CommandResult
from tests.certified.conftest import EXAMPLE_CONFIG
from tests.certified.specs import SPECS, Spec

pytestmark = pytest.mark.docker

ANALYST = Actor(id="certify", kind="human", roles={"analyst"})
APPROVER = Actor(id="certify-lead", kind="human", roles={"approver"})
REASON = "certified contract test"


def context_paths(value: Any, prefix: str = "") -> set[str]:
    """Dotted paths in an XSOAR entry context, with `(val.X == obj.X)` key suffixes stripped."""
    paths: set[str] = set()
    if isinstance(value, list):
        for item in value:
            paths |= context_paths(item, prefix)
    elif isinstance(value, dict):
        for key, sub in value.items():
            path = f"{prefix}{str(key).split('(', 1)[0]}"
            paths |= {path} | context_paths(sub, f"{path}.")
    return paths


async def run(gateway: Gateway, instance: str, command: str, args: dict[str, Any]) -> CommandResult:
    """Invoke as an analyst; a destructive command is approved by a second person and re-invoked."""
    result = await gateway.invoke(instance, command, args, REASON, ANALYST)
    if isinstance(result, Pending):
        decision = gateway.decide_approval(result.approval_id, APPROVER, approve=True)
        assert decision.status == "approved", decision
        result = await gateway.invoke(instance, command, args, REASON, ANALYST, approval_id=result.approval_id)
    assert isinstance(result, CommandResult), result
    return result


def by_id(spec: Spec) -> str:
    return spec.instance


READS = [pytest.param(spec, cmd, id=f"{spec.instance}-{cmd}") for spec in SPECS for cmd in spec.reads]


@pytest.mark.parametrize("spec", SPECS, ids=by_id)
async def test_test_module(gateway: Gateway, spec: Spec) -> None:
    result = await run(gateway, spec.instance, "test-module", {})
    assert result.status == "ok", (result.error_kind, result.error)


@pytest.mark.parametrize(("spec", "command"), READS)
async def test_curated_read(gateway: Gateway, catalog: Catalog, spec: Spec, command: str) -> None:
    result = await run(gateway, spec.instance, command, spec.reads[command])
    assert result.status == "ok", (result.error_kind, result.error)
    assert any(e.human_readable for e in result.entries), "no human_readable"
    _, command_def = catalog.resolve(f"{spec.integration}.{command}")
    declared = {o.context_path for o in command_def.outputs}
    present = context_paths(result.context)
    assert declared & present, f"none of the declared outputs in context; got {sorted(present)[:20]}"


@pytest.mark.parametrize("spec", SPECS, ids=by_id)
async def test_revoked_key_is_integration_error(gateway: Gateway, spec: Spec) -> None:
    result = await run(gateway, f"{spec.instance}-revoked", spec.error_command, spec.error_args)
    assert (result.status, result.error_kind) == ("error", "integration"), result.error
    assert spec.error_message in (result.error or "")


@pytest.mark.parametrize("spec", SPECS, ids=by_id)
def test_shipped_policy(catalog: Catalog, spec: Spec) -> None:
    config = load_config(EXAMPLE_CONFIG)
    inst = config.instance(spec.instance)
    assert risk("test-module", config.policy, None, inst) == "read"
    for command in spec.reads:
        _, command_def = catalog.resolve(f"{spec.integration}.{command}")
        assert risk(command, config.policy, command_def, inst) == "read", command
    for command in spec.destructive:
        _, command_def = catalog.resolve(f"{spec.integration}.{command}")
        assert risk(command, config.policy, command_def, inst) == "destructive", command
