# memorylake-hermes

[MemoryLake](https://app.memorylake.ai) memory plugin for [hermes-agent](https://github.com/hermes-ai/hermes-agent). Gives the agent persistent long-term memory, document retrieval, web search, and open data access via the MemoryLake platform API.

## Features

- **7 tools** exposed to the LLM:

  | Tool | Description |
  |------|-------------|
  | `memorylake_search` | Unified semantic search across memories AND uploaded documents |
  | `memorylake_store` | Save facts, preferences, decisions to long-term memory |
  | `memorylake_list` | List all stored memories for the user |
  | `memorylake_forget` | Delete a specific memory by ID |
  | `memorylake_download` | Get pre-signed download URL for a document |
  | `memorylake_web_search` | Web search across 12 domains (web, academic, news, code, ...) |
  | `memorylake_open_data` | Search open datasets (arXiv, PubMed, SEC, FRED, patents, ...) |

- **Auto-capture**: every conversation turn is sent to MemoryLake for server-side memory extraction (background, non-blocking).
- **Conflict detection**: surfaces unresolved memory conflicts in search results.
- **Two recall modes**:
  - `tool_driven` (default) — the model is instructed to call `memorylake_search` as its first action every turn, ensuring consistent memory recall regardless of model.
  - `prefetch` — the framework runs a unified search before the LLM call and injects results into context automatically.

## Installation

```bash
# Auto-detect hermes-agent location, symlink (dev mode)
./install.sh

# Explicit path
./install.sh /path/to/hermes-agent

# Copy files instead of symlink
./install.sh --copy
```

The plugin is installed to `plugins/memory/memorylake/` inside hermes-agent.

## Configuration

Set in `~/.hermes/.env`:

```bash
MEMORYLAKE_API_KEY=sk-...          # Required — get at https://app.memorylake.ai
MEMORYLAKE_PROJECT_ID=proj-...     # Required — from your MemoryLake project
```

Then enable the provider in `~/.hermes/config.yaml`:

```yaml
memory:
  provider: memorylake
```

### Optional Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `MEMORYLAKE_HOST` | `https://app.memorylake.ai` | MemoryLake server URL |
| `MEMORYLAKE_USER_ID` | `hermes-user` | User identifier (auto-set to platform user ID in gateway mode) |
| `MEMORYLAKE_TOP_K` | `5` | Max recall results |
| `MEMORYLAKE_SEARCH_THRESHOLD` | `0.3` | Min similarity score (0-1) |
| `MEMORYLAKE_RERANK` | `true` | Rerank search results |
| `MEMORYLAKE_MEMORY_MODE` | `tool_driven` | Recall mode: `tool_driven` or `prefetch` |

Config can also be set via `~/.hermes/memorylake.json` (overrides env vars).

## Memory Modes

### tool_driven (default, recommended)

The system prompt instructs the model to call `memorylake_search` as its first action on every turn. A per-turn reminder is also injected. This ensures consistent memory recall regardless of which LLM provider you use.

### prefetch

The framework calls `prefetch()` before each LLM call, running a parallel unified search (memories + documents) with the current user message. Results are injected into context automatically. The model does not need to call any tool to receive memory context.

## License

Proprietary. Requires a [MemoryLake](https://app.memorylake.ai) API key.
