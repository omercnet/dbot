"""The gateway: the only path from MCP, the agent and the CLI to a sandbox.

Every call is checked (instance, command, role, approval), runs with resolved secrets, and is audited.
Resolved params never leave ``invoke``: they are not logged, audited or returned.
"""

import os
import time
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel

from dbot.catalog import Catalog, CommandDef, IntegrationDef, index_content
from dbot.control import secrets
from dbot.control.approvals import Approval, ApprovalError, Approvals
from dbot.control.audit import AuditLog, canonical_json
from dbot.control.config import DbotConfig, load_config
from dbot.control.policy import risk as classify
from dbot.sandbox.types import CommandResult, ExecRequest, Sandbox

MAX_ARGS_BYTES = 64 * 1024
READ_ROLES = frozenset({"analyst", "approver", "agent"})
REPO_CONTENT_ROOT = Path(__file__).resolve().parent.parent / "content"
# XSOAR runs `test-module` on every integration even though no YAML lists it.
TEST_MODULE = CommandDef(name="test-module", description="Test connectivity and credentials")


class Actor(BaseModel):
    id: str
    kind: Literal["human", "agent"]
    roles: set[str]
    on_behalf_of: str | None = None


class Pending(BaseModel):
    status: Literal["approval_required"] = "approval_required"
    approval_id: str
    risk: str
    message: str


class Denied(BaseModel):
    status: Literal["denied"] = "denied"
    reason: str


InvokeResult = CommandResult | Pending | Denied


def content_root() -> Path:
    """``DBOT_CONTENT_ROOT``, else the repo's ``content/`` checkout."""
    return Path(os.environ.get("DBOT_CONTENT_ROOT") or REPO_CONTENT_ROOT)


