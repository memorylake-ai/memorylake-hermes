---
name: memorylake-api
description: Use when the user asks about MemoryLake features or wants to perform a MemoryLake action not covered by existing tools or skills. Catch-all for direct API calls — project management, document management, memory trace, statistics, and more.
version: 1.1.1
---

# MemoryLake API

Directly call MemoryLake's REST APIs for any capability not covered by the built-in memorylake tools.

## When to Use

- User asks about MemoryLake capabilities not covered by existing tools
- User wants to manage projects (create, update, delete, list, view stats)
- User wants to manage documents (list, delete, view details)
- User wants to view memory change history (trace)

## Step 1 — Get Config

```bash
python3 {SKILL_DIR}/scripts/get_config.py
```

This outputs JSON with `host`, `api_key`, `project_id`. Use these for all subsequent API calls. Auth header: `Authorization: Bearer {api_key}`.

## Step 2 — API Quick Reference

Base URL: `{host}/openapi/memorylake`.

### Projects

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/v1/projects` | List projects |
| `POST` | `/api/v1/projects` | Create project |
| `GET` | `/api/v1/projects/{id}` | Get details + stats |
| `PUT` | `/api/v1/projects/{id}` | Update project |
| `DELETE` | `/api/v1/projects/{id}` | Delete project |

### Memories (V2)

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/v2/projects/{id}/memories` | Add memory |
| `GET` | `/api/v2/projects/{id}/memories` | List memories |
| `POST` | `/api/v2/projects/{id}/memories/search` | Semantic search |
| `GET` | `/api/v2/projects/{id}/memories/{memoryId}` | Get single memory |
| `DELETE` | `/api/v2/projects/{id}/memories/{memoryId}` | Delete memory |
| `GET` | `/api/v2/projects/{id}/memories/{memoryId}/trace` | Change history |

### Documents

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/v1/projects/{id}/documents` | List documents |
| `GET` | `/api/v1/projects/{id}/documents/{docId}` | Get document details |
| `DELETE` | `/api/v1/projects/{id}/documents` | Batch delete |
| `POST` | `/api/v1/projects/{id}/documents/search` | Semantic search |

### OpenAPI Discovery

```bash
curl -s "{host}/openapi/memorylake/api-docs/open-api" | jq '.paths | keys'
```

## Step 3 — Execute API Call

Example:

```bash
curl -s "{host}/openapi/memorylake/api/v1/projects/{project_id}" \
  -H "Authorization: Bearer {api_key}" | jq
```

## Response Format

```json
{"success": true, "message": "...", "data": {...}, "error_code": "..."}
```

## Common Mistakes

- **Wrong base URL**: Must include `/openapi/memorylake` before the API path
- **Hardcoded credentials**: Always get config from `{SKILL_DIR}/scripts/get_config.py` first
