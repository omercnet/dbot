"""Stack for the real-LLM evals: certified replay proxy, a dbot.yaml, a warmed catalog and sandbox images.

The MCP server runs as the eval agent's stdio child process (through mcp_tap.py), configured purely by environment:
``Gateway.from_env`` reads DBOT_CONFIG, DBOT_DATA_DIR, DBOT_EGRESS_PROXY and DBOT_SANDBOX_CA_BUNDLE.

The model is a local ollama model: DBOT_EVAL_MODEL (default ollama:granite4:3b) served at OLLAMA_BASE_URL
(default http://127.0.0.1:11434/v1).
"""

import asyncio
import json
import os
import shutil
import urllib.request
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest
import yaml  # type: ignore[import-untyped]

from dbot.catalog import Catalog, index_content
from dbot.catalog.index import CACHE_FILENAME
from dbot.control.approvals import Approvals
from dbot.control.audit import AuditLog
from dbot.control.config import load_config
from dbot.gateway import Actor, Gateway
from dbot.sandbox.docker import DockerSandbox
from dbot.sandbox.types import CommandResult
from tests.certified.conftest import CONTENT, EXAMPLE_CONFIG, ReplayProxy, run_replay
from tests.certified.conftest import FIXTURES as CERTIFIED_FIXTURES
from tests.certified.specs import GOOD_KEY

HERE = Path(__file__).resolve().parent
INSTANCES = ("abuseipdb", "virustotal", "greynoise")
# The example config's `env://` names; the server gets the replay proxy's "good" key in each.
KEY_ENVS = ("ABUSEIPDB_API_KEY", "VIRUSTOTAL_API_KEY", "GREYNOISE_API_KEY")
MODEL = os.environ.get("DBOT_EVAL_MODEL", "ollama:granite4:3b")
DEFAULT_OLLAMA_BASE_URL = "http://127.0.0.1:11434/v1"

# One line per scenario attempt, printed in the terminal summary.
REPORT: list[str] = []


def pytest_terminal_summary(terminalreporter: pytest.TerminalReporter) -> None:
    if REPORT:
        terminalreporter.section("llm eval attempts")
        for line in REPORT:
            terminalreporter.write_line(line)


@pytest.fixture(scope="session")
def eval_model() -> Iterator[str]:
    """The model string. Skips only if the ollama server is unreachable or the model is not pulled."""
    if not MODEL.startswith("ollama:"):
        yield MODEL
        return
    base = os.environ.get("OLLAMA_BASE_URL") or DEFAULT_OLLAMA_BASE_URL
    name = MODEL.removeprefix("ollama:")
    name = name if ":" in name else f"{name}:latest"
    try:
        with urllib.request.urlopen(base.removesuffix("/").removesuffix("/v1") + "/api/tags", timeout=10) as resp:  # noqa: S310
            pulled = {m.get("name") for m in json.load(resp).get("models", [])}
    except OSError as e:
        pytest.skip(f"ollama unreachable at {base}: {e}")
    if name not in pulled:
        pytest.skip(f"ollama model {name} is not pulled (ollama pull {name})")
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("OLLAMA_BASE_URL", base)
        yield MODEL


@dataclass(frozen=True)
class Stack:
    proxy: ReplayProxy
    config: Path
    catalog_cache: Path  # a prebuilt catalog index, copied into each fresh DBOT_DATA_DIR
    assembled: Path  # DBOT_CACHE_DIR

    def env(self, data_dir: Path) -> dict[str, str]:
        """Environment for one MCP server run with its own DBOT_DATA_DIR (audit, approvals)."""
        if self.catalog_cache.exists():
            (data_dir / "cache").mkdir(parents=True, exist_ok=True)
            shutil.copy(self.catalog_cache, data_dir / "cache" / CACHE_FILENAME)
        return {
            "DBOT_CONFIG": str(self.config),
            "DBOT_DATA_DIR": str(data_dir),
            "DBOT_CACHE_DIR": str(self.assembled),
            "DBOT_EGRESS_PROXY": self.proxy.url,
            "DBOT_SANDBOX_CA_BUNDLE": str(self.proxy.ca_bundle),
            "DBOT_MCP_USER": "llm-eval",
        } | dict.fromkeys(KEY_ENVS, GOOD_KEY)


def write_config(path: Path) -> Path:
    """The example's abuseipdb, virustotal and greynoise instances (env:// keys) and its policy."""
    example = yaml.safe_load(EXAMPLE_CONFIG.read_text())
    instances = [i for i in example["instances"] if i["name"] in INSTANCES]
    assert {i["name"] for i in instances} == set(INSTANCES)
    config = {"instances": instances} | ({"policy": example["policy"]} if "policy" in example else {})
    path.write_text(yaml.safe_dump(config))
    return path


async def _warm(gateway: Gateway) -> dict[str, CommandResult]:
    actor = Actor(id="llm-eval-warmup", kind="human", roles={"analyst"})
    results = {}
    for name in INSTANCES:
        result = await gateway.invoke(name, "test-module", {}, "llm eval warm-up", actor)
        assert isinstance(result, CommandResult), result
        results[name] = result
    return results


@pytest.fixture(scope="session")
def stack(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Stack]:
    """Replay proxy over the certified fixtures plus tests/llm/fixtures, with images pulled and the catalog indexed.

    The warm-up runs `test-module` on every instance through the real gateway, so a broken proxy, CA or image
    fails here instead of being blamed on the model.
    """
    if not CONTENT.exists():
        pytest.skip("content submodule not initialized")
    root = tmp_path_factory.mktemp("llm-stack")
    fixtures = root / "fixtures"
    fixtures.mkdir()
    for src in [*sorted(CERTIFIED_FIXTURES.glob("*.json")), *sorted((HERE / "fixtures").glob("*.json"))]:
        shutil.copy(src, fixtures / src.name)
    (root / "mitmproxy").mkdir()
    with run_replay(fixtures, root / "mitmproxy") as proxy:
        stack = Stack(
            proxy=proxy,
            config=write_config(root / "dbot.yaml"),
            catalog_cache=root / "catalog" / CACHE_FILENAME,
            assembled=root / "assembled",
        )
        warm_data = root / "warmup-data"
        with pytest.MonkeyPatch.context() as mp:
            for key, value in stack.env(warm_data).items():
                mp.setenv(key, value)
            catalog = Catalog(index_content(CONTENT, cache_dir=stack.catalog_cache.parent))
            gateway = Gateway(
                load_config(stack.config),
                catalog,
                DockerSandbox(CONTENT, ca_bundle=proxy.ca_bundle),
                Approvals(warm_data / "dbot.db"),
                AuditLog(warm_data / "audit"),
            )
            results = asyncio.run(_warm(gateway))
        for name, result in results.items():
            assert result.status == "ok", f"warm-up test-module on {name}: {result.error_kind} {result.error}"
        yield stack
