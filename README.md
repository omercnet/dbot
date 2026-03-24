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

Opens a React SPA at `http://127.0.0.1:7932` with:
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
|   |   +-- web.py             # Web UI (Starlette + React SPA + settings)
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
-- tests/                     # 307 tests
+-- docs/
-- dbot/ui/                   # React SPA (Vite + TypeScript)
|   +-- src/                   # App.tsx, main.tsx
|   +-- package.json           # @ai-sdk/react, react 19, vite, biome
|   +-- vite.config.ts
|   +-- tsconfig.json
|   +-- PROTOCOL.md            # Vercel AI DSP wire format
-- biome.json                 # JS/TS lint + format config
-- .editorconfig              # Cross-editor settings
-- .pre-commit-config.yaml    # 11 pre-commit hooks
-- .github/workflows/ci.yml   # GitHub Actions CI
+-- pyproject.toml
```

---

## Development

dbot uses [hatch](https://hatch.pypa.io/) for Python and npm for the React frontend.

### Quick start

```bash
hatch env create                   # set up Python env
cd dbot/ui && npm install && cd ..  # install frontend deps
hatch run hooks                    # install pre-commit hooks
hatch run check                    # verify everything works
```

### Running locally

**Production-like** (static UI build + server):

```bash
hatch run dev              # builds UI, starts server at http://127.0.0.1:7932
```

**Frontend development** (hot reload — two terminals):

```bash
# Terminal 1: Python API server
hatch run dev-api          # starts backend at :7932

# Terminal 2: Vite dev server with HMR
cd dbot/ui && npm run dev  # starts at :5173, proxies /api/* to :7932
```

Open `http://localhost:5173` for live-reloading frontend development.
Open `http://localhost:7932` for the production build (after `hatch run build-ui`).

### Commands

| Command | What it does |
|---|---|
| `hatch run dev` | Build UI + start server (production-like) |
| `hatch run dev-api` | Start Python server only (pair with `npm run dev`) |
| `hatch run test` | Full Python test suite (307 tests) |
| `hatch run test-quick` | Skip integration tests |
| `hatch run test-ui` | Frontend tests (vitest) |
| `hatch run lint` | ruff check + format |
| `hatch run lint-ui` | biome ci + tsc |
| `hatch run fmt` | Autofix Python |
| `hatch run fmt-ui` | Autofix frontend |
| `hatch run build-ui` | npm run build |
| `hatch run typecheck` | mypy |
| `hatch run check` | Dev gate: ruff + biome + tsc + tests |
| `hatch run check-strict` | Dev gate + mypy |
| `hatch run ci` | Full CI pipeline |
| `hatch run hooks` | Install pre-commit hooks |

### Frontend (dbot/ui/)

```bash
npm run dev      # vite dev server with HMR, proxies /api to :7932
npm run build    # tsc + vite production build
npm run test     # vitest
npm run lint     # biome check
npm run check    # biome ci + tsc
npm run format   # biome format
```

---

## Stack

| Concern | Choice |
|---------|--------|
| Agent framework | [PydanticAI](https://github.com/pydantic/pydantic-ai) |
| MCP server | [FastMCP](https://github.com/jlowin/fastmcp) |
| Integration source | [demisto/content](https://github.com/demisto/content) (MIT) |
| Config/credentials | SQLite + Fernet encryption |
| Web UI | React SPA (Vite + TypeScript) + `@ai-sdk/react` + custom settings page |
| Execution model | Subprocess per invocation (in-process for dev) |
| Python | 3.13+ |
| Frontend | React 19, Vite 6, TypeScript 5.9, @ai-sdk/react |
| JS/TS linting | [Biome](https://biomejs.dev/) 2.4 |
| Code quality | Ruff (20 rule sets), Mypy, pre-commit (11 hooks) |
| CI | GitHub Actions (lint, mypy, test matrix, UI build) |
| Packaging | hatch + uv + pyproject.toml |

---

## CI / Quality

Four GitHub Actions jobs run on every push and PR:

| Job | What it runs |
|-----|--------------|
| lint | ruff, biome, tsc |
| mypy | strict type-checking |
| test | pytest matrix (Python 3.13 + 3.14) |
| ui-build | npm run build |

Pre-commit hooks (install with `hatch run hooks`) enforce ruff, biome,
trailing whitespace, merge conflict markers, large files, private keys,
debug statements, and YAML/TOML validity before every commit.

---

## Contributing

```bash
git clone https://github.com/omercnet/dbot.git && cd dbot
git submodule update --init
hatch env create
cd dbot/ui && npm install && cd ../..
hatch run hooks     # install pre-commit hooks
hatch run check     # must stay green before opening a PR
```

---


## Documentation

- [Quickstart](docs/quickstart.md) -- full setup guide
- [Architecture](docs/architecture.md) -- how dbot works internally
- [Credentials](docs/credentials.md) -- configuring API keys and secrets
- [Integrations](docs/integrations.md) -- adding and validating integrations

---

## License

MIT
