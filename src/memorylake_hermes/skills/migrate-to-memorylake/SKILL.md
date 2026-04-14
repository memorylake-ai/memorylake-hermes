---
name: migrate-to-memorylake
description: Migrates hermes sessions and conversations to MemoryLake. Use when the user wants to import existing sessions or conversation history into MemoryLake.
version: 1.0.0
---

# Migrate to MemoryLake

## Overview

Extract conversation history from hermes session files and submit them to MemoryLake's API for server-side memory extraction.

## When to Use

- User wants to migrate existing hermes conversations into MemoryLake
- User is setting up MemoryLake and needs to import history

## Preferred: Run the Migration Script

The script is at `scripts/migrate.py` relative to this SKILL.md. Always try the script first:

```bash
python3 {path-to-this-skill}/scripts/migrate.py \
  --host {host} \
  --api-key {api_key} \
  --project-id {project_id} \
  [--user-id {user_id}] \
  [--session-id {specific_session_id}]
```

Config can also be read from `$HERMES_HOME/memorylake.json` automatically if `--host`/`--api-key`/`--project-id` are not provided.

The script handles: session discovery, JSONL parsing, message extraction, batching, and API submission.

If the script succeeds, you are done. Only proceed to manual steps if the script fails.

---

## Fallback: Manual Steps

### Step 1 — Read MemoryLake Config

```bash
cat "$HERMES_HOME/memorylake.json"
```

Required: `host`, `api_key`, `project_id`.

### Step 2 — Find Session Files

Session files are at `$HERMES_HOME/sessions/`. Each session is a `.jsonl` file.

```bash
ls "$HERMES_HOME/sessions/"*.jsonl
```

### Step 3 — Parse and Submit Sessions

For each `.jsonl` session file:

1. Parse line by line — extract entries with `"role": "user"` or `"role": "assistant"`
2. Extract text content, skip tool_calls
3. Batch messages (max 20 per request) and POST to:

```bash
curl -X POST "{host}/openapi/memorylake/api/v2/projects/{project_id}/memories" \
  -H "Authorization: Bearer {api_key}" \
  -H "Content-Type: application/json" \
  -d '{
    "messages": [{"role": "user", "content": "..."}, ...],
    "user_id": "default",
    "metadata": {"source": "HERMES_MIGRATION"},
    "infer": true
  }'
```

### Progress Tracking

Report after each submission:
- `[session X/N] Submitted {count} messages — {status}`
- Final summary: total sessions, total API calls, errors

### Error Handling

- Missing/unreadable session: log warning, continue
- API failure: log error with context, continue
- Missing config: stop immediately, inform user
