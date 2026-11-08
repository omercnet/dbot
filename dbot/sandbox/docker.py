"""Docker sandbox: one hardened, throwaway container per command, in the integration's own image."""

import asyncio
import json
import logging
import time
import uuid
from pathlib import Path
from typing import Any

from dbot.catalog.models import IntegrationDef
from dbot.sandbox.assemble import SCRIPT_NAME, assemble
from dbot.sandbox.types import ENTRY_ERROR, CommandResult, Entry, ErrorKind, ExecRequest

PULL_TIMEOUT_S = 900.0
KILL_GRACE_S = 10.0
STDERR_TAIL = 500
PARAM_TYPE_BOOLEAN = 8
SANDBOX_USER = "65534:65534"  # nobody; demisto images default to root
CA_PATH = "/etc/dbot/ca.pem"

logger = logging.getLogger(__name__)


def _entry(raw: Any) -> Entry:
    if not isinstance(raw, dict):
        return Entry(type=1, contents=raw, human_readable=raw if isinstance(raw, str) else None)
    try:
        etype = int(raw.get("Type", 1))
    except (TypeError, ValueError):
        etype = 1
    hr = raw.get("HumanReadable")
    ec = raw.get("EntryContext")
    return Entry(
        type=etype,
        contents=raw.get("Contents"),
        human_readable=hr if isinstance(hr, str) else None,
        entry_context=ec if isinstance(ec, dict) else None,
    )


def _as_list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list) else [value]


def merge_context(entries: list[Entry]) -> dict[str, Any]:
    """Merge EntryContext across entries; a key set by several entries collects all values in a list."""
    ctx: dict[str, Any] = {}
    for e in entries:
        for key, value in (e.entry_context or {}).items():
            ctx[key] = _as_list(ctx[key]) + _as_list(value) if key in ctx else value
    return ctx


def _text(value: Any) -> str:
    return value if isinstance(value, str) else json.dumps(value, default=str)


def map_output(command: str, out: dict[str, Any]) -> CommandResult:
    """Map the runner's wire output to a CommandResult per contract C2."""
    entries = [_entry(r) for r in out.get("results") or []]
    logs = [str(line) for line in out.get("logs") or []]
    exit_kind = out.get("exit")
    runner_error = out.get("error")

    def fail(kind: ErrorKind, message: str) -> CommandResult:
        return CommandResult(
            status="error",
            error=message,
            error_kind=kind,
            entries=entries,
            context=merge_context(entries),
            logs=logs,
        )

    errors = [e for e in entries if e.type == ENTRY_ERROR]
    if errors:
        return fail("integration", _text(errors[0].contents))
    if exit_kind == "import":
        return fail("import", str(runner_error))
    if exit_kind == "exception" or (exit_kind == "sysexit" and runner_error):
        return fail("runtime", str(runner_error))
    if exit_kind == "sysexit" and not entries:
        return fail("runtime", "integration called sys.exit() without returning results")
    if exit_kind not in ("ok", "sysexit"):
        return fail("sandbox", f"unknown runner exit: {exit_kind!r}")
    if command == "test-module" and not any(e.contents == "ok" for e in entries):
        detail = "; ".join(_text(e.human_readable or e.contents) for e in entries)
        return fail("integration", detail or "test-module returned no 'ok' result")
    return CommandResult(status="ok", entries=entries, context=merge_context(entries), logs=logs)


def _last_json_line(stdout: bytes) -> dict[str, Any] | None:
    for line in reversed(stdout.decode("utf-8", "replace").splitlines()):
        if line.strip():
            try:
                parsed = json.loads(line)
            except ValueError:
                return None
            return parsed if isinstance(parsed, dict) else None
    return None


def default_params(integration: IntegrationDef) -> dict[str, Any]:
    """YAML defaults for params the instance did not set, typed like the XSOAR server sends them.

    XSOAR sends every checkbox (type 8) param, unchecked ones as False.
    """
    out: dict[str, Any] = {}
    for p in integration.params:
        if p.type == PARAM_TYPE_BOOLEAN:
            out[p.name] = (p.default or "").strip().lower() == "true"
        elif p.default is not None:
            out[p.name] = p.default
    return out