class Gateway:
    def __init__(
        self,
        config: DbotConfig,
        catalog: Catalog,
        sandbox: Sandbox,
        approvals: Approvals,
        audit: AuditLog,
        max_args_bytes: int = MAX_ARGS_BYTES,
    ) -> None:
        self.config = config
        self.catalog = catalog
        self.sandbox = sandbox
        self.approvals = approvals
        self.audit = audit
        self.max_args_bytes = max_args_bytes

    @classmethod
    def from_env(cls) -> "Gateway":
        """The production stack: DBOT_CONFIG, DBOT_CONTENT_ROOT, DockerSandbox, DBOT_DATA_DIR state."""
        from dbot.sandbox.docker import DockerSandbox

        root = content_root()
        return cls(
            load_config(),
            Catalog(index_content(root)),
            DockerSandbox(root, network=os.environ.get("DBOT_SANDBOX_NETWORK") or None),
            Approvals(),
            AuditLog(),
        )

    def resolve(self, integration: str, command: str) -> tuple[IntegrationDef, CommandDef]:
        """Catalog lookup, plus the implicit `test-module` of every integration. Raises KeyError."""
        try:
            return self.catalog.resolve(f"{integration}.{command}")
        except KeyError:
            if command != TEST_MODULE.name:
                raise
            return self.catalog.get(integration), TEST_MODULE

    def enabled_instances(self, integration: str) -> list[str]:
        return [i.name for i in self.config.instances if i.enabled and i.integration == integration]

    def list_instances(self) -> list[dict[str, Any]]:
        """Configured instances and whether they can run. Never includes params."""
        out = []
        for inst in self.config.instances:
            problem = None
            display = ""
            try:
                display = self.catalog.get(inst.integration).display
            except KeyError:
                problem = f"integration '{inst.integration}' is not in the catalog"
            if problem is None:
                try:
                    secrets.resolve(inst.params)
                except secrets.SecretError:
                    problem = "credentials could not be resolved"
            out.append(
                {
                    "name": inst.name,
                    "integration": inst.integration,
                    "display": display,
                    "enabled": inst.enabled,
                    "configured": problem is None,
                    "problem": problem,
                }
            )
        return out

    def _audit(self, event_type: str, actor: Actor, **fields: Any) -> None:
        self.audit.append(
            {
                "type": event_type,
                "actor": actor.id,
                "actor_kind": actor.kind,
                "roles": sorted(actor.roles),
                "on_behalf_of": actor.on_behalf_of,
                **fields,
            }
        )

    async def invoke(
        self,
        instance: str,
        command: str,
        args: dict[str, Any],
        reason: str,
        actor: Actor,
        approval_id: str | None = None,
    ) -> InvokeResult:
        start = time.monotonic()
        audit: dict[str, Any] = {
            "instance": instance,
            "command": command,
            "args": secrets.redact(args),
            "reason": reason,
            "risk": None,
            "approval_id": approval_id,
        }
        try:
            result = await self._invoke(instance, command, args, reason, actor, approval_id, audit)
        except BaseException as e:
            # Even a bug or a cancellation leaves a trace; only the exception type, never its text.
            audit.update(status="crashed", error_kind=type(e).__name__)
            self._audit("invoke", actor, duration_ms=int((time.monotonic() - start) * 1000), **audit)
            raise
        if isinstance(result, CommandResult):
            # Integrations can echo credentials (URLs with keys, auth errors); mask every known secret.
            result = CommandResult.model_validate(secrets.redact(result.model_dump()))
            audit.update(status=result.status, error_kind=result.error_kind, error=(result.error or "")[:2000] or None)
        elif isinstance(result, Pending):
            audit.update(status=result.status, approval_id=result.approval_id)
        else:
            audit.update(status=result.status, error=result.reason)
        self._audit("invoke", actor, duration_ms=int((time.monotonic() - start) * 1000), **audit)
        return result

    async def _invoke(
        self,
        instance: str,
        command: str,
        args: dict[str, Any],
        reason: str,
        actor: Actor,
        approval_id: str | None,
        audit: dict[str, Any],
    ) -> InvokeResult:
        if not reason or not reason.strip():
            return Denied(reason="a non-empty reason is required for every invocation")
        if not isinstance(args, dict):
            return Denied(reason="args must be an object")
        if len(canonical_json(args).encode("utf-8")) > self.max_args_bytes:
            return Denied(reason=f"args exceed {self.max_args_bytes} bytes")

        inst = self.config.instance(instance)
        if inst is None or not inst.enabled:
            names = ", ".join(i.name for i in self.config.instances if i.enabled) or "none"
            state = "is disabled" if inst is not None else "does not exist"
            return Denied(reason=f"instance '{instance}' {state}; enabled instances: {names}")

        name = command.removeprefix(f"{inst.integration}.")
        try:
            integration, command_def = self.resolve(inst.integration, name)
        except KeyError as e:
            return Denied(reason=f"instance '{instance}' ({inst.integration}): {e.args[0]}")
        audit["command"] = name

        risk = classify(name, self.config.policy, command_def, inst)
        audit["risk"] = risk

        if not actor.roles - {"viewer"}:
            return Denied(reason=f"{actor.id} has no role that may invoke commands")
        if risk == "read" and not actor.roles & READ_ROLES:
            return Denied(reason=f"{actor.id} needs one of the roles {sorted(READ_ROLES)} to run read commands")

        if risk == "destructive":
            if approval_id is None:
                approval = self.approvals.request(
                    instance, name, args, reason, requested_by=actor.id, on_behalf_of=actor.on_behalf_of
                )
                return Pending(
                    approval_id=approval.id,
                    risk=risk,
                    message=(
                        f"'{name}' on '{instance}' is destructive and needs approval {approval.id}. "
                        "Once an approver approves it, call again with the same args and this approval_id."
                    ),
                )
            if not self.approvals.consume(approval_id, instance, name, args):
                return Denied(reason=self._approval_problem(approval_id, instance, name))

        try:
            params = secrets.resolve(inst.params)
        except secrets.SecretError as e:
            # The message names the reference only; keep it for operators, give callers a generic reason.
            audit["secret_error"] = str(e)
            return Denied(
                reason=f"credentials for instance '{instance}' could not be resolved; run `dbot config check`"
            )

        proxy = os.environ.get("DBOT_EGRESS_PROXY") or None
        if proxy:
            # CommonServerPython's handle_proxy() drops the proxy env unless params["proxy"] is true, and the
            # sandbox network only routes through the egress proxy. `insecure` is left as configured.
            params["proxy"] = True
        req = ExecRequest(command=name, args=args, params=params, proxy=proxy)
        try:
            return await self.sandbox.run(integration, req)
        except Exception as e:
            return CommandResult(status="error", error_kind="sandbox", error=f"sandbox failure: {type(e).__name__}")

    def _approval_problem(self, approval_id: str, instance: str, command: str) -> str:
        approval = self.approvals.get(approval_id)
        if approval is None:
            return f"approval {approval_id} does not exist"
        if approval.status != "approved":
            return f"approval {approval_id} is {approval.status}, not approved"
        if (approval.instance, approval.command) != (instance, command):
            return f"approval {approval_id} is for '{approval.command}' on '{approval.instance}'"
        return f"approval {approval_id} does not match these args; request a new approval"

    def get_approval(self, approval_id: str) -> Approval | None:
        return self.approvals.get(approval_id)

    def decide_approval(self, approval_id: str, actor: Actor, approve: bool) -> Approval | Denied:
        """Approve or deny a pending request. Requires the approver role; nobody decides their own request."""
        outcome: Approval | Denied
        if "approver" not in actor.roles:
            outcome = Denied(reason=f"{actor.id} does not have the approver role")
        else:
            try:
                outcome = self.approvals.decide(approval_id, actor.id, approve)
            except ApprovalError as e:
                outcome = Denied(reason=str(e))
        self._audit(
            "approval_decision",
            actor,
            approval_id=approval_id,
            approve=approve,
            status=outcome.status,
            error=outcome.reason if isinstance(outcome, Denied) else None,
        )
        return outcome
