---
name: memorylake-upload
description: Use when the user wants to upload files, documents, PDFs, archives, directories, or other data to MemoryLake. Supports single files, archives (zip, tar.gz, tar.bz2, tar.xz, 7z) which are extracted then uploaded, and directories which are recursively uploaded.
version: 1.1.0
---

# MemoryLake File Upload

Upload local files, archives, or directories to MemoryLake as project documents.

## When to Use

- User wants to upload a file (PDF, DOCX, image, etc.) to MemoryLake
- User wants to upload an archive (zip, tar.gz, 7z) — files inside are extracted and uploaded individually
- User wants to upload a directory — all files are recursively collected and uploaded

## Step 1 — Get Config

```bash
python3 {SKILL_DIR}/scripts/get_config.py
```

This outputs JSON with `host`, `api_key`, `project_id`. If it exits with an error, inform the user and stop.

## Step 2 — Run Upload Script

Pass config values from Step 1 to the upload script:

```bash
python3 {SKILL_DIR}/scripts/upload.py \
  --host {host} \
  --api-key {api_key} \
  --project-id {project_id} \
  [--name "{custom_filename}"] \
  /path/to/file_or_dir
```

`--name` overrides the filename for single file uploads. Ignored for archives and directories.

## Step 3 — Report Results

The script prints progress and a summary. Relay success/failure to the user.

## Common Mistakes

- **Relative file paths**: Always resolve to absolute path before passing to the script
- **Skipping Step 1**: Always get config first — don't hardcode credentials
