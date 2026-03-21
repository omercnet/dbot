"""Subprocess runner — bootstraps demistomock + CSP, executes integration, returns JSON.

Usage: python -m dbot.runtime.runner <path/to/integration.py>

Reads {command, args, params} from stdin as JSON.
Writes {success, results, logs, error} to stdout as JSON.
"""

import importlib.util
import json
import os
import sys
from pathlib import Path


def main() -> None:
    """Run an integration in this subprocess."""
    if len(sys.argv) < 2:
        print(json.dumps({"success": False, "error": "No integration path provided", "results": [], "logs": []}))
        sys.exit(1)

    integration_path = sys.argv[1]
    payload = json.loads(sys.stdin.read())

    # Bootstrap runtime
    content_root = Path(os.environ.get("DBOT_CONTENT_ROOT", ""))
    if not content_root.exists():
        print(
            json.dumps(
                {
                    "success": False,
                    "error": f"DBOT_CONTENT_ROOT not found: {content_root}",
                    "results": [],
                    "logs": [],
                }
            )
        )
        sys.exit(1)

    from dbot.runtime.common_server import bootstrap_common_modules
    from dbot.runtime.demistomock import DemistoMock, _set_mock

    bootstrap_common_modules(content_root)

    mock = DemistoMock(
        command=payload["command"],
        args=payload.get("args", {}),
        params=payload.get("params", {}),
    )
    _set_mock(mock)

    # Run integration
    try:
        spec = importlib.util.spec_from_file_location("_integration", integration_path)
        if spec is None or spec.loader is None:
            raise ImportError(f"Could not load {integration_path}")

        module = importlib.util.module_from_spec(spec)
        sys.path.insert(0, str(Path(integration_path).parent))
        spec.loader.exec_module(module)

        if hasattr(module, "main"):
            module.main()

        output = {
            "success": True,
            "results": mock.get_results(),
            "logs": mock.get_logs(),
        }
    except Exception as e:
        output = {
            "success": False,
            "error": str(e),
            "error_type": type(e).__name__,
            "results": mock.get_results(),
            "logs": mock.get_logs(),
        }

    # Write result to stdout as JSON
    print(json.dumps(output, default=str))


if __name__ == "__main__":
    main()
