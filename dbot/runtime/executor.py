"""Integration execution engine — in-process and subprocess modes."""

import asyncio
import importlib.util
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any

from dbot.runtime.demistomock import DemistoMock, _reset_mock, _set_mock

logger = logging.getLogger("dbot.executor")


async def execute_inprocess(
    integration_py: Path,
    command: str,
    args: dict[str, Any],
    params: dict[str, Any],
    timeout: float = 30.0,
) -> dict[str, Any]:
    """Execute an integration command in-process.

    Creates a fresh DemistoMock, sets it as the current context,
    imports the integration module, calls main(), and captures results.
    """
    mock = DemistoMock(command=command, args=args, params=params)
    token = _set_mock(mock)

    try:
        result = await asyncio.wait_for(
            asyncio.get_event_loop().run_in_executor(None, _run_integration, integration_py, mock),
            timeout=timeout,
        )
        return result
    except TimeoutError:
        return {
            "success": False,
            "error": f"Timeout after {timeout}s",
            "error_type": "TimeoutError",
            "results": mock.get_results(),
            "logs": mock.get_logs(),
        }
    except Exception as e:
        logger.exception("Integration execution failed: %s", e)
        return {
            "success": False,
            "error": str(e),
            "error_type": type(e).__name__,
            "results": mock.get_results(),
            "logs": mock.get_logs(),
        }
    finally:
        _reset_mock(token)


def _run_integration(integration_py: Path, mock: DemistoMock) -> dict[str, Any]:
    token = _set_mock(mock)
    try:
        module_name = f"_dbot_integration_{integration_py.stem}_{id(mock)}"
        spec = importlib.util.spec_from_file_location(module_name, str(integration_py))
        if spec is None or spec.loader is None:
            raise ImportError(f"Could not load integration from {integration_py}")

        module = importlib.util.module_from_spec(spec)

        integration_dir = str(integration_py.parent)
        sys.path.insert(0, integration_dir)

        try:
            spec.loader.exec_module(module)

            if hasattr(module, "main"):
                module.main()
            else:
                logger.warning("Integration %s has no main() function", integration_py.stem)
        finally:
            if integration_dir in sys.path:
                sys.path.remove(integration_dir)
            sys.modules.pop(module_name, None)

        return {
            "success": True,
            "results": mock.get_results(),
            "logs": mock.get_logs(),
        }
    except Exception as e:
        logger.exception("Integration runtime error: %s", e)
        return {
            "success": False,
            "error": str(e),
            "error_type": type(e).__name__,
            "results": mock.get_results(),
            "logs": mock.get_logs(),
        }
    finally:
        _reset_mock(token)


async def execute_subprocess(
    integration_py: Path,
    command: str,
    args: dict[str, Any],
    params: dict[str, Any],
    timeout: float = 30.0,
    content_root: Path | None = None,
) -> dict[str, Any]:
    """Execute integration in isolated subprocess.

    Spawns: python -m dbot.runtime.runner <integration.py>
    Sends {command, args, params} via stdin.
    Reads {success, results, logs, error} from stdout.
    """
    payload = json.dumps({"command": command, "args": args, "params": params})

    env = {**os.environ, "DBOT_RUNNER": "1"}
    if content_root:
        env["DBOT_CONTENT_ROOT"] = str(content_root)

    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "dbot.runtime.runner",
        str(integration_py),
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )

    try:
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(payload.encode()),
            timeout=timeout,
        )
    except TimeoutError:
        proc.kill()
        await proc.communicate()
        return {
            "success": False,
            "error": f"Timeout after {timeout}s",
            "results": [],
            "logs": [],
        }

    if proc.returncode != 0:
        return {
            "success": False,
            "error": stderr.decode().strip(),
            "results": [],
            "logs": [],
        }

    try:
        return json.loads(stdout.decode())
    except json.JSONDecodeError:
        return {
            "success": False,
            "error": f"Invalid JSON output: {stdout.decode()[:200]}",
            "results": [],
            "logs": [],
        }
