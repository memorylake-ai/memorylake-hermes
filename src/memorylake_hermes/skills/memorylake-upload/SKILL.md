---
name: memorylake-upload
description: Use when the user wants to upload files, documents, PDFs, archives, directories, or other data to MemoryLake. Supports single files, archives (zip, tar.gz, tar.bz2, tar.xz, 7z) which are extracted then uploaded, and directories which are recursively uploaded.
version: 1.0.0
---

# MemoryLake File Upload

## Overview

Upload local files, archives, or directories to MemoryLake using the multipart upload API, then associate them with a project. Archives are automatically detected, extracted, and each file inside is uploaded individually. Directories are recursively traversed and all files inside are uploaded.

## When to Use

- User wants to upload a file (PDF, DOCX, image, etc.) to MemoryLake
- User wants to add a local document to a MemoryLake project
- User wants to upload an archive (zip, tar.gz, tar.bz2, tar.xz, 7z) — files inside will be extracted and uploaded one by one
- User wants to upload an entire directory/folder — all files will be recursively collected and uploaded

## Step 1 — Read MemoryLake Config

Read the config from `$HERMES_HOME/memorylake.json` (or environment variables):

```bash
cat "$HERMES_HOME/memorylake.json"
```

Required fields: `host` (default: `https://app.memorylake.ai`), `api_key`, `project_id`.

If config is missing or incomplete, inform the user and stop.

## Step 2 — Run the Upload Script

The upload script is at `scripts/upload.py` relative to this SKILL.md.

```bash
# Single file
python3 {path-to-this-skill}/scripts/upload.py \
  --host {host} \
  --api-key {api_key} \
  --project-id {project_id} \
  --name "{original_filename}" \
  /path/to/file

# Archive (auto-detected, extracted, each file uploaded)
python3 {path-to-this-skill}/scripts/upload.py \
  --host {host} \
  --api-key {api_key} \
  --project-id {project_id} \
  /path/to/archive.zip

# Directory (recursively uploads all files inside)
python3 {path-to-this-skill}/scripts/upload.py \
  --host {host} \
  --api-key {api_key} \
  --project-id {project_id} \
  /path/to/my-folder/
```

`--name` overrides the filename for single file uploads. For archives and directories it is ignored.

## Step 3 — Handle Output

The script prints progress for each step and a final summary.

- **Success**: Report uploaded file count and names to the user
- **Failure**: Relay the specific error message — don't guess the cause

## Common Mistakes

- **Relative file paths**: Always resolve the user's file path to an absolute path before passing to the script
- **Missing config**: Always read config first; don't hardcode host/apiKey/projectId
