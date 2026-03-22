# Quickstart

Get dbot running in under 5 minutes.

---

## Prerequisites

- Python 3.13+
- Node.js 22+
- [hatch](https://hatch.pypa.io/) (`pip install hatch`)
- git

---

## 1. Clone the Repository

```bash
git clone https://github.com/omercnet/dbot.git
cd dbot
```

---

## 2. Initialize the Content Submodule

dbot uses [demisto/content](https://github.com/demisto/content) as a git
submodule with sparse checkout. Only the packs you need are downloaded.

```bash
git submodule update --init
```

The submodule is pre-configured with ~24 priority packs. To add more:

```bash
cd content
git sparse-checkout add Packs/YourPackName
cd ..
```

---

## 3. Set Up the Environment

```bash
hatch env create
```

Then install the React SPA dependencies:

```bash
cd dbot/ui && npm install && cd ../..

This creates an isolated virtualenv with all runtime and dev dependencies
(PydanticAI, FastMCP, cryptography, ruff, pytest, etc).

---

## 4. Launch the Web UI

```bash
hatch run dbot-web
```

Open `http://127.0.0.1:7932` in your browser.

- Without an LLM API key: you'll see the **settings page** at `/settings`
  where you can configure everything via the browser.
- With an API key: the full **chat UI** loads at `/` with tool call
  visualization, model selector, and streaming responses.

### Setting your API key

Option A -- environment variable:
```bash
OPENAI_API_KEY=sk-... hatch run dbot-web
```

Option B -- via the settings page:
1. Go to `http://127.0.0.1:7932/settings`
2. Click the **LLM** tab, set your default model
3. Click the **Credentials** tab, add your API keys
4. Restart the server

---

## 5. Alternative: Terminal Chat

```bash
OPENAI_API_KEY=sk-... hatch run dbot-chat
```

Interactive REPL for IR investigations. Commands:
- Type your question and press Enter
- `/reset` -- clear conversation history
- `/quit` -- exit

---

## 6. Alternative: Autonomous Responder

Investigate an alert autonomously:

```bash
hatch run dbot-respond alert.json
hatch run dbot-respond alert.json --output json --output-file report.json
hatch run dbot-respond alert.json --no-hitl --block-category Endpoint
```

Or pipe from stdin:

```bash
echo '{"id":"alert-1","title":"Suspicious IP","severity":"high"}' | hatch run dbot-respond
```

---

## 7. Alternative: File Watcher

Watch a directory for new alert JSON files:

```bash
hatch run dbot-watch ./alerts/ --output-dir ./reports/
```

New `.json` files dropped into `./alerts/` are automatically investigated.
Reports appear in `./reports/`. Processed files move to `./alerts/done/`.

---

## 8. Alternative: MCP Server (external clients)

For Claude Desktop, add to `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "dbot": {
      "command": "hatch",
      "args": ["run", "python", "-m", "dbot.server"],
      "cwd": "/absolute/path/to/dbot"
    }
  }
}
```

For PydanticAI:

```python
from pydantic_ai import Agent
from pydantic_ai.mcp import MCPServerStdio

server = MCPServerStdio("hatch", args=["run", "python", "-m", "dbot.server"])
agent = Agent("openai:gpt-4o", toolsets=[server])

async with agent.run("Check if 8.8.8.8 is malicious") as result:
    print(result.output)
```

---

## 9. Run Tests

```bash
hatch run test           # full suite (307 tests)
hatch run test-quick     # skip integration tests (faster)
hatch run lint           # ruff check + format check
hatch run fmt            # autofix + format
hatch run typecheck      # mypy strict
hatch run check          # ruff + biome + tsc + tests (dev gate)
hatch run check-strict   # adds mypy
hatch run ci             # full pipeline: lint + mypy + build-ui + all tests
hatch run hooks          # install pre-commit hooks
```

### Frontend hot reload

To run the React dev server with HMR (proxies API calls to the backend at :7932):

```bash
# Terminal 1 -- start the backend
hatch run dbot-web

# Terminal 2 -- start the frontend dev server
cd dbot/ui && npm run dev
# -> http://localhost:5173  (hot reload)
```

For production, `hatch run build-ui` compiles the SPA into `dbot/ui/dist/`,
which the Starlette server then serves as static files.

---

## 10. Configuration

dbot stores all config in a SQLite database (`config/dbot.db`), manageable
via the web UI at `/settings` or the REST API.

### Via web UI

Go to `http://127.0.0.1:7932/settings`. Six tabs:

| Tab | What you configure |
|-----|-------------------|
| General | Execution mode, audit log path |
| LLM | Default model, available models, temperature, max tokens |
| Guardrails | Max tool calls, timeout, blocked categories/tools |
| Packs | Which integration packs to index |
| Credentials | API keys per pack (encrypted at rest) |
| Pack Inventory | Read-only view of indexed packs + command counts |

### Via REST API

```bash
# Get all settings
curl http://127.0.0.1:7932/api/settings

# Update LLM config
curl -X PUT http://127.0.0.1:7932/api/settings/llm \
  -H 'Content-Type: application/json' \
  -d '{"default_model": "anthropic:claude-sonnet-4-5", "temperature": 0.1, "max_tokens": 4096, "available_models": {}}'

# Add credentials
curl -X PUT http://127.0.0.1:7932/api/settings/credentials/VirusTotal \
  -H 'Content-Type: application/json' \
  -d '{"apikey": "your-vt-api-key"}'

# Test connection
curl -X POST http://127.0.0.1:7932/api/settings/credentials/VirusTotal/test

# List indexed packs
curl http://127.0.0.1:7932/api/packs
```

### Via credentials.yaml (legacy)

On first startup, dbot auto-migrates `config/credentials.yaml` into the
SQLite database. After migration, all credential management happens through
the web UI or API.

```bash
cp config/credentials.yaml.example config/credentials.yaml
# Edit with your API keys, then start dbot-web
```

---

## 11. CLI Reference

| Command | Description |
|---------|-------------|
| `hatch run dbot-web` | Web UI (chat + settings) |
| `hatch run dbot-chat` | Terminal chat REPL |
| `hatch run dbot-respond ALERT.json` | Autonomous investigation |
| `hatch run dbot-watch DIR` | File-drop alert watcher |
| `hatch run python -m dbot.server` | MCP server (stdio) |

### Common flags

```
--model TEXT          LLM model [env: DBOT_LLM_MODEL, default: openai:gpt-4o]
--audit-log PATH     Audit log file [default: dbot-agent-audit.log]
--port INT           Web UI port [default: 7932] (dbot-web only)
--max-calls INT      Max tool calls [default: 30] (dbot-respond/watch)
--block-category TXT Block tools from category (dbot-respond/watch, repeatable)
--no-hitl            Auto-deny dangerous tools (dbot-respond only)
--output FORMAT      Report format: markdown|json|jsonl (dbot-respond only)
```

---

## Troubleshooting

### "No LLM API key configured" on web UI

Set your provider's API key:
```bash
OPENAI_API_KEY=sk-... hatch run dbot-web
# or
ANTHROPIC_API_KEY=sk-... hatch run dbot-web --model anthropic:claude-sonnet-4-5
```

Or configure via `/settings` in the browser.

### "content submodule not initialized"

```bash
git submodule update --init
```

### "CommonServerPython.py not found"

```bash
cd content
git sparse-checkout add Packs/Base/Scripts/CommonServerPython
cd ..
```

### "No module named 'distutils'"

dbot includes a shim for Python 3.12+. If you still see this, ensure you're
running Python 3.13+ (`python --version`).

### Import errors from integrations

Some integrations need additional Python packages:
```bash
hatch run pip install dateparser tldextract
```

---

Next: [Architecture Guide](architecture.md) | [Credential Setup](credentials.md)
