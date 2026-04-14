---
name: memorylake-config
description: Use when the user asks to configure MemoryLake settings for the current profile — projectId, host, apiKey, autoUpload, web search defaults, etc. Reads and writes $HERMES_HOME/memorylake.json.
version: 1.0.0
---

# MemoryLake Config

Configure MemoryLake settings for the current hermes profile. The config file is at `$HERMES_HOME/memorylake.json`. Each hermes profile (`~/.hermes/profiles/<name>/`) has its own independent config.

## Supported Settings

| Key | Description | Example |
|-----|-------------|---------|
| `host` | MemoryLake server URL | `https://app.memorylake.ai` |
| `api_key` | API key | `ml_xxx` |
| `project_id` | Project ID | `proj_xxx` |
| `user_id` | User identifier | `hermes-user` |
| `top_k` | Max recall results | `5` |
| `search_threshold` | Min similarity 0-1 | `0.3` |
| `rerank` | Rerank results | `true` |
| `memory_mode` | `tool_driven` or `prefetch` | `tool_driven` |
| `auto_upload` | Auto-upload user documents | `true` |
| `web_search_include_domains` | Comma-separated domains to include in web search | `arxiv.org,nature.com` |
| `web_search_exclude_domains` | Comma-separated domains to exclude from web search | `pinterest.com` |
| `web_search_country` | Default country for web search | `US` |
| `web_search_timezone` | Default timezone for web search | `America/New_York` |

## Step 1 — Identify What to Configure

Ask the user which settings they want to change. If they've already specified values in their message, skip the question.

## Step 2 — Read Existing Config

```bash
cat "$HERMES_HOME/memorylake.json" 2>/dev/null || echo "{}"
```

## Step 3 — Merge and Write Config

1. Parse the existing config as JSON
2. Merge the new values (do NOT overwrite properties the user did not mention)
3. Write back to `$HERMES_HOME/memorylake.json`:

```python
import json
from pathlib import Path
import os

config_path = Path(os.environ["HERMES_HOME"]) / "memorylake.json"
existing = json.loads(config_path.read_text()) if config_path.exists() else {}
existing.update({"project_id": "new_value"})  # only keys user specified
config_path.write_text(json.dumps(existing, indent=2))
```

## Step 4 — Confirm

Read back the written config and confirm to the user.

## Common Mistakes

- **Overwriting unrelated keys**: Always merge, never replace the entire file
- **Wrong profile**: Config is per-profile — `$HERMES_HOME` points to the active profile's directory