class DockerSandbox:
    """Runs each command in `integration.dockerimage` with the assembled script mounted read-only."""

    def __init__(
        self,
        content_root: Path,
        *,
        cpus: float = 1,
        memory: str = "512m",
        network: str | None = None,
        docker: str = "docker",
        ca_bundle: Path | None = None,
    ) -> None:
        self.content_root = Path(content_root)
        self.cpus = cpus
        self.memory = memory
        self.network = network
        self.docker = docker
        self.ca_bundle = Path(ca_bundle).resolve() if ca_bundle else None
        self._present: set[str] = set()
        self._pull_locks: dict[str, asyncio.Lock] = {}
        if network is None:
            logger.warning("sandboxes have unrestricted egress; set DBOT_SANDBOX_NETWORK")

    async def _exec(self, *args: str, timeout: float) -> tuple[int, bytes]:
        proc = await asyncio.create_subprocess_exec(
            self.docker, *args, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE
        )
        try:
            _, err = await asyncio.wait_for(proc.communicate(), timeout)
        except TimeoutError:
            proc.kill()
            await proc.wait()
            return -1, b"timed out"
        return proc.returncode or 0, err

    async def ensure_image(self, image: str) -> None:
        """Pull `image` if it is not present locally. Raises RuntimeError on failure."""
        if image in self._present:
            return
        lock = self._pull_locks.setdefault(image, asyncio.Lock())
        async with lock:
            if image in self._present:
                return
            rc, _ = await self._exec("image", "inspect", image, timeout=30)
            if rc != 0:
                rc, err = await self._exec("pull", "--quiet", image, timeout=PULL_TIMEOUT_S)
                if rc != 0:
                    raise RuntimeError(f"docker pull {image} failed: {err.decode('utf-8', 'replace')[-STDERR_TAIL:]}")
            self._present.add(image)

    async def _kill(self, name: str) -> None:
        await self._exec("kill", name, timeout=KILL_GRACE_S)

    def _argv(self, name: str, mount_dir: Path, image: str, integration_name: str, proxy: str | None) -> list[str]:
        argv = [
            "run", "--rm", "-i",
            "--name", name,
            "--read-only",
            "--user", SANDBOX_USER,
            "--tmpfs", "/tmp",  # noqa: S108 - container-private tmpfs
            "--workdir", "/tmp",  # noqa: S108
            "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges",
            "--pids-limit", "256",
            "--memory", self.memory,
            "--cpus", str(self.cpus),
            "-e", "HOME=/tmp",
        ]  # fmt: skip
        if self.network:
            argv += ["--network", self.network]
        if proxy:
            for var in ("HTTPS_PROXY", "HTTP_PROXY", "https_proxy", "http_proxy"):
                argv += ["-e", f"{var}={proxy}"]
        if self.ca_bundle:
            # Extra trust root, e.g. a TLS-inspecting egress proxy or the replay proxy's CA. Must be world-readable.
            argv += ["-v", f"{self.ca_bundle}:{CA_PATH}:ro", "-e", f"REQUESTS_CA_BUNDLE={CA_PATH}"]
            argv += ["-e", f"SSL_CERT_FILE={CA_PATH}"]
        argv += ["-v", f"{mount_dir}:/dbot:ro", image, "python", "/dbot/runner.py", f"/dbot/{SCRIPT_NAME}"]
        argv.append(integration_name)
        return argv

    async def run(self, integration: IntegrationDef, req: ExecRequest) -> CommandResult:
        image = integration.dockerimage
        start = time.monotonic()

        def done(result: CommandResult) -> CommandResult:
            result.duration_ms = int((time.monotonic() - start) * 1000)
            result.image = image
            return result

        def sandbox_error(kind: ErrorKind, message: str) -> CommandResult:
            return done(CommandResult(status="error", error_kind=kind, error=message))

        if not image:
            return sandbox_error("sandbox", f"{integration.name} declares no docker image")
        try:
            script = await asyncio.to_thread(assemble, integration, self.content_root)
            await self.ensure_image(image)
        except (OSError, RuntimeError) as e:
            return sandbox_error("sandbox", str(e))
        start = time.monotonic()  # pulls and assembly are one-time costs, not execution time

        # Params carry resolved secrets: they travel only on stdin, never in argv, env or logs.
        payload = json.dumps(
            {"command": req.command, "args": req.args, "params": default_params(integration) | req.params},
            default=str,
        ).encode()
        name = f"dbot-{uuid.uuid4().hex[:16]}"
        try:
            proc = await asyncio.create_subprocess_exec(
                self.docker,
                *self._argv(name, script.parent, image, integration.name, req.proxy),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except OSError as e:
            return sandbox_error("sandbox", f"cannot start docker: {e}")

        communicate = asyncio.ensure_future(proc.communicate(payload))
        try:
            stdout, stderr = await asyncio.wait_for(asyncio.shield(communicate), req.timeout_s)
        except TimeoutError:
            await self._kill(name)
            try:
                await asyncio.wait_for(communicate, KILL_GRACE_S)
            except TimeoutError:
                proc.kill()
            return sandbox_error("timeout", f"{req.command} exceeded {req.timeout_s:g}s; container killed")
        except asyncio.CancelledError:
            await self._kill(name)
            communicate.cancel()
            raise

        rc = proc.returncode
        out = _last_json_line(stdout)
        if out is None:
            if rc is not None and (rc == 137 or rc > 128 or rc < 0):
                return sandbox_error("killed", f"container killed (exit {rc}; OOM or signal)")
            tail = stderr.decode("utf-8", "replace")[-STDERR_TAIL:]
            return sandbox_error("sandbox", f"no result from runner (exit {rc}): {tail}")
        return done(map_output(req.command, out))
