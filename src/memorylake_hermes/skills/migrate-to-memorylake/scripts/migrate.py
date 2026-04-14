#!/usr/bin/env python3
"""Migrate hermes session conversations to MemoryLake.

Usage:
    python migrate.py --host HOST --api-key KEY --project-id PID [--user-id UID] [--session-id SID]

If --host/--api-key/--project-id are not provided, reads from $HERMES_HOME/memorylake.json.
"""

import argparse
import json
import os
import sys
from pathlib import Path

import requests

BATCH_SIZE = 20
TIMEOUT = 30.0


def load_config_from_file() -> dict:
    """Load config from $HERMES_HOME/memorylake.json."""
    hermes_home = os.environ.get("HERMES_HOME", str(Path.home() / ".hermes"))
    config_path = Path(hermes_home) / "memorylake.json"
    if config_path.exists():
        return json.loads(config_path.read_text(encoding="utf-8"))
    return {}


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


def main():
    parser = argparse.ArgumentParser(description="Migrate hermes sessions to MemoryLake")
    parser.add_argument("--host", help="MemoryLake host URL")
    parser.add_argument("--api-key", help="API key")
    parser.add_argument("--project-id", help="Project ID")
    parser.add_argument("--user-id", help="Filter sessions by user ID")
    parser.add_argument("--session-id", help="Migrate a specific session only")
    args = parser.parse_args()

    # Resolve config
    file_cfg = load_config_from_file()
    host = (args.host or file_cfg.get("host", "https://app.memorylake.ai")).rstrip("/")
    api_key = args.api_key or file_cfg.get("api_key", "")
    project_id = args.project_id or file_cfg.get("project_id", "")

    if not api_key or not project_id:
        print("ERROR: api_key and project_id are required. "
              "Provide via flags or $HERMES_HOME/memorylake.json", file=sys.stderr)
        sys.exit(1)

    # Find session files
    hermes_home = os.environ.get("HERMES_HOME", str(Path.home() / ".hermes"))
    sessions_dir = Path(hermes_home) / "sessions"

    if not sessions_dir.is_dir():
        print(f"ERROR: Sessions directory not found: {sessions_dir}", file=sys.stderr)
        sys.exit(1)

    if args.session_id:
        session_files = list(sessions_dir.glob(f"{args.session_id}*.jsonl"))
    else:
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

    print(f"\n{'='*60}")
    print(f"Migration complete:")
    print(f"  Sessions processed: {total_sessions}")
    print(f"  API calls made:     {total_api_calls}")
    print(f"  Errors:             {total_errors}")
    print(f"{'='*60}")

    sys.exit(1 if total_errors > 0 and total_sessions == 0 else 0)


if __name__ == "__main__":
    main()
