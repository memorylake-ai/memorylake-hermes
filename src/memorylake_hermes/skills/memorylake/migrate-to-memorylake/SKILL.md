---
name: migrate-to-memorylake
description: Migrates memories, conversations to MemoryLake. Use when the user wants to import existing memories, conversations into MemoryLake.
version: 1.1.1
---

# Migrate Memories to MemoryLake

## Overview

Extract memory files and conversation history from hermes session files, then submit them to MemoryLake's API so memories are persisted in the platform.

## When to Use

- User wants to migrate memories or conversations into MemoryLake
- User is setting up MemoryLake and needs to import existing memories or conversations

## Preferred: Run the Migration Script

**Always try the script first.** The script is at `scripts/migrate.py` relative to this SKILL.md. Run:

```bash
python3 {SKILL_DIR}/scripts/migrate.py
```

Config is auto-resolved via `get_config.py` (env vars + `memorylake.json`). No arguments needed.

The script handles everything: config reading, session discovery, JSONL parsing, memory file collection, and API submission.

If the script succeeds, you are done. **Only proceed to the manual steps below if the script fails and you cannot fix it.**

---

## Fallback: Manual Steps

If the script fails, follow these steps manually.

### Step 1 — Read MemoryLake Config

```bash
python3 {SKILL_DIR}/scripts/get_config.py
```

The script outputs JSON with `host`, `api_key`, `project_id`, `hermes_home`, etc. If it exits with an error, stop and inform the user.

`hermes_home` is the resolved absolute path of the hermes home directory (e.g. `~/.hermes`).

### Step 2 — Find Session Files

Session files are at `{hermes_home}/sessions/`. Each session is a `.jsonl` file.

### Step 3 — Read Memory Files

Read the following two files from `{hermes_home}/memories/`:
- `MEMORY.md` — conversation summaries and facts
- `USER.md` — user profile

### Step 4 — Submit Data to MemoryLake

Use `host`, `api_key`, `project_id` from the config output in Step 1.

**When POSTing to the API, always use `"user_id": "default"`.**

#### 4a — Submit Session Conversations

For each `.jsonl` session file:

1. **Parse the JSONL file** line by line
2. **Extract message entries**: lines with `"role": "user"` or `"role": "assistant"`
3. **Extract text content**:
   - If `content` is a list, concatenate all `type: "text"` blocks into a single string
   - Skip `tool_call` content blocks — these are tool calls, not conversation text
   - **Discard messages where the concatenated text is empty**
4. **Build the messages array**: `[{role, content}, {role, content}, ...]`
5. **POST to the API**:

   ```bash
   curl -X POST "{host}/openapi/memorylake/api/v2/projects/{project_id}/memories" \
     -H "Authorization: Bearer {api_key}" \
     -H "Content-Type: application/json" \
     -d '{
       "messages": [
         {"role": "user", "content": "..."},
         {"role": "assistant", "content": "..."}
       ],
       "user_id": "default",
       "chat_session_id": "{sessionId}",
       "metadata": {"source": "HERMES_MIGRATION"},
       "infer": true
     }'
   ```

   **Important**: If a session has many messages, batch them in chunks of ~20 messages per request to avoid timeouts. Preserve message order within each batch.

#### 4b — Submit Memory Files

For each memory file (`MEMORY.md`, `USER.md`):

1. **Read the file content**
2. **POST to the API**:

   ```bash
   curl -X POST "{host}/openapi/memorylake/api/v2/projects/{project_id}/memories" \
     -H "Authorization: Bearer {api_key}" \
     -H "Content-Type: application/json" \
     -d '{
       "messages": [{"role": "user", "content": "..."}],
       "user_id": "default",
       "metadata": {"source": "HERMES_MIGRATION", "filename": "{filename}"},
       "infer": true
     }'
   ```

### Progress Tracking

Report progress after each submission:
- `[session X/N] Submitted {count} messages from session {sessionId} — {status}`
- `[file X/N] Submitted {filename} — {status}`

At the end, print a summary:
- Total sessions processed
- Total memory files processed
- Total API calls made
- Any errors encountered

### Error Handling

- If a session file is missing or unreadable, log a warning and continue with the next one
- If an API call fails, log the error with the session/file context and continue
- If `api_key` or `project_id` is missing from config, stop immediately and inform the user

### Quick Reference

| Item | Path / Value |
|------|-------------|
| Config script | `{SKILL_DIR}/scripts/get_config.py` |
| Session files | `{hermes_home}/sessions/{id}.jsonl` |
| Memory files | `{hermes_home}/memories/MEMORY.md`, `USER.md` |
| API endpoint | `{host}/openapi/memorylake/api/v2/projects/{project_id}/memories` |
| Auth header | `Authorization: Bearer {api_key}` |
| Default host | `https://app.memorylake.ai` |
