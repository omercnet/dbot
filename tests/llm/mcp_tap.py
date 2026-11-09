"""Run the dbot MCP server and log every `tools/call` request it receives as a JSON line.

Usage: python mcp_tap.py <log file>. The server inherits stdout and the environment; stdin is forwarded line by line.
"""

import json
import subprocess
import sys

with open(sys.argv[1], "a", encoding="utf-8") as log:
    server = subprocess.Popen([sys.executable, "-m", "dbot.mcp"], stdin=subprocess.PIPE)
    assert server.stdin is not None
    for line in sys.stdin.buffer:
        try:
            msg = json.loads(line)
        except ValueError:
            msg = None
        if isinstance(msg, dict) and msg.get("method") == "tools/call":
            params = msg.get("params") or {}
            log.write(json.dumps({"name": params.get("name"), "arguments": params.get("arguments")}) + "\n")
            log.flush()
        server.stdin.write(line)
        server.stdin.flush()
    server.stdin.close()
    sys.exit(server.wait())
