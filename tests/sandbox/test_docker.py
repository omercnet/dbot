"""Real containers. Skipped when docker is unavailable."""

import re
import subprocess
import time
from pathlib import Path

import pytest

from dbot.sandbox.docker import DockerSandbox
from dbot.sandbox.types import ExecRequest
from tests.sandbox.conftest import CONTENT, docker_available, make_content, real_integration

pytestmark = [
    pytest.mark.docker,
    pytest.mark.skipif(not docker_available(), reason="docker daemon not available"),
]


def dbot_containers() -> set[str]:
    out = subprocess.run(
        ["docker", "ps", "--filter", "name=dbot-", "--format", "{{.Names}}"],  # noqa: S607
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return set(out.split())


async def test_helloworld_say_hello_ok() -> None:
    integ = real_integration("Packs/HelloWorld/Integrations/HelloWorld/HelloWorld.yml")
    src = Path(integ.py_path).read_text()
    key = re.search(r'^DUMMY_API_KEY = "([^"]+)"', src, re.MULTILINE)
    assert key
    req = ExecRequest(
        command="helloworld-say-hello",
        args={"name": "dbot"},
        params={"url": "https://api.example.invalid", "credentials": {"password": key.group(1)}},
    )
    r = await DockerSandbox(CONTENT).run(integ, req)
    assert r.status == "ok", r.error
    assert r.context == {"hello": "Hello dbot"}
    assert r.entries[0].human_readable == "## Hello dbot"
    assert r.image == integ.dockerimage
    assert r.duration_ms > 0


async def test_helloworld_bad_credentials_surface_the_error() -> None:
    integ = real_integration("Packs/HelloWorld/Integrations/HelloWorld/HelloWorld.yml")
    req = ExecRequest(command="helloworld-say-hello", args={"name": "x"}, params={"url": "https://a.invalid"})
    r = await DockerSandbox(CONTENT).run(integ, req)
    assert r.status == "error"
    assert r.error
    assert "Invalid Credentials" in r.error


async def test_abuseipdb_unreachable_server_is_integration_error() -> None:
    integ = real_integration("Packs/AbuseDB/Integrations/AbuseDB/AbuseDB.yml")
    req = ExecRequest(
        command="ip",
        args={"ip": "8.8.8.8"},
        params={"server": "http://127.0.0.1:9", "credentials": {"password": "canary-secret-value"}},
    )
    r = await DockerSandbox(CONTENT).run(integ, req)
    assert (r.status, r.error_kind) == ("error", "integration")
    assert r.error
    assert "127.0.0.1" in r.error


async def test_microsoft_apimodule_integration_gets_past_import() -> None:
    integ = real_integration(
        "Packs/AzureActiveDirectory/Integrations/AzureADIdentityProtection/AzureADIdentityProtection.yml"
    )
    assert "MicrosoftApiModule" in integ.apimodules
    r = await DockerSandbox(CONTENT).run(integ, ExecRequest(command="dbot-nonexistent-command"))
    assert r.error_kind == "integration", r.error
    assert r.error
    assert "not implemented" in r.error


async def test_timeout_kills_container(tmp_path: Path) -> None:
    integ = make_content(tmp_path, "import time\ndef main():\n    time.sleep(120)\n")
    sandbox = DockerSandbox(tmp_path)
    await sandbox.ensure_image(integ.dockerimage)
    before = dbot_containers()
    start = time.monotonic()
    r = await sandbox.run(integ, ExecRequest(command="x", timeout_s=3))
    elapsed = time.monotonic() - start
    assert r.error_kind == "timeout"
    assert elapsed < 3 + 2
    assert dbot_containers() - before == set()


async def test_stray_stdout_does_not_break_protocol(tmp_path: Path) -> None:
    src = """import os, subprocess
def main():
    print("{\\"not\\": \\"the result\\"}")
    os.write(1, b"raw\\n")
    subprocess.run(["echo", "child"])
    return_results(CommandResults(outputs_prefix="T", outputs={"v": 1}, readable_output="fine"))
"""
    integ = make_content(tmp_path, src)
    r = await DockerSandbox(tmp_path).run(integ, ExecRequest(command="t-cmd"))
    assert r.status == "ok", r.error
    assert r.context == {"T": {"v": 1}}
    assert r.entries[0].human_readable == "fine"


async def test_oom_is_killed(tmp_path: Path) -> None:
    integ = make_content(
        tmp_path, "def main():\n    x = bytearray(400 * 1024 * 1024)\n    x[::4096] = b'1' * len(x[::4096])\n"
    )
    r = await DockerSandbox(tmp_path, memory="64m").run(integ, ExecRequest(command="x"))
    assert r.error_kind == "killed", r.error


async def test_missing_image_is_sandbox_error(tmp_path: Path) -> None:
    integ = make_content(tmp_path, "def main():\n    pass\n").model_copy(
        update={"dockerimage": "dbot-test/does-not-exist:0"}
    )
    r = await DockerSandbox(tmp_path).run(integ, ExecRequest(command="x"))
    assert r.error_kind == "sandbox"
