---
name: memorylake-config
description: Use when the user asks to configure MemoryLake settings for the current profile — projectId, host, apiKey, autoUpload, web search defaults, etc.
version: 1.1.1
---

# MemoryLake Config

Configure MemoryLake settings for the current hermes profile.

## Step 1 — Resolve hermes_home and Read Current Config

First resolve the hermes home directory:

```python
from hermes_constants import get_hermes_home

hermes_home = get_hermes_home()
```

Config is stored in two files under `{hermes_home}`:

| File | Purpose | Format |
|------|---------|--------|
| `{hermes_home}/.env` | Core credentials & host (env vars) | `KEY=value` |
| `{hermes_home}/memorylake.json` | Optional overrides & feature settings | JSON |

The plugin reads env vars first, then overlays `memorylake.json` (config file takes precedence).

Use `read_file` on `{hermes_home}/.env` to show raw `MEMORYLAKE_*` lines, and `read_file` on `{hermes_home}/memorylake.json` to show the overlay file.

## Step 2 — Identify What to Change

Ask the user which settings they want to change. If they've already specified values, skip the question.

### Where to write each setting

**Core credentials → `{hermes_home}/.env`**:

| Env var | Description |
|---------|-------------|
| `MEMORYLAKE_API_KEY` | API key |
| `MEMORYLAKE_PROJECT_ID` | Project ID |
| `MEMORYLAKE_HOST` | Server URL |

**Feature settings → `{hermes_home}/memorylake.json`**:

| JSON key | Description | Example |
|----------|-------------|---------|
| `user_id` | User identifier | `default` |
| `top_k` | Max recall results | `5` |
| `search_threshold` | Min similarity 0-1 | `0.3` |
| `rerank` | Rerank results | `true` |
| `memory_mode` | `tool_driven` or `prefetch` | `tool_driven` |
| `auto_upload` | Auto-upload user documents | `true` |
| `web_search_include_domains` | Comma-separated | `arxiv.org,nature.com` |
| `web_search_exclude_domains` | Comma-separated | `pinterest.com` |
| `web_search_country` | Country code | `US` |
| `web_search_timezone` | Timezone | `America/New_York` |

> `host`, `api_key`, `project_id` can also appear in `memorylake.json` as overrides.

## Step 3 — Write Changes

Use `read_file` then `write_file` on the appropriate file.

- **`.env`**: update or append only `MEMORYLAKE_*` lines, preserve everything else
- **`memorylake.json`**: merge new keys into existing JSON, don't clobber unmentioned keys

## Step 4 — Confirm

Read back the written file and confirm to the user.

## Common Mistakes

- **Overwriting unrelated keys**: Always merge, never replace
- **Clobbering .env lines**: Only touch `MEMORYLAKE_*` lines
