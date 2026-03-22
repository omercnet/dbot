# dbot

> Run 1,000+ XSOAR security integrations as MCP tools. No XSOAR required.

dbot is an open-source IR agent and MCP server that takes the entire
[demisto/content](https://github.com/demisto/content) integration library
(~1,100 security tool integrations) and runs them standalone -- no XSOAR,
no Cortex, no license.

It ships as both an **MCP server** (for Claude Desktop, PydanticAI, or any
MCP client) and a **standalone IR agent** with chat, autonomous responder,
and web UI.

---

## Quickstart

```bash
# Clone + setup
git clone https://github.com/omercnet/dbot.git && cd dbot
git submodule update --init
hatch env create

# Launch web UI
hatch run dbot-web
# -> http://127.0.0.1:7932           (chat UI, needs LLM API key)
# -> http://127.0.0.1:7932/settings  (configure everything via browser)
```

See [docs/quickstart.md](docs/quickstart.md) for the full setup guide.

---

## Four Ways to Use dbot

### 1. Web UI (recommended for getting started)

```bash
OPENAI_API_KEY=sk-... hatch run dbot-web
```

Opens PydanticAI's built-in chat interface at `http://127.0.0.1:7932` with:
- Chat with tool call visualization (collapsible input/output)
- Model selector dropdown
- Streaming responses
- Settings page at `/settings` (LLM config, guardrails, credentials, packs)

### 2. Interactive Chat (terminal)

```bash
OPENAI_API_KEY=sk-... hatch run dbot-chat
```

Terminal REPL for IR investigations. Multi-turn conversation with history.

### 3. Autonomous Responder

```bash
hatch run dbot-respond alert.json --output markdown
```

Feeds an alert JSON file to the agent. It autonomously investigates using
available tools and produces a structured IR report (markdown, JSON, or JSONL).

### 4. MCP Server (for external clients)

```bash
hatch run python -m dbot.server
```

Stdio MCP server exposing 3 tools (`search_tools`, `get_tool_schema`,
`invoke_tool`) for Claude Desktop, PydanticAI, or any MCP client.

---

## Architecture

```
            User / Alert Source
                   |
        +----------+----------+
        |                     |
   dbot-web/chat         dbot-respond
   (interactive)         (autonomous)
        |                     |
   ChatAgent            ResponderAgent
        |                     |
        +----------+----------+
                   |
          FunctionToolset (native)
          +-- search_tools()
          +-- get_tool_schema()
          +-- invoke_tool()
                   |
          +--------+--------+
          |                 |
       Catalog          Executor
       (YAML index)     (subprocess)
          |                 |
     demisto/content    demistomock
```

The agent uses **native PydanticAI tools** (not MCP) for zero-overhead
direct Python calls. The MCP server exists separately for external clients.

---

## Project Structure

```
dbot/
+-- dbot/
|   +-- server.py              # FastMCP entrypoint (external clients)
|   +-- audit.py               # JSON-lines audit logger
|   +-- agent/
|   |   +-- chat.py            # ChatAgent (interactive + streaming)
|   |   +-- responder.py       # ResponderAgent (autonomous + HITL)
|   |   +-- guardrails.py      # FunctionToolset + FilteredToolset
|   |   +-- models.py          # Alert, IRReport, Verdict, Severity
|   |   +-- deps.py            # IRDeps (RunContext dependencies)
|   |   +-- report.py          # Markdown/JSON/JSONL report renderer
|   |   +-- web.py             # Web UI (PydanticAI to_web + settings)
|   |   +-- cli.py             # CLI: dbot-chat, dbot-respond, dbot-watch, dbot-web
|   |   +-- ingestion/         # Alert loaders (file, stdin, watchdog)
|   +-- config/
|   |   +-- db.py              # SQLite config store
|   |   +-- encryption.py      # Fernet credential encryption
|   |   +-- api.py             # Settings REST API (10 routes)
|   |   +-- settings.html      # Settings UI (self-contained)
|   |   +-- models.py          # Config section Pydantic models
|   +-- runtime/               # demistomock shim, CSP loader, executor
|   +-- registry/              # YAML indexer, search catalog
|   +-- credentials/           # Credential store
+-- content/                   # git submodule -> demisto/content
+-- config/                    # dbot.db, .dbot-key, credentials.yaml
+-- tests/                     # 226 tests
+-- docs/
+-- pyproject.toml
```

---

## Development

dbot uses [hatch](https://hatch.pypa.io/) for project management.

```bash
hatch run test           # full test suite (226 tests)
hatch run test-quick     # skip integration tests
hatch run lint           # ruff check + format check
hatch run fmt            # autofix + format
hatch run typecheck      # mypy strict
hatch run check          # lint + typecheck + test (CI gate)
hatch version            # show current version
hatch version minor      # bump 0.1.0 -> 0.2.0
```

---

## Stack

| Concern | Choice |
|---------|--------|
| Agent framework | [PydanticAI](https://github.com/pydantic/pydantic-ai) |
| MCP server | [FastMCP](https://github.com/jlowin/fastmcp) |
| Integration source | [demisto/content](https://github.com/demisto/content) (MIT) |
| Config/credentials | SQLite + Fernet encryption |
| Web UI | PydanticAI `agent.to_web()` + custom settings page |
| Execution model | Subprocess per invocation (in-process for dev) |
| Python | 3.13+ |
| Packaging | hatch + uv + pyproject.toml |

---

## Documentation

- [Quickstart](docs/quickstart.md) -- full setup guide
- [Architecture](docs/architecture.md) -- how dbot works internally
- [Credentials](docs/credentials.md) -- configuring API keys and secrets
- [Integrations](docs/integrations.md) -- adding and validating integrations

---

## License

MIT
