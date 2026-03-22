# dbot

> Run 1,000+ XSOAR security integrations as MCP tools. No XSOAR required.

dbot is an open-source MCP server that takes the entire
[demisto/content](https://github.com/demisto/content) integration library
(~1,100 security tool integrations) and runs them standalone -- no XSOAR,
no Cortex, no license. It exposes every integration as an MCP tool that any
agent (PydanticAI, Claude Desktop, or any MCP client) can call.

---

## Quickstart

```bash
# 1. Clone
git clone https://github.com/omercnet/dbot.git && cd dbot

# 2. Initialize content submodule
git submodule update --init

# 3. Install
uv sync --all-extras

# 4. Configure credentials
cp config/credentials.yaml.example config/credentials.yaml
# Edit credentials.yaml -- add your API keys

# 5. Run
uv run python -m dbot.server
```

See [docs/quickstart.md](docs/quickstart.md) for the full setup guide.

---

## Architecture

```
MCP Client (Claude / PydanticAI agent)
         |  MCP protocol (stdio)
         v
    dbot MCP server
    |-- search_tools(query, category?)
    |-- get_tool_schema(tool_name)
    |-- invoke_tool(tool_name, args, reason)
         |              |
    Registry         Executor
    YAML index       subprocess per call
    + search         demistomock injection
         |              |
    demisto/content (git submodule, sparse checkout)
```

Every XSOAR integration starts with `import demistomock as demisto`. dbot
provides a fake `demistomock` that injects credentials, captures results,
and runs each integration in an isolated subprocess.

---

## The 3 MCP Tools

| Tool | Purpose |
|------|---------|
| `search_tools(query, category?)` | Discover available integrations by keyword. Returns names, descriptions, and arg summaries. |
| `get_tool_schema(tool_name)` | Get the full argument and output spec for a tool. Secret args are stripped. |
| `invoke_tool(tool_name, args, reason)` | Execute a tool. `reason` is required for the audit trail. Dangerous tools require human approval. |

The agent calls `search_tools` first, then `get_tool_schema` to understand
the args, then `invoke_tool` to execute.

---

## HITL Gate

Commands marked `execution: true` in the XSOAR YAML are automatically
flagged dangerous (host isolation, account suspension, firewall changes).
When the agent tries to invoke one, execution pauses and returns an
`approval_required` response with full context for a human operator.

---

## Project Structure

```
dbot/
├── dbot/
│   ├── server.py              # FastMCP entrypoint
│   ├── audit.py               # JSON-lines audit logger
│   ├── runtime/
│   │   ├── demistomock.py     # XSOAR runtime shim (~50 methods)
│   │   ├── common_server.py   # CommonServerPython loader
│   │   ├── executor.py        # In-process + subprocess execution
│   │   └── runner.py          # Subprocess entry point
│   ├── registry/
│   │   ├── models.py          # Pydantic models (IntegrationDef, CommandDef, etc.)
│   │   ├── indexer.py         # Walks Packs/, parses integration YAMLs
│   │   └── catalog.py         # In-memory search index
│   ├── credentials/
│   │   ├── models.py          # CredentialProfile
│   │   └── store.py           # Env var / YAML credential resolution
│   └── tools/
│       ├── search.py          # search_tools MCP tool
│       ├── meta.py            # get_tool_schema MCP tool
│       └── invoke.py          # invoke_tool MCP tool
├── content/                   # git submodule -> demisto/content
├── tests/
├── config/
│   ├── credentials.yaml.example
│   └── enabled_packs.yaml.example
└── pyproject.toml
```

---

## Stack

| Concern | Choice |
|---------|--------|
| MCP server | [FastMCP](https://github.com/jlowin/fastmcp) |
| Agent framework | [PydanticAI](https://github.com/pydantic/pydantic-ai) |
| Integration source | [demisto/content](https://github.com/demisto/content) (MIT) |
| Execution model | Subprocess per invocation (in-process for dev) |
| Credential management | Env vars / YAML (vault-pluggable) |
| Python | 3.13+ |
| Packaging | uv + hatch + pyproject.toml |
| Packaging | uv + pyproject.toml |

---

## Status

| Phase | Status |
|-------|--------|
| Core runtime (demistomock + CSP loader) | Done |
| In-process executor | Done |
| Registry + catalog (YAML indexer, search) | Done |
| FastMCP server + 3 tools | Done |
| Subprocess executor + credential store | Done |
| HITL gate + audit logging | Done |
| Tier-1 integration validation (20+ integrations) | In progress |
| Playbook indexer | Planned |

---

## Documentation

- [Quickstart](docs/quickstart.md) -- full setup guide
- [Architecture](docs/architecture.md) -- how dbot works internally
- [Credentials](docs/credentials.md) -- configuring API keys and secrets
- [Integrations](docs/integrations.md) -- adding and validating integrations

---

## Why This Matters

- **First FOSS bridge** between the XSOAR integration ecosystem and MCP
- **1,100+ battle-tested security integrations** available to any LLM agent
- **Zero commercial dependency** -- no XSOAR license, no Cortex subscription
- **The mock is the hard part** -- once solid, adding integrations is config not code
- **demisto/content is MIT licensed** -- dbot inherits that cleanly

---

## License

MIT
