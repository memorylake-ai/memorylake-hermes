---
name: bump-version
description: Bump the project version across all files, create a git tag, and push to remote main. Use when the user wants to release a new version, bump version, or cut a release.
---

# Version Bump

Bump the project version number, tag it, and push to remote main.

## Instructions

When the user invokes this skill, they should provide the new version number (e.g., `1.2.0`). If they don't, ask them for the target version before proceeding.

Follow these steps **in order**:

### Step 1: Sync with latest main

```bash
git fetch origin
git checkout main
git pull origin main
```

If there are uncommitted changes, abort and tell the user to commit or stash first.

### Step 2: Validate the version

- The version must follow semver format: `MAJOR.MINOR.PATCH` (e.g., `1.2.0`)
- Read the current version from `pyproject.toml` line 3 and confirm the bump direction with the user (e.g., "Current version is 1.1.1, bumping to 1.2.0 — confirm?")

### Step 3: Update ALL version locations

You MUST update the version string in **both** files listed below. Do not skip any.

| # | File | Format |
|---|------|--------|
| 1 | `pyproject.toml` (line ~3) | `version = "X.Y.Z"` |
| 2 | `src/memorylake_hermes/plugin.yaml` (line ~2) | `version: X.Y.Z` |

**After editing**, run a grep to verify no old version strings remain in these two files:

```bash
grep -n 'OLD_VERSION' pyproject.toml src/memorylake_hermes/plugin.yaml
```

If the grep finds any remaining old version strings, fix them before proceeding.

### Step 4: Commit

```bash
git add pyproject.toml src/memorylake_hermes/plugin.yaml
git commit -m "chore: bump version to X.Y.Z"
```

### Step 5: Tag

```bash
git tag vX.Y.Z
```

### Step 6: Push to remote main

```bash
git push origin main
git push origin vX.Y.Z
```

### Step 7: Confirm

Print a summary:
- Old version → New version
- Tag name
- Files updated
- Remote push status

## Important

- The `src/memorylake_hermes/client.py` reads version dynamically from `plugin.yaml` via `_read_plugin_version()`, so it does NOT need a manual update.
- Version strings inside `src/memorylake_hermes/skills/**/SKILL.md` are intentionally NOT updated by this skill.
