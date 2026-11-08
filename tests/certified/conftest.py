"""Replay proxy and gateway wiring for the certified integration contract tests."""

import shutil
import socket
import subprocess
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
import yaml  # type: ignore[import-untyped]

from dbot.catalog import Catalog
from dbot.catalog.index import parse_integration
from dbot.control.approvals import Approvals
from dbot.control.audit import AuditLog
from dbot.control.config import load_config
from dbot.gateway import Gateway
from dbot.sandbox.docker import DockerSandbox
from tests.certified.specs import BAD_KEY, GOOD_KEY, SPECS

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
CONTENT = REPO / "content"
FIXTURES = HERE / "fixtures"
EXAMPLE_CONFIG = REPO / "deploy" / "dbot.yaml.example"
STARTUP_TIMEOUT_S = 30.0


def docker_available() -> bool:
    if not shutil.which("docker"):
        return False
    return subprocess.run(["docker", "info"], capture_output=True, check=False).returncode == 0  # noqa: S607


def bridge_gateway_ip() -> str:
    """The host's address on docker's default bridge: reachable from sandboxes on Linux (incl. GitHub runners)."""
    out = subprocess.run(
        ["docker", "network", "inspect", "bridge", "-f", "{{(index .IPAM.Config 0).Gateway}}"],  # noqa: S607
        capture_output=True,
        text=True,
        check=True,
    )
    return out.stdout.strip()


def _free_port(host: str) -> int:
    with socket.socket() as s:
        s.bind((host, 0))
        return int(s.getsockname()[1])


def _mitmdump() -> str:
    path = Path(sys.executable).parent / "mitmdump"
    found = str(path) if path.exists() else shutil.which("mitmdump")
    if not found:
        pytest.fail("mitmdump not found; install the dev dependencies (uv sync)")
    return found


@dataclass(frozen=True)
class ReplayProxy:
    url: str
    ca_bundle: Path
    log: Path


@contextmanager
def run_replay(fixtures: Path, confdir: Path) -> Iterator[ReplayProxy]:
    """mitmdump on the docker bridge gateway, answering only from `fixtures` (lazy upstream: never dials out)."""
    if not docker_available():
        pytest.skip("docker daemon not available")
    host = bridge_gateway_ip()
    port = _free_port(host)
    log = confdir / "mitmdump.log"
    argv = [
        _mitmdump(),
        "--listen-host", host,
        "--listen-port", str(port),
        "--set", f"confdir={confdir}",
        "--set", "connection_strategy=lazy",
        "--set", "upstream_cert=false",
        "--set", "flow_detail=1",
        "-s", str(HERE / "replay_addon.py"),
        "--set", f"replay_fixtures={fixtures}",
    ]  # fmt: skip
    with log.open("wb") as out:
        proc = subprocess.Popen(argv, stdout=out, stderr=subprocess.STDOUT)  # noqa: S603
    ca = confdir / "mitmproxy-ca-cert.pem"
    deadline = time.monotonic() + STARTUP_TIMEOUT_S
    try:
        while True:
            if proc.poll() is not None:
                pytest.fail(f"mitmdump exited {proc.returncode}: {log.read_text()[-2000:]}")
            if ca.exists():
                try:
                    socket.create_connection((host, port), timeout=1).close()
                    break
                except OSError:
                    pass
            if time.monotonic() > deadline:
                pytest.fail(f"mitmdump did not start: {log.read_text()[-2000:]}")
            time.sleep(0.2)
        ca.chmod(0o644)  # sandboxes run as uid 65534
        yield ReplayProxy(url=f"http://{host}:{port}", ca_bundle=ca, log=log)
    finally:
        proc.terminate()
        try:
            proc.wait(10)
        except subprocess.TimeoutExpired:
            proc.kill()


@pytest.fixture(scope="session")
def replay(tmp_path_factory: pytest.TempPathFactory) -> Iterator[ReplayProxy]:
    with run_replay(FIXTURES, tmp_path_factory.mktemp("mitmproxy")) as proxy:
        yield proxy


@pytest.fixture(scope="session")
def catalog() -> Catalog:
    if not CONTENT.exists():
        pytest.skip("content submodule not initialized")
    integrations = []
    for spec in SPECS:
        parsed = parse_integration(CONTENT / spec.yml)
        assert parsed is not None, spec.yml
        integrations.append(parsed[0])
    return Catalog(integrations)


@pytest.fixture(scope="session")
def cache_dir(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Path]:
    cache = tmp_path_factory.mktemp("assembled")
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("DBOT_CACHE_DIR", str(cache))
        mp.delenv("DBOT_CONTENT_COMMIT", raising=False)
        yield cache


def write_config(path: Path) -> Path:
    """A dbot.yaml with a good-key and a revoked-key instance per spec, and the policy shipped in the example
    (global and per instance)."""
    example = yaml.safe_load(EXAMPLE_CONFIG.read_text())
    shipped = {i["name"]: i["policy"] for i in example["instances"] if "policy" in i}
    instances = []
    for spec in SPECS:
        for name, key_env in ((spec.instance, spec.key_env), (f"{spec.instance}-revoked", f"{spec.key_env}_REVOKED")):
            inst = spec.instance_config(name, key_env)
            if spec.instance in shipped:
                inst["policy"] = shipped[spec.instance]
            instances.append(inst)
    config: dict[str, Any] = {"instances": instances}
    if "policy" in example:
        config["policy"] = example["policy"]
    path.write_text(yaml.safe_dump(config))
    return path


@pytest.fixture
def gateway(
    replay: ReplayProxy,
    catalog: Catalog,
    cache_dir: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Gateway:
    """The real gateway over the real DockerSandbox, egressing through the replay proxy."""
    monkeypatch.setenv("DBOT_EGRESS_PROXY", replay.url)
    for spec in SPECS:
        monkeypatch.setenv(spec.key_env, GOOD_KEY)
        monkeypatch.setenv(f"{spec.key_env}_REVOKED", BAD_KEY)
    config = load_config(write_config(tmp_path / "dbot.yaml"))
    sandbox = DockerSandbox(CONTENT, ca_bundle=replay.ca_bundle)
    return Gateway(config, catalog, sandbox, Approvals(tmp_path / "dbot.db"), AuditLog(tmp_path / "audit"))
