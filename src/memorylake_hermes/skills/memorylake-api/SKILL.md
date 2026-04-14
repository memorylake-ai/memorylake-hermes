---
name: memorylake-api
description: Use when the user asks about MemoryLake features or wants to perform a MemoryLake action not covered by existing tools or skills. Catch-all for direct API calls — project management, document management, memory trace, statistics, and more.
version: 1.0.0
---

# MemoryLake API

## Overview

Directly call MemoryLake's REST APIs for any capability not covered by the built-in memorylake tools. Covers project management, document management, memory trace, statistics, and more.

## When to Use

- User asks about MemoryLake capabilities not covered by existing tools
- User wants to manage projects (create, update, delete, list, view stats)
- User wants to manage documents (list, delete, view details)
- User wants to view memory change history (trace)
- User wants to call any MemoryLake API endpoint directly

## Step 1 — Read Config

```bash
cat "$HERMES_HOME/memorylake.json"
```

Required: `host`, `api_key`, `project_id`. Auth header: `Authorization: Bearer {api_key}`.

## Step 2 — API Quick Reference

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
| `GET` | `/api/v2/projects/{id}/memories` | List memories (filter by `user_id`, `keyword`) |
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

Base URL: `{host}/openapi/memorylake`. Full URL example: `{host}/openapi/memorylake/api/v1/projects`.

### Examples

```bash
# List projects
curl -s "{host}/openapi/memorylake/api/v1/projects?page=1&size=20" \
  -H "Authorization: Bearer {api_key}" | jq

# Get project stats
curl -s "{host}/openapi/memorylake/api/v1/projects/{project_id}" \
  -H "Authorization: Bearer {api_key}" | jq

# Memory trace
curl -s "{host}/openapi/memorylake/api/v2/projects/{project_id}/memories/{memoryId}/trace" \
  -H "Authorization: Bearer {api_key}" | jq

# Search documents
curl -s -X POST "{host}/openapi/memorylake/api/v1/projects/{project_id}/documents/search" \
  -H "Authorization: Bearer {api_key}" \
  -H "Content-Type: application/json" \
  -d '{"query": "quarterly sales", "top_n": 10}' | jq
```

## Response Format

```json
{
  "success": true|false,
  "message": "...",
  "data": { ... },
  "error_code": "..."
}
```

| HTTP Status | Meaning |
|-------------|---------|
| 200 | Success — parse `data` |
| 400 | Invalid request — check `message` |
| 404 | Not found — verify IDs |
| 401/403 | Auth failure — verify `api_key` |

## Common Mistakes

- **Wrong base URL**: Must include `/openapi/memorylake` before the API path
- **Missing auth**: Every request needs `Authorization: Bearer {api_key}`
- **Hardcoded project ID**: Read from config, don't hardcode
