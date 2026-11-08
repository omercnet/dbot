"""Run the dbot MCP server and log the name of every `tools/call` request it receives.

Usage: python mcp_tap.py <log file>. The server inherits stdout; stdin is forwarded line by line.
"""

import json
import os
import subprocess
import sys

# The server never needs the LLM key; keep it out of its environment.
env = {k: v for k, v in os.environ.items() if k != "OPENCODE_API_KEY"}
with open(sys.argv[1], "a", encoding="utf-8") as log:
    server = subprocess.Popen([sys.executable, "-m", "dbot.server"], stdin=subprocess.PIPE, env=env)
    assert server.stdin is not None
    for line in sys.stdin.buffer:
        try:
            msg = json.loads(line)
        except ValueError:
            msg = None
        if isinstance(msg, dict) and msg.get("method") == "tools/call":
            log.write(msg["params"]["name"] + "\n")
            log.flush()
        server.stdin.write(line)
        server.stdin.flush()
    server.stdin.close()
    sys.exit(server.wait())
