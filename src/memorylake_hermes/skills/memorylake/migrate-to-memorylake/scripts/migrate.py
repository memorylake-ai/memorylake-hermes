#!/usr/bin/env python3
"""Migrate hermes sessions and memory files to MemoryLake.

Usage:
    python migrate.py

Config is auto-resolved via get_config.py (env vars + $HERMES_HOME/memorylake.json).

Migrates:
    - Session conversations ($HERMES_HOME/sessions/*.jsonl)
    - Memory files ($HERMES_HOME/memories/MEMORY.md, USER.md)
"""

import json
import os
import sys
from pathlib import Path

import requests

BATCH_SIZE = 20
TIMEOUT = 30.0


def headers(api_key: str) -> dict:
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }


def submit_batch(host: str, api_key: str, project_id: str,
                 messages: list, session_id: str = "") -> dict:
    """Submit a batch of messages to MemoryLake."""
    payload = {
        "messages": messages,
        "user_id": "default",
        "metadata": {"source": "HERMES_MIGRATION"},
        "infer": True,
    }
    if session_id:
        payload["chat_session_id"] = session_id

    resp = requests.post(
        f"{host}/openapi/memorylake/api/v2/projects/{project_id}/memories",
        json=payload,
        headers=headers(api_key),
        timeout=TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json()


def parse_session_file(path: str) -> list[dict]:
    """Parse a hermes JSONL session file and extract text messages."""
    messages = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue

            role = entry.get("role")
            if role not in ("user", "assistant"):
                continue

            content = entry.get("content", "")
            if isinstance(content, list):
                # Extract text blocks, skip tool_calls
                text_parts = []
                for block in content:
                    if isinstance(block, dict):
                        if block.get("type") == "text":
                            text_parts.append(block.get("text", ""))
                        elif isinstance(block.get("text"), str):
                            text_parts.append(block["text"])
                content = " ".join(t for t in text_parts if t)

            if not content or not content.strip():
                continue

            messages.append({"role": role, "content": content.strip()})

    return messages


def migrate_memory_files(host: str, api_key: str, project_id: str,
                         hermes_home: str) -> tuple[int, int]:
    """Migrate memory files (MEMORY.md, USER.md) to MemoryLake.

    Returns (submitted, errors).
    """
    memories_dir = Path(hermes_home) / "memories"
    if not memories_dir.is_dir():
        print("No memories directory found, skipping memory file migration.")
        return 0, 0

    # Only migrate MEMORY.md and USER.md
    files = [
        memories_dir / name
        for name in ("MEMORY.md", "USER.md")
        if (memories_dir / name).is_file()
    ]
    if not files:
        print("No memory files found.")
        return 0, 0

    print(f"\nMigrating {len(files)} memory file(s) from {memories_dir}")
    submitted = 0
    errors = 0

    for mf in files:
        try:
            content = mf.read_text(encoding="utf-8").strip()
        except Exception as e:
            print(f"  WARNING: Failed to read {mf.name}: {e}", file=sys.stderr)
            errors += 1
            continue

        if not content:
            print(f"  Skipped: {mf.name} (empty)")
            continue

        # Submit as a single user message with filename in metadata
        payload = {
            "messages": [{"role": "user", "content": content}],
            "user_id": "default",
            "metadata": {
                "source": "HERMES_MIGRATION",
                "filename": mf.name,
            },
            "infer": True,
        }

        try:
            resp = requests.post(
                f"{host}/openapi/memorylake/api/v2/projects/{project_id}/memories",
                json=payload,
                headers=headers(api_key),
                timeout=TIMEOUT,
            )
            resp.raise_for_status()
            result = resp.json()
            success = result.get("success", False)
            status = "OK" if success else result.get("message", "failed")
            print(f"  {mf.name}: {len(content)} chars — {status}")
            submitted += 1
        except Exception as e:
            print(f"  {mf.name}: FAILED — {e}", file=sys.stderr)
            errors += 1

    return submitted, errors


def main():
    # Load get_config.py from the same scripts/ directory
    scripts_dir = str(Path(__file__).resolve().parent)
    sys.path.insert(0, scripts_dir)
    from get_config import get_config
    cfg = get_config()
    host = cfg["host"]
    api_key = cfg.get("api_key", "")
    project_id = cfg.get("project_id", "")
    hermes_home = cfg["hermes_home"]

    if not api_key or not project_id:
        print("ERROR: api_key and project_id are required. "
              "Set MEMORYLAKE_API_KEY/MEMORYLAKE_PROJECT_ID in $HERMES_HOME/.env "
              "or add them to $HERMES_HOME/memorylake.json", file=sys.stderr)
        sys.exit(1)

    # Find session files
    sessions_dir = Path(hermes_home) / "sessions"

    if not sessions_dir.is_dir():
        print(f"ERROR: Sessions directory not found: {sessions_dir}", file=sys.stderr)
        sys.exit(1)

    session_files = sorted(sessions_dir.glob("*.jsonl"))

    if not session_files:
        print("No session files found.")
        sys.exit(0)

    print(f"Found {len(session_files)} session file(s)")

    total_sessions = 0
    total_api_calls = 0
    total_errors = 0

    for i, sf in enumerate(session_files, 1):
        session_id = sf.stem
        print(f"\n[{i}/{len(session_files)}] Processing: {session_id}")

        try:
            messages = parse_session_file(str(sf))
        except Exception as e:
            print(f"  WARNING: Failed to parse {sf.name}: {e}", file=sys.stderr)
            total_errors += 1
            continue

        if not messages:
            print(f"  Skipped: no text messages found")
            continue

        # Submit in batches
        batches = [messages[j:j + BATCH_SIZE] for j in range(0, len(messages), BATCH_SIZE)]
        print(f"  {len(messages)} messages in {len(batches)} batch(es)")

        for bi, batch in enumerate(batches, 1):
            try:
                result = submit_batch(host, api_key, project_id, batch, session_id)
                success = result.get("success", False)
                status = "OK" if success else result.get("message", "failed")
                print(f"  Batch {bi}/{len(batches)}: {len(batch)} messages — {status}")
                total_api_calls += 1
            except Exception as e:
                print(f"  Batch {bi}/{len(batches)}: FAILED — {e}", file=sys.stderr)
                total_errors += 1

        total_sessions += 1

    # -- Phase 2: Memory files --------------------------------------------------
    mem_submitted, mem_errors = migrate_memory_files(host, api_key, project_id, hermes_home)
    total_api_calls += mem_submitted
    total_errors += mem_errors

    print(f"\n{'='*60}")
    print(f"Migration complete:")
    print(f"  Sessions processed: {total_sessions}")
    print(f"  Memory files:       {mem_submitted}")
    print(f"  API calls made:     {total_api_calls}")
    print(f"  Errors:             {total_errors}")
    print(f"{'='*60}")

    sys.exit(1 if total_errors > 0 and total_sessions == 0 and mem_submitted == 0 else 0)


if __name__ == "__main__":
    main()
