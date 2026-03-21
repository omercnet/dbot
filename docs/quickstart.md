# Quickstart

Get dbot running in under 5 minutes.

---

## Prerequisites

- Python 3.13+
- [uv](https://docs.astral.sh/uv/) (Python package manager)
- git

---

## 1. Clone the Repository

```bash
git clone https://github.com/yourorg/dbot.git
cd dbot
```

---

## 2. Initialize the Content Submodule

dbot uses [demisto/content](https://github.com/demisto/content) as a git
submodule with sparse checkout. Only the packs you need are downloaded.

```bash
git submodule update --init
```

The submodule is already configured for sparse checkout with ~24 priority
packs. To add more packs:

```bash
cd content
git sparse-checkout add Packs/YourPackName
cd ..
```

Verify the submodule is working:

```bash
ls content/Packs/HelloWorld/Integrations/HelloWorld/HelloWorld.py
ls content/Packs/Base/Scripts/CommonServerPython/CommonServerPython.py
```

---

## 3. Install Dependencies

```bash
uv sync --all-extras
```

This installs all runtime and dev dependencies (FastMCP, PydanticAI, pytest,
ruff, mypy).

---

## 4. Configure Credentials

Copy the example credential file and fill in your API keys:

```bash
cp config/credentials.yaml.example config/credentials.yaml
```

Edit `config/credentials.yaml`:

```yaml
VirusTotal:
  apikey: ${VT_API_KEY}       # reads from env var

Shodan:
  apikey: your-actual-key-here  # or hardcode (less secure)
```

Set environment variables:

```bash
export VT_API_KEY=your_virustotal_key
export SHODAN_API_KEY=your_shodan_key
```

See [credentials.md](credentials.md) for the full credential configuration
guide.

---

## 5. (Optional) Configure Enabled Packs

By default, dbot indexes all packs in the content submodule. To limit which
packs are available:

```bash
cp config/enabled_packs.yaml.example config/enabled_packs.yaml
```

Edit to list only the packs you want:

```yaml
enabled_packs:
  - VirusTotal
  - Shodan_v2
  - CrowdStrikeFalcon
```

---

## 6. Run the Server

```bash
uv run python -m dbot.server
```

The server starts on stdio transport by default (for MCP client integration).

---

## 7. Connect from Claude Desktop

Add to your `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "dbot": {
      "command": "uv",
      "args": ["run", "python", "-m", "dbot.server"],
      "cwd": "/absolute/path/to/dbot"
    }
  }
}
```

Restart Claude Desktop. You should see dbot's 3 tools available.

---

## 8. Connect from PydanticAI

```python
from pydantic_ai import Agent
from pydantic_ai.mcp import MCPServerStdio

server = MCPServerStdio("uv", args=["run", "python", "-m", "dbot.server"])
agent = Agent("openai:gpt-4o", toolsets=[server])

async with agent.run("Check if 8.8.8.8 is malicious") as result:
    print(result.output)
```

---

## 9. Run Tests

```bash
hatch run test          # full suite (157 tests)
hatch run test-quick    # skip integration tests
hatch run lint          # ruff check + format check
hatch run fmt           # autofix + format
hatch run check         # lint + typecheck + test (CI gate)
```

All 157 tests should pass.

---

## 10. Execution Modes

dbot supports two execution modes:

| Mode | Env Var | Use Case |
|------|---------|----------|
| `inprocess` (default) | `DBOT_EXECUTION_MODE=inprocess` | Fast, for development |
| `subprocess` | `DBOT_EXECUTION_MODE=subprocess` | Isolated, for production |

```bash
# Production mode
DBOT_EXECUTION_MODE=subprocess uv run python -m dbot.server
```

---

## Troubleshooting

### "content submodule not initialized"

```bash
git submodule update --init
```

### "CommonServerPython.py not found"

The content submodule sparse checkout may not include the Base pack:

```bash
cd content
git sparse-checkout add Packs/Base/Scripts/CommonServerPython
cd ..
```

### "Environment variable 'X' not set"

Your credentials.yaml references an env var that isn't set. Either set it
or replace `${VAR}` with the actual value in credentials.yaml.

### Import errors from integrations

Some integrations have Python dependencies not included in dbot. Install
them as needed:

```bash
uv pip install <missing-package>
```

---

Next: [Architecture Guide](architecture.md) | [Credential Setup](credentials.md)
